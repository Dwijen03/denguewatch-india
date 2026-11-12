"""Tests for Step 7: the target, the time windows, and above all that no feature leaks future data."""

import numpy as np
import pandas as pd

from denguewatch.ml.features import FEATURES, LAG, _window_sum, build_panel
from denguewatch.ml.train import risk_levels

WEEKS = pd.date_range("2020-01-06", periods=120, freq="7D")  # Mondays


def _inputs(outbreak_weeks):
    districts = pd.DataFrame({"census_code": [1, 2], "state_name": ["A", "A"],
                              "latitude": [20.0, 21.0], "longitude": [75.0, 76.0]})
    weather = pd.DataFrame([{"census_code": c, "week_start": w, "rain_mm": 10.0 * (i % 5), "temp_mean_c": 27.0,
                             "temp_min_c": 23.0, "temp_max_c": 32.0, "humidity_pct": 70.0}
                            for c in (1, 2) for i, w in enumerate(WEEKS)])
    outbreaks = pd.DataFrame({"census_code": [1] * len(outbreak_weeks),
                              "start_date": [WEEKS[i] + pd.Timedelta(days=2) for i in outbreak_weeks]})
    return outbreaks, weather, districts


def _row(panel, code, week_idx):
    return panel[(panel["census_code"] == code) & (panel["week_start"] == WEEKS[week_idx])].iloc[0]


def test_window_sum():
    arr = np.array([[1, 2, 3, 4, 5]], dtype=float)
    assert list(_window_sum(arr, -1, 0)[0]) == [1, 3, 5, 7, 9]   # this week + previous
    assert list(_window_sum(arr, 1, 2)[0]) == [5, 7, 9, 5, 0]    # next two weeks


def test_target_is_outbreak_in_next_four_weeks():
    panel = build_panel(*_inputs([60]))
    assert [_row(panel, 1, i)["outbreak_next_4w"] for i in (55, 56, 59, 60)] == [0, 1, 1, 0]
    assert _row(panel, 2, 58)["outbreak_next_4w"] == 0  # other district unaffected


def test_history_features_ignore_unpublished_outbreaks():
    """An outbreak in week 60 must not influence any feature before week 60 + LAG (reporting delay)."""
    with_ob = build_panel(*_inputs([60]))
    without = build_panel(*_inputs([]))
    for i in range(0, 60 + LAG):
        a, b = _row(with_ob, 1, i)[FEATURES], _row(without, 1, i)[FEATURES]
        assert np.allclose(a.astype(float), b.astype(float), equal_nan=True), f"leak at week {i}"
    assert _row(with_ob, 1, 60 + LAG)["hist_district_1y"] == 1  # visible once published


def test_weather_windows():
    panel = build_panel(*_inputs([]))
    r = _row(panel, 1, 10)
    assert r["rain_w1_4"] == sum(10.0 * (i % 5) for i in range(7, 11))
    assert r["temp_w1_4"] == 27.0


def test_risk_levels():
    levels = risk_levels(np.array([0.05, 0.2, 0.5]), {"high": 0.35, "medium": 0.15})
    assert list(levels) == ["low", "medium", "high"]
