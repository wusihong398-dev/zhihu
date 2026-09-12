import re
from datetime import UTC, datetime
from typing import Any

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
                "button:has-text('发布')",
                "[role='button']:has-text('发布')",
            ),
        )
        if publish_button is None:
            raise ZhihuPublishError("知乎创作页面结构发生变化，未找到发布按钮")
        await publish_button.click(timeout=5000)
        await page.wait_for_timeout(1200)

        confirm = await _first_visible(
            page,
            (
                "[role='dialog'] button:has-text('确认发布')",
                "[role='dialog'] button:has-text('发布文章')",
                "[role='dialog'] button:has-text('发布')",
            ),
        )
        if confirm is not None:
            await confirm.click(timeout=5000)

        try:
            await page.wait_for_url(
                re.compile(r"https://zhuanlan\.zhihu\.com/p/\d+"), timeout=20000
            )
        except Exception:
            success = page.locator("text=发布成功").first
            if not await success.is_visible(timeout=1500):
                raise ZhihuPublishError(
                    "知乎未确认发布成功，可能需要选择话题或完成人工验证"
                )
        return page.url
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
