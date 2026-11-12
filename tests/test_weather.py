"""Tests for Step 6 weather integration (parsers always run; database tests need PostgreSQL)."""

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import text

from denguewatch.db.connection import check_connection, get_engine
from denguewatch.ingestion.weather import parse_open_meteo, parse_power

POWER_SAMPLE = {
    "header": {"fill_value": -999.0},
    "properties": {"parameter": {
        "PRECTOTCORR": {"20240701": 12.5, "20240702": 0.0, "20240703": -999.0},
        "T2M": {"20240701": 27.1, "20240702": 28.0, "20240703": -999.0},
        "T2M_MIN": {"20240701": 24.0, "20240702": 24.5, "20240703": -999.0},
        "T2M_MAX": {"20240701": 31.2, "20240702": 32.0, "20240703": -999.0},
        "RH2M": {"20240701": 85.3, "20240702": 80.1, "20240703": -999.0},
    }},
}

OM_SAMPLE = [
    {"daily": {"time": ["2026-11-10", "2026-11-11", "2026-11-12"],
               "precipitation_sum": [1.2, 0.0, 5.5], "temperature_2m_mean": [26.0, 26.5, 27.0],
               "temperature_2m_min": [22.0, 22.5, 23.0], "temperature_2m_max": [30.0, 31.0, 31.5],
               "relative_humidity_2m_mean": [70, 72, 80]}},
    {"daily": {"time": ["2026-11-10", "2026-11-11", "2026-11-12"],
               "precipitation_sum": [0.0, 0.0, 0.0], "temperature_2m_mean": [20.0, 20.5, 21.0],
               "temperature_2m_min": [15.0, 15.5, 16.0], "temperature_2m_max": [25.0, 25.5, 26.0],
               "relative_humidity_2m_mean": [50, 52, 55]}},
]


def test_parse_power_drops_fill_values():
    df = parse_power(POWER_SAMPLE)
    assert list(df["obs_date"]) == [date(2024, 7, 1), date(2024, 7, 2)]  # 3 July was all -999
    assert df.loc[0, "precip_mm"] == 12.5 and df.loc[1, "rel_humidity_pct"] == 80.1


def test_parse_open_meteo_marks_forecast_days():
    df = parse_open_meteo(OM_SAMPLE, [101, 102], today=date(2026, 11, 11))
    assert len(df) == 6 and set(df["district_id"]) == {101, 102}
    types = df[df["district_id"] == 101].set_index("obs_date")["data_type"]
    assert types[date(2026, 11, 10)] == "observed" and types[date(2026, 11, 12)] == "forecast"


def test_parse_open_meteo_rejects_wrong_location_count():
    with pytest.raises(ValueError):
        parse_open_meteo(OM_SAMPLE, [101], today=date(2026, 11, 11))


# ---------------- database: source priority (rows are removed again afterwards)

ok, _ = check_connection()


@pytest.mark.skipif(not ok, reason="PostgreSQL not reachable")
def test_observed_weather_replaces_forecast_but_not_the_reverse():
    from denguewatch.db.weather_store import upsert_weather

    engine = get_engine()
    with engine.connect() as conn:
        if not conn.execute(text("SELECT to_regclass('public.weather_daily')")).scalar():
            pytest.skip("tables not created yet")
        district_id = conn.execute(text("SELECT MIN(district_id) FROM districts")).scalar()
    day = date(2000, 1, 1)  # outside the real data range, deleted at the end

    def row(value, data_type, source):
        return pd.DataFrame([{"district_id": district_id, "obs_date": day, "precip_mm": value,
                              "temp_mean_c": 25.0, "temp_min_c": 20.0, "temp_max_c": 30.0,
                              "rel_humidity_pct": 60.0, "data_type": data_type, "source": source}])

    def stored():
        with engine.connect() as conn:
            return conn.execute(text("SELECT precip_mm, source FROM weather_daily "
                                     "WHERE district_id = :d AND obs_date = :o"), {"d": district_id, "o": day}).one()
    try:
        upsert_weather(row(1.0, "forecast", "open_meteo"), engine)
        upsert_weather(row(2.0, "observed", "nasa_power"), engine)
        assert stored() == (2.0, "nasa_power")           # observation replaced the forecast
        upsert_weather(row(9.0, "forecast", "open_meteo"), engine)
        assert stored() == (2.0, "nasa_power")           # a later forecast does not overwrite it
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM weather_daily WHERE obs_date = :o"), {"o": day})
