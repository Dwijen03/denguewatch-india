"""Step 11: "Ask DengueWatch" - answer plain-English questions with read-only SQL.

    question --(Gemini)--> SQL --guardrails--> read-only query --(Gemini)--> short answer

Agent loop: if the SQL fails a guardrail or the database reports an error, the error is
shown to the model and it gets one more try (self-correction). Every question is logged in
the agent_queries table with the SQL that ran, so answers can be audited.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import date
from functools import lru_cache

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from denguewatch.agent.llm import Gemini, LLMError, NotConfigured, default_client
from denguewatch.agent.sql_guard import UnsafeSQL, check_sql
from denguewatch.db.connection import get_engine

logging.getLogger("sqlglot").setLevel(logging.ERROR)
log = logging.getLogger(__name__)

MAX_ROWS = 200
MAX_QUESTION = 300
TIMEOUT_MS = 5000
ROWS_FOR_ANSWER = 40

SCHEMA = """
PostgreSQL views and tables you may query (read-only):

v_outbreaks  - one row per dengue outbreak reported in the IDSP weekly reports (India, 2009-2026)
  outbreak_id, state_name, district_name, census_code, district_name_raw,
  report_year, report_week   (year/week of the IDSP report that listed it),
  start_date (date the outbreak started), start_year, start_month (1-12),
  cases, deaths (may be NULL), is_suspected, is_mixed (mixed with other diseases),
  current_status ('Under control', 'Under surveillance', ...), report_lag_days (days from start to report),
  needs_review (TRUE = district match uncertain), idsp_unique_id
v_district_year - one row per district per report year: state_name, district_name, census_code, year, outbreaks, cases, deaths
  (only outbreaks matched to a district - for state or national totals use v_outbreaks)
v_weather_weekly - weekly weather per district: district_id, week_start (Monday), rain_mm (weekly total),
  temp_mean_c, temp_min_c, temp_max_c, humidity_pct, days, includes_forecast (TRUE = contains forecast days)
  (join to districts USING (district_id) to get district_name / census_code)
v_risk_latest - the model's current outbreak risk for the next 4 weeks: state_name, district_name, census_code,
  target_week_start, target_week_end, risk_score (0-1, ranks risk; not a probability), risk_level ('low','medium','high')
  (already only the current forecast week - never filter it by a date)
districts - district_id, census_code, state_id, district_name, latitude, longitude (640 Census 2011 districts)
states - state_id, state_name, state_code
reports - one row per IDSP weekly report: year, week_no, week_start, week_end, source_url, file_name, dengue_rows
news_signals - UNVERIFIED dengue reports found in news by the news agent: published_at, headline, url, source,
  district_id, state_name_raw, district_name_raw, extracted_cases, extracted_deaths, is_relevant, verification_status
""".strip()

RULES = """
Rules:
- Write ONE PostgreSQL SELECT query (WITH ... SELECT is fine). Never modify data.
- Use only the views/tables listed above, with no schema prefix.
- District names are Census 2011 spellings and may differ from today's names (Bangalore not Bengaluru,
  Gurgaon not Gurugram, Belgaum not Belagavi, Raigarh for Raigad, Mysore, Allahabad for Prayagraj).
  Match names with ILIKE '%name%' and also try the old name when you know it.
- "Outbreaks" means rows of v_outbreaks; "cases" means SUM(cases). Use report_year for "in <year>" unless the
  question is about when outbreaks started. Ignore rows with needs_review = TRUE only if asked for exact counts.
- Give columns readable aliases, order results sensibly and add LIMIT 50 unless the user needs all rows.
- If the question cannot be answered from this data (not about dengue, weather, risk or these reports), or asks to
  change data, refuse.
Reply with JSON only: {"sql": "...", "explanation": "one sentence on what the query does",
                       "chart": {"type": "bar" | "line" | "none", "x": "<column>", "y": "<column>"}}
