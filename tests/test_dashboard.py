"""Smoke tests for the dashboard (Step 9): the page and its layout load, and helpers behave."""

import pytest
from sqlalchemy import text

from denguewatch.app import create_app
from denguewatch.dashboard.app import nice
from denguewatch.db.connection import check_connection, get_engine

ok, _ = check_connection()


def _has_data() -> bool:
    if not ok:
        return False
    with get_engine().connect() as conn:
        return bool(conn.execute(text("SELECT to_regclass('public.outbreaks')")).scalar())


def test_common_district_names():
    assert nice("Raigarh", "Maharashtra") == "Raigad (Raigarh)"
    assert nice("Raigarh", "Chhattisgarh") == "Raigarh"   # same Census name, different district
    assert nice("Pune", "Maharashtra") == "Pune"


@pytest.mark.skipif(not _has_data(), reason="PostgreSQL with loaded data not available")
def test_dashboard_page_and_layout_load():
    client = create_app().test_client()
    assert client.get("/dashboard/").status_code == 200
    layout = client.get("/dashboard/_dash-layout")
    assert layout.status_code == 200
    body = layout.get_data(as_text=True)
    for tab in ("Risk this week", "District explorer", "Compare districts", "Model & data"):
        assert tab in body
