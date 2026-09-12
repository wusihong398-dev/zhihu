import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import require_active_user
from app.main import app
from app.models.user import UserRole


ADMIN = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)


def test_regular_user_can_save_own_ai_key() -> None:
    app.dependency_overrides[require_active_user] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            created_user = client.post(
                "/api/users",
                json={
                    "username": f"ai_user_{uuid.uuid4().hex[:8]}",
                    "password": "test-password-123",
                },
            )
            assert created_user.status_code == 201
            regular_user = SimpleNamespace(
                id=uuid.UUID(created_user.json()["id"]), role=UserRole.user
            )
            app.dependency_overrides[require_active_user] = lambda: regular_user

            providers = client.get("/api/ai/providers")
            assert providers.status_code == 200
            assert {item["provider"] for item in providers.json()} == {
                "openai",
                "deepseek",
                "volcengine",
            }

            saved = client.put(
                "/api/ai/providers/openai",
                json={
                    "api_key": "sk-user-private-key",
                    "model": "gpt-5-mini",
                    "enabled": True,
                },
            )
            assert saved.status_code == 200
            assert saved.json()["has_api_key"] is True
            assert saved.json()["masked_key"] != "sk-user-private-key"

            refreshed = client.get("/api/ai/providers")
            openai = next(
                item for item in refreshed.json() if item["provider"] == "openai"
            )
            assert openai["enabled"] is True
            assert openai["has_api_key"] is True
    finally:
        app.dependency_overrides.pop(require_active_user, None)
