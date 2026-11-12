"""Tests for the REST API (Step 8). Validation tests always run; data tests need PostgreSQL with data."""

import pytest
from sqlalchemy import text

from denguewatch import config
from denguewatch.app import create_app
from denguewatch.db.connection import check_connection, get_engine

ok, _ = check_connection()


def _has_data() -> bool:
    if not ok:
        return False
    with get_engine().connect() as conn:
        return bool(conn.execute(text("SELECT to_regclass('public.outbreaks')")).scalar())


needs_db = pytest.mark.skipif(not _has_data(), reason="PostgreSQL with loaded data not available")


@pytest.fixture(scope="module")
def client():
    return create_app().test_client()


@pytest.fixture(scope="module")
def code(client):
    """A district that has outbreaks (used by the district endpoints)."""
    with get_engine().connect() as conn:
        return conn.execute(text("""SELECT d.census_code FROM outbreaks o JOIN districts d USING (district_id)
                                    GROUP BY d.census_code ORDER BY COUNT(*) DESC LIMIT 1""")).scalar()


# ---------------- input validation (no database needed: rejected before any query)

@pytest.mark.parametrize("url,message", [
    ("/api/v1/outbreaks?limit=abc", "whole number"),
    ("/api/v1/outbreaks?limit=99999", "between"),
    ("/api/v1/outbreaks?from=12-07-2024", "date like"),
    ("/api/v1/outbreaks/summary?group_by=planet", "one of"),
    ("/api/v1/risk/current?level=extreme", "one of"),
])
def test_bad_parameters_give_400(client, url, message):
    r = client.get(url)
    assert r.status_code == 400 and message in r.get_json()["error"]["message"]


def test_unknown_endpoint_gives_json_404(client):
    r = client.get("/api/v1/no-such-thing")
    assert r.status_code == 404 and r.get_json()["error"]["status"] == 404


def test_admin_refresh_requires_token(client):
    r = client.post("/api/v1/admin/refresh?job=predict")
    assert r.status_code == 401


def test_sql_injection_attempt_is_harmless(client):
    r = client.get("/api/v1/districts?state=Kerala' OR '1'='1")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        assert r.get_json()["count"] == 0  # treated as a (non-existent) state name, not as SQL


# ---------------- endpoints with data

@needs_db
def test_index_lists_endpoints(client):
    paths = {e["path"] for e in client.get("/api/v1/").get_json()["endpoints"]}
    assert {"/api/v1/health", "/api/v1/outbreaks", "/api/v1/risk/current"} <= paths


@needs_db
def test_health(client):
    body = client.get("/api/v1/health").get_json()
    assert body["database"]["connected"] and body["data"]["outbreaks_loaded"] > 0


@needs_db
def test_states_and_districts(client):
    assert client.get("/api/v1/states").get_json()["count"] == 36
    kerala = client.get("/api/v1/districts?state=Kerala").get_json()
    assert kerala["count"] == 14 and {"census_code", "latitude"} <= set(kerala["results"][0])


@needs_db
def test_district_detail_and_404(client, code):
    body = client.get(f"/api/v1/districts/{code}").get_json()
    assert body["census_code"] == code and body["summary"]["outbreaks"] > 0
    assert client.get("/api/v1/districts/99999").status_code == 404


@needs_db
def test_outbreaks_filters_and_paging(client):
    body = client.get("/api/v1/outbreaks?state=Kerala&year=2023&limit=5").get_json()
    assert body["count"] <= 5 and body["total"] >= body["count"]
    assert all(r["state_name"] == "Kerala" and r["report_year"] == 2023 for r in body["results"])


@needs_db
@pytest.mark.parametrize("group", ["year", "month", "state", "district"])
def test_outbreak_summary(client, group):
    body = client.get(f"/api/v1/outbreaks/summary?group_by={group}").get_json()
    assert body["count"] > 0 and group in body["results"][0]


@needs_db
def test_timeline_and_weather(client, code):
    tl = client.get(f"/api/v1/districts/{code}/timeline?from=2023-01-01&to=2023-12-31").get_json()
    assert 50 <= tl["count"] <= 53 and "rain_mm" in tl["results"][0]
    wx = client.get(f"/api/v1/weather/{code}?from=2024-07-01&to=2024-07-07").get_json()
    assert wx["count"] == 7
    wk = client.get(f"/api/v1/weather/{code}?from=2024-01-01&to=2024-03-31&granularity=weekly").get_json()
    assert wk["granularity"] == "weekly" and wk["count"] >= 12


@needs_db
def test_risk_and_model(client, code):
    cur = client.get("/api/v1/risk/current").get_json()
    if cur["count"] == 0:
        pytest.skip("no predictions yet - run scripts/train_model.py")
    assert cur["count"] == 640 and set(cur["levels"]) <= {"low", "medium", "high"}
    high = client.get("/api/v1/risk/current?level=high").get_json()
    assert all(r["risk_level"] == "high" for r in high["results"])
    assert client.get(f"/api/v1/risk/{code}").get_json()["count"] >= 1
    model = client.get("/api/v1/model").get_json()
    assert model["evaluation"]["test_pr_auc"] > 0.05


@needs_db
def test_csv_export(client):
    r = client.get("/api/v1/export/outbreaks.csv?state=Goa")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert r.data.decode().splitlines()[0].startswith("outbreak_id,state_name")


@needs_db
def test_admin_refresh_with_token_starts_job(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "test-token")
    from denguewatch.api import routes
    monkeypatch.setitem(routes.JOBS, "predict", lambda: None)  # do not actually run the job in tests
    r = client.post("/api/v1/admin/refresh?job=predict", headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 202 and r.get_json()["started"] == "predict"


@needs_db
def test_schedule_endpoint(client):
    body = client.get("/api/v1/schedule").get_json()
    assert {r["job"] for r in body["results"]} == {"daily_update", "weekly_reports", "monthly_retrain", "news_signals"}
    assert all(set(r) == {"job", "last_success", "overdue", "next_run"} for r in body["results"])


def test_admin_refresh_accepts_step10_jobs(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "test-token")
    from denguewatch.api import routes
    monkeypatch.setitem(routes.JOBS, "reports", lambda: None)
    r = client.post("/api/v1/admin/refresh?job=reports", headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 202 and r.get_json()["started"] == "reports"
