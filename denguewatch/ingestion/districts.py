"""Match district names written in IDSP reports to the canonical Census 2011 districts.

Matching order (the first that succeeds wins):
  1. exact   - same letters-only name within the reported state
  2. alias   - listed in data/reference/district_aliases.csv (renamed or newer districts)
  3. fuzzy   - closest name within the state, if similarity >= FUZZY_THRESHOLD
  4. other    - exact/alias match in a *different* state, when exactly one state has it
                 (fixes rows where the state cell was carried over wrongly in the PDF)
Special cases: states with a single district (e.g. Chandigarh, Lakshadweep) always match it;
"All districts" / "09 Districts" are kept as state-wide rows; when several districts are
listed ("Rohtas, Bhabhua"), the first one is used and the row is flagged.
States that were split since 2011 (Telangana / Andhra Pradesh, Ladakh / Jammu and Kashmir)
are searched together. Anything else is returned unmatched and flagged for review.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from rapidfuzz import fuzz, process

from denguewatch import config
from denguewatch.ingestion.names import letters

REFERENCE_DIR = config.DATA_DIR / "reference"
FUZZY_THRESHOLD = 85

# States whose 2011 districts may be reported under either name
STATE_GROUPS = {
    "Telangana": ["Telangana", "Andhra Pradesh"],
    "Andhra Pradesh": ["Andhra Pradesh", "Telangana"],
    "Ladakh": ["Ladakh", "Jammu and Kashmir"],
    "Jammu and Kashmir": ["Jammu and Kashmir", "Ladakh"],
}

# Words that add nothing to matching ("Pune District", "Mumbai (MCGM)")
STATEWIDE = re.compile(r"\ball\b|\b\d+\s*districts?\b|^districts?$", re.I)
SPLIT = re.compile(r"\s*(?:,|&|/|\band\b|[-–])\s*", re.I)
NOISE = re.compile(r"\(.*?\)|\b(district|dist|distt|city|corporation|municipal|mcgm|mc)\b\.?", re.I)


def district_key(name: str) -> str:
    """Letters-only key used for matching: 'Mahabubnaga r' -> 'mahabubnagar'."""
    return letters(NOISE.sub(" ", name or ""))


@dataclass(frozen=True)
class DistrictMatch:
    census_code: int | None
    district: str | None
    state: str | None
    method: str          # exact / alias / fuzzy / unmatched
    score: float


class DistrictMatcher:
    def __init__(self, districts_csv: Path = REFERENCE_DIR / "districts.csv",
                 aliases_csv: Path = REFERENCE_DIR / "district_aliases.csv"):
        with open(districts_csv, encoding="utf-8") as f:
            self.districts = [dict(r, census_code=int(r["census_code"])) for r in csv.DictReader(f)]
        self.by_code = {d["census_code"]: d for d in self.districts}
        self.by_state: dict[str, dict[str, dict]] = {}
        for d in self.districts:
            self.by_state.setdefault(d["state_name"], {})[district_key(d["district_name"])] = d
        self.aliases: dict[tuple[str, str], int] = {}
        if Path(aliases_csv).exists():
            with open(aliases_csv, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    self.aliases[(r["state_name"], district_key(r["alias"]))] = int(r["census_code"])

    def _result(self, d: dict, method: str, score: float) -> DistrictMatch:
        return DistrictMatch(d["census_code"], d["district_name"], d["state_name"], method, round(score, 1))

    def _match_in(self, states: list[str], key: str) -> DistrictMatch | None:
        for st in states:  # exact
            if key in self.by_state.get(st, {}):
                return self._result(self.by_state[st][key], "exact", 100)
        for st in states:  # alias
            code = self.aliases.get((st, key))
            if code is not None:
                return self._result(self.by_code[code], "alias", 100)
        return None

    def _fuzzy(self, states: list[str], key: str) -> DistrictMatch | None:
        choices = {k: d for st in states for k, d in self.by_state.get(st, {}).items()}
        if choices:
            best = process.extractOne(key, list(choices), scorer=fuzz.ratio)
            if best and best[1] >= FUZZY_THRESHOLD:
                return self._result(choices[best[0]], "fuzzy", best[1])
        return None

    @cache
    def match(self, state: str | None, district_raw: str | None) -> DistrictMatch:
        raw = district_raw or ""
        key = district_key(raw)
        states = STATE_GROUPS.get(state, [state]) if state else []

        # Single-district states: whatever the report says, it is that district.
        if state and len(self.by_state.get(state, {})) == 1:
            return self._result(next(iter(self.by_state[state].values())), "single-district", 100)
        if state and STATEWIDE.search(raw):
            return DistrictMatch(None, None, state, "statewide", 0.0)
        if not key:
            return DistrictMatch(None, None, state, "unmatched", 0.0)

        found = (self._match_in(states, key) or self._fuzzy(states, key)) if states else None
        if found:
            return found
        # Several districts listed: use the first one that matches.
        parts = [p for p in SPLIT.split(raw) if district_key(p)]
        if len(parts) > 1:
            for part in parts:
                pk = district_key(part)
                hit = (self._match_in(states, pk) or self._fuzzy(states, pk)) if states else None
                if hit:
                    return DistrictMatch(hit.census_code, hit.district, hit.state, "first-of-list", hit.score)
        # The state may be wrong (carried over in the PDF): look in all states for a unique hit.
        for k in [key] + [district_key(p) for p in parts[:1] if len(parts) > 1]:
            hits = {d["census_code"]: d for st, dmap in self.by_state.items() if st not in states
                    for kk, d in dmap.items() if kk == k}
            hits.update({c: self.by_code[c] for (st, kk), c in self.aliases.items() if kk == k and st not in states})
            if len(hits) == 1:
                return self._result(next(iter(hits.values())), "other-state", 100)
        return DistrictMatch(None, None, state, "unmatched", 0.0)
