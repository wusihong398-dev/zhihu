from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.security import require_admin
from app.main import app


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_managed_users_have_isolated_account_data_and_expiration() -> None:
    app.dependency_overrides[require_admin] = lambda: object()
    try:
        with TestClient(app) as client:
            future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
            first_user = client.post(
                "/api/users",
                json={
                    "username": "tenant_one",
                    "password": "tenant-one-password",
                    "expires_at": future,
                },
            )
            second_user = client.post(
                "/api/users",
                json={
                    "username": "tenant_two",
                    "password": "tenant-two-password",
                    "expires_at": future,
                },
            )
            assert first_user.status_code == 201
            assert second_user.status_code == 201
            app.dependency_overrides.pop(require_admin, None)

            first_login = client.post(
                "/api/auth/login",
                json={"username": "tenant_one", "password": "tenant-one-password"},
            )
            second_login = client.post(
                "/api/auth/login",
                json={"username": "tenant_two", "password": "tenant-two-password"},
            )
            assert first_login.status_code == 200
            assert second_login.status_code == 200
            first_headers = _authorization(first_login.json()["access_token"])
            second_headers = _authorization(second_login.json()["access_token"])

            first_account = client.post(
                "/api/accounts",
                json={"display_name": "用户一的知乎账号"},
                headers=first_headers,
            )
            second_account = client.post(
                "/api/accounts",
                json={"display_name": "用户二的知乎账号"},
                headers=second_headers,
            )
            assert first_account.status_code == 201
            assert second_account.status_code == 201
            first_account_id = first_account.json()["id"]

            first_names = [
                item["display_name"]
                for item in client.get("/api/accounts", headers=first_headers).json()
            ]
            second_names = [
                item["display_name"]
                for item in client.get("/api/accounts", headers=second_headers).json()
            ]
            assert first_names == ["用户一的知乎账号"]
            assert second_names == ["用户二的知乎账号"]
            assert (
                client.get(
                    f"/api/accounts/{first_account_id}", headers=second_headers
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/accounts/{first_account_id}/login-session",
                    headers=second_headers,
                ).status_code
                == 404
            )

            product = client.post(
                f"/api/accounts/{first_account_id}/products",
                json={"name": "用户一的商品"},
                headers=first_headers,
            )
            assert product.status_code == 201
            assert (
                client.get(
                    f"/api/accounts/{first_account_id}/products",
                    headers=second_headers,
                ).status_code
                == 404
            )

            first_ai = client.put(
                "/api/ai/providers/openai",
                json={
                    "api_key": "sk-user-one-private-key",
                    "model": "user-one-model",
                    "enabled": True,
                },
                headers=first_headers,
            )
            assert first_ai.status_code == 200
            second_ai = client.get("/api/ai/providers", headers=second_headers)
            assert second_ai.status_code == 200
            second_openai = next(
                item for item in second_ai.json() if item["provider"] == "openai"
            )
            assert second_openai["model"] != "user-one-model"
            assert second_openai["has_api_key"] is False

            app.dependency_overrides[require_admin] = lambda: object()
            expired = client.patch(
                f"/api/users/{first_user.json()['id']}",
                json={
                    "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
                },
            )
            assert expired.status_code == 200
            app.dependency_overrides.pop(require_admin, None)
            expired_login = client.post(
                "/api/auth/login",
                json={"username": "tenant_one", "password": "tenant-one-password"},
            )
            assert expired_login.status_code == 403
            assert "到期" in expired_login.json()["detail"]
    finally:
        app.dependency_overrides.pop(require_admin, None)
