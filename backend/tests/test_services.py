import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.services.ai_providers import AIProviderError, _extract_article_json
from app.services.keyword_collector import (
    is_verification_page,
    normalize_keyword,
    parse_baidu_related,
    parse_baidu_suggestions,
    parse_google_related,
    parse_google_suggestions,
)
from app.services.keyword_recycle import mark_keyword_used
from app.services.secret_box import decrypt_secret, encrypt_secret, mask_secret
from app.services.zhihu_article_sync import _published_article_from_payload
from app.services.zhihu_answer_publisher import _answer_id_from_payload
from app.services.zhihu_login import has_zhihu_auth_cookie
from app.services.zhihu_publisher import (
    ZhihuPublishError,
    ZhihuPublicVerificationUnavailable,
    _article_id_from_url,
    _check_public_page_snapshot,
    _is_publish_response,
    _publish_article_id,
    _publish_error_message,
    _public_article_url,
    _read_publish_response,
    _verify_public_article,
)
from app.services.zhihu_question_collector import questions_from_search_payload
from app.api.articles import _job_read
from app.models.article_job import ArticleJobStatus, ArticleJobType
from app.models.keyword import AccountKeyword, KeywordSource


def test_secret_round_trip() -> None:
    source = "sk-test-1234567890"
    encrypted = encrypt_secret(source)
    assert encrypted != source
    assert decrypt_secret(encrypted) == source
    assert mask_secret(source) == "sk-t••••••••7890"


def test_article_json_parser_accepts_plain_and_fenced_json() -> None:
    assert _extract_article_json('{"title":"测试标题","content":"测试正文"}') == (
        "测试标题",
        "测试正文",
    )
    assert _extract_article_json(
        '```json\n{"title":"另一个标题","content":"另一篇正文"}\n```'
    ) == ("另一个标题", "另一篇正文")


def test_article_json_parser_rejects_incomplete_result() -> None:
    with pytest.raises(AIProviderError):
        _extract_article_json('{"title":"只有标题"}')


def test_keyword_normalization_and_baidu_parser() -> None:
    html = """
    <div id="rs"><a> 智能 家居 </a><a>智能门锁推荐</a><a>智能 家居</a></div>
    """
    assert normalize_keyword("  智能   家居 ") == "智能 家居"
    assert parse_baidu_related(html) == ["智能 家居", "智能门锁推荐"]


def test_used_keyword_can_be_recycled_for_later_restore() -> None:
    keyword = AccountKeyword(
        account_id=uuid.uuid4(),
        keyword="头发护理",
        normalized_keyword="头发护理",
        source=KeywordSource.baidu,
        seed_keyword="脱发",
        depth=1,
        used_count=0,
        is_recycled=False,
    )
    mark_keyword_used(keyword, recycle=True)
    assert keyword.used_count == 1
    assert keyword.is_recycled is True
    assert keyword.last_used_at is not None
    assert keyword.recycled_at is not None


def test_google_related_parser() -> None:
    html = """
    <div id="botstuff">
      <a href="/search?q=AI%E5%86%99%E4%BD%9C">AI写作</a>
      <a href="/search?q=AI%E6%96%87%E7%AB%A0%E7%94%9F%E6%88%90">AI文章生成</a>
    </div>
    """
    assert parse_google_related(html) == ["AI写作", "AI文章生成"]


def test_google_related_uses_query_when_link_has_no_text() -> None:
    html = '<div id="botstuff"><a href="/search?q=脱发怎么办"></a></div>'
    assert parse_google_related(html) == ["脱发怎么办"]


def test_search_suggestion_parsers() -> None:
    baidu = 'window.baidu.sug({"q":"脱发","p":false,"s":["脱发原因","脱发怎么办"]});'
    google = '["脱发", ["脱发原因", "脱发怎么办"]]'
    assert parse_baidu_suggestions(baidu) == ["脱发原因", "脱发怎么办"]
    assert parse_google_suggestions(google) == ["脱发原因", "脱发怎么办"]


def test_verification_page_detection() -> None:
    request = httpx.Request("GET", "https://www.google.com/sorry/index")
    response = httpx.Response(
        200, text="Our systems have detected unusual traffic", request=request
    )
    assert is_verification_page("google", response)


def test_zhihu_login_requires_real_auth_cookie() -> None:
    assert has_zhihu_auth_cookie([{"name": "z_c0", "value": "encrypted-login-cookie"}])
    assert not has_zhihu_auth_cookie([{"name": "d_c0", "value": "device-cookie"}])
    assert not has_zhihu_auth_cookie([{"name": "z_c0", "value": ""}])


