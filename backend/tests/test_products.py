from fastapi.testclient import TestClient

from app.core.security import require_admin
from app.main import app


def test_product_crud_is_account_scoped() -> None:
    app.dependency_overrides[require_admin] = lambda: object()
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/accounts",
                json={"display_name": "商品测试账号一"},
            )
            second = client.post(
                "/api/accounts",
                json={"display_name": "商品测试账号二"},
            )
            assert first.status_code == 201
            assert second.status_code == 201
            first_id = first.json()["id"]
            second_id = second.json()["id"]

            created = client.post(
                f"/api/accounts/{first_id}/products",
                json={
                    "name": "测试防脱洗发水",
                    "category": "洗护",
                    "description": "用于接口测试的商品",
                    "selling_points": "温和清洁\n方便使用",
                    "target_audience": "关注头皮护理的人群",
                    "promotion_url": "https://example.com/product",
                    "content_requirements": "客观介绍",
                    "forbidden_terms": "保证治愈",
                    "enabled": True,
                },
            )
            assert created.status_code == 201
            product_id = created.json()["id"]

            own_list = client.get(f"/api/accounts/{first_id}/products")
            other_list = client.get(f"/api/accounts/{second_id}/products")
            assert own_list.status_code == 200
            assert own_list.json()["total"] == 1
            assert other_list.status_code == 200
            assert other_list.json()["total"] == 0

            cross_account = client.get(
                f"/api/accounts/{second_id}/products/{product_id}"
            )
            assert cross_account.status_code == 404

            updated = client.patch(
                f"/api/accounts/{first_id}/products/{product_id}",
                json={"name": "更新后的商品", "enabled": False},
            )
            assert updated.status_code == 200
            assert updated.json()["name"] == "更新后的商品"
            assert updated.json()["enabled"] is False

            deleted = client.delete(f"/api/accounts/{first_id}/products/{product_id}")
            assert deleted.status_code == 204
            assert client.get(f"/api/accounts/{first_id}/products").json()["total"] == 0
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_product_rejects_non_http_promotion_url() -> None:
    app.dependency_overrides[require_admin] = lambda: object()
    try:
        with TestClient(app) as client:
            account = client.post(
                "/api/accounts",
                json={"display_name": "链接校验测试账号"},
            )
            response = client.post(
                f"/api/accounts/{account.json()['id']}/products",
                json={"name": "测试商品", "promotion_url": "javascript:alert(1)"},
            )
            assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(require_admin, None)
