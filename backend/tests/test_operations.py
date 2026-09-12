import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import require_active_user, require_admin
from app.main import app
from app.models.schedule import ScheduleRunStatus, ScheduleTaskType
from app.models.user import UserRole
from app.services.schedule_runner import calculate_next_run


ADMIN = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)


def test_schedule_crud_validation_and_user_isolation(monkeypatch) -> None:
    current = {"user": ADMIN}

    async def fake_run(schedule_id: uuid.UUID, manual: bool = False) -> None:
        return None

    monkeypatch.setattr("app.api.operations.run_schedule", fake_run)
    app.dependency_overrides[require_active_user] = lambda: current["user"]
    app.dependency_overrides[require_admin] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/users",
                json={
                    "username": f"schedule_first_{uuid.uuid4().hex[:8]}",
                    "password": "test-password-123",
                },
            ).json()
            second = client.post(
                "/api/users",
                json={
                    "username": f"schedule_second_{uuid.uuid4().hex[:8]}",
                    "password": "test-password-456",
                },
            ).json()
            app.dependency_overrides.pop(require_admin, None)

            current["user"] = SimpleNamespace(
                id=uuid.UUID(first["id"]), role=UserRole.user
            )
            account = client.post(
                "/api/accounts",
                json={"display_name": "定时计划账号", "timezone": "Asia/Shanghai"},
            ).json()

            missing_config = client.post(
                "/api/schedules",
                json={
                    "account_id": account["id"],
                    "name": "缺少主词",
                    "task_type": "keyword_collect",
                    "config": {},
                },
            )
            assert missing_config.status_code == 422
            assert "seed_keyword" in missing_config.json()["detail"]

            created = client.post(
                "/api/schedules",
                json={
                    "account_id": account["id"],
                    "name": "每日采集防脱关键词",
                    "task_type": "keyword_collect",
                    "hour": 8,
                    "minute": 30,
                    "weekdays": [0, 2, 4],
                    "config": {
                        "seed_keyword": "防脱",
                        "source": "both",
                        "target_count": 50,
                    },
                },
            )
            assert created.status_code == 201
            schedule = created.json()
            assert schedule["user_id"] == first["id"]
            assert schedule["account_id"] == account["id"]
            assert schedule["weekdays"] == [0, 2, 4]
            assert schedule["next_run_at"] is not None

            schedule_id = schedule["id"]
            updated = client.patch(
                f"/api/schedules/{schedule_id}",
                json={"name": "每周关键词采集", "enabled": False},
            )
            assert updated.status_code == 200
            assert updated.json()["name"] == "每周关键词采集"
            assert updated.json()["enabled"] is False
            assert updated.json()["next_run_at"] is None

            current["user"] = SimpleNamespace(
                id=uuid.UUID(second["id"]), role=UserRole.user
            )
            assert client.get("/api/schedules").json() == []
            assert client.patch(
                f"/api/schedules/{schedule_id}", json={"enabled": True}
            ).status_code == 404

            current["user"] = SimpleNamespace(
                id=uuid.UUID(first["id"]), role=UserRole.user
            )
            run = client.post(f"/api/schedules/{schedule_id}/run")
            assert run.status_code == 202
            assert run.json() == {"status": "started"}
            assert client.delete(f"/api/schedules/{schedule_id}").status_code == 204
    finally:
        app.dependency_overrides.pop(require_active_user, None)
        app.dependency_overrides.pop(require_admin, None)


def test_system_settings_are_admin_only_and_validated() -> None:
    app.dependency_overrides[require_admin] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            current = client.get("/api/system-settings")
            assert current.status_code == 200
            invalid = client.put(
                "/api/system-settings",
                json={
                    "log_retention_days": 90,
                    "publish_interval_min": 20,
                    "publish_interval_max": 10,
                    "browser_timeout_seconds": 30,
                    "default_timezone": "Asia/Shanghai",
                },
            )
            assert invalid.status_code == 422
            saved = client.put(
                "/api/system-settings",
                json={
                    "log_retention_days": 120,
                    "publish_interval_min": 3,
                    "publish_interval_max": 9,
                    "browser_timeout_seconds": 45,
                    "default_timezone": "Asia/Shanghai",
                },
            )
            assert saved.status_code == 200
            assert saved.json()["log_retention_days"] == 120
            assert saved.json()["publish_interval_max"] == 9

            client.put(
                "/api/system-settings",
                json={
                    "log_retention_days": 90,
                    "publish_interval_min": 5,
                    "publish_interval_max": 12,
                    "browser_timeout_seconds": 30,
                    "default_timezone": "Asia/Shanghai",
                },
            )
    finally:
        app.dependency_overrides.pop(require_admin, None)

    with TestClient(app) as client:
        assert client.get("/api/system-settings").status_code == 401


def test_next_run_uses_account_timezone_and_selected_weekdays() -> None:
    schedule = SimpleNamespace(
        hour=9,
        minute=30,
        weekdays="0,2,4",
        _account_timezone="Asia/Shanghai",
        task_type=ScheduleTaskType.keyword_collect,
        last_status=ScheduleRunStatus.never,
    )
    # 2026-09-14 is Monday. 00:00 UTC is 08:00 in Shanghai, so today's
    # 09:30 local run is still upcoming and corresponds to 01:30 UTC.
    result = calculate_next_run(
        schedule, datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    )
    assert result == datetime(2026, 9, 14, 1, 30, tzinfo=UTC)
