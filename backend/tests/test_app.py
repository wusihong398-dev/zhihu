from fastapi.testclient import TestClient

from app.main import app


def test_health_version_and_auth_boundary() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "database": "ok",
            "redis": "ok",
        }

        version = client.get("/api/version")
        assert version.status_code == 200
        assert version.json()["version"] == "0.10.1"

        accounts = client.get("/api/accounts")
        assert accounts.status_code == 401
