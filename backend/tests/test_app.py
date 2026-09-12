from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_health_version_and_auth_boundary() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "database": "ok",
            "redis": "ok",
        }

        version = client.get("/api/version")
        assert version.status_code == 200
        assert version.json()["version"] == "0.10.7"

        accounts = client.get("/api/accounts")
        assert accounts.status_code == 401


def test_article_list_requires_one_account_at_a_time() -> None:
    index = (PROJECT_ROOT / "frontend/dist/index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "frontend/dist/assets/app.js").read_text(
        encoding="utf-8"
    )
    assert "全部知乎账号" not in index
    assert "全部状态" not in index
    assert 'data-status="draft"' in index
    assert 'data-status="ready"' in index
    assert 'data-status="published"' in index
    assert 'data-status="failed"' in index
    assert 'params.set("account_id", accountId)' in script
    assert 'params.set("status", articleStatus)' in script
    assert "state.articles = { items: [], total: 0 }" in script
    assert ">发布到知乎</button>" not in script
    assert ">发布</button>" in script
    assert "失败原因：" in script
    assert "发布时间" in index
    assert "item.publish_attempted_at" in script
    assert 'class="button button-ghost edit-account"' in script
    assert 'class="button button-ghost danger-text delete-account"' in script
    assert "登录资料、商品、关键词和文章将一并删除" in script
    login_css = (PROJECT_ROOT / "frontend/dist/assets/login.css").read_text(
        encoding="utf-8"
    )
    assert 'id="zhihu-login-open"' in index
    assert "openZhihuLoginScreenshot" in script
    assert "width: min(1600px, calc(100vw - 32px)) !important" in login_css
    assert "object-fit: contain" in login_css
    assert 'id="article-sync"' in index
    assert "syncPublishedArticles" in script
    assert "/articles/sync" in script
