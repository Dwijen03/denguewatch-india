"""Load the inputs for the model, from PostgreSQL (normal use) or from CSV files (sharing / testing)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from denguewatch import config
from denguewatch.db.connection import get_engine


@dataclass
class ModelInputs:
    outbreaks: pd.DataFrame        # census_code, start_date
    weather_weekly: pd.DataFrame   # census_code, week_start, rain_mm, temp_*, humidity_pct, days, includes_forecast
    districts: pd.DataFrame        # census_code, state_name, latitude, longitude
    last_report_week: pd.Timestamp  # start of the latest IDSP report week loaded


def load_from_db(engine: Engine | None = None) -> ModelInputs:
    engine = engine or get_engine()
    with engine.connect() as conn:
        outbreaks = pd.read_sql(text("""
            SELECT census_code, start_date FROM v_outbreaks
            WHERE census_code IS NOT NULL AND start_date IS NOT NULL AND NOT needs_review"""), conn)
        weather = pd.read_sql(text("""
            SELECT d.census_code, w.week_start, w.rain_mm, w.temp_mean_c, w.temp_min_c, w.temp_max_c,
                   w.humidity_pct, w.days, w.includes_forecast
            FROM v_weather_weekly w JOIN districts d USING (district_id)"""), conn)
        districts = pd.read_sql(text("""
            SELECT d.census_code, d.district_name, s.state_name, d.latitude::float AS latitude, d.longitude::float AS longitude
            FROM districts d JOIN states s USING (state_id)"""), conn)
        last_week = conn.execute(text("SELECT MAX(week_start) FROM reports")).scalar()
    return ModelInputs(outbreaks, weather, districts, pd.Timestamp(last_week))


def load_from_files(outbreaks_csv: Path | None = None, weather_csv: Path | None = None) -> ModelInputs:
    """Same inputs from data/ files (dengue_outbreaks.csv + weather_weekly.csv.gz)."""
    ob_path = outbreaks_csv or (config.PROCESSED_DIR / "dengue_outbreaks.csv")
    if not Path(ob_path).exists():
        ob_path = config.SAMPLE_DIR / "dengue_outbreaks.csv"
    ob = pd.read_csv(ob_path)
    ob = ob[ob["census_code"].notna() & ob["start_date"].notna() & ~ob["needs_review"].astype(bool)]
    weather = pd.read_csv(weather_csv or (config.PROCESSED_DIR / "weather_weekly.csv.gz"))
    districts = pd.read_csv(config.DATA_DIR / "reference" / "districts.csv")
    last = pd.to_datetime(ob["week_start"]).max() if "week_start" in ob else pd.Timestamp(ob["start_date"].max())
    return ModelInputs(ob[["census_code", "start_date"]], weather,
                       districts[["census_code", "district_name", "state_name", "latitude", "longitude"]], pd.Timestamp(last))
