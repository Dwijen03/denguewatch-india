"""DengueWatch India REST API (Flask blueprint, mounted at /api/v1).

Conventions
  * Districts are identified by their Census 2011 code (stable, public), e.g. /districts/592.
  * All responses are JSON except /export/outbreaks.csv. Errors look like
        {"error": {"status": 400, "message": "..."}}
  * List endpoints accept ?limit= (max 5000) and ?offset= and return {"count", "total", "results"}.
  * Every query is parameterised; nothing from the URL is ever pasted into SQL text.
  * Read-only, except POST /admin/refresh which needs the ADMIN_TOKEN from .env.
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
from datetime import date, datetime, timezone

import pandas as pd
from flask import Blueprint, Response, current_app, jsonify, request
from sqlalchemy import text
from werkzeug.exceptions import HTTPException

from denguewatch import __version__, config
from denguewatch.db.connection import check_connection, get_engine
from denguewatch.db.queries import run_query

log = logging.getLogger(__name__)
api = Blueprint("api", __name__, url_prefix="/api/v1")

MAX_LIMIT = 5000
RISK_LEVELS = {"low", "medium", "high"}
GROUP_BY = {"year": "report_year", "month": "start_month", "state": "state_name", "district": "district_name"}


# ------------------------------------------------------------------ helpers

class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})T00:00:00(\.000)?(Z)?$")


def _tidy(value):
    """'2026-09-11T00:00:00.000' -> '2026-09-11' (plain dates); recurse into lists/dicts."""
    if isinstance(value, str):
        m = _MIDNIGHT.match(value)
        return m.group(1) if m else value
    if isinstance(value, list):
        return [_tidy(v) for v in value]
    if isinstance(value, dict):
        return {k: _tidy(v) for k, v in value.items()}
    return value


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> JSON-safe list of dicts (dates as 'YYYY-MM-DD', timestamps as ISO, NaN as null)."""
    if not len(df):
        return []
    df = df.copy()
    for col in df.columns:  # date / datetime objects from the database -> pandas timestamps
        if df[col].dtype == object and df[col].map(lambda v: isinstance(v, (date, datetime))).any():
            df[col] = pd.to_datetime(df[col], utc=df[col].map(lambda v: getattr(v, "tzinfo", None) is not None).any())
    return _tidy(json.loads(df.to_json(orient="records", date_format="iso", default_handler=str)))


