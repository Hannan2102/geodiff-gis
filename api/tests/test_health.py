import pytest
from fastapi.testclient import TestClient

from geodiff.main import app


def test_health_is_independent_of_database() -> None:
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "X-Request-ID" in r.headers


@pytest.mark.db
def test_ready_reports_postgis_after_migrations() -> None:
    from geodiff.migrate import apply_migrations

    apply_migrations()
    apply_migrations()  # idempotent
    with TestClient(app) as client:
        r = client.get("/ready")
    body = r.json()
    assert r.status_code == 200, body
    assert body["database"] is True
    assert body["postgis"].startswith("3.")
    assert body["migrations"]["pending"] == []


def test_errors_are_machine_readable() -> None:
    with TestClient(app) as client:
        r = client.get("/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "http_error"
