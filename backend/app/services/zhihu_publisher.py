import asyncio
import html
import json
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


class ZhihuPublicVerificationUnavailable(ZhihuPublishError):
    pass


_ARTICLE_ID_RE = re.compile(r"^/p/(?P<article_id>\d+)(?:/edit)?/?$")
_PUBLISH_RESPONSE_PATHS = (
    "/api/v4/content/publish",
    "/api/articles/",
)


def _is_publish_response(response: Any) -> bool:
    """Return whether a browser response belongs to a formal publish request."""
    try:
        method = response.request.method.upper()
        url = response.url
    except Exception:
        return False
    if method != "POST":
        return False
    return _PUBLISH_RESPONSE_PATHS[0] in url or (
        _PUBLISH_RESPONSE_PATHS[1] in url and "/publish" in url
    )


def _publish_article_id(payload: Any) -> str | None:
    """Extract the public article id from Zhihu's old and new publish payloads."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (TypeError, ValueError):
                result = None
        if isinstance(result, dict):
            published = result.get("publish")
            if isinstance(published, dict) and published.get("id"):
                return str(published["id"])
        published = data.get("publish")
        if isinstance(published, dict) and published.get("id"):
            return str(published["id"])
        if data.get("id"):
            return str(data["id"])
    published = payload.get("publish")
    if isinstance(published, dict) and published.get("id"):
        return str(published["id"])
    if payload.get("id"):
        return str(payload["id"])
    return None


def _publish_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("message", "msg", "error_message", "error_description"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:240]
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return " ".join(error.split())[:240]
    if isinstance(error, dict):
        return _publish_error_message(error)
    data = payload.get("data")
    if isinstance(data, dict):
        return _publish_error_message(data)
    return None


async def _read_publish_response(response: Any) -> tuple[str | None, str | None]:
    """Return (public id, failure reason) without exposing raw response data."""
    status = int(getattr(response, "status", 0) or 0)
    payload = None
    response_text = ""
    try:
        response_text = await response.text()
        payload = json.loads(response_text) if response_text else None
    except Exception:
        payload = None
    article_id = _publish_article_id(payload)
    reason = _publish_error_message(payload)
    if status >= 400:
        suffix = f"：{reason}" if reason else ""
        return None, f"知乎正式发布接口拒绝（HTTP {status}）{suffix}"
    reported_failure = isinstance(payload, dict) and (
        payload.get("success") is False
        or "error" in payload
        or payload.get("code") not in (None, 0, 200, "0", "200")
    )
    if reported_failure and reason:
        return None, f"知乎正式发布未通过：{reason}"
    return article_id, None


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
    verification_unavailable = False
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
                    verification_unavailable = True
                    last_reason = (
                        f"知乎公开核验被限制（HTTP {response.status_code}），"
                        "改用公开网页核验"
                    )
                    break
                else:
                    verification_unavailable = True
                    last_reason = (
                        f"知乎公开核验返回 HTTP {response.status_code}，"
                        "改用公开网页核验"
                    )
            except (httpx.HTTPError, ValueError):
                verification_unavailable = True
                last_reason = "连接知乎公开接口失败，无法确认文章已发布"
            if attempt + 1 < max(attempts, 1):
                await asyncio.sleep(wait_seconds)
    finally:
        if owns_client:
            await client.aclose()
    error_type = (
        ZhihuPublicVerificationUnavailable
        if verification_unavailable
        else ZhihuPublishError
    )
    raise error_type(last_reason)


async def _verify_public_article_page(
    playwright: Any, article_id: str, expected_title: str
) -> str:
    """Fallback verification in a clean browser when Zhihu blocks its API."""
    public_url = f"https://zhuanlan.zhihu.com/p/{article_id}"
    browser = None
    context = None
    try:
        browser = await playwright.chromium.launch(
            executable_path=settings.chromium_executable,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context(
            locale="zh-CN",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        response = await page.goto(
            public_url,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(2500)
        if response is not None and response.status == 404:
            raise ZhihuPublishError("知乎公开文章不存在，可能只保存了编辑草稿")
        if _public_article_url(page.url) != public_url:
            raise ZhihuPublishError("知乎公开文章地址未生效，可能仍处于编辑草稿状态")
        body_text = await page.locator("body").inner_text(timeout=5000)
        missing_markers = (
            "你似乎来到了没有知识存在的荒原",
            "内容不存在",
            "页面不存在",
        )
        if any(marker in body_text for marker in missing_markers):
            raise ZhihuPublishError("知乎公开页面显示内容不存在，文章没有发布成功")
        if _normalized_title(expected_title) not in _normalized_title(body_text):
            verification_markers = ("安全验证", "异常流量", "验证码")
            if any(marker in body_text for marker in verification_markers):
                raise ZhihuPublishError("知乎公开页面触发安全验证，无法确认发布成功")
            raise ZhihuPublishError("知乎公开页面未显示文章标题，无法确认发布成功")
        return public_url
    except ZhihuPublishError:
        raise
    except Exception as exc:
        raise ZhihuPublishError("打开知乎公开文章页面失败，无法确认发布成功") from exc
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


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


async def _last_visible(page: Any, selector: str) -> Any:
    """Return the last visible match; portals are normally appended to the DOM."""
    try:
        matches = page.locator(selector)
        for index in range(min(await matches.count(), 30) - 1, -1, -1):
            locator = matches.nth(index)
            if await locator.is_visible(timeout=500):
                return locator
    except Exception:
        pass
    return None


async def _publish_confirmation_button(page: Any) -> Any:
    """Locate Zhihu's final confirmation across dialogs, popovers and portals."""
    strong_labels = (
        "确认发布",
        "发布文章",
        "立即发布",
        "确定发布",
        "确定",
        "确认",
    )
    containers = (
        "[role='dialog']",
        "[aria-modal='true']",
        "[class*='Modal']",
        "[class*='Popover']",
        "[class*='Publish']",
        "[class*='Drawer']",
    )
    selectors: list[str] = []
    for label in strong_labels:
        for container in containers:
            selectors.extend(
                (
                    f"{container} button:text-is('{label}')",
                    f"{container} [role='button']:text-is('{label}')",
                )
            )
        selectors.extend(
            (
                f"button:text-is('{label}')",
                f"[role='button']:text-is('{label}')",
            )
        )
    button = await _first_visible(page, tuple(selectors))
    if button is not None:
        return button

    for container in containers:
        for selector in (
            f"{container} button:not([data-totod-initial-publish]):text-is('发布')",
            f"{container} [role='button']:not([data-totod-initial-publish]):text-is('发布')",
        ):
            button = await _last_visible(page, selector)
            if button is not None:
                return button

    # Some versions render the publish panel in a generic body portal without
    # dialog semantics. The editor's first button is marked and excluded; the
    # portal's final button is normally the last visible exact-text match.
    for selector in (
        "button:not([data-totod-initial-publish]):text-is('发布')",
        "[role='button']:not([data-totod-initial-publish]):text-is('发布')",
    ):
        button = await _last_visible(page, selector)
        if button is not None:
            return button
    return None


