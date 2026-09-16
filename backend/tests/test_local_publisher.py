import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import require_active_user
from app.main import app
from app.models.user import UserRole
from app.services.zhihu_question_collector import ZhihuQuestionCandidate


ADMIN = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)


def test_local_publisher_device_claim_and_success_result(monkeypatch) -> None:
    current = {"user": ADMIN}

    async def fake_collect(account, keywords, target_count):
        return [
            ZhihuQuestionCandidate(
                question_id="987654321",
                title="本地客户端测试问题",
                url="https://www.zhihu.com/question/987654321",
                keyword="测试",
                excerpt="测试问题补充",
                answer_count=0,
                follower_count=1,
            )
        ]

    monkeypatch.setattr("app.api.qa.collect_zhihu_questions", fake_collect)
    monkeypatch.setattr("app.api.qa.start_answer_job", lambda job_id: None)
    app.dependency_overrides[require_active_user] = lambda: current["user"]
    try:
        with TestClient(app) as client:
            operator = client.post(
                "/api/users",
                json={
                    "username": f"local_publisher_{uuid.uuid4().hex[:8]}",
                    "password": "test-password-123",
                },
            ).json()
            current["user"] = SimpleNamespace(
                id=uuid.UUID(operator["id"]), role=UserRole.user
            )
            account = client.post(
                "/api/accounts",
                json={
                    "display_name": "本地发布账号",
                    "daily_answer_limit": 5,
                    "answer_publish_mode": "local",
                },
            ).json()
            assert account["answer_publish_mode"] == "local"
            account_id = account["id"]
            server_account = client.post(
                "/api/accounts",
                json={
                    "display_name": "尚未切换发布方式的账号",
                    "daily_answer_limit": 5,
                    "answer_publish_mode": "server",
                },
            ).json()
            collected = client.post(
                f"/api/accounts/{account_id}/questions/collect",
                json={"keywords": ["测试"], "target_count": 1},
            ).json()
            answer = client.post(
                f"/api/accounts/{account_id}/answers",
                json={
                    "question_id": collected["items"][0]["id"],
                    "content": "这是一段本地发布客户端测试回答。" * 20,
                },
            ).json()
            client.post(
                f"/api/accounts/{account_id}/answers/bulk-status",
                json={"answer_ids": [answer["id"]], "status": "ready"},
            )
            job = client.post(
                "/api/answer-jobs/publish/start",
                json={"answer_ids": [answer["id"]]},
            )
            assert job.status_code == 202
            assert job.json()["status"] == "pending"
            assert "Windows" in job.json()["current_item"]

            created_device = client.post(
                "/api/local-publisher/devices", json={"name": "测试电脑"}
            )
            assert created_device.status_code == 201
            token = created_device.json()["token"]
            device_headers = {"X-TOTOD-Device-Token": token}
            accounts = client.get(
                "/api/local-publisher/client/accounts", headers=device_headers
            ).json()
            account_modes = {
                item["id"]: item["answer_publish_mode"] for item in accounts
            }
            assert account_modes == {
                account_id: "local",
                server_account["id"]: "server",
            }

            current["user"] = ADMIN
            other_user = client.post(
                "/api/users",
                json={
                    "username": f"other_publisher_{uuid.uuid4().hex[:8]}",
                    "password": "test-password-456",
                },
            ).json()
            current["user"] = SimpleNamespace(
                id=uuid.UUID(other_user["id"]), role=UserRole.user
            )
            other_device = client.post(
                "/api/local-publisher/devices", json={"name": "其他用户电脑"}
            ).json()
            other_claim = client.post(
                "/api/local-publisher/client/tasks/claim",
                headers={"X-TOTOD-Device-Token": other_device["token"]},
            )
            assert other_claim.status_code == 200
            assert other_claim.json() is None
            current["user"] = SimpleNamespace(
                id=uuid.UUID(operator["id"]), role=UserRole.user
            )

            claimed = client.post(
                f"/api/local-publisher/client/tasks/claim?account_id={account_id}",
                headers=device_headers,
            )
            assert claimed.status_code == 200
            task = claimed.json()
            assert task["answer_id"] == answer["id"]
            assert task["content"].startswith("这是一段")

            result = client.post(
                f"/api/local-publisher/client/tasks/{task['id']}/result",
                headers=device_headers,
                json={
                    "success": True,
                    "published_url": "https://www.zhihu.com/question/987654321/answer/123456789",
                },
            )
            assert result.status_code == 204
            finished = client.get(f"/api/answer-jobs/{job.json()['id']}").json()
            assert finished["status"] == "completed"
            assert finished["success_count"] == 1
            saved_answer = client.get(
                f"/api/accounts/{account_id}/answers/{answer['id']}"
            ).json()
            assert saved_answer["status"] == "published"
            assert saved_answer["published_at"] is not None
            assert client.get(
                f"/api/accounts/{account_id}/auto-answer/summary"
            ).json()["remaining_today"] == 4

            empty_article = client.post(
                f"/api/accounts/{account_id}/articles",
                json={
                    "title": "生成失败：空正文不能发布",
                    "content": "",
                    "status": "failed",
                },
            ).json()
            rejected_empty_article = client.post(
                "/api/article-jobs/publish",
                json={"article_ids": [empty_article["id"]]},
            )
            assert rejected_empty_article.status_code == 400
            assert "正文为空" in rejected_empty_article.json()["detail"]
            assert "不能直接发布" in rejected_empty_article.json()["detail"]
            listed_empty_article = client.get(
                f"/api/accounts/{account_id}/articles/{empty_article['id']}"
            ).json()
            assert "重新生成或点击编辑" in listed_empty_article["error_message"]

            article = client.post(
                f"/api/accounts/{account_id}/articles",
                json={
                    "title": "本地客户端测试文章",
                    "content": "这是一篇通过本机内置 Edge 自动发布的知乎文章。" * 20,
                    "status": "ready",
                },
            ).json()
            article_job = client.post(
                "/api/article-jobs/publish",
                json={"article_ids": [article["id"]]},
            )
            assert article_job.status_code == 202
            assert article_job.json()["status"] == "pending"
            assert "Windows" in article_job.json()["current_item"]

            legacy_claim = client.post(
                f"/api/local-publisher/client/tasks/claim?account_id={account_id}",
                headers=device_headers,
            )
            assert legacy_claim.status_code == 200
            assert legacy_claim.json() is None

            article_claim = client.post(
                f"/api/local-publisher/client/tasks/claim?account_id={account_id}"
                "&task_types=answer,article",
                headers=device_headers,
            )
            assert article_claim.status_code == 200
            article_task = article_claim.json()
            assert article_task["task_type"] == "article"
            assert article_task["article_id"] == article["id"]
            assert article_task["article_title"] == "本地客户端测试文章"

            article_result = client.post(
                f"/api/local-publisher/client/tasks/{article_task['id']}/result",
                headers=device_headers,
                json={
                    "success": True,
                    "published_url": "https://zhuanlan.zhihu.com/p/123456789",
                },
            )
            assert article_result.status_code == 204
            finished_article_job = client.get(
                f"/api/article-jobs/{article_job.json()['id']}"
            ).json()
            assert finished_article_job["status"] == "completed"
            assert finished_article_job["success_count"] == 1
            saved_article = client.get(
                f"/api/accounts/{account_id}/articles/{article['id']}"
            ).json()
            assert saved_article["status"] == "published"
            assert saved_article["published_url"].endswith("/123456789")
            assert saved_article["published_at"] is not None
    finally:
        app.dependency_overrides.pop(require_active_user, None)
