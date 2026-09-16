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
        assert version.json()["version"] == "0.19.0"

        accounts = client.get("/api/accounts")
        assert accounts.status_code == 401


def test_article_list_requires_one_account_at_a_time() -> None:
    index = (PROJECT_ROOT / "frontend/dist/index.html").read_text(encoding="utf-8")
    article_page = index.split('<div id="articles-page"', 1)[1].split(
        '<div id="article-publish-page"', 1
    )[0]
    script = (PROJECT_ROOT / "frontend/dist/assets/app.js").read_text(encoding="utf-8")
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
    assert "登录官网" in script
    assert 'id="answer-publish-mode"' in index
    assert "文章和回答发布方式" in index
    assert 'data-page="local-publisher"' in index
    assert 'id="local-device-create"' in index
    assert "自动发布知乎文章和回答" in index
    assert "openZhihuWebsite" in script
    assert 'id="account-sync-config"' in index
    assert "sync_config_from_account_id" in script
    assert "登录状态与内容记录保持独立" in index
    assert "/website-session" in script
    assert "/browser-action" in script
    assert 'id="zhihu-browser-controls"' in index
    assert 'id="zhihu-browser-text"' in index
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
    assert "进度连接暂时中断，正在自动重试" in script
    assert "articleJobPollFailures" in script
    assert 'id="article-generation-view-failures"' in index
    assert 'articleStatus: "failed"' in script


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
    assert 'id="answer-prompt-template-select"' in index
    assert 'id="answer-prompt-template-create"' in index
    assert 'id="answer-prompt-template-update"' in index
    assert "保存当前模板" in index
    assert "/answer-prompt-templates" in script
    assert "async function activate() { await loadBase(); }" in script
    assert "（已登录）" in script
    assert "服务器前后端版本不一致，请重新执行完整部署" in script
    assert "answer-diagnostic" in script
    assert "/failure-screenshot" in script
    assert "暂无诊断截图" in script
    assert "popup.close()" not in script
    assert "采集时间" in script
    assert "item.discovered_at" in script
    assert "回答时间 / 尝试时间" in script
    assert 'id="answer-select-all-table"' in index
    assert "selectVisibleAnswers" in script
    assert 'item.status === "published" ? item.published_at' in script
    assert "finishedJob?.failed_count" in script
    assert 'accounts = await api("/accounts")' in script
    assert 'const providers = await api("/ai/providers")' in script
    assert (PROJECT_ROOT / "frontend/dist/assets/answer-prompts.css").is_file()
    bootstrap = (PROJECT_ROOT / "scripts/bootstrap_server.sh").read_text(encoding="utf-8")
    assert "answer-prompts.css" in bootstrap
    assert 'data-page="products"' in index
    assert 'data-page="prompt-templates"' in index
    assert 'id="prompt-templates-page"' in index
    assert 'id="prompt-library-title-prompt"' in index
    assert 'id="prompt-library-content-prompt"' in index
    assert 'id="prompt-library-answer-prompt"' in index
    prompt_library = (
        PROJECT_ROOT / "frontend/dist/assets/prompt-library.js"
    ).read_text(encoding="utf-8")
    migration = (PROJECT_ROOT / "backend/app/db/session.py").read_text(
        encoding="utf-8"
    )
    assert "Promise.allSettled" in prompt_library
    assert "/article-prompt-templates" in prompt_library
    assert "/answer-prompt-templates" in prompt_library
    assert "/article-prompt-folders" in prompt_library
    assert "/answer-prompt-folders" in prompt_library
    assert "文章模板库与问答模板库分开保存" in index
    assert "INSERT INTO answer_prompt_folders" in migration
    assert "REFERENCES answer_prompt_folders(id)" in migration
    assert "prompt-library.css" in bootstrap
    assert "prompt-library.js" in bootstrap


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
    script = (PROJECT_ROOT / "frontend/dist/assets/app.js").read_text(encoding="utf-8")
    assert 'id="prompt-template-update"' in index
    assert "保存当前模板" in index
    assert "另存为新模板" in index
    assert "markPromptTemplateChanged" in script
    assert "已保存当前模板" in script


def test_navigation_is_grouped_and_keyword_recycle_is_functional() -> None:
    index = (PROJECT_ROOT / "frontend/dist/index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "frontend/dist/assets/app.js").read_text(encoding="utf-8")
    assert 'data-nav-section="account"' in index
    assert 'data-nav-section="keyword"' in index
    assert 'data-nav-section="article"' in index
    assert 'data-nav-section="qa"' in index
    assert 'id="keyword-collect-page"' in index
    assert 'id="keywords-page"' in index
    assert 'id="keyword-recycle-page"' in index
    assert 'data-article-status="published"' in index
    assert 'data-answer-status="failed"' in index
    assert "/keywords/bulk-recycle" in script
    assert "/keywords/bulk-restore" in script
    assert "/keywords/auto-restore" in script


def test_local_media_page_and_generation_variable_are_exposed() -> None:
    index = (PROJECT_ROOT / "frontend/dist/index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "frontend/dist/assets/app.js").read_text(encoding="utf-8")
    assert 'data-page="local-media"' in index
    assert 'id="local-media-page"' in index
    assert 'id="media-upload-form"' in index
    assert 'id="media-upload-progress"' in index
    assert 'id="media-upload-current"' in index
    assert 'id="media-upload-remaining"' in index
    assert 'id="article-local-image-folder"' in index
    assert "{本地图片}" in index
    assert "/local-media/upload" in script
    assert "uploadSingleMediaFile" in script
    assert "request.upload.addEventListener" in script
    assert "files.length - index - 1" in script
    assert "/extract" in script
    assert "local_image_folder_id" in script
