"""Step 4 pipeline: download reports -> parse PDFs -> clean -> match districts -> de-duplicate.

Outputs (in data/processed/):
    reports_index.csv          every weekly report: URL, file, fingerprint, rows found
    dengue_rows_raw.csv        every dengue row exactly as read from the PDFs
    dengue_rows_clean.csv      the same rows cleaned, matched and flagged
    dengue_outbreaks.csv       one row per outbreak (duplicates removed)  <- main output
    unmatched_districts.csv    district names that still need an alias
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from denguewatch import config
from denguewatch.ingestion.cleaning import clean_rows, deduplicate
from denguewatch.ingestion.districts import DistrictMatcher
from denguewatch.ingestion.pdf_parser import parse_report
from denguewatch.ingestion.reports import ReportRef, download_report, file_sha256, list_reports

log = logging.getLogger(__name__)
P = config.PROCESSED_DIR


def download_all(refs: list[ReportRef]) -> pd.DataFrame:
    """Download any reports not yet on disk. Returns the report index."""
    rows = []
    with requests.Session() as session:
        for n, ref in enumerate(refs, 1):
            try:
                path = download_report(ref, config.RAW_DIR, session)
                rows.append({"year": ref.year, "week": ref.week, "url": ref.url, "file": path.name,
                             "week_start": ref.week_start, "week_end": ref.week_end,
                             "sha256": file_sha256(path), "download_status": "ok"})
            except RuntimeError as exc:
                rows.append({"year": ref.year, "week": ref.week, "url": ref.url, "file": ref.filename,
                             "week_start": ref.week_start, "week_end": ref.week_end,
                             "sha256": None, "download_status": f"failed: {exc}"})
            if n % 50 == 0:
                log.info("Downloaded %d / %d", n, len(refs))
    return pd.DataFrame(rows)


def _parse_one(path: str) -> tuple[str, list[dict], str | None]:
    try:
        return path, [r.to_dict() for r in parse_report(path)], None
    except Exception as exc:  # noqa: BLE001 - a broken PDF must not stop the whole run
        return path, [], str(exc)


def parse_all(index: pd.DataFrame, workers: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse every downloaded report (in parallel). Returns (raw rows, updated index)."""
    ok = index[index["download_status"] == "ok"]
    paths = [str(config.RAW_DIR / f) for f in ok["file"]]
    meta = {str(config.RAW_DIR / f): (y, w, f) for f, y, w in zip(ok["file"], ok["year"], ok["week"])}
    rows, counts, errors = [], {}, {}
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for n, (path, found, err) in enumerate(pool.map(_parse_one, paths, chunksize=4), 1):
            y, w, f = meta[path]
            rows += [{"year": y, "week": w, "file": f, **r} for r in found]
            counts[f], errors[f] = len(found), err
            if n % 50 == 0:
                log.info("Parsed %d / %d reports", n, len(paths))
    index = index.copy()
    index["dengue_rows"] = index["file"].map(counts)
    index["parse_error"] = index["file"].map(errors)
    return pd.DataFrame(rows), index


def run(download: bool = True, workers: int | None = None) -> pd.DataFrame:
    """Run the whole Step 4 pipeline and write the output files. Returns the outbreak table."""
    P.mkdir(parents=True, exist_ok=True)
    if download:
        index = download_all(list_reports())
    else:  # re-use PDFs already in data/raw
        refs = []
        for path in sorted(config.RAW_DIR.glob("*_week*.pdf")):
            y, w = path.stem.split("_week")
            refs.append({"year": int(y), "week": int(w), "url": None, "file": path.name,
                         "sha256": file_sha256(path), "download_status": "ok"})
        index = pd.DataFrame(refs)

    raw, index = parse_all(index, workers)
    index.to_csv(P / "reports_index.csv", index=False)
    raw.to_csv(P / "dengue_rows_raw.csv", index=False)
    log.info("Parsed %d dengue rows from %d reports", len(raw), int((index["download_status"] == "ok").sum()))

    clean = clean_rows(raw, DistrictMatcher())
    clean.to_csv(P / "dengue_rows_clean.csv", index=False)
    outbreaks = deduplicate(clean)
    outbreaks.to_csv(P / "dengue_outbreaks.csv", index=False)

    unmatched = (clean[clean["match_method"] == "unmatched"]
                 .groupby(["state", "district_raw"], dropna=False).size()
                 .rename("rows").reset_index().sort_values("rows", ascending=False))
    unmatched.to_csv(P / "unmatched_districts.csv", index=False)
    log.info("%d outbreaks after removing duplicates; %d district names unmatched",
             len(outbreaks), len(unmatched))
    return outbreaks
