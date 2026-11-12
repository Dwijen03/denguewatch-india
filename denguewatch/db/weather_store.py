"""Store weather in PostgreSQL and run the Step 6 weather update.

Rows are keyed by (district_id, obs_date). When the same day arrives from more than one
source, the more reliable value wins:
    NASA POWER observed (3)  >  Open-Meteo observed (2)  >  Open-Meteo forecast (1)
so a forecast is replaced by an observation once that day has passed.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Callable

import pandas as pd
import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine

from denguewatch.db.connection import get_engine
from denguewatch.db.loader import pipeline_run
from denguewatch.ingestion import weather

log = logging.getLogger(__name__)

HISTORY_START = date(2009, 1, 1)
STORE_COLS = ["district_id", "obs_date", "precip_mm", "temp_mean_c", "temp_min_c", "temp_max_c",
              "rel_humidity_pct", "data_type", "source"]

PRIORITY_SQL = """CASE WHEN {t}.source = 'nasa_power' THEN 3
                       WHEN {t}.data_type = 'observed' THEN 2 ELSE 1 END"""


def district_status(engine: Engine | None = None) -> pd.DataFrame:
    """Every district with its coordinates and the last day of NASA POWER history already stored."""
    with (engine or get_engine()).connect() as conn:
        return pd.read_sql(text("""
            SELECT d.district_id, d.census_code, d.district_name, s.state_name,
                   d.latitude::float AS latitude, d.longitude::float AS longitude,
                   MAX(w.obs_date) FILTER (WHERE w.source = 'nasa_power') AS last_history_date
            FROM districts d
            JOIN states s USING (state_id)
            LEFT JOIN weather_daily w ON w.district_id = d.district_id
            GROUP BY d.district_id, d.census_code, d.district_name, s.state_name, d.latitude, d.longitude
            ORDER BY d.district_id"""), conn)


def upsert_weather(df: pd.DataFrame, engine: Engine | None = None) -> int:
    """Bulk-load rows with COPY into a temporary table, then merge them by source priority."""
    if df.empty:
        return 0
    df = df[STORE_COLS].astype(object).where(df[STORE_COLS].notna(), None)
    for col in ("precip_mm",):
        df[col] = [None if v is None else max(0.0, round(float(v), 1)) for v in df[col]]
    for col in ("temp_mean_c", "temp_min_c", "temp_max_c"):
        df[col] = [None if v is None else round(float(v), 1) for v in df[col]]
    df["rel_humidity_pct"] = [None if v is None else min(100.0, max(0.0, round(float(v), 1)))
                              for v in df["rel_humidity_pct"]]
    raw = (engine or get_engine()).raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("CREATE TEMP TABLE tmp_weather (LIKE weather_daily INCLUDING DEFAULTS) ON COMMIT DROP")
            with cur.copy(f"COPY tmp_weather ({', '.join(STORE_COLS)}) FROM STDIN") as cp:
                for row in df.itertuples(index=False, name=None):
                    cp.write_row(row)
            cur.execute(f"""
                INSERT INTO weather_daily AS w ({', '.join(STORE_COLS)}, fetched_at)
                SELECT {', '.join(STORE_COLS)}, now() FROM tmp_weather
                ON CONFLICT (district_id, obs_date) DO UPDATE
                   SET precip_mm = EXCLUDED.precip_mm, temp_mean_c = EXCLUDED.temp_mean_c,
                       temp_min_c = EXCLUDED.temp_min_c, temp_max_c = EXCLUDED.temp_max_c,
                       rel_humidity_pct = EXCLUDED.rel_humidity_pct, data_type = EXCLUDED.data_type,
                       source = EXCLUDED.source, fetched_at = now()
                WHERE {PRIORITY_SQL.format(t='EXCLUDED')} >= {PRIORITY_SQL.format(t='w')}""")
        raw.commit()
    finally:
        raw.close()
    return len(df)


def update_history(engine: Engine | None = None, end: date | None = None, limit: int | None = None,
                   workers: int = 3, fetch: Callable = weather.fetch_power) -> dict:
    """Download missing NASA POWER history for every district (resumable: only missing days)."""
    engine = engine or get_engine()
    end = end or date.today() - timedelta(days=1)
    status = district_status(engine)
    todo = []
    for r in status.itertuples(index=False):
        last = r.last_history_date
        start = HISTORY_START if pd.isna(last) else last + timedelta(days=1)
        # NASA POWER lags real time by a few days; only ask again once a week of new data exists
        # (Open-Meteo fills the most recent days in the meantime).
        if start <= end - timedelta(days=7):
            todo.append((r.district_id, r.district_name, r.latitude, r.longitude, start))
    if limit:
        todo = todo[:limit]
    log.info("NASA POWER: %d of %d districts need history", len(todo), len(status))
    done, rows, failed, t0 = 0, 0, [], time.time()

    local = threading.local()  # one HTTP session per worker thread

    def job(item):
        if not hasattr(local, "session"):
            local.session = requests.Session()
        district_id, name, lat, lon, start = item
        df = fetch(lat, lon, start, end, local.session)
        df = df[df["obs_date"] >= start].copy()
        df["district_id"], df["data_type"], df["source"] = district_id, "observed", "nasa_power"
        return name, upsert_weather(df, engine)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(job, item): item for item in todo}
        for fut in as_completed(futures):
            try:
                _, n = fut.result()
                rows += n
            except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                failed.append(futures[fut][1])
                log.warning("Failed: %s (%s)", futures[fut][1], exc)
            done += 1
            if done % 20 == 0 or done == len(todo):
                eta = (time.time() - t0) / done * (len(todo) - done) / 60
                log.info("NASA POWER: %d / %d districts, %s rows stored, about %.0f min left",
                         done, len(todo), f"{rows:,}", eta)
    return {"districts_updated": done - len(failed), "rows": rows, "failed": failed}


def update_recent(engine: Engine | None = None, fetch: Callable = weather.fetch_open_meteo,
                  limit: int | None = None) -> dict:
    """Recent days + 16-day forecast for every district from Open-Meteo."""
    engine = engine or get_engine()
    status = district_status(engine)
    if limit:
        status = status.head(limit)
    locations = list(zip(status["district_id"], status["latitude"], status["longitude"]))
    df = fetch(locations)
    if df.empty:
        return {"rows": 0}
    df["source"] = "open_meteo"
    return {"rows": upsert_weather(df, engine),
            "forecast_days": int((df["data_type"] == "forecast").sum() / max(1, df["district_id"].nunique()))}


def update_weather(history: bool = True, recent: bool = True, limit: int | None = None,
                   workers: int = 3, engine: Engine | None = None) -> dict:
    """Run the whole Step 6 update and log it in pipeline_runs."""
    engine = engine or get_engine()
    result: dict = {}
    with pipeline_run("update_weather", engine) as run:
        if history:
            result["history"] = update_history(engine, limit=limit, workers=workers)
        if recent:
            result["recent"] = update_recent(engine, limit=limit)
        run["rows"] = sum(v.get("rows", 0) for v in result.values())
        failed = result.get("history", {}).get("failed", [])
        run["message"] = f"{len(failed)} districts failed" if failed else "ok"
    return result


def coverage(engine: Engine | None = None) -> pd.DataFrame:
    """Summary of what is stored, by source and type."""
    with (engine or get_engine()).connect() as conn:
        return pd.read_sql(text("""
            SELECT source, data_type, COUNT(DISTINCT district_id) AS districts,
                   MIN(obs_date) AS first_day, MAX(obs_date) AS last_day, COUNT(*) AS rows
            FROM weather_daily GROUP BY source, data_type ORDER BY source, data_type"""), conn)