or, to refuse: {"refuse": "short reason"}
""".strip()

ANSWER_SYSTEM = """You explain database results about dengue in India to public-health officers.
Use ONLY the numbers in the result rows given. Do not add outside facts. If the result is empty, say no matching data
was found. Be concise: 1-3 sentences, plain English, mention key numbers. Reply as JSON: {"answer": "..."}"""


class RateLimiter:
    """At most `limit` calls per `window` seconds (protects the free Gemini quota)."""

    def __init__(self, limit: int = 10, window: float = 60.0):
        self.limit, self.window, self.calls = limit, window, deque()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = time.monotonic()
            while self.calls and now - self.calls[0] > self.window:
                self.calls.popleft()
            if len(self.calls) >= self.limit:
                return False
            self.calls.append(now)
            return True


limiter = RateLimiter()


@lru_cache(maxsize=1)
def _state_names(engine_id: int) -> str:
    with get_engine().connect() as conn:
        return ", ".join(r[0] for r in conn.execute(text("SELECT state_name FROM states ORDER BY 1")))


def system_prompt(engine: Engine) -> str:
    try:
        states = _state_names(id(engine))
    except Exception:  # noqa: BLE001
        states = "(unavailable)"
    return (f"You are the query agent of DengueWatch India. Today is {date.today():%Y-%m-%d}. "
            f"The latest IDSP data is from 2026.\n\n{SCHEMA}\n\nState names: {states}\n\n{RULES}")


def run_safe(sql: str, engine: Engine) -> pd.DataFrame:
    """Run checked SQL in a READ ONLY transaction with a time limit and a row cap."""
    sql = check_sql(sql)
    wrapped = f"SELECT * FROM ({sql}) AS agent_result LIMIT {MAX_ROWS}"
    wrapped = wrapped.replace("%", "%%")    # psycopg treats % as a parameter marker; ILIKE '%Pune%' needs %%
    with engine.connect() as conn:
        try:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")       # first statement of the transaction
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {TIMEOUT_MS}")
            result = conn.exec_driver_sql(wrapped)
            return pd.DataFrame(result.fetchall(), columns=list(result.keys()))
        finally:
            conn.rollback()                                          # nothing is ever committed


def _jsonable(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso", default_handler=str))


def _log(engine: Engine, question: str, res: dict) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO agent_queries (question, sql_text, status, rows_returned, attempts,
                                                           duration_ms, message)
                                 VALUES (:q, :s, :st, :n, :a, :ms, :m)"""),
                         {"q": question[:MAX_QUESTION], "s": res.get("sql"), "st": res["status"],
                          "n": len(res.get("rows") or []) if res["status"] == "answered" else None,
                          "a": res.get("attempts"), "ms": res.get("duration_ms"), "m": (res.get("message") or "") + (f" [model {res.get('model')}]" if res.get("model") else "") or None})
    except Exception:  # noqa: BLE001 - logging must never break an answer
        log.exception("Could not log agent query")


def ask(question: str, llm: Gemini | None = None, engine: Engine | None = None, max_attempts: int = 2,
        log_query: bool = True, summarise: bool = True) -> dict:
    """Answer one question. Returns a dict with status answered / refused / blocked / failed."""
    t0 = time.perf_counter()
    question = (question or "").strip()
    llm = llm or default_client()
    engine = engine or get_engine()
    res: dict = {"question": question, "status": "failed", "answer": None, "sql": None, "explanation": None,
                 "columns": [], "rows": [], "chart": None, "attempts": 0, "message": None}
    if not question:
        raise ValueError("Please type a question")
    if len(question) > MAX_QUESTION:
        raise ValueError(f"Questions are limited to {MAX_QUESTION} characters")
    if not llm.configured:
        raise NotConfigured("The AI agent is not set up - run setup_agent.bat to add a free Gemini API key")

    system, prompt = system_prompt(engine), f"Question: {question}"
    df = None
    try:
        for attempt in range(1, max_attempts + 1):
            res["attempts"] = attempt
            plan = llm.generate_json(system, prompt)
            if not isinstance(plan, dict):
                raise LLMError("unexpected reply format")
            if plan.get("refuse"):
                res.update(status="refused", answer=f"I can't answer that from DengueWatch data: {plan['refuse']}",
                           message=str(plan["refuse"])[:300])
                break
            res.update(sql=plan.get("sql"), explanation=plan.get("explanation"), chart=plan.get("chart"))
            try:
                df = run_safe(res["sql"] or "", engine)
                break
            except UnsafeSQL as exc:
                res.update(status="blocked", message=f"Guardrail: {exc}")
                problem = f"The query was rejected by a safety check: {exc}"
            except Exception as exc:  # noqa: BLE001 - database error: let the model fix its query
                msg = str(getattr(exc, "orig", exc)).splitlines()[0][:300]
                res.update(status="failed", message=f"Database error: {msg}")
                problem = f"The query failed in PostgreSQL: {msg}"
            prompt = (f"Question: {question}\n\nYour previous SQL:\n{res['sql']}\n\n{problem}\n"
                      "Write a corrected query (same JSON format).")
        if df is not None:
            rows = _jsonable(df)
            res.update(status="answered", columns=list(df.columns), rows=rows, message=None)
            if not summarise:                       # evaluation only checks the SQL result (saves quota)
                res["answer"] = f"{len(rows)} rows"
                reply = {"answer": res["answer"]}
            else:
                try:
                    reply = llm.generate_json(ANSWER_SYSTEM, (
                        f"Question: {question}\nSQL: {res['sql']}\nRows returned: {len(rows)}"
                        f"{' (showing first ' + str(ROWS_FOR_ANSWER) + ')' if len(rows) > ROWS_FOR_ANSWER else ''}\n"
                        f"Result: {json.dumps(rows[:ROWS_FOR_ANSWER], default=str)}"))
                except LLMError as exc:             # the data is still valid - show it without a summary
                    reply = {"answer": f"Here are the results ({len(rows)} rows). "
                                       f"The written summary is unavailable right now: {exc}"}
            res["answer"] = reply.get("answer") if isinstance(reply, dict) else str(reply)
        elif res["status"] == "blocked":
            res["answer"] = "The query the AI wrote was blocked by a safety check, so nothing was run."
        elif res["status"] == "failed":
            res["answer"] = "Sorry, I could not build a working query for that question. Try rephrasing it."
    except LLMError as exc:
        res.update(status="failed", message=str(exc), answer=str(exc))
    res["model"] = getattr(llm, "last_model", None)
    res["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    if log_query:
        _log(engine, question, res)
    return res
