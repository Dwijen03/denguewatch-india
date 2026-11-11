"""Run Step 4: download IDSP weekly outbreak reports, extract dengue outbreaks, clean them.

    python scripts/run_ingestion.py              # download new reports + parse everything
    python scripts/run_ingestion.py --no-download  # only re-parse PDFs already in data/raw

The first run downloads ~900 PDFs (~450 MB) and takes roughly 15-30 minutes.
Results are written to data/processed/ (see denguewatch/ingestion/pipeline.py).
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from denguewatch.ingestion.pipeline import run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-download", action="store_true", help="use PDFs already in data/raw")
    parser.add_argument("--workers", type=int, default=None, help="parallel PDF parsers (default: CPUs - 1)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    outbreaks = run(download=not args.no_download, workers=args.workers)

    print("\n================ Step 4 summary ================")
    print(f"Dengue outbreaks (after removing duplicates): {len(outbreaks):,}")
    print(f"Years covered:        {outbreaks['report_year'].min()} - {outbreaks['report_year'].max()}")
    print(f"Districts involved:   {outbreaks['census_code'].nunique():,}")
    print(f"Total cases reported: {int(outbreaks['cases'].sum()):,}")
    print(f"Rows needing review:  {int(outbreaks['needs_review'].sum()):,}")
    print("Output files are in data/processed/")


if __name__ == "__main__":
    main()