async def _visible_publish_feedback(page: Any) -> str | None:
    """Collect a concise validation/safety message shown by Zhihu."""
    selectors = (
        "[role='alert']",
        "[class*='Toast']",
        "[class*='Error']",
        "[class*='warning']",
        "[class*='Warning']",
    )
    for selector in selectors:
        try:
            matches = page.locator(selector)
            for index in range(min(await matches.count(), 20) - 1, -1, -1):
                locator = matches.nth(index)
                if not await locator.is_visible(timeout=300):
                    continue
                value = " ".join((await locator.inner_text(timeout=1000)).split())
                if value:
                    return value[:240]
        except Exception:
            continue
    try:
        body_text = await page.locator("body").inner_text(timeout=2000)
    except Exception:
        return None
    markers = (
        "安全验证",
        "完成验证",
        "请选择话题",
        "至少选择一个话题",
        "发布失败",
        "账号异常",
        "内容违规",
    )
    for line in body_text.splitlines():
        value = " ".join(line.split())
        if value and any(marker in value for marker in markers):
            return value[:240]
    return None


async def _finish_publish(
    page: Any, publish_response: asyncio.Future[Any]
) -> tuple[str | None, str | None]:
    """Drive the final publish panel and observe Zhihu's authoritative response."""
    for _ in range(12):
        if publish_response.done():
            break
        if _public_article_url(page.url):
            break
        confirm = await _publish_confirmation_button(page)
        if confirm is not None:
            try:
                if await confirm.is_enabled(timeout=500):
                    await confirm.click(timeout=5000)
            except Exception:
                try:
                    await confirm.click(timeout=5000, force=True)
                except Exception:
                    pass
        try:
            await asyncio.wait_for(asyncio.shield(publish_response), timeout=1.25)
        except TimeoutError:
            pass

    if publish_response.done() and not publish_response.cancelled():
        return await _read_publish_response(publish_response.result())
    feedback = await _visible_publish_feedback(page)
    if feedback:
        return None, f"知乎发布页面提示：{feedback}"
    return None, "未触发知乎正式发布接口，最终确认按钮没有生效"


async def publish_article_to_zhihu(account: ZhihuAccount, article: Article) -> str:
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

        publish_response: asyncio.Future[Any] = (
            asyncio.get_running_loop().create_future()
        )

        def capture_publish_response(response: Any) -> None:
            if _is_publish_response(response) and not publish_response.done():
                publish_response.set_result(response)

        page.on("response", capture_publish_response)

        publish_button = await _first_visible(
            page,
            (
                "button:text-is('发布')",
                "[role='button']:text-is('发布')",
            ),
        )
        if publish_button is None:
            raise ZhihuPublishError("知乎创作页面结构发生变化，未找到发布按钮")
        try:
            await publish_button.evaluate(
                "element => element.setAttribute('data-totod-initial-publish', 'true')"
            )
        except Exception:
            pass
        await publish_button.click(timeout=5000)
        api_article_id, publish_error = await _finish_publish(page, publish_response)
        if publish_error:
            raise ZhihuPublishError(publish_error)

        # An /edit URL only means that Zhihu allocated an editor draft. It is not
        # evidence that the article is public. Extract the id, then independently
        # verify it through the unauthenticated public article API.
        article_id = api_article_id
        for _ in range(20):
            if article_id:
                break
            article_id = _article_id_from_url(page.url)
            if article_id:
                break
            await page.wait_for_timeout(1000)
        if article_id is None:
            raise ZhihuPublishError(
                "知乎未返回文章编号，可能需要选择话题或完成人工验证"
            )
        try:
            return await _verify_public_article(article_id, article.title)
        except ZhihuPublicVerificationUnavailable:
            return await _verify_public_article_page(
                playwright, article_id, article.title
            )
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
