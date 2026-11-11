"""Find and download IDSP Weekly Outbreak Reports from the NCDC website."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests

from denguewatch import config

log = logging.getLogger(__name__)

# e.g. /uploads/weekly_outbreaks/2026/week30_1789731731.pdf  or  .../2010/week35.pdf
REPORT_LINK = re.compile(r'/uploads/weekly_outbreaks/(\d{4})/week(\d{1,2})(?:_\d+)?\.pdf', re.I)
HEADERS = {"User-Agent": "DengueWatch-India academic project (Mahindra University)"}


@dataclass(frozen=True)
class ReportRef:
    """One weekly report available on the NCDC website."""

    year: int
    week: int
    url: str

    @property
    def filename(self) -> str:
        return f"{self.year}_week{self.week:02d}.pdf"

    @property
    def week_start(self) -> date:
        return week_dates(self.year, self.week)[0]

    @property
    def week_end(self) -> date:
        return week_dates(self.year, self.week)[1]


def week_dates(year: int, week: int) -> tuple[date, date]:
    """Monday and Sunday of an IDSP reporting week.

    IDSP weeks follow ISO-8601 numbering (Monday to Sunday); e.g. week 30 of
    2026 is 20-26 July 2026, as printed on that report.
    """
    try:
        start = date.fromisocalendar(year, week, 1)
    except ValueError:  # a year that has no ISO week 53
        start = date.fromisocalendar(year, week - 1, 1) + timedelta(weeks=1)
    return start, start + timedelta(days=6)


def list_reports(archive_url: str = config.IDSP_ARCHIVE_URL, session: requests.Session | None = None) -> list[ReportRef]:
    """Return every weekly report linked from the NCDC Weekly Outbreaks page, oldest first."""
    s = session or requests.Session()
    html = s.get(archive_url, headers=HEADERS, timeout=60).text
    seen: dict[tuple[int, int], ReportRef] = {}
    for match in REPORT_LINK.finditer(html):
        year, week = int(match.group(1)), int(match.group(2))
        ref = ReportRef(year, week, urljoin(archive_url, match.group(0)))
        seen.setdefault((year, week), ref)  # keep the first link if a week is listed twice
    refs = sorted(seen.values(), key=lambda r: (r.year, r.week))
    log.info("Found %d weekly reports (%s to %s)", len(refs),
             f"{refs[0].year}-W{refs[0].week}" if refs else "-", f"{refs[-1].year}-W{refs[-1].week}" if refs else "-")
    return refs


def download_report(ref: ReportRef, dest_dir: Path = config.RAW_DIR, session: requests.Session | None = None,
                    retries: int = 3, pause: float = 1.0) -> Path:
    """Download one report into dest_dir (skipped if already present). Returns the file path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / ref.filename
    if path.exists() and path.stat().st_size > 0:
        return path
    s = session or requests.Session()
    for attempt in range(1, retries + 1):
        try:
            resp = s.get(ref.url, headers=HEADERS, timeout=120)
            resp.raise_for_status()
            if not resp.content.startswith(b"%PDF"):
                raise ValueError("response is not a PDF")
            path.write_bytes(resp.content)
            time.sleep(pause)  # be polite to the government server
            return path
        except (requests.RequestException, ValueError) as exc:
            log.warning("Download failed (%s, attempt %d/%d): %s", ref.filename, attempt, retries, exc)
            time.sleep(pause * attempt * 2)
    raise RuntimeError(f"Could not download {ref.url}")


def file_sha256(path: Path) -> str:
    """Fingerprint of a downloaded file, stored so changed files can be detected."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
