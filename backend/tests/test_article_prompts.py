import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import require_active_user
from app.main import app
from app.models.user import UserRole


ADMIN = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)


def _create_user(client: TestClient, prefix: str) -> dict:
    return client.post(
        "/api/users",
        json={
            "username": f"{prefix}_{uuid.uuid4().hex[:8]}",
            "password": "test-password-123",
        },
    ).json()


def test_prompt_folder_template_crud_and_user_isolation() -> None:
    app.dependency_overrides[require_active_user] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            first = _create_user(client, "prompt_first")
            second = _create_user(client, "prompt_second")
            first_user = SimpleNamespace(id=uuid.UUID(first["id"]), role=UserRole.user)
            second_user = SimpleNamespace(
                id=uuid.UUID(second["id"]), role=UserRole.user
            )

            app.dependency_overrides[require_active_user] = lambda: first_user
            created = client.post(
                "/api/article-prompt-templates",
                json={
                    "name": "专业测评",
                    "folder_name": "洗护模板",
                    "title_prompt": "围绕{关键词}写标题",
                    "content_prompt": "围绕{关键词}写正文并介绍{商品名称}",
                },
            )
            assert created.status_code == 201
            template = created.json()
            assert template["folder_name"] == "洗护模板"
            folders = client.get("/api/article-prompt-folders").json()
            assert folders[0]["template_count"] == 1

            updated = client.patch(
                f"/api/article-prompt-templates/{template['id']}",
                json={"name": "客观测评", "title_prompt": "新的标题提示词"},
            )
            assert updated.status_code == 200
            assert updated.json()["name"] == "客观测评"
            assert updated.json()["title_prompt"] == "新的标题提示词"

            app.dependency_overrides[require_active_user] = lambda: second_user
            assert client.get("/api/article-prompt-templates").json()["total"] == 0
            assert (
                client.patch(
                    f"/api/article-prompt-templates/{template['id']}",
                    json={"name": "越权修改"},
                ).status_code
                == 404
            )

            app.dependency_overrides[require_active_user] = lambda: first_user
            folder_id = folders[0]["id"]
            assert (
                client.delete(f"/api/article-prompt-folders/{folder_id}").status_code
                == 204
            )
            kept = client.get("/api/article-prompt-templates").json()["items"][0]
            assert kept["folder_id"] is None
            assert (
                client.delete(
                    f"/api/article-prompt-templates/{template['id']}"
                ).status_code
                == 204
            )
    finally:
        app.dependency_overrides.pop(require_active_user, None)
