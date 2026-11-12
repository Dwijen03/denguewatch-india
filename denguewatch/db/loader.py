"""Create the database schema and load Step 4 outputs into PostgreSQL.

All loads are "upserts" (INSERT ... ON CONFLICT ... DO UPDATE), so running the
loader again after new reports arrive adds the new rows and never duplicates old ones.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from denguewatch import config
from denguewatch.db.connection import get_engine
from denguewatch.ingestion.reports import week_dates

log = logging.getLogger(__name__)

SCHEMA_SQL = config.PROJECT_ROOT / "database" / "schema.sql"
REFERENCE = config.DATA_DIR / "reference"
TABLES_IN_DROP_ORDER = ["predictions", "model_versions", "news_signals", "weather_daily", "outbreaks",
                        "reports", "diseases", "district_aliases", "districts", "states", "pipeline_runs"]


def _none(v):
    """pandas NaN / NaT -> None, so they are stored as SQL NULL."""
    return None if pd.isna(v) else v


# ---------------------------------------------------------------- schema

def create_schema(engine: Engine | None = None, reset: bool = False) -> None:
    """Run database/schema.sql. With reset=True all project tables are dropped first."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        if reset:
            for view in ("v_district_year", "v_outbreaks", "v_weather_weekly"):
                conn.exec_driver_sql(f"DROP VIEW IF EXISTS {view} CASCADE")
            for table in TABLES_IN_DROP_ORDER:
                conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table} CASCADE")
        conn.exec_driver_sql(SCHEMA_SQL.read_text(encoding="utf-8"))
    log.info("Schema ready%s", " (reset)" if reset else "")


@contextmanager
def pipeline_run(job_name: str, engine: Engine | None = None):
    """Record a job in pipeline_runs. Use: with pipeline_run('load') as run: run['rows'] = n"""
    engine = engine or get_engine()
    with engine.begin() as conn:
        run_id = conn.execute(text("INSERT INTO pipeline_runs (job_name) VALUES (:j) RETURNING run_id"),
                              {"j": job_name}).scalar_one()
    info = {"rows": None, "message": None}
    try:
        yield info
        status = "success"
    except Exception as exc:
        status, info["message"] = "failed", str(exc)[:500]
        raise
    finally:
        with engine.begin() as conn:
            conn.execute(text("""UPDATE pipeline_runs SET finished_at = now(), status = :s,
                                 rows_added = :n, message = :m WHERE run_id = :id"""),
                         {"s": status, "n": info["rows"], "m": info["message"], "id": run_id})


# ---------------------------------------------------------------- reference data

def load_reference(engine: Engine | None = None) -> dict[str, int]:
    """Load states, districts, district aliases and diseases from data/reference/."""
    engine = engine or get_engine()
    districts = pd.read_csv(REFERENCE / "districts.csv")
    aliases = pd.read_csv(REFERENCE / "district_aliases.csv")
    states = districts[["state_name", "state_code"]].drop_duplicates()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO states (state_name, state_code) VALUES (:state_name, :state_code)
            ON CONFLICT (state_name) DO UPDATE SET state_code = EXCLUDED.state_code"""),
            states.to_dict("records"))
        conn.execute(text("""
            INSERT INTO districts (census_code, state_id, district_name, latitude, longitude)
            SELECT :census_code, s.state_id, :district_name, :latitude, :longitude
            FROM states s WHERE s.state_name = :state_name
            ON CONFLICT (census_code) DO UPDATE
               SET state_id = EXCLUDED.state_id, district_name = EXCLUDED.district_name,
                   latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude"""),
            districts.to_dict("records"))
        conn.execute(text("""
            INSERT INTO district_aliases (district_id, state_id, alias_name, reason)
            SELECT d.district_id, s.state_id, :alias, :reason
            FROM districts d, states s
            WHERE d.census_code = :census_code AND s.state_name = :state_name
            ON CONFLICT (state_id, alias_name) DO UPDATE
               SET district_id = EXCLUDED.district_id, reason = EXCLUDED.reason"""),
            aliases.to_dict("records"))
        conn.execute(text("INSERT INTO diseases (disease_name) VALUES ('Dengue') ON CONFLICT DO NOTHING"))
    counts = {"states": len(states), "districts": len(districts), "district_aliases": len(aliases)}
    log.info("Reference data loaded: %s", counts)
    return counts


# ---------------------------------------------------------------- reports and outbreaks

def _reports_frame(outbreaks: pd.DataFrame, index_csv: Path | None) -> pd.DataFrame:
    """Report rows from reports_index.csv, or (for the sample data only) rebuilt from the outbreaks."""
    if index_csv and index_csv.exists():
        idx = pd.read_csv(index_csv)
        idx = idx[idx["download_status"] == "ok"].copy()
    else:
        idx = (outbreaks[["report_year", "report_week", "report_file"]].drop_duplicates()
               .rename(columns={"report_year": "year", "report_week": "week", "report_file": "file"}))
        idx["url"] = idx["sha256"] = idx["dengue_rows"] = None
    rows = []
    for r in idx.itertuples(index=False):
        start, end = week_dates(int(r.year), int(r.week))
        rows.append({"year": int(r.year), "week_no": int(r.week), "week_start": start, "week_end": end,
                     "source_url": _none(r.url), "file_name": r.file, "file_hash": _none(r.sha256),
                     "dengue_rows": None if _none(r.dengue_rows) is None else int(r.dengue_rows)})
    return pd.DataFrame(rows)


def load_reports(outbreaks: pd.DataFrame, index_csv: Path | None, engine: Engine | None = None) -> int:
    engine = engine or get_engine()
    rows = _reports_frame(outbreaks, index_csv)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO reports (year, week_no, week_start, week_end, source_url, file_name, file_hash, dengue_rows)
            VALUES (:year, :week_no, :week_start, :week_end, :source_url, :file_name, :file_hash, :dengue_rows)
            ON CONFLICT (file_name) DO UPDATE
               SET source_url = COALESCE(EXCLUDED.source_url, reports.source_url),
                   file_hash  = COALESCE(EXCLUDED.file_hash,  reports.file_hash),
                   dengue_rows = COALESCE(EXCLUDED.dengue_rows, reports.dengue_rows),
                   loaded_at = now()"""),
            rows.to_dict("records"))
    return len(rows)


