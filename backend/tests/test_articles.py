import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import require_active_user
from app.main import app
from app.models.user import UserRole


ADMIN = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)


def test_article_crud_bulk_and_account_scope() -> None:
    app.dependency_overrides[require_active_user] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            first = client.post("/api/accounts", json={"display_name": "文章账号一"})
            second = client.post("/api/accounts", json={"display_name": "文章账号二"})
            assert first.status_code == 201
            first_id = first.json()["id"]
            second_id = second.json()["id"]
            created = client.post(
                f"/api/accounts/{first_id}/articles",
                json={
                    "keyword_text": "测试关键词",
                    "product_name": "测试商品",
                    "title": "第一篇测试文章",
                    "content": "这是一篇用于验证文章接口的正文。",
                },
            )
            assert created.status_code == 201
            article_id = created.json()["id"]
            assert created.json()["content_length"] > 0

            listed = client.get(f"/api/articles?account_id={first_id}")
            assert listed.status_code == 200
            assert listed.json()["total"] == 1
            assert listed.json()["items"][0]["account_name"] == "文章账号一"
            assert client.get(f"/api/articles?account_id={second_id}").json()["total"] == 0
            assert (
                client.get(f"/api/accounts/{second_id}/articles/{article_id}").status_code
                == 404
            )

            updated = client.patch(
                f"/api/accounts/{first_id}/articles/{article_id}",
                json={"title": "修改后的标题", "status": "ready"},
            )
            assert updated.status_code == 200
            assert updated.json()["status"] == "ready"

            bulk = client.post(
                "/api/articles/bulk-status",
                json={"article_ids": [article_id], "status": "draft"},
            )
            assert bulk.status_code == 200
            assert bulk.json()["affected_count"] == 1
            deleted = client.post(
                "/api/articles/bulk-delete", json={"article_ids": [article_id]}
            )
            assert deleted.status_code == 200
            assert deleted.json()["affected_count"] == 1
    finally:
        app.dependency_overrides.pop(require_active_user, None)


def test_regular_users_cannot_see_each_others_articles() -> None:
    app.dependency_overrides[require_active_user] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            first_user = client.post(
                "/api/users",
                json={"username": f"article_user_{uuid.uuid4().hex[:8]}", "password": "test-password-123"},
            ).json()
            second_user = client.post(
                "/api/users",
                json={"username": f"article_user_{uuid.uuid4().hex[:8]}", "password": "test-password-456"},
            ).json()

            current = {"user": SimpleNamespace(id=uuid.UUID(first_user["id"]), role=UserRole.user)}
            app.dependency_overrides[require_active_user] = lambda: current["user"]
            first_account = client.post(
                "/api/accounts", json={"display_name": "用户一文章账号"}
            ).json()
            created = client.post(
                f"/api/accounts/{first_account['id']}/articles",
                json={"title": "用户一的私有文章", "content": "私有正文"},
            )
            assert created.status_code == 201

            current["user"] = SimpleNamespace(
                id=uuid.UUID(second_user["id"]), role=UserRole.user
            )
            assert client.get("/api/articles").json()["total"] == 0
            assert (
                client.get(
                    f"/api/accounts/{first_account['id']}/articles/{created.json()['id']}"
                ).status_code
                == 404
            )
    finally:
        app.dependency_overrides.pop(require_active_user, None)
