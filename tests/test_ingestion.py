"""Tests for Step 4: report dates, PDF parsing, cleaning and district matching."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from denguewatch.ingestion.cleaning import clean_rows, deduplicate, parse_date
from denguewatch.ingestion.districts import DistrictMatcher, district_key
from denguewatch.ingestion.names import match_state
from denguewatch.ingestion.pdf_parser import parse_report
from denguewatch.ingestion.reports import REPORT_LINK, week_dates

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def matcher():
    return DistrictMatcher()


# ---------- report weeks and links ----------

def test_week_dates_match_printed_report():
    # Week 30 of 2026 is printed on the report as 20-26 July 2026
    assert week_dates(2026, 30) == (date(2026, 7, 20), date(2026, 7, 26))


def test_report_link_pattern():
    m = REPORT_LINK.search('href="/uploads/weekly_outbreaks/2026/week30_1789731731.pdf"')
    assert m and m.group(1) == "2026" and m.group(2) == "30"
    assert REPORT_LINK.search("/uploads/weekly_outbreaks/2010/week35.pdf")


# ---------- names and dates ----------

@pytest.mark.parametrize("text,expected", [
    ("Maharashtr a", "Maharashtra"), ("Tamil Nadu Tamil Nadu", "Tamil Nadu"), ("Orissa", "Odisha"),
    ("Jammu & Kashmir", "Jammu and Kashmir"), ("Madhya Pradesh", "Madhya Pradesh"), ("Hassan", None),
])
def test_match_state(text, expected):
    assert match_state(text) == expected


@pytest.mark.parametrize("raw,year,expected", [
    ("14-07- 2026", 2026, date(2026, 7, 14)), ("13.08.10", 2010, date(2010, 8, 13)),
    ("04/08/12", 2012, date(2012, 8, 4)), ("31-02-2020", 2020, None), (None, 2020, None),
])
def test_parse_date(raw, year, expected):
    assert parse_date(raw, year) == expected


# ---------- district matching ----------

def test_district_key_cleans_broken_words():
    assert district_key("Mahabubnaga r") == "mahabubnagar"
    assert district_key("Mumbai (MCGM)") == "mumbai"


@pytest.mark.parametrize("state,district,expected,method", [
    ("Kerala", "Malappuram", "Malappuram", "exact"),
    ("Maharashtra", "Beed", "Bid", "alias"),                    # spelling in Census map
    ("Karnataka", "Belagavi", "Belgaum", "alias"),              # renamed in 2014
    ("Maharashtra", "Palghar", "Thane", "alias"),               # carved out of Thane in 2014
    ("Tamil Nadu", "Thiruvannamalai", "Tiruvannamalai", "fuzzy"),
    ("Lakshadweep", "Kavaratti Island", "Lakshadweep", "single-district"),
    ("Uttar Pradesh", "Malappuram", "Malappuram", "other-state"),  # wrong state carried in PDF
    ("Bihar", "Rohtas, Bhabhua", "Rohtas", "first-of-list"),
])
def test_district_matching(matcher, state, district, expected, method):
    m = matcher.match(state, district)
    assert (m.district, m.method) == (expected, method)


def test_statewide_and_unknown(matcher):
    assert matcher.match("Delhi", "All districts").method == "statewide"
    assert matcher.match("Kerala", "Atlantis").method == "unmatched"


# ---------- PDF parsing (real IDSP reports) ----------

def test_parse_2018_report_with_unique_ids():
    rows = parse_report(FIXTURES / "idsp_2018_week40.pdf")
    got = {(r.district_raw, r.cases_raw) for r in rows}
    assert ("Pune", "51") in got and ("South 24 Parganas", "728") in got
    pune = next(r for r in rows if r.district_raw == "Pune")
    assert pune.idsp_id == "MH/PNE/2018/40/1338" and pune.state_raw == "Maharashtra"


def test_parse_2010_report_with_merged_state_cells():
    rows = parse_report(FIXTURES / "idsp_2010_week33.pdf")
    hassan = next(r for r in rows if r.district_raw == "Hassan")
    assert hassan.state_raw == "Karnataka" and hassan.state_carried  # state cell merged in PDF
    assert (hassan.cases_raw, hassan.deaths_raw) == ("17", "0")      # "17 / 0" format


# ---------- cleaning and de-duplication ----------

def _raw(**kw):
    base = dict(year=2018, week=40, file="f.pdf", page=1, idsp_id=None, state_raw="Maharashtra",
                district_raw="Pune", disease_raw="Dengue", cases_raw="51", deaths_raw="0",
                start_date_raw="06-08-18", reporting_date_raw=None, status_raw="Under Surveillance",
                comments=None, state_carried=False, district_carried=False, notes="")
    base.update(kw)
    return base


def test_clean_and_flag(matcher):
    raw = pd.DataFrame([_raw(), _raw(cases_raw=None, district_raw="Nowhere")])
    clean = clean_rows(raw, matcher)
    ok, bad = clean.iloc[0], clean.iloc[1]
    assert ok["district"] == "Pune" and ok["cases"] == 51 and not ok["needs_review"]
    assert bad["needs_review"] and "cases missing" in bad["review_reasons"]


def test_same_outbreak_in_two_weeks_is_counted_once(matcher):
    raw = pd.DataFrame([_raw(week=40), _raw(week=41, cases_raw="60")])
    out = deduplicate(clean_rows(raw, matcher))
    assert len(out) == 1 and out.iloc[0]["times_listed"] == 2 and out.iloc[0]["report_week"] == 40