def outbreak_key(r) -> str:
    """Same identity rule as the de-duplication in Step 4: district (or state + raw name) + start date."""
    place = (str(int(r["census_code"])) if _none(r["census_code"]) is not None
             else f"{r['state'] or ''}|{r['district_raw'] or ''}")
    when = str(r["start_date"]) if _none(r["start_date"]) is not None else f"{r['report_file']}#{r['page']}"
    return f"{place}|{when}"[:120]


def load_outbreaks(outbreaks: pd.DataFrame, engine: Engine | None = None) -> int:
    engine = engine or get_engine()
    df = outbreaks.astype(object).where(outbreaks.notna(), None)
    records = []
    for r in df.to_dict("records"):
        records.append({
            "outbreak_key": outbreak_key(r), "report_file": r["report_file"],
            "census_code": None if r["census_code"] is None else int(r["census_code"]),
            "state": r["state"], "idsp_unique_id": r["idsp_id"], "district_name_raw": r["district_raw"],
            "disease_raw": r["disease_raw"], "is_suspected": bool(r["is_suspected"]), "is_mixed": bool(r["is_mixed"]),
            "cases": None if r["cases"] is None else int(r["cases"]),
            "deaths": None if r["deaths"] is None else int(r["deaths"]),
            "start_date": r["start_date"], "reporting_date": r["reporting_date"],
            "report_lag_days": None if r["report_lag_days"] is None else int(r["report_lag_days"]),
            "current_status": r["status"], "action_taken": r["comments"],
            "times_listed": int(r["times_listed"]), "match_method": r["match_method"],
            "needs_review": bool(r["needs_review"]), "review_reasons": r["review_reasons"],
            "source_page": None if r["page"] is None else int(r["page"]),
        })
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO outbreaks (outbreak_key, report_id, district_id, state_id, disease_id, idsp_unique_id,
                   district_name_raw, disease_raw, is_suspected, is_mixed, cases, deaths, start_date,
                   reporting_date, report_lag_days, current_status, action_taken, times_listed,
                   match_method, needs_review, review_reasons, source_page)
            SELECT :outbreak_key, r.report_id,
                   (SELECT district_id FROM districts WHERE census_code = :census_code),
                   (SELECT state_id FROM states WHERE state_name = :state),
                   (SELECT disease_id FROM diseases WHERE disease_name = 'Dengue'),
                   :idsp_unique_id, :district_name_raw, :disease_raw, :is_suspected, :is_mixed, :cases, :deaths,
                   CAST(:start_date AS DATE), CAST(:reporting_date AS DATE), :report_lag_days, :current_status,
                   :action_taken, :times_listed, :match_method, :needs_review, :review_reasons, :source_page
            FROM reports r WHERE r.file_name = :report_file
            ON CONFLICT (outbreak_key) DO UPDATE
               SET cases = EXCLUDED.cases, deaths = EXCLUDED.deaths, current_status = EXCLUDED.current_status,
                   times_listed = EXCLUDED.times_listed, needs_review = EXCLUDED.needs_review,
                   review_reasons = EXCLUDED.review_reasons, district_id = EXCLUDED.district_id,
                   state_id = EXCLUDED.state_id, match_method = EXCLUDED.match_method"""),
            records)
    return len(records)


def load_all(data_dir: Path | None = None, reset: bool = False, engine: Engine | None = None) -> dict[str, int]:
    """Create the schema and load everything. Uses data/processed if present, else data/sample."""
    engine = engine or get_engine()
    create_schema(engine, reset=reset)
    processed = config.PROCESSED_DIR / "dengue_outbreaks.csv"
    source = data_dir or (config.PROCESSED_DIR if processed.exists() else config.SAMPLE_DIR)
    outbreaks = pd.read_csv(source / "dengue_outbreaks.csv")
    with pipeline_run("load_database", engine) as run:
        counts = load_reference(engine)
        counts["reports"] = load_reports(outbreaks, source / "reports_index.csv", engine)
        counts["outbreaks"] = load_outbreaks(outbreaks, engine)
        run["rows"] = counts["outbreaks"]
        run["message"] = f"loaded from {source.name}/"
    counts["source"] = str(source.relative_to(config.PROJECT_ROOT))
    return counts


def table_counts(engine: Engine | None = None) -> dict[str, int]:
    engine = engine or get_engine()
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one()
                for t in reversed(TABLES_IN_DROP_ORDER)}