def test_website_action_is_scoped_to_one_account_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from app.models.account import AccountStatus
    from app.services import zhihu_login

    events: list[tuple[str, float, float]] = []

    class FakeMouse:
        async def click(self, x: float, y: float) -> None:
            events.append(("click", x, y))

    class FakeKeyboard:
        async def insert_text(self, _: str) -> None:
            return None

        async def press(self, _: str) -> None:
            return None

    class FakeContext:
        async def cookies(self):
            return [{"name": "z_c0", "value": "account-one-cookie"}]

    class FakePage:
        url = "https://www.zhihu.com/"
        mouse = FakeMouse()
        keyboard = FakeKeyboard()

        async def wait_for_timeout(self, _: int) -> None:
            return None

    async def fake_timeout() -> int:
        return 30_000

    async def fake_capture(session) -> None:
        session.page_url = session.page.url
        session.screenshot_version += 1

    first_account_id = uuid.uuid4()
    second_account_id = uuid.uuid4()
    first_session_id = uuid.uuid4()
    second_session_id = uuid.uuid4()
    first_session = zhihu_login.ZhihuLoginSession(
        id=first_session_id,
        account_id=first_account_id,
        mode="website",
        status=AccountStatus.online,
        message="",
        screenshot_path=tmp_path / "first.png",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + zhihu_login.WEBSITE_SESSION_TTL,
        context=FakeContext(),
        page=FakePage(),
    )
    second_session = zhihu_login.ZhihuLoginSession(
        id=second_session_id,
        account_id=second_account_id,
        mode="website",
        status=AccountStatus.online,
        message="",
        screenshot_path=tmp_path / "second.png",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + zhihu_login.WEBSITE_SESSION_TTL,
        context=FakeContext(),
        page=FakePage(),
    )
    monkeypatch.setattr(zhihu_login, "browser_timeout_ms", fake_timeout)
    monkeypatch.setattr(zhihu_login, "_capture", fake_capture)
    zhihu_login._sessions[first_session_id] = first_session
    zhihu_login._sessions[second_session_id] = second_session

    async def verify() -> None:
        result = await zhihu_login.perform_website_action(
            first_account_id,
            first_session_id,
            action="click",
            x=320,
            y=240,
        )
        assert result.account_id == first_account_id
        assert result.status == AccountStatus.online
        assert result.screenshot_version == 1
        assert events == [("click", 320, 240)]
        assert second_session.screenshot_version == 0
        with pytest.raises(zhihu_login.ZhihuLoginError):
            await zhihu_login.perform_website_action(
                second_account_id,
                first_session_id,
                action="click",
                x=10,
                y=10,
            )

    try:
        asyncio.run(verify())
    finally:
        zhihu_login._sessions.pop(first_session_id, None)
        zhihu_login._sessions.pop(second_session_id, None)


def test_zhihu_article_sync_payload_builds_public_url_and_time() -> None:
    item = _published_article_from_payload(
        {"id": 2082171778986664628, "title": "已经发布的文章", "created": 1789246500}
    )
    assert item is not None
    assert item.url == "https://zhuanlan.zhihu.com/p/2082171778986664628"
    assert item.published_at is not None
    assert _published_article_from_payload({"id": "draft", "title": "草稿"}) is None


def test_qa_payload_parsers_extract_question_and_answer_ids() -> None:
    questions = questions_from_search_payload(
        {
            "data": [
                {
                    "type": "search_result",
                    "object": {
                        "question": {
                            "id": 123456789,
                            "title": "如何正确护理头发？",
                            "answer_count": 8,
                        }
                    },
                }
            ]
        },
        "头发护理",
    )
    assert len(questions) == 1
    assert questions[0].url == "https://www.zhihu.com/question/123456789"
    assert questions[0].keyword == "头发护理"
    assert _answer_id_from_payload({"data": {"id": 987654321}}) == "987654321"
    assert _answer_id_from_payload({"message": "failed"}) is None


def test_zhihu_public_url_does_not_accept_editor_url() -> None:
    public = "https://zhuanlan.zhihu.com/p/2082171778986664628"
    editor = f"{public}/edit"
    assert _article_id_from_url(public) == "2082171778986664628"
    assert _article_id_from_url(editor) == "2082171778986664628"
    assert _public_article_url(public) == public
    assert _public_article_url(f"{public}?utm_source=test") == public
    assert _public_article_url(editor) is None
    assert _public_article_url("https://example.com/p/2082171778986664628") is None


