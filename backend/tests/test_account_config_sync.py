import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import require_active_user, require_admin
from app.main import app
from app.models.user import UserRole


ADMIN = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin)


def test_account_can_copy_reusable_configuration_without_duplicates() -> None:
    current = {"user": ADMIN}
    app.dependency_overrides[require_active_user] = lambda: current["user"]
    app.dependency_overrides[require_admin] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            suffix = uuid.uuid4().hex[:8]
            user = client.post(
                "/api/users",
                json={
                    "username": f"account_sync_{suffix}",
                    "password": "test-password-123",
                },
            ).json()
            app.dependency_overrides.pop(require_admin, None)
            current["user"] = SimpleNamespace(
                id=uuid.UUID(user["id"]), role=UserRole.user
            )
            source = client.post(
                "/api/accounts",
                json={
                    "display_name": f"同步来源-{suffix}",
                    "daily_article_limit": 7,
                    "daily_answer_limit": 12,
                    "recycle_keywords_after_use": False,
                    "auto_restore_keywords": True,
                    "keyword_restore_threshold": 35,
                    "timezone": "Asia/Shanghai",
                },
            ).json()
            source_id = source["id"]
            product = client.post(
                f"/api/accounts/{source_id}/products",
                json={
                    "name": f"同步商品-{suffix}",
                    "category": "测试分类",
                    "selling_points": "卖点一\n卖点二",
                    "enabled": True,
                },
            ).json()
            folder = client.post(
                f"/api/accounts/{source_id}/keyword-folders",
                json={"name": f"同步关键词-{suffix}"},
            ).json()
            created_schedule = client.post(
                "/api/schedules",
                json={
                    "account_id": source_id,
                    "name": f"同步生成计划-{suffix}",
                    "task_type": "article_generate",
                    "enabled": True,
                    "hour": 10,
                    "minute": 15,
                    "weekdays": [0, 1, 2, 3, 4],
                    "config": {
                        "product_id": product["id"],
                        "folder_id": folder["id"],
                        "provider": "deepseek",
                    },
                },
            )
            assert created_schedule.status_code == 201

            target = client.post(
                "/api/accounts",
                json={
                    "display_name": f"同步目标-{suffix}",
                    "sync_config_from_account_id": source_id,
                },
            )
            assert target.status_code == 201
            target_data = target.json()
            target_id = target_data["id"]
            assert target_data["daily_article_limit"] == 7
            assert target_data["daily_answer_limit"] == 12
            assert target_data["auto_restore_keywords"] is True
            assert target_data["keyword_restore_threshold"] == 35
            assert target_data["status"] == "pending_login"

            target_products = client.get(
                f"/api/accounts/{target_id}/products"
            ).json()["items"]
            target_folders = client.get(
                f"/api/accounts/{target_id}/keyword-folders"
            ).json()
            target_schedules = client.get(
                f"/api/schedules?account_id={target_id}"
            ).json()
            assert len(target_products) == 1
            assert len(target_folders) == 1
            assert len(target_schedules) == 1
            assert target_schedules[0]["config"]["product_id"] == target_products[0]["id"]
            assert target_schedules[0]["config"]["folder_id"] == target_folders[0]["id"]

            repeated = client.patch(
                f"/api/accounts/{target_id}",
                json={"sync_config_from_account_id": source_id},
            )
            assert repeated.status_code == 200
            assert client.get(f"/api/accounts/{target_id}/products").json()["total"] == 1
            assert len(client.get(f"/api/accounts/{target_id}/keyword-folders").json()) == 1
            assert len(client.get(f"/api/schedules?account_id={target_id}").json()) == 1

            current["user"] = ADMIN
            admin_target = client.post(
                "/api/accounts",
                json={
                    "display_name": f"管理员同步目标-{suffix}",
                    "sync_config_from_account_id": source_id,
                },
            )
            assert admin_target.status_code == 201
            assert admin_target.json()["owner_user_id"] is None
            assert admin_target.json()["daily_article_limit"] == 7
            assert client.get(
                f"/api/accounts/{admin_target.json()['id']}/products"
            ).json()["total"] == 1
    finally:
        app.dependency_overrides.pop(require_active_user, None)
        app.dependency_overrides.pop(require_admin, None)
