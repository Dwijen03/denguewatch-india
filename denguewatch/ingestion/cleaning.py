"""Turn raw parsed rows into clean, validated, de-duplicated dengue outbreak records."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd

from denguewatch.ingestion.districts import DistrictMatch, DistrictMatcher
from denguewatch.ingestion.names import match_state
from denguewatch.ingestion.reports import week_dates

OTHER_DISEASES = re.compile(
    r"chikungunya|malaria|typhus|typhoid|lepto|fever|measles|hepatitis|diarrh|cholera|encephalitis|zika|jaundice", re.I)
SUSPECTED = re.compile(r"\?|suspect", re.I)
# State codes used in IDSP unique IDs (first part of e.g. "KL/MAL/2026/30/1219")
ID_STATE_CODES = {
    "AN": "Andaman and Nicobar Islands", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam",
    "BH": "Bihar", "BR": "Bihar", "CH": "Chandigarh", "CT": "Chhattisgarh", "CG": "Chhattisgarh",
    "DD": "Dadra and Nagar Haveli and Daman and Diu", "DN": "Dadra and Nagar Haveli and Daman and Diu",
    "DL": "Delhi", "GA": "Goa", "GO": "Goa", "GJ": "Gujarat", "HP": "Himachal Pradesh", "HR": "Haryana",
    "JH": "Jharkhand", "JK": "Jammu and Kashmir", "KA": "Karnataka", "KN": "Karnataka", "KL": "Kerala",
    "LA": "Ladakh", "LD": "Lakshadweep", "MG": "Meghalaya", "ML": "Meghalaya", "MH": "Maharashtra",
    "MN": "Manipur", "MP": "Madhya Pradesh", "MZ": "Mizoram", "NG": "Nagaland", "NL": "Nagaland",
    "OD": "Odisha", "OR": "Odisha", "PB": "Punjab", "PD": "Puducherry", "PN": "Puducherry", "PY": "Puducherry",
    "RJ": "Rajasthan", "SK": "Sikkim", "TL": "Telangana", "TS": "Telangana", "TN": "Tamil Nadu",
    "TR": "Tripura", "UA": "Uttarakhand", "UK": "Uttarakhand", "UP": "Uttar Pradesh", "WB": "West Bengal",
}

# Problems serious enough to exclude a row from modelling until checked by a person
REVIEW_REASONS = {"state not recognised", "district not matched", "cases missing", "deaths > cases",
                  "start date missing/invalid", "start date outside plausible range"}
DATE_PARTS = re.compile(r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{2,4})")


def parse_date(raw: str | None, report_year: int) -> date | None:
    """Parse '14-07- 2026', '13.08.10', '04/08/12' (day first). Returns None if invalid."""
    if not isinstance(raw, str):
        return None
    m = DATE_PARTS.search(raw)
    if not m:
        return None
    d, mth, y = (int(x) for x in m.groups())
    if y < 100:
        y += 2000
    if not (2000 <= y <= report_year + 1):
        return None
    try:
        return date(y, mth, d)
    except ValueError:
        return None


def to_int(raw) -> int | None:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def normalise_status(raw: str | None) -> str | None:
    t = raw.lower().replace(" ", "") if isinstance(raw, str) else ""
    if "control" in t:
        return "Under Control"
    if "surveil" in t:
        return "Under Surveillance"
    return None


def _id_parts(idsp_id) -> tuple[str | None, str | None]:
    """'KL/MAL/2026/30/1219' -> ('Kerala', 'MAL')."""
    if not isinstance(idsp_id, str) or "/" not in idsp_id:
        return None, None
    parts = idsp_id.split("/")
    return ID_STATE_CODES.get(parts[0]), parts[1]


def _carried(r) -> bool:
    return bool(r.state_carried) or bool(r.district_carried)


def build_id_lookup(raw: pd.DataFrame, matcher: DistrictMatcher) -> dict[tuple[str, str], int]:
    """Learn which district each IDSP ID abbreviation stands for (e.g. ('Kerala','MAL') -> Malappuram).

    Uses only rows where state and district were printed on the row itself, and keeps an
    abbreviation only if it always pointed to the same district.
    """
    seen: dict[tuple[str, str], set[int]] = {}
    for r in raw.itertuples(index=False):
        id_state, abbr = _id_parts(r.idsp_id)
        if not id_state or _carried(r) or not isinstance(r.district_raw, str):
            continue
        m = matcher.match(id_state, r.district_raw)
        if m.census_code is not None and m.method in ("exact", "alias", "fuzzy", "single-district"):
            seen.setdefault((id_state, abbr), set()).add(m.census_code)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def clean_rows(raw: pd.DataFrame, matcher: DistrictMatcher | None = None) -> pd.DataFrame:
    """Clean every raw row and add validation flags. One output row per input row."""
    matcher = matcher or DistrictMatcher()
    id_lookup = build_id_lookup(raw, matcher)
    out = []
    for r in raw.itertuples(index=False):
        week_start, week_end = week_dates(int(r.year), int(r.week))
        reasons: list[str] = []

        state = match_state(r.state_raw) if isinstance(r.state_raw, str) else None
        district_raw = r.district_raw if isinstance(r.district_raw, str) else None
        m = None
        # From 2016 each row has a unique ID like KL/MAL/2026/30/1219. When the state or
        # district had to be carried over from another row, the ID is the more reliable source.
        id_state, abbr = _id_parts(r.idsp_id)
        if id_state and (_carried(r) or not state):
            code = id_lookup.get((id_state, abbr))
            if code is not None:
                d = matcher.by_code[code]
                m = DistrictMatch(code, d["district_name"], d["state_name"], "from-id", 100)
                district_raw = district_raw if not _carried(r) else d["district_name"]
                reasons.append("state/district taken from the IDSP unique ID")
            elif id_state != state:
                state, district_raw = id_state, None
                reasons.append("state taken from the IDSP unique ID")
        if not state and not (m and m.state):
            reasons.append("state not recognised")
        if m is None:
            m = matcher.match(state, district_raw)
        if m.method == "unmatched":
            reasons.append("district not matched")
        elif m.method == "fuzzy":
            reasons.append(f"district fuzzy-matched ({m.score})")
        elif m.method == "first-of-list":
            reasons.append("several districts listed - first one used")
        elif m.method == "statewide":
            reasons.append("state-wide outbreak (no single district)")
        if m.state and m.state != state:
            if m.method == "other-state":
                reasons.append(f"state corrected from {state} to {m.state} using the district name")
            state = m.state  # e.g. a Telangana district reported under Andhra Pradesh before 2014

        cases, deaths = to_int(r.cases_raw), to_int(r.deaths_raw)
        if cases is None:
            reasons.append("cases missing")
        if deaths is None:
            deaths = 0 if cases is not None else None
        if cases is not None and deaths is not None and deaths > cases:
            reasons.append("deaths > cases")

        start = parse_date(r.start_date_raw, int(r.year))
        reporting = parse_date(r.reporting_date_raw, int(r.year))
        if start is None:
            reasons.append("start date missing/invalid")
        elif not (week_start - timedelta(days=365) <= start <= week_end + timedelta(days=7)):
            reasons.append("start date outside plausible range")
        if start and reporting and reporting < start:
            # Typo in one of the two dates in the PDF (0.2% of rows); the start date is still used.
            reasons.append("reporting date before start date (source inconsistency)")

        disease = str(r.disease_raw)
        notes = r.notes if isinstance(r.notes, str) else ""
        if "carried" in notes:
            reasons.append(notes)

        out.append({
            "report_year": int(r.year), "report_week": int(r.week),
            "week_start": week_start, "week_end": week_end,
            "report_file": r.file, "page": int(r.page),
            "idsp_id": r.idsp_id if isinstance(r.idsp_id, str) else None,
            "state": state, "district_raw": district_raw,
            "census_code": m.census_code, "district": m.district,
            "match_method": m.method, "match_score": m.score,
            "disease_raw": re.sub(r"^[ivxlc]+\.?\s+", "", disease, flags=re.I).strip(),
            "is_suspected": bool(SUSPECTED.search(disease)),
            "is_mixed": bool(OTHER_DISEASES.search(disease)),
            "cases": cases, "deaths": deaths,
            "start_date": start, "reporting_date": reporting,
            "report_lag_days": (week_end - start).days if start else None,
            "status": normalise_status(r.status_raw),
            "comments": r.comments if isinstance(r.comments, str) else None,
            "needs_review": bool([x for x in reasons if x.split(" (")[0] in REVIEW_REASONS]),
            "review_reasons": "; ".join(reasons),
        })
    return pd.DataFrame(out)


def deduplicate(clean: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per outbreak.

    The same outbreak can be listed again in later weeks (follow-up or "reported late").
    Outbreaks are identified by district + outbreak start date;
    the earliest report is kept and the number of listings is recorded.
    """
    df = clean.sort_values(["report_year", "report_week", "page"]).copy()
    place = df["census_code"].astype("Int64").astype(str).where(df["census_code"].notna(),
                                                               df["state"].fillna("") + "|" + df["district_raw"].fillna(""))
    df["_key"] = place + "|" + df["start_date"].astype(str)
    no_date = df["start_date"].isna()
    df.loc[no_date, "_key"] = df.loc[no_date, "report_file"] + "|" + df.loc[no_date, "page"].astype(str) + "|" + place[no_date]
    df["times_listed"] = df.groupby("_key")["_key"].transform("size")
    out = df.drop_duplicates("_key", keep="first").drop(columns="_key")
    out.insert(0, "outbreak_id", range(1, len(out) + 1))
    return out.reset_index(drop=True)
