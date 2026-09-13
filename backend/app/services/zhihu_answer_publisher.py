import re
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.models.account import ZhihuAccount
from app.models.answer import ZhihuAnswer
from app.services.account_storage import account_storage_path
from app.services.browser_lock import get_account_browser_lock
from app.services.system_settings import browser_timeout_ms
from app.services.zhihu_login import has_zhihu_auth_cookie


class ZhihuAnswerPublishError(RuntimeError):
    pass


class ZhihuAnswerLoginRequired(ZhihuAnswerPublishError):
    pass


class ZhihuAnswerRiskControlError(ZhihuAnswerPublishError):
    pass


_ANSWER_EDITOR_SELECTORS = (
    ".AnswerForm-editor .ProseMirror[contenteditable='true']",
    ".AnswerForm-editor [contenteditable='true']",
    ".ProseMirror[contenteditable='true']",
    ".DraftEditor-root [contenteditable='true']",
    ".public-DraftEditor-content[contenteditable='true']",
    "[contenteditable='true'][role='textbox']",
    "[contenteditable='true'][data-contents='true']",
)

_WRITE_ANSWER_SELECTORS = (
    "button:has-text('写回答')",
    "[role='button']:has-text('写回答')",
    "a:has-text('写回答')",
    "button:has-text('添加回答')",
    "[role='button']:has-text('添加回答')",
    "button:has-text('回答问题')",
    "[role='button']:has-text('回答问题')",
    "button:has-text('参与回答')",
    "button:text-is('回答')",
    "[role='button']:text-is('回答')",
    "a:text-is('回答')",
    ".QuestionButtonGroup button:has-text('回答')",
    ".QuestionHeaderActions button:has-text('回答')",
    "[aria-label*='写回答']",
    "[data-za-detail-view-element_name*='Answer']:has-text('回答')",
)

_PUBLISH_ANSWER_SELECTORS = (
    "button:has-text('发布回答')",
    "[role='button']:has-text('发布回答')",
    "button:has-text('提交回答')",
    "[role='button']:has-text('提交回答')",
    ".AnswerForm button:has-text('发布')",
    ".AnswerForm [role='button']:has-text('发布')",
)


def _answer_id_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for value in (payload, payload.get("data"), payload.get("answer")):
        if not isinstance(value, dict):
            continue
        answer_id = str(value.get("id") or value.get("answer_id") or "").strip()
        if answer_id.isdigit():
            return answer_id
    return None


async def _first_visible(page: Any, selectors: tuple[str, ...]) -> Any:
    for selector in selectors:
        try:
            matches = page.locator(selector)
            for index in range(min(await matches.count(), 30)):
                item = matches.nth(index)
                if await item.is_visible(timeout=700):
                    return item
        except Exception:
            continue
    return None


async def _wait_for_visible(
    page: Any,
    selectors: tuple[str, ...],
    *,
    attempts: int = 12,
    interval_ms: int = 500,
) -> Any:
    for _ in range(max(1, attempts)):
        item = await _first_visible(page, selectors)
        if item is not None:
            return item
        await page.wait_for_timeout(interval_ms)
    return None


def _answer_unavailable_reason(body: str) -> str | None:
    compact = "".join(body.split())
    if any(marker in compact for marker in ("问题已关闭", "已关闭回答", "不能回答")):
        return "该问题已关闭回答，无法发布"
    if any(marker in compact for marker in ("问题已删除", "内容不存在", "页面不存在")):
        return "知乎问题已删除或不存在"
    if any(marker in compact for marker in ("修改回答", "编辑回答", "你已经回答过")):
        return "当前知乎账号已经回答过该问题，请在知乎修改原回答"
    return None


def _answer_risk_control_reason(body: str) -> str | None:
    compact = "".join(body.split())
    if (
        '"code":40362' in compact
        or "当前请求存在异常" in compact
        or "暂时限制本次访问" in compact
    ):
        return (
            "知乎风控 40362：当前服务器访问被临时限制。本批任务已停止，请勿连续重试；"
            "请先在“知乎账号→登录官网”中检查登录及安全验证，稍后再试。"
            "若仍出现 40362，请改用该账号常用网络人工发布"
        )
    return None


async def _visible_feedback(page: Any) -> str | None:
    for selector in (
        "[role='alert']",
        "[class*='Toast']",
        "[class*='Message']",
        "[class*='Error']",
    ):
        try:
            items = page.locator(selector)
            for index in range(min(await items.count(), 20)):
                item = items.nth(index)
                if await item.is_visible(timeout=300):
                    text = " ".join((await item.inner_text()).split())
                    if text:
                        return text[:500]
        except Exception:
            continue
    return None


