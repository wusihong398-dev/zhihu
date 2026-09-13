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
        assert version.json()["version"] == "0.12.1"

        accounts = client.get("/api/accounts")
        assert accounts.status_code == 401


def test_article_list_requires_one_account_at_a_time() -> None:
    index = (PROJECT_ROOT / "frontend/dist/index.html").read_text(encoding="utf-8")
    article_page = index.split('<div id="articles-page"', 1)[1].split(
        '<div id="article-publish-page"', 1
    )[0]
    script = (PROJECT_ROOT / "frontend/dist/assets/app.js").read_text(
        encoding="utf-8"
    )
    assert "全部知乎账号" not in article_page
    assert "全部状态" not in article_page
    assert 'data-status="draft"' in article_page
    assert 'data-status="ready"' in article_page
    assert 'data-status="published"' in article_page
    assert 'data-status="failed"' in article_page
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


def test_qa_pages_are_functional_modules() -> None:
    index = (PROJECT_ROOT / "frontend/dist/index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "frontend/dist/assets/qa.js").read_text(encoding="utf-8")
    assert 'id="question-collect-form"' in index
    assert 'id="answer-list"' in index
    assert 'id="auto-answer-run"' in index
    assert "功能待填充</span><h2>知乎问题库" not in index
    assert "/questions/collect" in script
    assert "/answer-jobs/generate" in script
    assert "/answer-jobs/publish/start" in script


def test_operations_pages_are_functional_modules() -> None:
    index = (PROJECT_ROOT / "frontend/dist/index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "frontend/dist/assets/operations.js").read_text(
        encoding="utf-8"
    )
    assert 'id="schedule-form"' in index
    assert 'id="logs-list"' in index
    assert 'id="system-settings-form"' in index
    assert "/schedules" in script
    assert "/operation-logs" in script
    assert "/system-settings" in script


def test_prompt_toolbar_has_distinct_update_and_save_as_actions() -> None:
    index = (PROJECT_ROOT / "frontend/dist/index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "frontend/dist/assets/app.js").read_text(
        encoding="utf-8"
    )
    assert 'id="prompt-template-update"' in index
    assert "保存当前模板" in index
    assert "另存为新模板" in index
    assert "markPromptTemplateChanged" in script
    assert "已保存当前模板" in script
