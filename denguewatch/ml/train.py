"""Train, validate and save the dengue early-warning model.

Time-based evaluation (never a random split):
    train       2010 - 2022 (2020 left out: COVID-19 disrupted surveillance, see Step 4)
    validation  2022, using a model trained on 2010 - 2021 -> used to choose settings and alert thresholds
    test        2023 onwards (unseen years) -> the numbers reported in the documentation
    production  final model retrained on ALL labelled weeks for live predictions

Settings (PARAMS) were chosen by precision-recall AUC on the 2022 validation year from a small
grid (num_leaves 7/15/31, min_child_samples 100/400, n_estimators 300/800, class weight 1/10).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from denguewatch import config
from denguewatch.ml.data import ModelInputs
from denguewatch.ml.features import FEATURE_GROUPS, FEATURES, build_panel, label_cutoff

log = logging.getLogger(__name__)

TARGET = "outbreak_next_4w"
TRAIN_START = pd.Timestamp("2010-01-04")
VAL_YEAR = 2022
TEST_START = pd.Timestamp("2023-01-01")
EXCLUDED_YEARS = (2020,)
HIGH_ALERT_SHARE = 0.05     # top 5% of district-weeks in the validation year -> "high"
MEDIUM_ALERT_SHARE = 0.15   # next 10% -> "medium"
PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=100,
              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
              scale_pos_weight=10, random_state=42, n_jobs=-1, verbose=-1)


def lgbm(features: list[str], frame: pd.DataFrame) -> lgb.LGBMClassifier:
    return lgb.LGBMClassifier(**PARAMS).fit(frame[features], frame[TARGET])


def logistic(features: list[str], frame: pd.DataFrame):
    log1p_cols = [i for i, c in enumerate(features) if c.startswith(("rain", "hist"))]

    def _log(X):
        X = np.asarray(X, dtype=float).copy()
        X[:, log1p_cols] = np.log1p(np.clip(X[:, log1p_cols], 0, None))
        return X

    model = make_pipeline(FunctionTransformer(_log), StandardScaler(),
                          LogisticRegression(max_iter=3000, class_weight="balanced", C=0.5))
    return model.fit(frame[features].to_numpy(), frame[TARGET])


def alert_metrics(y: pd.Series, score: pd.Series, flagged: pd.Series) -> dict:
    base = y.mean()
    prec = y[flagged].mean() if flagged.any() else 0.0
    return {"alert_rate": float(flagged.mean()), "precision": float(prec),
            "recall": float(y[flagged].sum() / max(1, y.sum())), "lift": float(prec / base) if base else None}


def lead_times(test: pd.DataFrame, levels: tuple[str, ...]) -> np.ndarray:
    """For each district-week in which an outbreak started, how many weeks earlier (1-4) was it first
    flagged at one of `levels`? 0 = not flagged in the 4 preceding weeks."""
    key = test.set_index(["census_code", "week_start"])["risk_level"]
    out = []
    for r in test[test["outbreaks_this_week"] > 0].itertuples():
        lead = 0
        for k in range(4, 0, -1):
            if key.get((r.census_code, r.week_start - pd.Timedelta(weeks=k))) in levels:
                lead = k
                break
        out.append(lead)
    return np.array(out)


def risk_levels(score: np.ndarray, thresholds: dict) -> np.ndarray:
    return np.where(score >= thresholds["high"], "high", np.where(score >= thresholds["medium"], "medium", "low"))


def train_and_evaluate(inputs: ModelInputs) -> dict:
    """Build features, evaluate on unseen years, train the production model. Returns a result bundle."""
    panel = build_panel(inputs.outbreaks, inputs.weather_weekly, inputs.districts)
    cutoff = label_cutoff(inputs.last_report_week)
    labelled = panel[(panel["week_start"] >= TRAIN_START) & (panel["week_start"] <= cutoff)].copy()
    year = labelled["week_start"].dt.year
    usable = ~year.isin(EXCLUDED_YEARS)
    train_a = labelled[usable & (year < VAL_YEAR)]
    val = labelled[year == VAL_YEAR]
    train_b = labelled[usable & (labelled["week_start"] < TEST_START)]
    test = labelled[labelled["week_start"] >= TEST_START].copy()
    log.info("Rows: train %d, validation %d, test %d (positives %d / %d / %d)", len(train_b), len(val), len(test),
             int(train_b[TARGET].sum()), int(val[TARGET].sum()), int(test[TARGET].sum()))

    # 1. alert thresholds from the validation year (model trained only on earlier years)
    model_a = lgbm(FEATURES, train_a)
    val_scores = model_a.predict_proba(val[FEATURES])[:, 1]
    thresholds = {"high": float(np.quantile(val_scores, 1 - HIGH_ALERT_SHARE)),
                  "medium": float(np.quantile(val_scores, 1 - MEDIUM_ALERT_SHARE))}

    # 2. compare models on the unseen test years (all trained on the same 2010-2022 data)
    y = test[TARGET]
    comparison = {}
    seasonal = (test["hist_district_season"] + 0.01 * test["hist_district_years"]).to_numpy()
    comparison["Seasonal history baseline"] = seasonal
    comparison["Logistic regression (all features)"] = logistic(FEATURES, train_b).predict_proba(
        test[FEATURES].to_numpy())[:, 1]
    variants = {
        "LightGBM, weather only": FEATURE_GROUPS["weather"],
        "LightGBM, no weather (history + season + location)":
            FEATURE_GROUPS["history"] + FEATURE_GROUPS["season"] + FEATURE_GROUPS["location"],
    }
    for name, cols in variants.items():
        comparison[name] = lgbm(cols, train_b).predict_proba(test[cols])[:, 1]
    model_b = lgbm(FEATURES, train_b)
    test["score"] = model_b.predict_proba(test[FEATURES])[:, 1]
    comparison["DengueWatch model (LightGBM, all features)"] = test["score"].to_numpy()
    compare_rows = [{"model": k, "pr_auc": float(average_precision_score(y, s)),
                     "roc_auc": float(roc_auc_score(y, s))} for k, s in comparison.items()]

    # 3. operational metrics for the chosen model
    test["risk_level"] = risk_levels(test["score"].to_numpy(), thresholds)
    high = test["risk_level"] == "high"
    med_high = test["risk_level"].isin(["high", "medium"])
    k = int(high.sum())
    top_seasonal = pd.Series(False, index=test.index)
    top_seasonal.iloc[np.argsort(-seasonal, kind="stable")[:k]] = True
    lead_h, lead_mh = lead_times(test, ("high",)), lead_times(test, ("high", "medium"))
    by_year = {int(yr): float(average_precision_score(g[TARGET], g["score"]))
               for yr, g in test.groupby(test["week_start"].dt.year) if g[TARGET].sum() > 0}

    # 4. explanation: mean |SHAP| per feature on the test years
    sample = test[FEATURES].sample(min(40000, len(test)), random_state=0)
    shap = model_b.predict_proba(sample, pred_contrib=True)[:, :-1]
    importance = pd.Series(np.abs(shap).mean(axis=0), index=FEATURES).sort_values(ascending=False)
    group_share = {g: float(importance[c].sum() / importance.sum()) for g, c in FEATURE_GROUPS.items()}

    # 5. production model on all labelled data
    final = lgbm(FEATURES, labelled[usable])

    metrics = {
        "test_period": f"{TEST_START.date()} to {test['week_start'].max().date()}",
        "train_period": f"{TRAIN_START.date()} to {cutoff.date()} (excluding {', '.join(map(str, EXCLUDED_YEARS))})",
        "rows": {"train": len(train_b), "validation": len(val), "test": len(test)},
        "test_base_rate": float(y.mean()),
        "test_pr_auc": float(average_precision_score(y, test["score"])),
        "test_roc_auc": float(roc_auc_score(y, test["score"])),
        "test_brier": float(brier_score_loss(y, test["score"])),
        "pr_auc_by_year": by_year,
        "thresholds": thresholds,
        "high_alert": alert_metrics(y, test["score"], high),
        "medium_or_high_alert": alert_metrics(y, test["score"], med_high),
        "seasonal_baseline_same_alert_volume": alert_metrics(y, pd.Series(seasonal, index=test.index), top_seasonal),
        "outbreak_weeks_in_test": int(len(lead_h)),
        "warned": {f"{lvl}_{n}plus_weeks": float((arr >= n).mean())
                   for lvl, arr in (("high", lead_h), ("medium_or_high", lead_mh)) for n in (1, 2, 3, 4)},
        "comparison": compare_rows,
        "feature_importance": {k: float(v) for k, v in importance.items()},
        "importance_by_group": group_share,
    }
    return {"model": final, "features": FEATURES, "thresholds": thresholds, "params": PARAMS,
            "trained_through": str(cutoff.date()), "metrics": metrics}


def save_bundle(bundle: dict, models_dir: Path = config.MODELS_DIR) -> Path:
    """Save the model + metadata as one joblib file, and the metrics as JSON next to it."""
    models_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    path = models_dir / f"denguewatch_lgbm_{stamp}.joblib"
    joblib.dump({k: v for k, v in bundle.items()}, path)
    (models_dir / f"denguewatch_lgbm_{stamp}_metrics.json").write_text(json.dumps(bundle["metrics"], indent=2))
    return path
