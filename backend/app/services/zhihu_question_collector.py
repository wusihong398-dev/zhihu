import re
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


class ZhihuQuestionCollectionError(RuntimeError):
    pass


class ZhihuQuestionLoginRequired(ZhihuQuestionCollectionError):
    pass


@dataclass(frozen=True)
class ZhihuQuestionCandidate:
    question_id: str
    title: str
    url: str
    keyword: str
    excerpt: str = ""
    answer_count: int = 0
    follower_count: int = 0


def _clean_text(value: Any, limit: int) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(text.split())[:limit]


def _candidate_from_object(raw: Any, keyword: str) -> ZhihuQuestionCandidate | None:
    if not isinstance(raw, dict):
        return None
    question = raw.get("question") if isinstance(raw.get("question"), dict) else raw
    question_id = str(question.get("id") or "").strip()
    title = _clean_text(question.get("title") or question.get("name"), 500)
    if not question_id.isdigit() or not title:
        return None
    excerpt = _clean_text(
        question.get("excerpt") or question.get("detail") or raw.get("excerpt"), 2000
    )
    return ZhihuQuestionCandidate(
        question_id=question_id,
        title=title,
        url=f"https://www.zhihu.com/question/{question_id}",
        keyword=keyword,
        excerpt=excerpt,
        answer_count=int(question.get("answer_count") or 0),
        follower_count=int(question.get("follower_count") or 0),
    )


def questions_from_search_payload(payload: Any, keyword: str) -> list[ZhihuQuestionCandidate]:
    found: dict[str, ZhihuQuestionCandidate] = {}

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        candidate = None
        if isinstance(value.get("question"), dict):
            candidate = _candidate_from_object(value, keyword)
        elif value.get("type") == "question":
            candidate = _candidate_from_object(value, keyword)
        if candidate is not None:
            found.setdefault(candidate.question_id, candidate)
        for key, item in value.items():
            if key not in {"question", "author"}:
                visit(item)

    visit(payload)
    return list(found.values())


async def _collect_from_page(
    page: Any, keyword: str, timeout_ms: int = 30000
) -> list[ZhihuQuestionCandidate]:
    url = f"https://www.zhihu.com/search?type=content&q={quote(keyword)}"
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_timeout(2200)
    for _ in range(3):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(700)
    raw_items = await page.evaluate(
        r"""() => {
          const result = new Map();
          for (const anchor of document.querySelectorAll('a[href*="/question/"]')) {
            const match = anchor.href.match(/\/question\/(\d+)/);
            if (!match) continue;
            const container = anchor.closest('article, .List-item, [class*="ContentItem"]');
            const heading = container?.querySelector('h1, h2, h3, [class*="Title"]');
            const title = (anchor.getAttribute('title') || heading?.innerText || anchor.innerText || '').trim();
            if (!title || title.length < 4) continue;
            const excerpt = (container?.querySelector('[class*="RichText"], [class*="Excerpt"]')?.innerText || '').trim();
            const current = result.get(match[1]);
            if (!current || title.length > current.title.length) result.set(match[1], {id: match[1], title, excerpt});
          }
          return Array.from(result.values());
        }"""
    )
    if not isinstance(raw_items, list):
        return []
    return [
        candidate
        for raw in raw_items
        if (candidate := _candidate_from_object(raw, keyword)) is not None
    ]


async def collect_zhihu_questions(
    account: ZhihuAccount, keywords: list[str], target_count: int
) -> list[ZhihuQuestionCandidate]:
    root = account_storage_path(account.id, account.profile_key)
    screenshot_path = root / "screenshots" / (
        f"questions-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.png"
    )
    lock = get_account_browser_lock(account.id)
    if lock.locked():
        raise ZhihuQuestionCollectionError("该账号正在登录、发布或同步，请稍后再采集")
    await lock.acquire()
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
            raise ZhihuQuestionLoginRequired("知乎登录已失效，请先重新扫码登录")
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)
        found: dict[str, ZhihuQuestionCandidate] = {}
        for keyword in keywords:
            try:
                response = await context.request.get(
                    f"https://www.zhihu.com/api/v4/search_v3?t=general&q={quote(keyword)}&offset=0&limit=20",
                    timeout=timeout_ms,
                )
                if response.status == 200:
                    for item in questions_from_search_payload(await response.json(), keyword):
                        found.setdefault(item.question_id, item)
            except Exception:
                pass
            if len(found) < target_count:
                for item in await _collect_from_page(page, keyword, timeout_ms):
                    found.setdefault(item.question_id, item)
            if len(found) >= target_count:
                break
        if not found:
            body = await page.locator("body").inner_text(timeout=5000)
            if any(marker in body for marker in ("安全验证", "验证码", "登录知乎")):
                raise ZhihuQuestionLoginRequired("知乎要求重新登录或完成安全验证")
            raise ZhihuQuestionCollectionError("没有采集到知乎问题，请更换关键词后重试")
        return list(found.values())[:target_count]
    except (ZhihuQuestionLoginRequired, ZhihuQuestionCollectionError):
        if page is not None:
            try:
                await page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
        raise
    except Exception as exc:
        raise ZhihuQuestionCollectionError("连接知乎问题搜索失败，请稍后重试") from exc
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
        if lock.locked():
            lock.release()
