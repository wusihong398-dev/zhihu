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
from app.services.secret_box import decrypt_secret, encrypt_secret, mask_secret
from app.services.zhihu_login import has_zhihu_auth_cookie


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
    assert has_zhihu_auth_cookie(
        [{"name": "z_c0", "value": "encrypted-login-cookie"}]
    )
    assert not has_zhihu_auth_cookie(
        [{"name": "d_c0", "value": "device-cookie"}]
    )
    assert not has_zhihu_auth_cookie([{"name": "z_c0", "value": ""}])