async def publish_answer_to_zhihu(account: ZhihuAccount, answer: ZhihuAnswer) -> str:
    match = re.search(r"/question/(\d+)", answer.question_url)
    if not match:
        raise ZhihuAnswerPublishError("问题链接格式不正确")
    question_id = match.group(1)
    root = account_storage_path(account.id, account.profile_key)
    screenshot_path = root / "screenshots" / (
        f"answer-{answer.id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.png"
    )
    lock = get_account_browser_lock(account.id)
    if lock.locked():
        raise ZhihuAnswerPublishError("该账号正在执行其他浏览器任务，请稍后再发布")
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
            viewport={"width": 1440, "height": 1000},
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        if not has_zhihu_auth_cookie(await context.cookies()):
            raise ZhihuAnswerLoginRequired("知乎登录已失效，请先重新扫码登录")
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)
        responses: list[Any] = []
        page.on("response", lambda response: responses.append(response))
        response = await page.goto(
            f"https://www.zhihu.com/question/{question_id}",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        await page.wait_for_timeout(2200)
        if response is not None and response.status == 404:
            raise ZhihuAnswerPublishError("知乎问题已删除或不存在")
        body = await page.locator("body").inner_text(timeout=5000)
        if any(marker in body for marker in ("安全验证", "验证码", "登录知乎")):
            raise ZhihuAnswerLoginRequired("知乎要求重新登录或完成安全验证")

        risk_control_reason = _answer_risk_control_reason(body)
        if risk_control_reason:
            raise ZhihuAnswerRiskControlError(risk_control_reason)
        unavailable_reason = _answer_unavailable_reason(body)
        if unavailable_reason:
            raise ZhihuAnswerPublishError(unavailable_reason)

        editor = await _first_visible(page, _ANSWER_EDITOR_SELECTORS)
        if editor is None:
            await page.evaluate("window.scrollTo(0, 0)")
            write_button = await _wait_for_visible(
                page, _WRITE_ANSWER_SELECTORS, attempts=20
            )
            if write_button is None:
                body = await page.locator("body").inner_text(timeout=5000)
                unavailable_reason = _answer_unavailable_reason(body)
                if unavailable_reason:
                    raise ZhihuAnswerPublishError(unavailable_reason)
                raise ZhihuAnswerPublishError(
                    "知乎页面已打开，但没有识别到回答入口；可能是页面结构更新或账号回答权限受限"
                )
            try:
                await write_button.scroll_into_view_if_needed(timeout=3000)
                await write_button.click(timeout=5000)
            except Exception:
                await write_button.click(timeout=5000, force=True)
            editor = await _wait_for_visible(page, _ANSWER_EDITOR_SELECTORS)
        if editor is None:
            raise ZhihuAnswerPublishError("知乎回答编辑器没有正常打开")

        await editor.click()
        try:
            await editor.fill(answer.content)
        except Exception:
            await editor.evaluate(
                """(node, text) => {
                  node.focus(); node.innerText = text;
                  node.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: text}));
                }""",
                answer.content,
            )
        await page.wait_for_timeout(700)
        publish_button = await _wait_for_visible(
            page, _PUBLISH_ANSWER_SELECTORS, attempts=8
        )
        if publish_button is None:
            raise ZhihuAnswerPublishError("没有找到“发布回答”按钮")
        if await publish_button.is_disabled():
            raise ZhihuAnswerPublishError("“发布回答”按钮不可用，请检查回答内容")
        responses.clear()
        await publish_button.click()

        for _ in range(15):
            await page.wait_for_timeout(500)
            url_match = re.search(rf"/question/{question_id}/answer/(\d+)", page.url)
            if url_match:
                return f"https://www.zhihu.com/question/{question_id}/answer/{url_match.group(1)}"
            for item in list(responses):
                if item.request.method.upper() not in {"POST", "PUT"} or "/answers" not in item.url:
                    continue
                try:
                    payload = await item.json()
                except Exception:
                    continue
                if item.status >= 400:
                    message = payload.get("message") if isinstance(payload, dict) else None
                    raise ZhihuAnswerPublishError(
                        f"知乎拒绝发布（HTTP {item.status}）：{message or '请稍后重试'}"
                    )
                answer_id = _answer_id_from_payload(payload)
                if answer_id:
                    return f"https://www.zhihu.com/question/{question_id}/answer/{answer_id}"
            feedback = await _visible_feedback(page)
            if feedback and any(word in feedback for word in ("失败", "错误", "验证", "限制", "频繁")):
                raise ZhihuAnswerPublishError(f"知乎页面提示：{feedback}")
        raise ZhihuAnswerPublishError("知乎未返回回答编号，无法确认是否发布成功")
    except (ZhihuAnswerLoginRequired, ZhihuAnswerPublishError):
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
        raise ZhihuAnswerPublishError("打开知乎回答页面失败，请稍后重试") from exc
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
