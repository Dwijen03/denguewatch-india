"""Step 10: the three automatic jobs, each logged in the pipeline_runs table.

    daily_update      weather (NASA POWER + Open-Meteo) -> new risk scores        every morning
    weekly_reports    new IDSP PDFs only -> parse -> clean -> load -> risk scores  every Monday
    monthly_retrain   retrain the model; keep it only if it is not clearly worse  1st of the month
    news_signals      AI news agent: unverified dengue reports from the news      every day (Step 11)

The same functions are used by the in-app scheduler (runner.py), by scripts/run_jobs.py
(Windows Task Scheduler) and by POST /api/v1/admin/refresh.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from denguewatch import config
from denguewatch.db.connection import get_engine
from denguewatch.db.loader import load_outbreaks, load_reports, pipeline_run

log = logging.getLogger(__name__)

# A retrained model replaces the active one only if its test PR AUC is at least this share of the old one.
RETRAIN_TOLERANCE = 0.95

# When a job counts as "due" (used for catch-up after the laptop was switched off).
MAX_AGE = {
    "daily_update": timedelta(hours=20),
    "weekly_reports": timedelta(days=7),
    "monthly_retrain": timedelta(days=30),
    "news_signals": timedelta(hours=20),
}

_locks: dict[str, threading.Lock] = {name: threading.Lock() for name in MAX_AGE}


# ---------------------------------------------------------------- incremental report ingestion

def _with_retry(fn: Callable, attempts: int = 3, pause: float = 20.0):
    """The NCDC site sometimes drops connections; try a few times before giving up."""
    for n in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if n == attempts:
                raise
            log.warning("Attempt %d/%d failed (%s) - retrying in %.0f s", n, attempts, exc, pause)
            time.sleep(pause)


def ingest_new_reports(refs: list | None = None, processed_dir: Path | None = None,
                       engine: Engine | None = None, workers: int | None = 2) -> dict:
    """Download and parse only reports that are not in reports_index.csv yet, then refresh the
    cleaned outbreak table and upsert it into PostgreSQL. Returns counts."""
    from denguewatch.ingestion.cleaning import clean_rows, deduplicate
    from denguewatch.ingestion.districts import DistrictMatcher
    from denguewatch.ingestion.pipeline import download_all, parse_all
    from denguewatch.ingestion.reports import list_reports

    P = processed_dir or config.PROCESSED_DIR
    index_csv, raw_csv = P / "reports_index.csv", P / "dengue_rows_raw.csv"
    if not index_csv.exists() or not raw_csv.exists():
        raise RuntimeError("No processed data yet - run run_data_pipeline.bat (Step 4) once first")

    old_index = pd.read_csv(index_csv)
    known = set(zip(old_index["year"].astype(int), old_index["week"].astype(int)))  # (year, week) already processed
    refs = _with_retry(list_reports) if refs is None else refs
    new_refs = [r for r in refs if (r.year, r.week) not in known]
    result = {"listed": len(refs), "new_reports": len(new_refs), "new_rows": 0, "outbreaks": None,
              "latest": f"{refs[-1].year}-W{refs[-1].week:02d}" if refs else None}
    if not new_refs:
        log.info("No new IDSP reports (latest listed %s)", result["latest"])
        return result

    log.info("%d new report(s): %s", len(new_refs), ", ".join(r.filename for r in new_refs))
    new_index, new_raw = download_all(new_refs), pd.DataFrame()
    if (new_index["download_status"] == "ok").any():
        new_raw, new_index = parse_all(new_index, workers)
    result["new_rows"] = len(new_raw)

    index = pd.concat([old_index, new_index], ignore_index=True)
    raw = pd.concat([pd.read_csv(raw_csv), new_raw], ignore_index=True)
    # Cleaning and de-duplication are re-run on all rows: an outbreak listed again in a
    # new report must update the existing record (times_listed, status), not create a new one.
    clean = clean_rows(raw, DistrictMatcher())
    outbreaks = deduplicate(clean)

    index.to_csv(index_csv, index=False)
    raw.to_csv(raw_csv, index=False)
    clean.to_csv(P / "dengue_rows_clean.csv", index=False)
    outbreaks.to_csv(P / "dengue_outbreaks.csv", index=False)

    engine = engine or get_engine()
    load_reports(outbreaks, index_csv, engine)
    load_outbreaks(outbreaks, engine)
    result["outbreaks"] = len(outbreaks)
    return result


# ---------------------------------------------------------------- the jobs

def _predict(weeks_back: int, engine: Engine) -> int:
    from denguewatch.ml.predict import predict_latest
    return len(predict_latest(weeks_back=weeks_back, engine=engine))


def daily_update(engine: Engine | None = None) -> dict:
    """Fetch new weather (history gaps + last 10 days + 16-day forecast) and re-score risk."""
    from denguewatch.db.weather_store import update_weather
    engine = engine or get_engine()
    with pipeline_run("daily_update", engine) as run:
        weather = update_weather(history=True, recent=True, engine=engine)
        scored = _predict(1, engine)
        run["rows"] = scored
        run["message"] = f"weather rows {sum(v.get('rows', 0) for v in weather.values())}, scored {scored}"
    return {"weather": weather, "scored": scored}


def weekly_reports(engine: Engine | None = None, refs: list | None = None) -> dict:
    """Check NCDC for new weekly outbreak reports; load them and re-score risk if any were found."""
    engine = engine or get_engine()
    with pipeline_run("weekly_reports", engine) as run:
        res = ingest_new_reports(refs=refs, engine=engine)
        if res["new_reports"]:
            res["scored"] = _predict(1, engine)
        run["rows"] = res["new_rows"]
        run["message"] = f"{res['new_reports']} new report(s); latest listed {res['latest']}"
    return res


def _active_pr_auc(engine: Engine) -> float | None:
    with engine.connect() as conn:
        v = conn.execute(text("SELECT (test_metrics->>'test_pr_auc')::float FROM model_versions "
                              "WHERE is_active")).scalar()
    return v


def monthly_retrain(engine: Engine | None = None, train: Callable | None = None) -> dict:
    """Retrain on all data. The new model becomes active only if it is not clearly worse
    (test PR AUC >= 95% of the current model's); otherwise the current model is kept."""
    from denguewatch.ml.data import load_from_db
    from denguewatch.ml.predict import register_model
    from denguewatch.ml.train import save_bundle, train_and_evaluate
    engine = engine or get_engine()
    train = train or train_and_evaluate
    with pipeline_run("monthly_retrain", engine) as run:
        old = _active_pr_auc(engine)
        bundle = train(load_from_db(engine))
        new = bundle["metrics"]["test_pr_auc"]
        accept = old is None or new >= RETRAIN_TOLERANCE * old
        res = {"old_pr_auc": old, "new_pr_auc": new, "accepted": accept}
        if accept:
            res["model_id"] = register_model(save_bundle(bundle), bundle, engine)
            res["scored"] = _predict(11, engine)
            run["rows"] = res["scored"]
        old_txt = "none" if old is None else f"{old:.3f}"
        run["message"] = (f"PR AUC {new:.3f} vs {old_txt}: "
                          + (f"activated model {res['model_id']}" if accept else "kept current model"))
    return res


def news_signals(engine: Engine | None = None) -> dict:
    """Step 11 news agent (skipped quietly when no Gemini key is set)."""
    from denguewatch.agent import news_agent
    engine = engine or get_engine()
    with pipeline_run("news_signals", engine) as run:
        res = news_agent.run(engine=engine)
        run["rows"] = res.get("relevant", 0)
        run["message"] = (f"skipped: {res['skipped']}" if "skipped" in res else
                          f"{res['new']} new articles, {res['relevant']} relevant, "
                          f"{res['newly_matched_idsp']} matched IDSP")
    return res


JOBS: dict[str, Callable[..., dict]] = {
    "daily_update": daily_update,
    "weekly_reports": weekly_reports,
    "monthly_retrain": monthly_retrain,
    "news_signals": news_signals,
}


def run_job(name: str, **kwargs) -> dict | None:
    """Run a job unless the same job is already running (scheduler and admin API share this)."""
    lock = _locks[name]
    if not lock.acquire(blocking=False):
        log.info("%s is already running - skipped", name)
        return None
    try:
        log.info("Job %s started", name)
        result = JOBS[name](**kwargs)
        log.info("Job %s finished: %s", name, result)
        return result
    except Exception:  # noqa: BLE001 - a failed job is logged in pipeline_runs; keep the app alive
        log.exception("Job %s failed", name)
        return None
    finally:
        lock.release()


# ---------------------------------------------------------------- what is due

def mark_interrupted(older_than_hours: int = 6, engine: Engine | None = None) -> int:
    """Runs still 'running' after hours were cut off (laptop shut down, window closed): mark them failed."""
    with (engine or get_engine()).begin() as conn:
        return conn.execute(text("""
            UPDATE pipeline_runs SET status = 'failed', finished_at = now(),
                   message = COALESCE(message, 'interrupted (app or computer was closed)')
            WHERE status = 'running' AND started_at < now() - make_interval(hours => :h)"""),
            {"h": older_than_hours}).rowcount


def last_success(engine: Engine | None = None) -> dict[str, datetime | None]:
    with (engine or get_engine()).connect() as conn:
        rows = conn.execute(text("""SELECT job_name, MAX(finished_at) FROM pipeline_runs
                                    WHERE status = 'success' GROUP BY job_name""")).all()
    done = {name: ts for name, ts in rows}
    # Earlier manual runs (Steps 4-7) count as well.
    equivalents = {"daily_update": "update_weather", "weekly_reports": "load_database"}
    out = {}
    for job, alt in [*equivalents.items(), ("monthly_retrain", None), ("news_signals", None)]:
        times = [t for t in (done.get(job), done.get(alt)) if t is not None]
        out[job] = max(times) if times else None
    # The model trained by hand in Step 7 counts as a training run too.
    with (engine or get_engine()).connect() as conn:
        trained = conn.execute(text("SELECT MAX(trained_at) FROM model_versions")).scalar()
    if trained is not None and (out["monthly_retrain"] is None or trained > out["monthly_retrain"]):
        out["monthly_retrain"] = trained
    return out


def due_jobs(now: datetime | None = None, engine: Engine | None = None) -> list[str]:
    """Jobs whose last successful run is older than MAX_AGE (in run order)."""
    now = now or datetime.now(UTC)
    last = last_success(engine)
    return [job for job, age in MAX_AGE.items() if last[job] is None or now - last[job] > age]
