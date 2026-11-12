"""Step 7: train the dengue early-warning model, evaluate it on unseen years and store risk scores.

    python scripts/train_model.py              # train + evaluate + save + predict (about 2-4 minutes)
    python scripts/train_model.py --predict-only   # only refresh risk scores with the active model

Reads outbreaks and weather from PostgreSQL. Saves the model to models/ and its metrics as JSON,
registers it in model_versions, and writes risk scores for the last 12 weeks to predictions.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from denguewatch.ml.data import load_from_db  # noqa: E402
from denguewatch.ml.predict import predict_latest, register_model  # noqa: E402
from denguewatch.ml.train import save_bundle, train_and_evaluate  # noqa: E402


def pct(x):
    return f"{100 * x:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--predict-only", action="store_true", help="skip training; score with the active model")
    parser.add_argument("--weeks-back", type=int, default=11, help="also score this many earlier weeks (history)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    pd.set_option("display.width", 160)

    if not args.predict_only:
        bundle = train_and_evaluate(load_from_db())
        path = save_bundle(bundle)
        model_id = register_model(path, bundle)
        m = bundle["metrics"]
        print("\n================ Model evaluation on unseen years ================")
        print(f"Test period: {m['test_period']}   (base rate {pct(m['test_base_rate'])} of district-weeks)")
        print(pd.DataFrame(m["comparison"]).round(3).to_string(index=False))
        h, mh = m["high_alert"], m["medium_or_high_alert"]
        print(f"\nHIGH alerts:   {pct(h['alert_rate'])} of district-weeks, precision {pct(h['precision'])}, "
              f"recall {pct(h['recall'])}, {h['lift']:.1f}x better than chance")
        print(f"MEDIUM+HIGH:   {pct(mh['alert_rate'])} of district-weeks, recall {pct(mh['recall'])}")
        w = m["warned"]
        print(f"Outbreaks flagged medium/high 1+ weeks ahead: {pct(w['medium_or_high_1plus_weeks'])}, "
              f"2+ weeks ahead: {pct(w['medium_or_high_2plus_weeks'])}")
        print(f"Importance by group: " + ", ".join(f"{g} {pct(v)}" for g, v in m["importance_by_group"].items()))
        print(f"\nSaved model {model_id}: {path}")

    rows = predict_latest(weeks_back=args.weeks_back)
    latest = rows[rows["target_week_start"] == rows["target_week_start"].max()]
    print(f"\n================ Risk for the 4 weeks from {latest['target_week_start'].max().date()} ================")
    print(latest["risk_level"].value_counts().to_string())
    top = latest.sort_values("risk_score", ascending=False).head(10)
    for r in top.itertuples():
        why = "; ".join(f["label"] for f in r.top_factors)
        print(f"  {r.district_name:<18} {r.state_name:<15} score {r.risk_score:.2f}  {r.risk_level:<6}  ({why})")


if __name__ == "__main__":
    main()
