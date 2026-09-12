from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from app.core.config import settings
from app.models.account import ZhihuAccount
from app.services.account_storage import account_storage_path
from app.services.browser_lock import get_account_browser_lock
from app.services.system_settings import browser_timeout_ms
from app.services.zhihu_login import has_zhihu_auth_cookie


class ZhihuArticleSyncError(RuntimeError):
    pass


class ZhihuArticleSyncLoginRequired(ZhihuArticleSyncError):
    pass


@dataclass(frozen=True)
class ZhihuPublishedArticle:
    article_id: str
    title: str
    url: str
    published_at: datetime | None = None


def _published_article_from_payload(payload: Any) -> ZhihuPublishedArticle | None:
    if not isinstance(payload, dict):
        return None
    article_id = str(payload.get("id") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not article_id.isdigit() or not title:
        return None
    timestamp = payload.get("created") or payload.get("created_time")
    published_at = None
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        try:
            published_at = datetime.fromtimestamp(timestamp, UTC)
        except (OverflowError, OSError, ValueError):
            pass
    return ZhihuPublishedArticle(
        article_id=article_id,
        title=title,
        url=f"https://zhuanlan.zhihu.com/p/{article_id}",
        published_at=published_at,
    )


async def _fetch_articles_from_api(
    request: Any, url_token: str, max_items: int, timeout_ms: int = 30000
) -> list[ZhihuPublishedArticle]:
    items: list[ZhihuPublishedArticle] = []
    seen_ids: set[str] = set()
    offset = 0
    limit = 20
    while offset < max_items:
        url = (
            f"https://www.zhihu.com/api/v4/members/{quote(url_token, safe='')}"
            f"/articles?offset={offset}&limit={limit}&sort_by=created"
        )
        response = await request.get(url, timeout=timeout_ms)
        if response.status != 200:
            if items:
                break
            raise ZhihuArticleSyncError(
                f"知乎文章列表接口暂时不可用（HTTP {response.status}）"
            )
        payload = await response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ZhihuArticleSyncError("知乎文章列表返回格式发生变化")
        for raw in data:
            article = _published_article_from_payload(raw)
            if article is None or article.article_id in seen_ids:
                continue
            seen_ids.add(article.article_id)
            items.append(article)
            if len(items) >= max_items:
                return items
        paging = payload.get("paging") if isinstance(payload, dict) else None
        if not data or (isinstance(paging, dict) and paging.get("is_end") is True):
            break
        offset += limit
    return items


async def _extract_articles_from_page(
    page: Any, profile_url: str, max_items: int, timeout_ms: int = 30000
) -> list[ZhihuPublishedArticle]:
    await page.goto(profile_url, wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_timeout(2500)
    stable_rounds = 0
    previous_height = 0
    for _ in range(16):
        height = await page.evaluate("document.body.scrollHeight")
        if height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_height = height
        if stable_rounds >= 2:
            break
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(900)

    raw_items = await page.evaluate(
        r"""() => {
          const result = new Map();
          for (const anchor of document.querySelectorAll('a[href*="/p/"]')) {
            const match = anchor.href.match(/^https:\/\/zhuanlan\.zhihu\.com\/p\/(\d+)/);
            if (!match) continue;
            let title = (anchor.getAttribute('title') || anchor.innerText || '').trim();
            if (!title || /^(阅读全文|查看全文|编辑)$/.test(title)) {
              const container = anchor.closest('article, .List-item, [class*="ContentItem"]');
              const heading = container?.querySelector('h1, h2, h3, [class*="Title"]');
              title = (heading?.innerText || '').trim();
            }
            if (!title) continue;
            const current = result.get(match[1]);
            if (!current || title.length > current.title.length) {
              result.set(match[1], {id: match[1], title});
            }
          }
          return Array.from(result.values());
        }"""
    )
    articles: list[ZhihuPublishedArticle] = []
    for raw in raw_items if isinstance(raw_items, list) else []:
        article = _published_article_from_payload(raw)
        if article is not None:
            articles.append(article)
        if len(articles) >= max_items:
            break
    return articles


async def fetch_zhihu_published_articles(
    account: ZhihuAccount, *, max_items: int = 500
) -> list[ZhihuPublishedArticle]:
    """Read recent published articles using one account's isolated browser profile."""
    root = account_storage_path(account.id, account.profile_key)
    screenshot_path = root / "screenshots" / (
        f"sync-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.png"
    )
    browser_lock = get_account_browser_lock(account.id)
    if browser_lock.locked():
        raise ZhihuArticleSyncError("该账号正在登录或发布文章，请稍后再同步")
    await browser_lock.acquire()
    playwright = None
    context = None
    page = None
    try:
        from playwright.async_api import async_playwright

        timeout_ms = await browser_timeout_ms()

        playwright = await async_playwright().start()
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(root / "browser-profile"),
            executable_path=settings.chromium_executable,
            headless=True,
            locale="zh-CN",
            timezone_id=account.timezone,
            viewport={"width": 1280, "height": 900},
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        if not has_zhihu_auth_cookie(await context.cookies()):
            raise ZhihuArticleSyncLoginRequired("知乎登录已失效，请先重新扫码登录")

        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)
        url_token = ""
        try:
            me_response = await context.request.get(
                "https://www.zhihu.com/api/v4/me", timeout=timeout_ms
            )
            if me_response.status == 200:
                me_payload = await me_response.json()
                if isinstance(me_payload, dict):
                    url_token = str(me_payload.get("url_token") or "").strip()
        except Exception:
            # The creator page remains a useful authenticated fallback when the
            # private profile request is temporarily blocked or changes format.
            pass

        if url_token:
            try:
                articles = await _fetch_articles_from_api(
                    context.request, url_token, max_items, timeout_ms
                )
                if articles:
                    return articles
            except ZhihuArticleSyncError:
                pass
            profile_url = f"https://www.zhihu.com/people/{quote(url_token, safe='')}/posts"
        else:
            profile_url = "https://www.zhihu.com/creator/manage/creation/all"

        articles = await _extract_articles_from_page(
            page, profile_url, max_items, timeout_ms
        )
        if not articles:
            body_text = await page.locator("body").inner_text(timeout=5000)
            if any(marker in body_text for marker in ("登录知乎", "安全验证", "验证码")):
                raise ZhihuArticleSyncLoginRequired(
                    "知乎要求重新登录或完成安全验证，请先重新登录账号"
                )
            raise ZhihuArticleSyncError(
                "没有读取到知乎已发布文章，请确认创作中心可以正常打开"
            )
        return articles
    except (ZhihuArticleSyncLoginRequired, ZhihuArticleSyncError):
        if page is not None:
            try:
                await page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
        raise
    except Exception as exc:
        if page is not None:
            try:
                await page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
        raise ZhihuArticleSyncError("连接知乎创作中心失败，请稍后重试") from exc
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass
        if browser_lock.locked():
            browser_lock.release()
