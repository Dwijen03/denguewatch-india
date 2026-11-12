"""Step 6: download daily weather for all 640 districts into PostgreSQL.

    python scripts/update_weather.py --check       # quick test that both weather services answer
    python scripts/update_weather.py --limit 5     # try it on 5 districts first
    python scripts/update_weather.py               # everything (first run: roughly 30-60 minutes)
    python scripts/update_weather.py --no-history  # only refresh recent days + forecast (fast, daily)

The first full run downloads 2009-today for every district from NASA POWER. It can be
stopped and restarted at any time: each run only downloads days that are still missing.
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from denguewatch.db.loader import create_schema  # noqa: E402
from denguewatch.db.queries import run_query  # noqa: E402
from denguewatch.db.weather_store import coverage, update_weather  # noqa: E402
from denguewatch.ingestion import weather  # noqa: E402


def check() -> None:
    """Fetch one week for one district from each service and print it."""
    lat, lon = 9.98, 76.28  # Ernakulam, Kerala
    end = date.today() - timedelta(days=10)
    print("NASA POWER (history) ...")
    print(weather.fetch_power(lat, lon, end - timedelta(days=6), end).to_string(index=False))
    print("\nOpen-Meteo (recent days + forecast) ...")
    df = weather.fetch_open_meteo([(0, lat, lon)], past_days=3, forecast_days=5)
    print(df.drop(columns="district_id").to_string(index=False))
    print("\nBoth weather services are reachable.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only test that the weather services answer")
    parser.add_argument("--limit", type=int, help="only process the first N districts (for a trial run)")
    parser.add_argument("--no-history", action="store_true", help="skip NASA POWER history")
    parser.add_argument("--no-recent", action="store_true", help="skip Open-Meteo recent days + forecast")
    parser.add_argument("--workers", type=int, default=3, help="parallel NASA POWER downloads (default 3)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    pd.set_option("display.width", 160)

    if args.check:
        check()
        return

    create_schema()  # adds the weather 'source' column to databases created in Step 5
    result = update_weather(history=not args.no_history, recent=not args.no_recent,
                            limit=args.limit, workers=args.workers)
    print("\n================ Step 6 summary ================")
    hist = result.get("history")
    if hist:
        print(f"History: {hist['districts_updated']} districts updated, {hist['rows']:,} daily rows")
        if hist["failed"]:
            print(f"  {len(hist['failed'])} districts failed - just run this script again to retry them:")
            print("  " + ", ".join(hist["failed"][:20]))
    if result.get("recent"):
        print(f"Recent days + forecast: {result['recent']['rows']:,} rows")
    print("\nWhat is stored now:")
    print(coverage().to_string(index=False))
    print("\nExample: rainfall in the 12 weeks before recent outbreaks")
    print(run_query("rainfall_before_outbreaks", limit=8).to_string(index=False))


if __name__ == "__main__":
    main()
