"""Database tests (Step 5). They need a running PostgreSQL with DATABASE_URL set in .env
and data loaded (python scripts/setup_database.py). Otherwise they are skipped.
Nothing is changed permanently: write tests run inside a transaction that is rolled back.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from denguewatch.db.connection import check_connection, get_engine
from denguewatch.db.queries import query_names, run_query

ok, _ = check_connection()
pytestmark = pytest.mark.skipif(not ok, reason="PostgreSQL not reachable (set DATABASE_URL in .env)")


@pytest.fixture(scope="module")
def engine():
    eng = get_engine()
    with eng.connect() as conn:
        if not conn.execute(text("SELECT to_regclass('public.outbreaks')")).scalar():
            pytest.skip("tables not created yet - run scripts/setup_database.py")
    return eng


def test_core_tables_have_data(engine):
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM districts")).scalar() == 640
        assert conn.execute(text("SELECT COUNT(*) FROM outbreaks")).scalar() > 0


def test_every_outbreak_points_to_an_existing_report(engine):
    with engine.connect() as conn:
        orphans = conn.execute(text(
            "SELECT COUNT(*) FROM outbreaks o LEFT JOIN reports r USING (report_id) WHERE r.report_id IS NULL")).scalar()
    assert orphans == 0


@pytest.mark.parametrize("name", query_names())
def test_every_named_query_runs(engine, name):
    df = run_query(name, engine=engine)
    assert df is not None


def test_seasonality_covers_twelve_months(engine):
    df = run_query("monthly_seasonality", engine=engine)
    assert list(df["start_month"]) == list(range(1, 13))
    assert abs(df["pct_of_outbreaks"].sum() - 100) < 1


def test_constraints_reject_bad_data(engine):
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(text("UPDATE outbreaks SET cases = -5 WHERE outbreak_id = "
                                      "(SELECT MIN(outbreak_id) FROM outbreaks)"))
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(text("INSERT INTO weather_daily (district_id, obs_date, rel_humidity_pct) "
                                      "SELECT MIN(district_id), DATE '2026-01-01', 150 FROM districts"))
        finally:
            trans.rollback()
