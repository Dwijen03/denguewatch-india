"""Canonical Indian state names and helpers to recognise them in messy report text."""

from __future__ import annotations

import re

# Current state / UT names (matching data/reference/districts.csv)
STATES = [
    "Andaman and Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar",
    "Chandigarh", "Chhattisgarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka",
    "Kerala", "Ladakh", "Lakshadweep", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Puducherry", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
]

# Spellings and old names seen in IDSP reports -> canonical name
STATE_ALIASES = {
    "orissa": "Odisha", "pondicherry": "Puducherry", "uttaranchal": "Uttarakhand",
    "tamilnadu": "Tamil Nadu", "chattisgarh": "Chhattisgarh", "chhatisgarh": "Chhattisgarh",
    "jk": "Jammu and Kashmir", "jandk": "Jammu and Kashmir", "jammuandkashmir": "Jammu and Kashmir",
    "nctofdelhi": "Delhi", "newdelhi": "Delhi", "andaman": "Andaman and Nicobar Islands",
    "andamannicobar": "Andaman and Nicobar Islands", "dadra": "Dadra and Nagar Haveli and Daman and Diu",
    "daman": "Dadra and Nagar Haveli and Daman and Diu", "dnh": "Dadra and Nagar Haveli and Daman and Diu",
    "arunachal": "Arunachal Pradesh", "himachal": "Himachal Pradesh", "telengana": "Telangana",
    "karnatka": "Karnataka", "maharastra": "Maharashtra", "jharkhad": "Jharkhand",
}


def letters(text: str) -> str:
    """Lower-case letters only: 'Maharashtr a' -> 'maharashtra', 'J & K' -> 'jk'."""
    return re.sub(r"[^a-z]", "", (text or "").lower().replace("&", "and"))


_KEYS = sorted(
    [(letters(s), s) for s in STATES] + [(k, v) for k, v in STATE_ALIASES.items()],
    key=lambda kv: -len(kv[0]),  # longest first, so "westbengal" wins over shorter keys
)


def match_state(text: str | None) -> str | None:
    """Return the canonical state if the text starts with a known state name, else None.

    Handles broken words ('Maharashtr a'), repeats ('Tamil Nadu Tamil Nadu'),
    old names ('Orissa') and '&' vs 'and'.
    """
    key = letters(text or "")
    if len(key) < 2:
        return None
    for k, name in _KEYS:
        if key.startswith(k) and (len(k) >= 4 or key == k):
            return name
    return None
