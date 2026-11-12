"""Step 5: create the PostgreSQL tables and load the dengue outbreak data.

    python scripts/setup_database.py           # create tables (if missing) and load / update data
    python scripts/setup_database.py --reset   # drop all project tables first, then rebuild

Data comes from data/processed/ (output of Step 4) if present, otherwise data/sample/.
Safe to run as often as you like: existing rows are updated, never duplicated.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from denguewatch.db.loader import load_all, table_counts  # noqa: E402
from denguewatch.db.queries import run_query  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="drop and recreate all project tables")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 12)

    result = load_all(reset=args.reset)
    print(f"\nLoaded from {result['source']}")
    print("\n================ Rows in each table ================")
    for table, n in table_counts().items():
        print(f"  {table:<18} {n:>8,}")

    print("\n================ Example queries ================")
    print("\nWhen do dengue outbreaks start? (all India)")
    print(run_query("monthly_seasonality").to_string(index=False))
    print("\nPersistent hotspots (outbreaks in 5 or more different years)")
    print(run_query("repeat_hotspots", min_years=5).head(10).to_string(index=False))
    print("\nData freshness")
    print(run_query("data_freshness").T.to_string(header=False))
    print("\nStep 5 complete. All queries are in database/queries.sql.")


if __name__ == "__main__":
    main()
