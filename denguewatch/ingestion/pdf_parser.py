"""Extract dengue outbreak rows from an IDSP Weekly Outbreak Report PDF.

The report layout changed over the years:
  * 2009-2011: "Sr. No | State | District | Disease | cases / deaths | start | reporting | status | comments"
  * 2012-2015: same, with cases and deaths in separate columns
  * 2016-2026: a "Unique ID" (e.g. KL/MAL/2026/30/1219) replaces the serial number

All layouts share the same order around the disease column:
    ... State | District | Disease | Cases | Deaths | Start date | Reporting date | Status | Comments
so the parser locates the disease cell first and reads the other fields relative to it.
State and district cells are merged across rows in the PDF (blank on continuation
rows), so they are carried forward from the previous row of the same report.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pdfplumber

from denguewatch.ingestion.names import match_state

log = logging.getLogger(__name__)

DENGUE = re.compile(r"dengue|\bDHF\b", re.I)
IDSP_ID = re.compile(r"^[A-Z]{2,3}/[A-Z]{2,4}/\d{4}/\d{1,2}/\d+$")
CASES_DEATHS = re.compile(r"^\s*(\d{1,6})\s*/\s*(\d{1,4})\s*\*?\s*$")
INTEGER = re.compile(r"^\s*(\d{1,6})\s*\*?\s*$")
LEADING_NUMBER = re.compile(r"^\s*(\d{1,6})\s*[(\[*]")  # e.g. "310 (lab confirmed 12)"
DATE = re.compile(r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}")
STATUS = re.compile(r"under\s*(control|surveillance)|controlled|surveillance", re.I)
HEADER_WORDS = re.compile(r"name of|disease|illness|no\. of|date of|unique id|sr\.?\s*no", re.I)


@dataclass
class RawOutbreak:
    """One dengue-related row exactly as read from the PDF (before cleaning)."""

    page: int
    idsp_id: str | None
    state_raw: str | None
    district_raw: str | None
    disease_raw: str
    cases_raw: str | None
    deaths_raw: str | None
    start_date_raw: str | None
    reporting_date_raw: str | None
    status_raw: str | None
    comments: str | None
    state_carried: bool = False
    district_carried: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["notes"] = "; ".join(self.notes)
        return d


def _clean(cell: str | None) -> str:
    """Collapse line breaks and repeated spaces inside a table cell."""
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", cell.replace("\n", " ")).strip()


def _is_disease_cell(text: str) -> bool:
    return bool(DENGUE.search(text)) and len(text) <= 80


def _find_state(cells: list[str]) -> str | None:
    """A canonical state name found in the given cells, if any."""
    for c in cells:
        st = match_state(c)
        if st:
            return st
    return None


def _parse_row(cells: list[str], page: int, carry: dict) -> RawOutbreak | None:
    # 1. Find the disease column: the first short cell mentioning dengue.
    d = next((i for i, c in enumerate(cells) if _is_disease_cell(c)), None)
    if d is None:
        return None
    after = [c for c in cells[d + 1:] if c]  # skip empty spacer columns
    if not any(CASES_DEATHS.match(c) or INTEGER.match(c) or LEADING_NUMBER.match(c) for c in after[:3]):
        return None  # "dengue" mentioned, but this is not an outbreak row (e.g. a footnote)

    notes: list[str] = []

    # 2. Columns before the disease: [Sr. No / Unique ID] | State | District
    before = cells[:d]
    idsp_id = next((c.replace(" ", "") for c in before if IDSP_ID.match(c.replace(" ", ""))), None)
    filled = [c for c in before if c and not IDSP_ID.match(c.replace(" ", "")) and not re.fullmatch(r"\d+\.?", c)]
    district = filled[-1] if filled else ""
    state_cell = _find_state(filled[:-1])
    if district and match_state(district) and not state_cell:
        state_cell, district = match_state(district), ""  # only the state is filled; district merged from above
    state, state_carried = (state_cell, False) if state_cell else (carry.get("state"), True)
    district_carried = False
    if not district or IDSP_ID.match(district.replace(" ", "")) or re.fullmatch(r"\d+\.?", district):
        district, district_carried = carry.get("district"), True
        notes.append("district carried from previous row")
    if state_carried:
        notes.append("state carried from previous row")

    # 3. Columns after the disease: cases, deaths, dates, status, comments.
    cases = deaths = None
    i = 0
    if after and CASES_DEATHS.match(after[0]):
        cases, deaths = CASES_DEATHS.match(after[0]).groups()
        i = 1
    else:
        nums = []
        while i < len(after) and len(nums) < 2:
            m = INTEGER.match(after[i]) or LEADING_NUMBER.match(after[i])
            if not m or DATE.search(after[i]):
                break
            if not INTEGER.match(after[i]):
                notes.append(f"number taken from '{after[i][:30]}'")
            nums.append(m.group(1))
            i += 1
        if nums:
            cases = nums[0]
        if len(nums) > 1:
            deaths = nums[1]
    rest = after[i:]
    dates = [DATE.search(c).group(0) for c in rest if DATE.search(c) and len(c) <= 25]
    status = next((c for c in rest if STATUS.search(c) and len(c) <= 40), None)
    long_cells = [c for c in rest if len(c) > 40]
    comments = max(long_cells, key=len) if long_cells else None

    if cases is None:
        notes.append("cases not found")
    if not dates:
        notes.append("no dates found")

    return RawOutbreak(
        page=page, idsp_id=idsp_id, state_raw=state, district_raw=district or None,
        disease_raw=cells[d], cases_raw=cases, deaths_raw=deaths,
        start_date_raw=dates[0] if dates else None,
        reporting_date_raw=dates[1] if len(dates) > 1 else None,
        status_raw=status, comments=comments,
        state_carried=state_carried, district_carried=district_carried, notes=notes,
    )


def _update_carry(cells: list[str], carry: dict) -> None:
    """Remember the last state and district seen in any outbreak row (for merged cells)."""
    # first number that comes after some text (so a leading serial number is not mistaken for cases)
    k = next((i for i, c in enumerate(cells) if (CASES_DEATHS.match(c) or INTEGER.match(c))
              and any(t and not INTEGER.match(t) and not re.fullmatch(r"\d+\.?", t) for t in cells[:i])), None)
    if k is None or k < 2:
        return
    filled = [(i, c) for i, c in enumerate(cells[:k]) if c]
    if not filled:
        return
    state = _find_state([c for _, c in filled[:3]])
    if state:
        carry["state"] = state
    # District: the filled cell just before the disease cell, if it is not a state / serial / ID.
    if len(filled) >= 2:
        cand = filled[-2][1]
        if not (match_state(cand) or IDSP_ID.match(cand.replace(" ", "")) or re.fullmatch(r"\d+\.?", cand)
                or HEADER_WORDS.search(cand)):
            carry["district"] = cand


def parse_report(pdf_path: str | Path) -> list[RawOutbreak]:
    """Return all dengue-related outbreak rows found in one weekly report."""
    rows: list[RawOutbreak] = []
    carry: dict = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables():
                for raw_row in table:
                    cells = [_clean(c) for c in raw_row]
                    if not any(cells) or HEADER_WORDS.search(" ".join(cells[:4])) and not any(DENGUE.search(c) for c in cells):
                        continue
                    parsed = _parse_row(cells, page_no, carry)
                    _update_carry(cells, carry)
                    if parsed:
                        rows.append(parsed)
    return rows


def count_dengue_mentions(pdf_path: str | Path) -> int:
    """Rough count of 'dengue' mentions in a report's text (used for parse-coverage checks)."""
    with pdfplumber.open(pdf_path) as pdf:
        return sum(len(DENGUE.findall(p.extract_text() or "")) for p in pdf.pages)