def test_zhihu_publish_response_detection_accepts_old_and_new_endpoints() -> None:
    new_response = SimpleNamespace(
        url="https://www.zhihu.com/api/v4/content/publish",
        request=SimpleNamespace(method="POST"),
    )
    old_response = SimpleNamespace(
        url="https://zhuanlan.zhihu.com/api/articles/123/publish",
        request=SimpleNamespace(method="POST"),
    )
    public_lookup = SimpleNamespace(
        url="https://www.zhihu.com/api/v4/articles/123",
        request=SimpleNamespace(method="GET"),
    )
    assert _is_publish_response(new_response)
    assert _is_publish_response(old_response)
    assert not _is_publish_response(public_lookup)


def test_zhihu_publish_payload_extracts_id_and_error() -> None:
    payload = {"data": {"result": '{"publish":{"id":"2082171778986664628"}}'}}
    assert _publish_article_id(payload) == "2082171778986664628"
    assert _publish_error_message({"error": {"message": "请选择文章话题"}}) == (
        "请选择文章话题"
    )


def test_zhihu_publish_response_preserves_success_and_failure_details() -> None:
    class FakeResponse:
        def __init__(self, status: int, body: str) -> None:
            self.status = status
            self.body = body

        async def text(self) -> str:
            return self.body

    success = asyncio.run(
        _read_publish_response(FakeResponse(200, '{"message":"success"}'))
    )
    failure = asyncio.run(
        _read_publish_response(
            FakeResponse(400, '{"error":{"message":"请选择文章话题"}}')
        )
    )
    assert success == (None, None)
    assert failure == (None, "知乎正式发布接口拒绝（HTTP 400）：请选择文章话题")


def test_public_article_verification_requires_matching_public_record() -> None:
    async def verify() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"id": 123456789, "title": "已公开的 测试文章"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await _verify_public_article(
                "123456789",
                "已公开的测试文章",
                client=client,
                attempts=1,
                wait_seconds=0,
            )
        assert result == "https://zhuanlan.zhihu.com/p/123456789"

    asyncio.run(verify())


def test_public_article_verification_rejects_missing_draft() -> None:
    async def verify() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ZhihuPublishError, match="编辑草稿"):
                await _verify_public_article(
                    "2082171778986664628",
                    "并未公开的文章",
                    client=client,
                    attempts=1,
                    wait_seconds=0,
                )

    asyncio.run(verify())


def test_public_article_verification_falls_back_when_api_is_blocked() -> None:
    async def verify() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ZhihuPublicVerificationUnavailable, match="公开网页"):
                await _verify_public_article(
                    "2082171778986664628",
                    "等待网页核验的文章",
                    client=client,
                    attempts=3,
                    wait_seconds=0,
                )

    asyncio.run(verify())


def test_public_page_snapshot_distinguishes_missing_from_blocked_render() -> None:
    public_url = "https://zhuanlan.zhihu.com/p/2082171778986664628"
    with pytest.raises(ZhihuPublishError, match="没有发布成功"):
        _check_public_page_snapshot(
            "2082171778986664628",
            "等待核验的文章",
            current_url=public_url,
            response_status=200,
            body_text="你似乎来到了没有知识存在的荒原",
        )
    with pytest.raises(ZhihuPublicVerificationUnavailable, match="无法二次核验"):
        _check_public_page_snapshot(
            "2082171778986664628",
            "等待核验的文章",
            current_url=public_url,
            response_status=200,
            body_text="知乎页面正在加载",
            document_title="知乎",
        )


def test_public_page_snapshot_accepts_body_or_metadata_title() -> None:
    public_url = "https://zhuanlan.zhihu.com/p/2082171778986664628"
    _check_public_page_snapshot(
        "2082171778986664628",
        "已经发布的测试文章",
        current_url=public_url,
        response_status=200,
        body_text="页面内容尚未渲染",
        open_graph_title="已经发布的测试文章 - 知乎",
    )
    with pytest.raises(ZhihuPublishError, match="标题.*不一致"):
        _check_public_page_snapshot(
            "2082171778986664628",
            "已经发布的测试文章",
            current_url=public_url,
            response_status=200,
            body_text="页面内容尚未渲染",
            open_graph_title="另一篇文章 - 知乎",
        )


def test_article_job_progress_is_percentage() -> None:
    job = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        account_id=None,
        job_type=ArticleJobType.publish,
        status=ArticleJobStatus.running,
        output_mode=None,
        total_count=8,
        completed_count=3,
        success_count=2,
        failed_count=1,
        current_item="发布测试文章",
        error_message=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=None,
    )
    assert _job_read(job).progress_percent == 38
