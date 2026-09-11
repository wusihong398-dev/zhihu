from app.services.keyword_collector import (
    normalize_keyword,
    parse_baidu_related,
    parse_google_related,
)
from app.services.secret_box import decrypt_secret, encrypt_secret, mask_secret


def test_secret_round_trip() -> None:
    source = "sk-test-1234567890"
    encrypted = encrypt_secret(source)
    assert encrypted != source
    assert decrypt_secret(encrypted) == source
    assert mask_secret(source) == "sk-t••••••••7890"


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
