"""Build the district-week modelling table for the dengue early-warning model.

One row = one district at the end of one week t (the moment a prediction is made).

Target
    outbreak_next_4w = 1 if an outbreak STARTS in that district in weeks t+1 .. t+4.

Features (all computable at the end of week t - no future information)
    * weather in lag windows before t: rain totals, mean/min/max temperature, humidity
      over weeks 1-4, 5-8, 9-12 and 13-16 back (dengue follows weather by ~2-15 weeks)
    * season: week of the year (sine/cosine) and month
    * location: latitude, longitude
    * outbreak history, using ONLY outbreaks that would already have been published:
      the public IDSP reports appear ~8 weeks late, so an outbreak that started after
      week t-8 is invisible at prediction time (config.REPORTING_LAG_WEEKS). Using it would
      leak future information and overstate accuracy.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from denguewatch import config

LAG = config.REPORTING_LAG_WEEKS          # 8 weeks
HORIZON = config.PREDICTION_HORIZON_WEEKS  # 4 weeks

WEATHER_FEATURES = [
    "rain_w1_4", "rain_w5_8", "rain_w9_12", "rain_w13_16",
    "temp_w1_4", "temp_w5_8", "temp_w9_12",
    "tmin_w1_4", "tmax_w1_4",
    "hum_w1_4", "hum_w5_8", "hum_w9_12",
    "rainy_weeks_w1_8",
]
SEASON_FEATURES = ["woy_sin", "woy_cos", "month"]
LOCATION_FEATURES = ["latitude", "longitude"]
HISTORY_FEATURES = [
    "hist_district_1y",        # district outbreaks in the last year that are already published
    "hist_district_years",     # number of past calendar years with >= 1 district outbreak
    "hist_district_season",    # past outbreaks in this district at this time of year (+/- 2 weeks)
    "hist_state_recent",       # outbreaks in the same state published in the last 8 weeks
    "hist_state_1y",           # outbreaks in the same state in the last year (published)
]
FEATURES = WEATHER_FEATURES + SEASON_FEATURES + LOCATION_FEATURES + HISTORY_FEATURES
FEATURE_GROUPS = {"weather": WEATHER_FEATURES, "season": SEASON_FEATURES,
                  "location": LOCATION_FEATURES, "history": HISTORY_FEATURES}


def week_start(dates) -> pd.Series:
    """Monday of the ISO week (the weeks IDSP uses)."""
    d = pd.to_datetime(pd.Series(dates))
    return (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.normalize()


def _window_sum(arr: np.ndarray, lo: int, hi: int) -> np.ndarray:
    """For a (districts x weeks) array, sum over weeks t+lo .. t+hi (inclusive) for every t; 0 outside."""
    n_w = arr.shape[1]
    csum = np.concatenate([np.zeros((arr.shape[0], 1)), np.cumsum(arr, axis=1)], axis=1)
    t = np.arange(n_w)
    a = np.clip(t + lo, 0, n_w)
    b = np.clip(t + hi + 1, 0, n_w)
    return csum[:, b] - csum[:, a]


def build_panel(outbreaks: pd.DataFrame, weather_weekly: pd.DataFrame, districts: pd.DataFrame) -> pd.DataFrame:
    """Return the full district x week table with features and (where known) the target.

    outbreaks       columns: census_code, start_date  (one row per outbreak; district known)
    weather_weekly  columns: census_code, week_start, rain_mm, temp_mean_c, temp_min_c, temp_max_c, humidity_pct
    districts       columns: census_code, state_name, latitude, longitude
    """
    w = weather_weekly.copy()
    w["week_start"] = pd.to_datetime(w["week_start"])
    weeks = pd.date_range(w["week_start"].min(), w["week_start"].max(), freq="7D")
    codes = np.sort(districts["census_code"].unique())
    d_idx = {c: i for i, c in enumerate(codes)}
    w_idx = {wk: i for i, wk in enumerate(weeks)}
    n_d, n_w = len(codes), len(weeks)

    def matrix(col: str) -> np.ndarray:
        m = np.full((n_d, n_w), np.nan)
        rows = w["census_code"].map(d_idx)
        cols = w["week_start"].map(w_idx)
        ok = rows.notna() & cols.notna()
        m[rows[ok].astype(int), cols[ok].astype(int)] = w.loc[ok, col].to_numpy()
        return m

    rain, temp, tmin, tmax, hum = (matrix(c) for c in
                                   ["rain_mm", "temp_mean_c", "temp_min_c", "temp_max_c", "humidity_pct"])

    def wmean(m, lo, hi):
        filled = np.nan_to_num(m)
        cnt = _window_sum((~np.isnan(m)).astype(float), lo, hi)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(cnt > 0, _window_sum(filled, lo, hi) / cnt, np.nan)

    feats = {
        "rain_w1_4": _window_sum(np.nan_to_num(rain), -3, 0),
        "rain_w5_8": _window_sum(np.nan_to_num(rain), -7, -4),
        "rain_w9_12": _window_sum(np.nan_to_num(rain), -11, -8),
        "rain_w13_16": _window_sum(np.nan_to_num(rain), -15, -12),
        "temp_w1_4": wmean(temp, -3, 0), "temp_w5_8": wmean(temp, -7, -4), "temp_w9_12": wmean(temp, -11, -8),
        "tmin_w1_4": wmean(tmin, -3, 0), "tmax_w1_4": wmean(tmax, -3, 0),
        "hum_w1_4": wmean(hum, -3, 0), "hum_w5_8": wmean(hum, -7, -4), "hum_w9_12": wmean(hum, -11, -8),
        "rainy_weeks_w1_8": _window_sum((np.nan_to_num(rain) >= 10).astype(float), -7, 0),
    }

    # ---- outbreak counts per district-week (by week the outbreak STARTED)
    ob = outbreaks.dropna(subset=["census_code", "start_date"]).copy()
    ob["week_start"] = week_start(ob["start_date"])
    ob = ob[ob["census_code"].isin(d_idx) & ob["week_start"].isin(w_idx)]
    counts = np.zeros((n_d, n_w))
    np.add.at(counts, (ob["census_code"].map(d_idx).to_numpy(), ob["week_start"].map(w_idx).to_numpy()), 1)

    target = (_window_sum(counts, 1, HORIZON) > 0).astype(float)
    feats["hist_district_1y"] = _window_sum(counts, -52, -LAG)
    # seasonal history: same time of year in each earlier year (all already published)
    seasonal = np.zeros_like(counts)
    for k in range(1, n_w // 52 + 1):
        seasonal += _window_sum(counts, 1 - 52 * k - 2, HORIZON - 52 * k + 2)
    feats["hist_district_season"] = seasonal
    # number of earlier calendar years with an outbreak (years that ended before the published cut-off)
    years = weeks.year.to_numpy()
    year_has = {}
    for y in np.unique(years):
        year_has[y] = counts[:, years == y].sum(axis=1) > 0
    yrs_mat = np.zeros_like(counts)
    for j, wk in enumerate(weeks):
        cutoff_year = (wk - pd.Timedelta(weeks=LAG)).year
        yrs_mat[:, j] = sum(year_has[y].astype(float) for y in year_has if y < cutoff_year)
    feats["hist_district_years"] = yrs_mat

    # state-level history
    state_of = districts.set_index("census_code").loc[codes, "state_name"].to_numpy()
    st_recent = _window_sum(counts, -LAG - 7, -LAG)
    st_year = _window_sum(counts, -52, -LAG)
    for name, src in (("hist_state_recent", st_recent), ("hist_state_1y", st_year)):
        out = np.zeros_like(src)
        for st in np.unique(state_of):
            m = state_of == st
            out[m] = src[m].sum(axis=0)
        feats[name] = out

    # ---- assemble long table
    panel = pd.DataFrame({
        "census_code": np.repeat(codes, n_w),
        "week_start": np.tile(weeks, n_d),
    })
    for k, v in feats.items():
        panel[k] = v.reshape(-1)
    panel["outbreak_next_4w"] = target.reshape(-1)
    panel["outbreaks_this_week"] = counts.reshape(-1)
    woy = panel["week_start"].dt.isocalendar().week.astype(int).to_numpy()
    panel["woy_sin"] = np.sin(2 * np.pi * woy / 52.18)
    panel["woy_cos"] = np.cos(2 * np.pi * woy / 52.18)
    panel["month"] = panel["week_start"].dt.month
    loc = districts.set_index("census_code")[["latitude", "longitude", "state_name"]]
    panel = panel.join(loc, on="census_code")
    return panel


def label_cutoff(last_report_week_start: pd.Timestamp, grace_weeks: int = 2) -> pd.Timestamp:
    """Last week t whose 4-week target window is fully covered by published reports.

    Outbreaks usually appear in the report 1-2 weeks after they start, so the last
    `grace_weeks` report weeks are treated as incomplete.
    """
    return pd.Timestamp(last_report_week_start) - pd.Timedelta(weeks=grace_weeks + HORIZON)


# Plain-language names, used to explain predictions on the dashboard
FEATURE_LABELS = {
    "rain_w1_4": "Rainfall, last 4 weeks (mm)", "rain_w5_8": "Rainfall, 5-8 weeks ago (mm)",
    "rain_w9_12": "Rainfall, 9-12 weeks ago (mm)", "rain_w13_16": "Rainfall, 13-16 weeks ago (mm)",
    "temp_w1_4": "Mean temperature, last 4 weeks (°C)", "temp_w5_8": "Mean temperature, 5-8 weeks ago (°C)",
    "temp_w9_12": "Mean temperature, 9-12 weeks ago (°C)", "tmin_w1_4": "Minimum temperature, last 4 weeks (°C)",
    "tmax_w1_4": "Maximum temperature, last 4 weeks (°C)", "hum_w1_4": "Humidity, last 4 weeks (%)",
    "hum_w5_8": "Humidity, 5-8 weeks ago (%)", "hum_w9_12": "Humidity, 9-12 weeks ago (%)",
    "rainy_weeks_w1_8": "Rainy weeks (≥10 mm) in last 8 weeks", "woy_sin": "Time of year", "woy_cos": "Time of year",
    "month": "Month", "latitude": "Location (latitude)", "longitude": "Location (longitude)",
    "hist_district_1y": "District outbreaks in the past year", "hist_district_years": "Years with past outbreaks in district",
    "hist_district_season": "Past outbreaks in district at this time of year",
    "hist_state_recent": "Recent outbreaks in the state", "hist_state_1y": "State outbreaks in the past year",
}
