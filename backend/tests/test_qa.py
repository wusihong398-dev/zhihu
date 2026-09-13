import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import require_active_user
from app.main import app
from app.models.user import UserRole
from app.services.zhihu_question_collector import ZhihuQuestionCandidate


ADMIN = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)


def test_question_collection_answer_crud_and_daily_queue(monkeypatch) -> None:
    current = {"user": ADMIN}

    async def fake_collect(account, keywords, target_count):
        assert keywords == ["防脱", "头发护理"]
        assert target_count == 20
        return [
            ZhihuQuestionCandidate(
                question_id="123456789",
                title="日常应该怎样科学护理头发？",
                url="https://www.zhihu.com/question/123456789",
                keyword="头发护理",
                excerpt="希望了解日常清洁和护理方法。",
                answer_count=12,
                follower_count=30,
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
                    "username": f"qa_operator_{uuid.uuid4().hex[:8]}",
                    "password": "test-password-123",
                },
            ).json()
            current["user"] = SimpleNamespace(
                id=uuid.UUID(operator["id"]), role=UserRole.user
            )
            account = client.post(
                "/api/accounts",
                json={
                    "display_name": f"问答账号-{uuid.uuid4().hex[:8]}",
                    "daily_answer_limit": 2,
                },
            ).json()
            account_id = account["id"]
            collected = client.post(
                f"/api/accounts/{account_id}/questions/collect",
                json={"keywords": ["防脱", "头发护理"], "target_count": 20},
            )
            assert collected.status_code == 200
            assert collected.json()["added_count"] == 1
            question_id = collected.json()["items"][0]["id"]
            duplicate = client.post(
                f"/api/accounts/{account_id}/questions/collect",
                json={"keywords": ["防脱", "头发护理"], "target_count": 20},
            )
            assert duplicate.json()["duplicate_count"] == 1
            assert client.get(f"/api/accounts/{account_id}/questions").json()["total"] == 1

            answer = client.post(
                f"/api/accounts/{account_id}/answers",
                json={
                    "question_id": question_id,
                    "content": "这是一段用于测试的专业回答正文。" * 20,
                },
            )
            assert answer.status_code == 201
            answer_id = answer.json()["id"]
            fake_published = client.post(
                f"/api/accounts/{account_id}/answers",
                json={
                    "question_id": question_id,
                    "content": "不能由人工直接标记为发布成功。",
                    "status": "published",
                },
            )
            assert fake_published.status_code == 400
            ready = client.post(
                f"/api/accounts/{account_id}/answers/bulk-status",
                json={"answer_ids": [answer_id], "status": "ready"},
            )
            assert ready.json()["affected_count"] == 1
            summary = client.get(
                f"/api/accounts/{account_id}/auto-answer/summary"
            ).json()
            assert summary["daily_limit"] == 2
            assert summary["ready_count"] == 1
            queued = client.post(f"/api/accounts/{account_id}/auto-answer/run")
            assert queued.status_code == 200
            assert queued.json()["queued_count"] == 1
            assert queued.json()["job"]["job_type"] == "publish"
    finally:
        app.dependency_overrides.pop(require_active_user, None)


def test_regular_user_cannot_read_another_users_question_library() -> None:
    current = {"user": ADMIN}
    app.dependency_overrides[require_active_user] = lambda: current["user"]
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/users",
                json={
                    "username": f"qa_first_{uuid.uuid4().hex[:8]}",
                    "password": "test-password-123",
                },
            ).json()
            second = client.post(
                "/api/users",
                json={
                    "username": f"qa_second_{uuid.uuid4().hex[:8]}",
                    "password": "test-password-456",
                },
            ).json()
            current["user"] = SimpleNamespace(
                id=uuid.UUID(first["id"]), role=UserRole.user
            )
            account = client.post(
                "/api/accounts", json={"display_name": "用户一问答账号"}
            ).json()
            current["user"] = SimpleNamespace(
                id=uuid.UUID(second["id"]), role=UserRole.user
            )
            assert client.get(f"/api/accounts/{account['id']}/questions").status_code == 404
    finally:
        app.dependency_overrides.pop(require_active_user, None)


def test_answer_prompt_template_crud_and_user_isolation() -> None:
    current = {"user": ADMIN}
    app.dependency_overrides[require_active_user] = lambda: current["user"]
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/users",
                json={
                    "username": f"prompt_first_{uuid.uuid4().hex[:8]}",
                    "password": "test-password-123",
                },
            ).json()
            second = client.post(
                "/api/users",
                json={
                    "username": f"prompt_second_{uuid.uuid4().hex[:8]}",
                    "password": "test-password-456",
                },
            ).json()
            first_id = uuid.UUID(first["id"])
            second_id = uuid.UUID(second["id"])
            current["user"] = SimpleNamespace(id=first_id, role=UserRole.user)

            assert (
                client.post(
                    "/api/answer-prompt-templates",
                    json={"name": "空提示词", "prompt": "   "},
                ).status_code
                == 422
            )

            created = client.post(
                "/api/answer-prompt-templates",
                json={"name": "专业回答", "prompt": "先解决问题，再自然介绍商品。"},
            )
            assert created.status_code == 201
            template_id = created.json()["id"]
            assert client.get("/api/answer-prompt-templates").json()["total"] == 1

            updated = client.patch(
                f"/api/answer-prompt-templates/{template_id}",
                json={"name": "专业回答新版", "prompt": "先给出真实建议，再自然介绍商品。"},
            )
            assert updated.status_code == 200
            assert updated.json()["name"] == "专业回答新版"
            assert updated.json()["prompt"] == "先给出真实建议，再自然介绍商品。"

            current["user"] = SimpleNamespace(id=second_id, role=UserRole.user)
            assert client.get("/api/answer-prompt-templates").json()["total"] == 0
            assert (
                client.patch(
                    f"/api/answer-prompt-templates/{template_id}",
                    json={"prompt": "不应允许跨用户修改"},
                ).status_code
                == 404
            )

            current["user"] = SimpleNamespace(id=first_id, role=UserRole.user)
            assert (
                client.delete(f"/api/answer-prompt-templates/{template_id}").status_code
                == 204
            )
            assert client.get("/api/answer-prompt-templates").json()["total"] == 0
    finally:
        app.dependency_overrides.pop(require_active_user, None)
