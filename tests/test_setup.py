"""Smoke tests for the project skeleton (no database needed)."""

from denguewatch import config
from denguewatch.app import create_app


def test_config_constants():
    assert config.PREDICTION_HORIZON_WEEKS == 4
    assert config.REPORTING_LAG_WEEKS == 8
    assert config.IDSP_ARCHIVE_URL.startswith("https://")


def test_health_endpoint_responds():
    client = create_app().test_client()
    response = client.get("/api/v1/health")
    # 200 when PostgreSQL is reachable, 503 when it is not - both mean the API works.
    assert response.status_code in (200, 503)
    assert response.get_json()["service"] == "DengueWatch India API"


def test_dashboard_page_loads():
    client = create_app().test_client()
    assert client.get("/dashboard/").status_code == 200
