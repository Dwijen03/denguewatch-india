"""Export weekly weather per district from PostgreSQL to data/processed/weather_weekly.csv.gz.

    python scripts/export_weather_weekly.py

Used to share the (large) weather table in a compact form, e.g. for model development
or for reviewers who do not want to run the full weather download.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from denguewatch import config  # noqa: E402
from denguewatch.db.connection import get_engine  # noqa: E402
from denguewatch.db.weather_store import coverage  # noqa: E402

OUT = config.PROCESSED_DIR / "weather_weekly.csv.gz"

SQL = """
SELECT d.census_code, w.week_start, w.rain_mm, w.temp_mean_c, w.temp_min_c, w.temp_max_c,
       w.humidity_pct, w.days, w.includes_forecast
FROM v_weather_weekly w
JOIN districts d USING (district_id)
ORDER BY d.census_code, w.week_start
"""


def main() -> None:
    print("Weather stored in the database:")
    print(coverage().to_string(index=False))
    print("\nExporting weekly weather ...")
    with get_engine().connect() as conn:
        df = pd.read_sql(text(SQL), conn)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, compression="gzip")
    print(f"Saved {len(df):,} rows ({df['census_code'].nunique()} districts, "
          f"{df['week_start'].min()} to {df['week_start'].max()})")
    print(f"File: {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
