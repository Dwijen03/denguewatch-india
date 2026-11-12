"""Fetch daily weather for every district centroid.

Two free sources are combined, because no single free API covers both needs:

* NASA POWER (https://power.larc.nasa.gov) - daily history from 2009 to a few days ago.
  One request returns the whole period for one location, so the full 17-year history for
  640 districts needs only ~640 requests.
* Open-Meteo forecast API (https://open-meteo.com) - the most recent days and a 16-day
  forecast, many districts per request. (Open-Meteo's free terms count every 2 weeks of
  data per location as one call, so 17 years x 640 districts would exceed its daily limit;
  that is why history comes from NASA POWER.)

Both are free for non-commercial / academic use. Values are daily:
precipitation (mm), mean / min / max 2 m temperature (deg C) and mean relative humidity (%).
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas as pd
import requests

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "DengueWatch-India academic project (Mahindra University)"}
COLUMNS = ["obs_date", "precip_mm", "temp_mean_c", "temp_min_c", "temp_max_c", "rel_humidity_pct"]

# ------------------------------------------------------------------ NASA POWER (history)
POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMS = {  # POWER parameter -> our column
    "PRECTOTCORR": "precip_mm",   # precipitation, bias-corrected (mm/day)
    "T2M": "temp_mean_c",
    "T2M_MIN": "temp_min_c",
    "T2M_MAX": "temp_max_c",
    "RH2M": "rel_humidity_pct",
}


def parse_power(payload: dict) -> pd.DataFrame:
    """Turn a NASA POWER daily JSON response into a tidy DataFrame (missing values dropped)."""
    params = payload.get("properties", {}).get("parameter", {})
    fill = payload.get("header", {}).get("fill_value", -999.0)
    frame = {}
    for api_name, col in POWER_PARAMS.items():
        series = params.get(api_name, {})
        frame[col] = pd.Series({k: (None if v is None or v <= fill + 1e-6 or v <= -990 else v)
                                for k, v in series.items()}, dtype="float64")
    df = pd.DataFrame(frame)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df.index = pd.to_datetime(df.index, format="%Y%m%d").date
    df = df.dropna(how="all").reset_index(names="obs_date")
    return df[COLUMNS]


def fetch_power(lat: float, lon: float, start: date, end: date,
                session: requests.Session | None = None, retries: int = 4) -> pd.DataFrame:
    """Daily history for one point. Retries politely; shortens the end date if it is not yet available."""
    s = session or requests.Session()
    params = {"parameters": ",".join(POWER_PARAMS), "community": "AG", "latitude": round(lat, 4),
              "longitude": round(lon, 4), "format": "JSON", "time-standard": "LST"}
    for attempt in range(1, retries + 1):
        params.update(start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
        try:
            resp = s.get(POWER_URL, params=params, headers=HEADERS, timeout=180)
            if resp.status_code == 422 and end > start:  # end date beyond the data available
                end = max(start, end - timedelta(days=7))
                continue
            if resp.status_code == 429:
                time.sleep(30 * attempt)
                continue
            resp.raise_for_status()
            return parse_power(resp.json())
        except (requests.RequestException, ValueError) as exc:
            log.warning("NASA POWER request failed (%.2f, %.2f) attempt %d: %s", lat, lon, attempt, exc)
            time.sleep(5 * attempt)
    raise RuntimeError(f"NASA POWER: no data for ({lat}, {lon})")


# ------------------------------------------------------------------ Open-Meteo (recent days + forecast)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OM_DAILY = {
    "precipitation_sum": "precip_mm",
    "temperature_2m_mean": "temp_mean_c",
    "temperature_2m_min": "temp_min_c",
    "temperature_2m_max": "temp_max_c",
    "relative_humidity_2m_mean": "rel_humidity_pct",
}


def parse_open_meteo(payload: dict | list, district_ids: list[int], today: date) -> pd.DataFrame:
    """Turn an Open-Meteo response (one location -> dict, several -> list) into rows per district.

    Days before today are marked 'observed', today and later 'forecast'.
    """
    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(district_ids):
        raise ValueError(f"Open-Meteo returned {len(items)} locations for {len(district_ids)} requested")
    frames = []
    for district_id, item in zip(district_ids, items):
        daily = item.get("daily", {})
        if not daily.get("time"):
            continue
        df = pd.DataFrame({"obs_date": pd.to_datetime(daily["time"]).date})
        for api_name, col in OM_DAILY.items():
            df[col] = pd.to_numeric(pd.Series(daily.get(api_name, [None] * len(df))), errors="coerce")
        df["district_id"] = district_id
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["district_id", *COLUMNS, "data_type"])
    out = pd.concat(frames, ignore_index=True).dropna(subset=COLUMNS[1:], how="all")
    out["data_type"] = ["observed" if d < today else "forecast" for d in out["obs_date"]]
    return out


def fetch_open_meteo(locations: list[tuple[int, float, float]], past_days: int = 10, forecast_days: int = 16,
                     batch_size: int = 50, pause: float = 12.0, session: requests.Session | None = None,
                     today: date | None = None) -> pd.DataFrame:
    """Recent days and forecast for many districts. locations = [(district_id, lat, lon), ...].

    Requests are batched and spaced out to stay inside Open-Meteo's free limits
    (600 calls/minute, 10,000/day, where each location counts as ~2 calls here).
    """
    s = session or requests.Session()
    today = today or date.today()
    frames = []
    for i in range(0, len(locations), batch_size):
        batch = locations[i:i + batch_size]
        params = {
            "latitude": ",".join(f"{lat:.4f}" for _, lat, _ in batch),
            "longitude": ",".join(f"{lon:.4f}" for _, _, lon in batch),
            "daily": ",".join(OM_DAILY), "timezone": "Asia/Kolkata",
            "past_days": past_days, "forecast_days": forecast_days,
        }
        for attempt in range(1, 5):
            try:
                resp = s.get(OPEN_METEO_URL, params=params, headers=HEADERS, timeout=120)
                if resp.status_code == 429:
                    time.sleep(60 * attempt)
                    continue
                resp.raise_for_status()
                frames.append(parse_open_meteo(resp.json(), [d for d, _, _ in batch], today))
                break
            except (requests.RequestException, ValueError) as exc:
                log.warning("Open-Meteo batch %d failed (attempt %d): %s", i // batch_size + 1, attempt, exc)
                time.sleep(10 * attempt)
        else:
            raise RuntimeError(f"Open-Meteo: batch starting at district #{i} failed")
        log.info("Open-Meteo: %d / %d districts", min(i + batch_size, len(locations)), len(locations))
        if i + batch_size < len(locations):
            time.sleep(pause)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
