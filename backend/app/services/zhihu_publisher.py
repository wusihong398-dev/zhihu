import asyncio
import html
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.models.account import ZhihuAccount
from app.models.article import Article
from app.services.account_storage import account_storage_path
from app.services.browser_lock import get_account_browser_lock
from app.services.zhihu_login import has_zhihu_auth_cookie


class ZhihuPublishError(RuntimeError):
    pass


class ZhihuLoginRequired(ZhihuPublishError):
    pass


_ARTICLE_ID_RE = re.compile(r"^/p/(?P<article_id>\d+)(?:/edit)?/?$")


def _article_id_from_url(url: str) -> str | None:
    """Return a Zhihu column article id from either its public or edit URL."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.hostname != "zhuanlan.zhihu.com":
        return None
    match = _ARTICLE_ID_RE.fullmatch(parsed.path)
    return match.group("article_id") if match else None


def _public_article_url(url: str) -> str | None:
    """Return a canonical public URL, refusing editor URLs as proof of success."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
    except ValueError:
        return None
    article_id = _article_id_from_url(url)
    if article_id is None or parsed.path.rstrip("/").endswith("/edit"):
        return None
    return f"https://zhuanlan.zhihu.com/p/{article_id}"


def _normalized_title(value: str) -> str:
    return "".join(html.unescape(value).split())


async def _verify_public_article(
    article_id: str,
    expected_title: str,
    *,
    client: httpx.AsyncClient | None = None,
    attempts: int = 6,
    wait_seconds: float = 2,
) -> str:
    """Verify the article through Zhihu's unauthenticated public article API."""
    api_url = f"https://www.zhihu.com/api/v4/articles/{article_id}"
    public_url = f"https://zhuanlan.zhihu.com/p/{article_id}"
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=15,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": public_url,
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
            },
        )
    last_reason = "知乎公开接口没有返回文章"
    try:
        for attempt in range(max(attempts, 1)):
            try:
                response = await client.get(api_url)
                if response.status_code == 200:
                    payload = response.json()
                    returned_id = str(payload.get("id", ""))
                    returned_title = str(payload.get("title", ""))
                    if returned_id != article_id:
                        last_reason = "知乎公开接口返回了其他文章"
                    elif not returned_title:
                        last_reason = "知乎公开文章缺少标题"
                    elif _normalized_title(returned_title) != _normalized_title(
                        expected_title
                    ):
                        last_reason = "知乎公开文章标题与待发布文章不一致"
                    else:
                        return public_url
                elif response.status_code == 404:
                    last_reason = "公开文章不存在，知乎可能只保存了编辑草稿"
                elif response.status_code in {401, 403, 429}:
                    last_reason = (
                        f"知乎公开核验被限制（HTTP {response.status_code}），"
                        "无法确认文章已发布"
                    )
                else:
                    last_reason = (
                        f"知乎公开核验返回 HTTP {response.status_code}，"
                        "无法确认文章已发布"
                    )
            except (httpx.HTTPError, ValueError):
                last_reason = "连接知乎公开接口失败，无法确认文章已发布"
            if attempt + 1 < max(attempts, 1):
                await asyncio.sleep(wait_seconds)
    finally:
        if owns_client:
            await client.aclose()
    raise ZhihuPublishError(last_reason)


async def _first_visible(page: Any, selectors: tuple[str, ...]) -> Any:
    for selector in selectors:
        try:
            matches = page.locator(selector)
            for index in range(min(await matches.count(), 20)):
                locator = matches.nth(index)
                if await locator.is_visible(timeout=1000):
                    return locator
        except Exception:
            continue
    return None


async def publish_article_to_zhihu(
    account: ZhihuAccount, article: Article
) -> str:
    root = account_storage_path(account.id, account.profile_key)
    screenshot_path = (
        root
        / "screenshots"
        / f"publish-{article.id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.png"
    )
    playwright = None
    context = None
    browser_lock = get_account_browser_lock(account.id)
    if browser_lock.locked():
        raise ZhihuPublishError("该账号正在登录或执行其他发布任务，请稍后重试")
    await browser_lock.acquire()
    try:
        from playwright.async_api import async_playwright

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
            raise ZhihuLoginRequired("知乎登录已失效，请先重新扫码登录")

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(
            "https://zhuanlan.zhihu.com/write",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(1800)
        if "signin" in page.url.lower() or not has_zhihu_auth_cookie(
            await context.cookies()
        ):
            raise ZhihuLoginRequired("知乎登录已失效，请先重新扫码登录")

        title = await _first_visible(
            page,
            (
                "textarea[placeholder*='标题']",
                "input[placeholder*='标题']",
                ".WriteIndex-titleInput textarea",
            ),
        )
        editor = await _first_visible(
            page,
            (
                ".public-DraftEditor-content[contenteditable='true']",
                "[contenteditable='true'][role='textbox']",
                "div[contenteditable='true']",
            ),
        )
        if title is None or editor is None:
            raise ZhihuPublishError("知乎创作页面结构发生变化，未找到标题或正文编辑器")

        await title.fill(article.title)
        try:
            await editor.fill(article.content)
        except Exception:
            await editor.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.insert_text(article.content)
        await page.wait_for_timeout(800)

        publish_button = await _first_visible(
            page,
            (
                "button:text-is('发布')",
                "[role='button']:text-is('发布')",
            ),
        )
        if publish_button is None:
            raise ZhihuPublishError("知乎创作页面结构发生变化，未找到发布按钮")
        await publish_button.click(timeout=5000)
        await page.wait_for_timeout(1200)

        # Zhihu may show one or more publish-setting dialogs. Only click exact
        # confirmation labels inside a dialog so the editor's original button is
        # never mistaken for the final confirmation button.
        for _ in range(3):
            confirm = await _first_visible(
                page,
                (
                    "[role='dialog'] button:text-is('确认发布')",
                    "[role='dialog'] button:text-is('发布文章')",
                    "[role='dialog'] button:text-is('发布')",
                    "[class*='Modal'] button:text-is('确认发布')",
                    "[class*='Modal'] button:text-is('发布文章')",
                    "[class*='Modal'] button:text-is('发布')",
                ),
            )
            if confirm is None:
                break
            await confirm.click(timeout=5000)
            await page.wait_for_timeout(1200)

        # An /edit URL only means that Zhihu allocated an editor draft. It is not
        # evidence that the article is public. Extract the id, then independently
        # verify it through the unauthenticated public article API.
        article_id = None
        for _ in range(20):
            article_id = _article_id_from_url(page.url)
            if article_id:
                break
            await page.wait_for_timeout(1000)
        if article_id is None:
            raise ZhihuPublishError(
                "知乎未返回文章编号，可能需要选择话题或完成人工验证"
            )
        return await _verify_public_article(article_id, article.title)
    except (ZhihuLoginRequired, ZhihuPublishError):
        if context is not None and context.pages:
            try:
                await context.pages[0].screenshot(
                    path=str(screenshot_path), full_page=True
                )
            except Exception:
                pass
        raise
    except Exception as exc:
        if context is not None and context.pages:
            try:
                await context.pages[0].screenshot(
                    path=str(screenshot_path), full_page=True
                )
            except Exception:
                pass
        raise ZhihuPublishError("连接知乎发布页面失败，请稍后重试") from exc
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