def sql(query: str, **params) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def arg_int(name: str, default: int | None = None, lo: int | None = None, hi: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(400, f"'{name}' must be a whole number")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ApiError(400, f"'{name}' must be between {lo} and {hi}")
    return value


def arg_date(name: str) -> date | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ApiError(400, f"'{name}' must be a date like 2024-07-01")


def arg_choice(name: str, choices, default=None):
    raw = request.args.get(name, default)
    if raw is not None and raw not in choices:
        raise ApiError(400, f"'{name}' must be one of: {', '.join(sorted(choices))}")
    return raw


def paging() -> tuple[int, int]:
    return arg_int("limit", 500, 1, MAX_LIMIT), arg_int("offset", 0, 0)


def district_or_404(code: int) -> dict:
    df = sql("""SELECT d.district_id, d.census_code, d.district_name, s.state_name,
                       d.latitude::float AS latitude, d.longitude::float AS longitude
                FROM districts d JOIN states s USING (state_id) WHERE d.census_code = :c""", c=code)
    if df.empty:
        raise ApiError(404, f"No district with census code {code}")
    return records(df)[0]


@api.errorhandler(ApiError)
def _api_error(err: ApiError):
    return jsonify({"error": {"status": err.status, "message": err.message}}), err.status


@api.errorhandler(HTTPException)
def _http_error(err: HTTPException):
    return jsonify({"error": {"status": err.code, "message": err.description}}), err.code


@api.errorhandler(Exception)
def _unexpected(err: Exception):
    from sqlalchemy.exc import OperationalError
    if isinstance(err, OperationalError) or "DATABASE_URL is not set" in str(err):
        return jsonify({"error": {"status": 503, "message": "Database not available - check .env and PostgreSQL"}}), 503
    log.exception("API error")
    return jsonify({"error": {"status": 500, "message": "Internal error - see server log"}}), 500


# ------------------------------------------------------------------ service

@api.get("")
@api.get("/")
def index():
    """List of endpoints (self-documenting)."""
    rules = sorted((r.rule, sorted(r.methods - {"HEAD", "OPTIONS"}), current_app.view_functions[r.endpoint].__doc__)
                   for r in current_app.url_map.iter_rules() if r.rule.startswith("/api/v1"))
    return jsonify({"service": "DengueWatch India API", "version": __version__,
                    "endpoints": [{"path": p, "methods": m, "description": (d or "").strip().split("\n")[0]}
                                  for p, m, d in rules]})


@api.get("/health")
def health():
    """Service status, database connection and data freshness."""
    ok, detail = check_connection()
    body = {"service": "DengueWatch India API", "version": __version__,
            "database": {"connected": ok, "detail": detail}}
    if ok:
        try:
            body["data"] = records(run_query("data_freshness"))[0]
        except Exception as exc:  # noqa: BLE001 - tables may not exist yet
            body["data"] = {"error": str(exc).splitlines()[0]}
    return jsonify(body), (200 if ok else 503)


# ------------------------------------------------------------------ geography

@api.get("/states")
def states():
    """All states/UTs with district and outbreak counts."""
    df = sql("""SELECT s.state_name, s.state_code, COUNT(DISTINCT d.district_id) AS districts,
                       COUNT(o.outbreak_id) AS outbreaks
                FROM states s LEFT JOIN districts d USING (state_id)
                LEFT JOIN outbreaks o ON o.district_id = d.district_id
                GROUP BY s.state_name, s.state_code ORDER BY s.state_name""")
    return jsonify({"count": len(df), "results": records(df)})


@api.get("/districts")
def districts():
    """Districts, optionally filtered by ?state=."""
    df = sql("""SELECT d.census_code, d.district_name, s.state_name,
                       d.latitude::float AS latitude, d.longitude::float AS longitude
                FROM districts d JOIN states s USING (state_id)
                WHERE (CAST(:state AS TEXT) IS NULL OR s.state_name = :state)
                ORDER BY s.state_name, d.district_name""", state=request.args.get("state"))
    return jsonify({"count": len(df), "results": records(df)})


@api.get("/districts/<int:code>")
def district_detail(code: int):
    """One district with outbreak summary and latest risk."""
    d = district_or_404(code)
    summary = sql("""SELECT COUNT(*) AS outbreaks, COALESCE(SUM(cases), 0) AS cases, COALESCE(SUM(deaths), 0) AS deaths,
                            COUNT(DISTINCT report_year) AS years_with_outbreaks,
                            MIN(start_date) AS first_outbreak, MAX(start_date) AS last_outbreak
                     FROM v_outbreaks WHERE census_code = :c""", c=code)
    risk = run_query("current_risk")
    risk = risk[risk["census_code"] == code]
    d.pop("district_id")
    return jsonify({**d, "summary": records(summary)[0],
                    "current_risk": records(risk.drop(columns=["state_name", "district_name", "census_code",
                                                               "latitude", "longitude"]))[0] if len(risk) else None})


# ------------------------------------------------------------------ outbreaks

@api.get("/outbreaks")
def outbreaks():
    """Outbreak records. Filters: state, district_code, year, from, to (start date), level of review."""
    limit, offset = paging()
    params = dict(state=request.args.get("state"), code=arg_int("district_code"), year=arg_int("year", None, 2009, 2100),
                  d_from=arg_date("from"), d_to=arg_date("to"), limit=limit, offset=offset)
    where = """WHERE (CAST(:state AS TEXT) IS NULL OR state_name = :state)
                 AND (CAST(:code AS INT) IS NULL OR census_code = :code)
                 AND (CAST(:year AS INT) IS NULL OR report_year = :year)
                 AND (CAST(:d_from AS DATE) IS NULL OR start_date >= :d_from)
                 AND (CAST(:d_to AS DATE) IS NULL OR start_date <= :d_to)"""
    total = int(sql(f"SELECT COUNT(*) AS n FROM v_outbreaks {where}", **params)["n"].iloc[0])
    df = sql(f"""SELECT outbreak_id, state_name, district_name, census_code, district_name_raw, report_year,
                        report_week, start_date, cases, deaths, current_status, is_suspected, needs_review,
                        idsp_unique_id
                 FROM v_outbreaks {where}
                 ORDER BY start_date DESC NULLS LAST, outbreak_id DESC LIMIT :limit OFFSET :offset""", **params)
    return jsonify({"count": len(df), "total": total, "limit": limit, "offset": offset, "results": records(df)})


@api.get("/outbreaks/summary")
def outbreaks_summary():
    """Outbreaks, cases and deaths grouped by ?group_by=year|month|state|district (filters: state, year)."""
    group = arg_choice("group_by", GROUP_BY, "year")
    col = GROUP_BY[group]
    df = sql(f"""SELECT {col} AS {group}, COUNT(*) AS outbreaks, SUM(cases) AS cases, SUM(deaths) AS deaths
                 FROM v_outbreaks
                 WHERE {col} IS NOT NULL
                   AND (CAST(:state AS TEXT) IS NULL OR state_name = :state)
                   AND (CAST(:year AS INT) IS NULL OR report_year = :year)
                 GROUP BY {col} ORDER BY {'outbreaks DESC' if group in ('state', 'district') else col}""",
             state=request.args.get("state"), year=arg_int("year", None, 2009, 2100))
    return jsonify({"group_by": group, "count": len(df), "results": records(df)})


@api.get("/districts/<int:code>/timeline")
def timeline(code: int):
    """Weekly outbreaks, cases and weather for one district (?from=, ?to=; default last 2 years)."""
    d = district_or_404(code)
    d_to = arg_date("to") or date.today() + pd.Timedelta(days=21).to_pytimedelta()
    d_from = arg_date("from") or (pd.Timestamp(d_to) - pd.DateOffset(years=2)).date()
    df = sql("""
        WITH ob AS (
            SELECT date_trunc('week', start_date)::DATE AS week_start, COUNT(*) AS outbreaks, SUM(cases) AS cases
            FROM outbreaks WHERE district_id = :id AND start_date BETWEEN :f AND :t GROUP BY 1)
        SELECT w.week_start, COALESCE(ob.outbreaks, 0) AS outbreaks, COALESCE(ob.cases, 0) AS cases,
               w.rain_mm::float AS rain_mm, w.temp_mean_c::float AS temp_mean_c, w.temp_min_c::float AS temp_min_c,
               w.temp_max_c::float AS temp_max_c, w.humidity_pct::float AS humidity_pct, w.includes_forecast
        FROM v_weather_weekly w LEFT JOIN ob USING (week_start)
        WHERE w.district_id = :id AND w.week_start BETWEEN :f AND :t
        ORDER BY w.week_start""", id=d["district_id"], f=d_from, t=d_to)
    return jsonify({"district": d["district_name"], "state": d["state_name"], "census_code": code,
                    "from": str(d_from), "to": str(d_to), "count": len(df), "results": records(df)})


# ------------------------------------------------------------------ weather

@api.get("/weather/<int:code>")
def weather(code: int):
    """Daily (default) or weekly weather for one district; observed and forecast. ?from=, ?to=, ?granularity=."""
    d = district_or_404(code)
    gran = arg_choice("granularity", {"daily", "weekly"}, "daily")
    d_to = arg_date("to") or date.today() + pd.Timedelta(days=21).to_pytimedelta()
    d_from = arg_date("from") or (pd.Timestamp(d_to) - pd.Timedelta(days=90)).date()
    if (d_to - d_from).days > 366 * 20:
        raise ApiError(400, "Date range too long (max 20 years)")
    if gran == "daily":
        df = sql("""SELECT obs_date, precip_mm::float AS precip_mm, temp_mean_c::float AS temp_mean_c,
                           temp_min_c::float AS temp_min_c, temp_max_c::float AS temp_max_c,
                           rel_humidity_pct::float AS humidity_pct, data_type, source
                    FROM weather_daily WHERE district_id = :id AND obs_date BETWEEN :f AND :t ORDER BY obs_date""",
                 id=d["district_id"], f=d_from, t=d_to)
    else:
        df = sql("""SELECT week_start, rain_mm::float AS rain_mm, temp_mean_c::float AS temp_mean_c,
                           temp_min_c::float AS temp_min_c, temp_max_c::float AS temp_max_c,
                           humidity_pct::float AS humidity_pct, days, includes_forecast
                    FROM v_weather_weekly WHERE district_id = :id AND week_start BETWEEN :f AND :t ORDER BY week_start""",
                 id=d["district_id"], f=d_from, t=d_to)
    return jsonify({"district": d["district_name"], "state": d["state_name"], "census_code": code,
                    "granularity": gran, "from": str(d_from), "to": str(d_to), "count": len(df), "results": records(df)})


# ------------------------------------------------------------------ risk and model

@api.get("/risk/current")
def risk_current():
    """Latest risk score and level for every district (active model). Filters: ?state=, ?level=."""
    level = arg_choice("level", RISK_LEVELS)
    df = run_query("current_risk", state=request.args.get("state"), level=level)
    counts = df["risk_level"].value_counts().to_dict() if len(df) else {}
    window = ({"from": str(df["target_week_start"].iloc[0])[:10], "to": str(df["target_week_end"].iloc[0])[:10]}
              if len(df) else None)
    return jsonify({"forecast_window": window, "levels": counts, "count": len(df), "results": records(df)})


@api.get("/risk/<int:code>")
def risk_history(code: int):
    """Risk history and explanation for one district (all stored weeks of the active model)."""
    d = district_or_404(code)
    df = sql("""SELECT p.target_week_start, p.target_week_start + 27 AS target_week_end,
                       p.risk_score::float AS risk_score, p.risk_level, p.top_factors
                FROM predictions p JOIN model_versions m ON m.model_id = p.model_id AND m.is_active
                WHERE p.district_id = :id ORDER BY p.target_week_start""", id=d["district_id"])
    return jsonify({"district": d["district_name"], "state": d["state_name"], "census_code": code,
                    "count": len(df), "results": records(df)})


@api.get("/model")
def model_info():
    """The active model: algorithm, training period and validation metrics."""
    df = sql("""SELECT model_id, algorithm, train_period, trained_at, test_metrics
                FROM model_versions WHERE is_active""")
    if df.empty:
        raise ApiError(404, "No model has been trained yet")
    row = records(df)[0]
    m = row.pop("test_metrics") or {}
    if isinstance(m, str):
        m = json.loads(m)
    row["train_period"] = m.get("train_period", row["train_period"])  # full text (DB column is shortened)
    row["evaluation"] = {k: m.get(k) for k in ("test_period", "test_base_rate", "test_pr_auc", "test_roc_auc",
                                              "high_alert", "medium_or_high_alert", "warned", "comparison",
                                              "importance_by_group", "thresholds")}
    row["top_features"] = dict(list((m.get("feature_importance") or {}).items())[:10])
    return jsonify(row)


# ------------------------------------------------------------------ data pipeline status

@api.get("/reports")
def reports():
    """Weekly IDSP reports loaded (?year=), newest first."""
    limit, offset = paging()
    df = sql("""SELECT year, week_no, week_start, week_end, file_name, source_url, dengue_rows, loaded_at
                FROM reports WHERE (CAST(:y AS INT) IS NULL OR year = :y)
                ORDER BY year DESC, week_no DESC LIMIT :limit OFFSET :offset""",
             y=arg_int("year", None, 2009, 2100), limit=limit, offset=offset)
    return jsonify({"count": len(df), "results": records(df)})


@api.get("/pipeline-runs")
def pipeline_runs():
    """Most recent automated jobs (data loads, weather updates, model scoring)."""
    df = sql("""SELECT run_id, job_name, started_at, finished_at, status, rows_added, message
                FROM pipeline_runs ORDER BY run_id DESC LIMIT :n""", n=arg_int("limit", 20, 1, 200))
    return jsonify({"count": len(df), "results": records(df)})


@api.get("/signals")
def signals():
    """Unverified dengue reports found in the news by the AI news agent (Step 11). ?all=1 includes irrelevant."""
    limit, offset = paging()
    df = sql("""SELECT n.signal_id, n.published_at, n.headline, n.source, n.url, s.state_name,
                       d.district_name, d.census_code, n.state_name_raw, n.district_name_raw,
                       n.extracted_cases, n.extracted_deaths, n.confidence::float AS confidence, n.summary,
                       n.is_relevant, n.verification_status
                FROM news_signals n LEFT JOIN districts d USING (district_id)
                LEFT JOIN states s ON s.state_id = d.state_id
                WHERE (n.is_relevant OR :all)
                ORDER BY n.published_at DESC NULLS LAST LIMIT :limit OFFSET :offset""",
             all=request.args.get("all") == "1", limit=limit, offset=offset)
    return jsonify({"note": "Unverified signals from news headlines - not official IDSP data",
                    "count": len(df), "results": records(df)})


@api.route("/ask", methods=["GET", "POST"])
def ask():
    """Ask a question in plain English (?q= or JSON {"question": ...}); the AI agent answers with read-only SQL."""
    from denguewatch.agent.llm import NotConfigured
    from denguewatch.agent.sql_agent import ask as agent_ask, limiter
    body = request.get_json(silent=True) or {}
    question = (request.args.get("q") or body.get("question") or "").strip()
    if not question:
        raise ApiError(400, "Give a question: /api/v1/ask?q=Which districts had the most outbreaks in 2025?")
    if len(question) > 300:
        raise ApiError(400, "Questions are limited to 300 characters")
    if not limiter.allow():
        raise ApiError(429, "Too many questions - please wait a minute (protects the free AI quota)")
    try:
        res = agent_ask(question)
    except NotConfigured as exc:
        raise ApiError(503, str(exc)) from exc
    res["note"] = "AI-generated query and summary - check the SQL and numbers before relying on them"
    return jsonify(res)


# ------------------------------------------------------------------ export

@api.get("/export/outbreaks.csv")
def export_outbreaks():
    """CSV download of outbreaks (filters: state, district_code, year)."""
    df = sql("""SELECT outbreak_id, state_name, district_name, census_code, district_name_raw, report_year, report_week,
                       start_date, cases, deaths, current_status, is_suspected, needs_review, idsp_unique_id
                FROM v_outbreaks
                WHERE (CAST(:state AS TEXT) IS NULL OR state_name = :state)
                  AND (CAST(:code AS INT) IS NULL OR census_code = :code)
                  AND (CAST(:year AS INT) IS NULL OR report_year = :year)
                ORDER BY start_date""", state=request.args.get("state"), code=arg_int("district_code"),
             year=arg_int("year", None, 2009, 2100))
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=denguewatch_outbreaks.csv"})


# ------------------------------------------------------------------ admin

def _job_predict():
    from denguewatch.db.loader import pipeline_run
    from denguewatch.ml.predict import predict_latest
    with pipeline_run("predict") as run:
        run["rows"] = len(predict_latest(weeks_back=0))


def _job_weather(history: bool):
    from denguewatch.db.weather_store import update_weather
    update_weather(history=history, recent=True)


def _scheduled(name: str):
    from denguewatch.scheduler.jobs import run_job
    return lambda: run_job(name)


JOBS = {
    "predict": _job_predict,
    "weather": lambda: _job_weather(True),
    "weather_recent": lambda: _job_weather(False),
    "daily": _scheduled("daily_update"),        # Step 10 jobs
    "reports": _scheduled("weekly_reports"),
    "retrain": _scheduled("monthly_retrain"),
    "news": _scheduled("news_signals"),         # Step 11
}


@api.get("/schedule")
def schedule():
    """Automatic updates (Step 10): last successful run, whether overdue, and next planned run."""
    from denguewatch.scheduler.jobs import MAX_AGE, last_success
    from denguewatch.scheduler.runner import next_runs
    last = last_success()
    nxt = {r["job"]: r["next_run"] for r in next_runs()}
    now = datetime.now(timezone.utc)
    out = [{"job": job,
            "last_success": last[job].isoformat(timespec="minutes") if last[job] else None,
            "overdue": last[job] is None or now - last[job] > age,
            "next_run": nxt.get(job)} for job, age in MAX_AGE.items()]
    return jsonify({"scheduler_running": bool(nxt), "results": out})


@api.post("/admin/refresh")
def admin_refresh():
    """Start a background job (?job=predict|weather|weather_recent|daily|reports|retrain|news).
    Needs header X-Admin-Token."""
    token = request.headers.get("X-Admin-Token", "")
    if not config.ADMIN_TOKEN or token != config.ADMIN_TOKEN:
        raise ApiError(401, "Missing or wrong X-Admin-Token header")
    job = arg_choice("job", JOBS, "predict")
    threading.Thread(target=JOBS[job], name=f"refresh-{job}", daemon=True).start()
    return jsonify({"started": job, "check_progress": "/api/v1/pipeline-runs"}), 202
