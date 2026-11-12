"""Score every district with the active model and store the results in the predictions table."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from denguewatch.db.connection import get_engine
from denguewatch.ml.data import ModelInputs, load_from_db
from denguewatch.ml.features import FEATURE_LABELS, HORIZON, build_panel
from denguewatch.ml.train import risk_levels

log = logging.getLogger(__name__)


def register_model(path: Path, bundle: dict, engine: Engine | None = None) -> int:
    """Add the model to model_versions and make it the only active one. Returns model_id."""
    engine = engine or get_engine()
    m = bundle["metrics"]
    with engine.begin() as conn:
        conn.execute(text("UPDATE model_versions SET is_active = FALSE WHERE is_active"))
        return conn.execute(text("""
            INSERT INTO model_versions (algorithm, train_period, test_metrics, file_path, is_active)
            VALUES (:a, :t, CAST(:m AS JSONB), :f, TRUE) RETURNING model_id"""),
            {"a": "LightGBM gradient-boosted trees", "t": m["train_period"][:40],
             "m": json.dumps(m), "f": str(path)}).scalar_one()


def active_model(engine: Engine | None = None) -> tuple[int, dict]:
    with (engine or get_engine()).connect() as conn:
        row = conn.execute(text("SELECT model_id, file_path FROM model_versions WHERE is_active")).first()
    if row is None:
        raise RuntimeError("No active model - run scripts/train_model.py first")
    return row.model_id, joblib.load(row.file_path)


def latest_complete_week(weather_weekly: pd.DataFrame) -> pd.Timestamp:
    """Most recent week with 7 days of observed weather for every district."""
    w = weather_weekly.copy()
    w["week_start"] = pd.to_datetime(w["week_start"])
    full = w[(w["days"] == 7) & (~w["includes_forecast"].astype(bool))]
    per_week = full.groupby("week_start")["census_code"].nunique()
    return per_week[per_week == per_week.max()].index.max()


def explain(model, X: pd.DataFrame, top: int = 3) -> list[list[dict]]:
    """Top features pushing each prediction up (LightGBM SHAP contributions)."""
    contrib = model.predict_proba(X, pred_contrib=True)[:, :-1]
    out = []
    for row_c, row_x in zip(contrib, X.itertuples(index=False)):
        order = np.argsort(-row_c)[:top]
        out.append([{"feature": X.columns[i], "label": FEATURE_LABELS.get(X.columns[i], X.columns[i]),
                     "value": round(float(row_x[i]), 2), "effect": round(float(row_c[i]), 3)}
                    for i in order if row_c[i] > 0])
    return out


def score_weeks(bundle: dict, inputs: ModelInputs, weeks_back: int = 0) -> pd.DataFrame:
    """Risk for every district, for the latest complete week and optionally the `weeks_back` weeks before it."""
    panel = build_panel(inputs.outbreaks, inputs.weather_weekly, inputs.districts)
    last = latest_complete_week(inputs.weather_weekly)
    weeks = [last - pd.Timedelta(weeks=k) for k in range(weeks_back, -1, -1)]
    rows = panel[panel["week_start"].isin(weeks)].copy()
    X = rows[bundle["features"]]
    rows["risk_score"] = bundle["model"].predict_proba(X)[:, 1]
    rows["risk_level"] = risk_levels(rows["risk_score"].to_numpy(), bundle["thresholds"])
    rows["top_factors"] = explain(bundle["model"], X)
    rows["target_week_start"] = rows["week_start"] + pd.Timedelta(weeks=1)  # risk covers weeks t+1 .. t+4
    if "district_name" in inputs.districts:
        rows = rows.merge(inputs.districts[["census_code", "district_name"]], on="census_code", how="left")
    return rows


def store_predictions(model_id: int, rows: pd.DataFrame, engine: Engine | None = None) -> int:
    records = [{"m": model_id, "c": int(r.census_code), "w": r.target_week_start.date(), "h": HORIZON,
                "s": round(float(r.risk_score), 4), "l": r.risk_level, "f": json.dumps(r.top_factors)}
               for r in rows.itertuples()]
    with (engine or get_engine()).begin() as conn:
        conn.execute(text("""
            INSERT INTO predictions (model_id, district_id, target_week_start, horizon_weeks, risk_score, risk_level, top_factors)
            SELECT :m, district_id, :w, :h, :s, :l, CAST(:f AS JSONB) FROM districts WHERE census_code = :c
            ON CONFLICT (model_id, district_id, target_week_start) DO UPDATE
               SET risk_score = EXCLUDED.risk_score, risk_level = EXCLUDED.risk_level,
                   top_factors = EXCLUDED.top_factors, created_at = now()"""), records)
    return len(records)


def predict_latest(weeks_back: int = 0, engine: Engine | None = None) -> pd.DataFrame:
    """Score with the active model and save. Returns the scored rows."""
    engine = engine or get_engine()
    model_id, bundle = active_model(engine)
    rows = score_weeks(bundle, load_from_db(engine), weeks_back)
    n = store_predictions(model_id, rows, engine)
    log.info("Stored %d predictions (model %d), weeks %s to %s", n, model_id,
             rows["target_week_start"].min().date(), rows["target_week_start"].max().date())
    return rows
