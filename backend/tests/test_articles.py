import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import require_active_user
from app.main import app
from app.models.user import UserRole
from app.services.zhihu_publisher import ZhihuPublishError
from app.services.zhihu_article_sync import ZhihuPublishedArticle


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


def test_ready_article_can_be_published_with_its_account(monkeypatch) -> None:
    async def fake_publish(account, article) -> str:
        assert account.id == article.account_id
        return "https://zhuanlan.zhihu.com/p/123456789"

    monkeypatch.setattr(
        "app.api.articles.publish_article_to_zhihu", fake_publish
    )
    app.dependency_overrides[require_active_user] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            account = client.post(
                "/api/accounts", json={"display_name": "真实发布测试账号"}
            ).json()
            article = client.post(
                f"/api/accounts/{account['id']}/articles",
                json={
                    "title": "一篇等待发布的文章",
                    "content": "这是一篇将通过独立知乎登录会话发布的正文。",
                    "status": "ready",
                },
            ).json()
            published = client.post(
                f"/api/accounts/{account['id']}/articles/{article['id']}/publish"
            )
            assert published.status_code == 200
            assert published.json()["status"] == "published"
            assert published.json()["published_url"].endswith("/123456789")
            assert published.json()["published_at"] is not None
            assert published.json()["publish_attempted_at"] is not None
    finally:
        app.dependency_overrides.pop(require_active_user, None)


def test_failed_publish_records_attempt_time_and_reason(monkeypatch) -> None:
    async def fake_publish(account, article) -> str:
        raise ZhihuPublishError("知乎测试拒绝发布")

    monkeypatch.setattr("app.api.articles.publish_article_to_zhihu", fake_publish)
    app.dependency_overrides[require_active_user] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            account = client.post(
                "/api/accounts", json={"display_name": "失败时间测试账号"}
            ).json()
            article = client.post(
                f"/api/accounts/{account['id']}/articles",
                json={
                    "title": "一篇会发布失败的文章",
                    "content": "这篇文章用于验证失败后仍然记录发布时间。",
                    "status": "ready",
                },
            ).json()
            failed = client.post(
                f"/api/accounts/{account['id']}/articles/{article['id']}/publish"
            )
            assert failed.status_code == 502

            listed = client.get(
                f"/api/articles?account_id={account['id']}&status=failed"
            ).json()
            saved = next(item for item in listed["items"] if item["id"] == article["id"])
            assert saved["error_message"] == "知乎测试拒绝发布"
            assert saved["publish_attempted_at"] is not None
            assert saved["published_at"] is None
    finally:
        app.dependency_overrides.pop(require_active_user, None)


def test_sync_recovers_false_failed_article_without_touching_unmatched(monkeypatch) -> None:
    async def fake_sync(account):
        return [
            ZhihuPublishedArticle(
                article_id="2082171778986664628",
                title="实际已经发布的文章",
                url="https://zhuanlan.zhihu.com/p/2082171778986664628",
                published_at=datetime(2026, 9, 12, 20, 55, tzinfo=UTC),
            )
        ]

    monkeypatch.setattr(
        "app.api.articles.fetch_zhihu_published_articles", fake_sync
    )
    app.dependency_overrides[require_active_user] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            account = client.post(
                "/api/accounts", json={"display_name": "同步测试账号"}
            ).json()
            recovered = client.post(
                f"/api/accounts/{account['id']}/articles",
                json={
                    "title": "实际已经发布的文章",
                    "content": "正文",
                    "status": "failed",
                },
            ).json()
            client.post(
                f"/api/accounts/{account['id']}/articles",
                json={
                    "title": "确实没有发布的文章",
                    "content": "正文",
                    "status": "failed",
                },
            )

            synced = client.post(
                f"/api/accounts/{account['id']}/articles/sync"
            )
            assert synced.status_code == 200
            assert synced.json() == {
                "scanned_count": 1,
                "matched_count": 1,
                "already_synced_count": 0,
                "unmatched_failed_count": 1,
            }
            published = client.get(
                f"/api/articles?account_id={account['id']}&status=published"
            ).json()
            saved = next(
                item for item in published["items"] if item["id"] == recovered["id"]
            )
            assert saved["published_url"].endswith("/2082171778986664628")
            assert saved["error_message"] is None
            failed = client.get(
                f"/api/articles?account_id={account['id']}&status=failed"
            ).json()
            assert failed["total"] == 1
    finally:
        app.dependency_overrides.pop(require_active_user, None)
