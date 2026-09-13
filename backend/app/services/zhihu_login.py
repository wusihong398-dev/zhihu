import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.models.account import AccountStatus, ZhihuAccount
from app.services.account_storage import account_storage_path
from app.services.browser_lock import get_account_browser_lock
from app.services.system_settings import browser_timeout_ms


LOGIN_SESSION_TTL = timedelta(minutes=10)
WEBSITE_SESSION_TTL = timedelta(minutes=30)


class ZhihuLoginError(RuntimeError):
    pass


@dataclass
class ZhihuLoginSession:
    id: uuid.UUID
    account_id: uuid.UUID
    status: AccountStatus
    message: str
    screenshot_path: Path
    created_at: datetime
    expires_at: datetime
    mode: str = "login"
    page_url: str = ""
    screenshot_version: int = 0
    playwright: Any = None
    context: Any = None
    page: Any = None
    browser_lock: asyncio.Lock | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_sessions: dict[uuid.UUID, ZhihuLoginSession] = {}
_expiry_tasks: dict[uuid.UUID, asyncio.Task] = {}
logger = logging.getLogger(__name__)


def has_zhihu_auth_cookie(cookies: list[dict[str, Any]]) -> bool:
    return any(
        cookie.get("name") == "z_c0" and bool(cookie.get("value"))
        for cookie in cookies
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _close_browser(session: ZhihuLoginSession) -> None:
    context, playwright = session.context, session.playwright
    session.page = None
    session.context = None
    session.playwright = None
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
    if session.browser_lock is not None:
        if session.browser_lock.locked():
            session.browser_lock.release()
        session.browser_lock = None


async def _capture(session: ZhihuLoginSession) -> None:
    if session.page is None:
        return
    session.page_url = session.page.url
    await session.page.screenshot(
        path=str(session.screenshot_path),
        full_page=session.mode == "login",
        animations="disabled",
    )
    session.screenshot_version += 1


async def _expire_session_after_idle(session_id: uuid.UUID) -> None:
    try:
        while True:
            session = _sessions.get(session_id)
            if session is None:
                return
            delay = (session.expires_at - _now()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
                continue
            async with session.lock:
                if _now() < session.expires_at:
                    continue
                await _close_browser(session)
                session.screenshot_path.unlink(missing_ok=True)
                _sessions.pop(session_id, None)
                return
    except asyncio.CancelledError:
        return
    finally:
        _expiry_tasks.pop(session_id, None)


async def _select_qr_login(page: Any) -> None:
    selectors = (
        "text=扫码登录",
        "button:has-text('扫码登录')",
        "[role='tab']:has-text('扫码登录')",
        "text=二维码登录",
    )
    for selector in selectors:
        try:
            matches = page.locator(selector)
            for index in range(min(await matches.count(), 10)):
                target = matches.nth(index)
                if await target.is_visible(timeout=600):
                    await target.click(timeout=2000)
                    await page.wait_for_timeout(800)
                    return
        except Exception:
            continue


async def _browser_login_state(session: ZhihuLoginSession) -> AccountStatus:
    if session.context is None or session.page is None:
        return session.status
    cookies = await session.context.cookies()
    if has_zhihu_auth_cookie(cookies):
        return AccountStatus.online
    url = session.page.url.lower()
    body = ""
    try:
        body = (await session.page.locator("body").inner_text(timeout=2500)).lower()
    except Exception:
        pass
    challenge_markers = ("captcha", "unhuman", "安全验证", "异常流量", "完成验证")
    if any(marker in url or marker in body for marker in challenge_markers):
        return AccountStatus.verification_required
    return AccountStatus.pending_login


async def _start_browser_session(
    account: ZhihuAccount, *, mode: str
) -> ZhihuLoginSession:
    await close_account_login_sessions(account.id)
    root = account_storage_path(account.id, account.profile_key)
    browser_lock = get_account_browser_lock(account.id)
    if browser_lock.locked():
        raise ZhihuLoginError("该账号正在执行其他浏览器任务，请稍后重试")
    await browser_lock.acquire()
    session_id = uuid.uuid4()
    session = ZhihuLoginSession(
        id=session_id,
        account_id=account.id,
        status=AccountStatus.pending_login,
        message="请使用知乎手机 App 扫描二维码",
        screenshot_path=root / "screenshots" / f"login-{session_id}.png",
        created_at=_now(),
        expires_at=_now()
        + (WEBSITE_SESSION_TTL if mode == "website" else LOGIN_SESSION_TTL),
        mode=mode,
        browser_lock=browser_lock,
    )
    _sessions[session_id] = session
    _expiry_tasks[session_id] = asyncio.create_task(
        _expire_session_after_idle(session_id)
    )

    try:
        from playwright.async_api import async_playwright

        timeout_ms = await browser_timeout_ms()

        session.playwright = await async_playwright().start()
        session.context = await session.playwright.chromium.launch_persistent_context(
            user_data_dir=str(root / "browser-profile"),
            executable_path=settings.chromium_executable,
            headless=True,
            locale="zh-CN",
            timezone_id=account.timezone,
            viewport={"width": 1280, "height": 900},
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        session.page = (
            session.context.pages[0]
            if session.context.pages
            else await session.context.new_page()
        )
        session.page.set_default_timeout(timeout_ms)
        session.page.set_default_navigation_timeout(timeout_ms)
        target_url = (
            "https://www.zhihu.com/"
            if mode == "website"
            else "https://www.zhihu.com/signin?next=%2F"
        )
        await session.page.goto(
            target_url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        if mode == "login":
            await _select_qr_login(session.page)
        session.status = await _browser_login_state(session)
        if mode == "website" and session.status != AccountStatus.online:
            await session.page.goto(
                "https://www.zhihu.com/signin?next=%2F",
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            await _select_qr_login(session.page)
            session.status = await _browser_login_state(session)
        if session.status == AccountStatus.online and mode == "login":
            session.message = "知乎账号已经登录"
            await _capture(session)
            await _close_browser(session)
        else:
            if session.status == AccountStatus.online:
                session.message = "已使用当前账号的独立登录状态打开知乎官网"
            elif session.status == AccountStatus.verification_required:
                session.message = "知乎要求安全验证，请按页面提示人工完成"
            elif mode == "website":
                session.message = "当前账号登录状态已失效，请扫码后进入知乎官网"
            await _capture(session)
        return session
    except Exception as exc:
        session.status = AccountStatus.offline
        session.message = "知乎登录页面启动失败"
        await _close_browser(session)
        _sessions.pop(session_id, None)
        expiry_task = _expiry_tasks.pop(session_id, None)
        if expiry_task is not None:
            expiry_task.cancel()
        logger.exception("Failed to start Zhihu login browser for account %s", account.id)
        raise ZhihuLoginError(
            "知乎登录浏览器启动失败，请稍后重试或查看后端日志"
        ) from exc


async def start_login_session(account: ZhihuAccount) -> ZhihuLoginSession:
    return await _start_browser_session(account, mode="login")


async def start_website_session(account: ZhihuAccount) -> ZhihuLoginSession:
    return await _start_browser_session(account, mode="website")


async def poll_login_session(
    account_id: uuid.UUID, session_id: uuid.UUID
) -> ZhihuLoginSession:
    session = _sessions.get(session_id)
    if session is None or session.account_id != account_id:
        raise ZhihuLoginError("登录会话不存在或已经结束")
    async with session.lock:
        if session.status == AccountStatus.online and session.mode == "login":
            return session
        if _now() >= session.expires_at:
            session.status = AccountStatus.offline
            session.message = "登录二维码已过期，请重新发起登录"
            await _close_browser(session)
            return session
        try:
            session.status = await _browser_login_state(session)
            if session.status == AccountStatus.online:
                if session.mode == "website":
                    if "/signin" in session.page.url:
                        timeout_ms = await browser_timeout_ms()
                        await session.page.goto(
                            "https://www.zhihu.com/",
                            wait_until="domcontentloaded",
                            timeout=timeout_ms,
                        )
                    session.message = "登录成功，已进入当前账号的知乎官网"
                    session.expires_at = _now() + WEBSITE_SESSION_TTL
                    await _capture(session)
                else:
                    session.message = "扫码成功，知乎登录状态已保存"
                    await _capture(session)
                    await _close_browser(session)
            else:
                session.message = (
                    "知乎要求安全验证，请按页面提示人工完成"
                    if session.status == AccountStatus.verification_required
                    else "等待扫码，请使用知乎手机 App 扫描二维码"
                )
                await _capture(session)
        except Exception as exc:
            session.status = AccountStatus.offline
            session.message = "知乎登录页面连接中断，请重新发起登录"
            await _close_browser(session)
            raise ZhihuLoginError(session.message) from exc
        return session


async def perform_website_action(
    account_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    action: str,
    x: float | None = None,
    y: float | None = None,
    text: str | None = None,
    key: str | None = None,
    delta_y: int | None = None,
) -> ZhihuLoginSession:
    session = get_login_session(account_id, session_id)
    if session.mode != "website" or session.page is None:
        raise ZhihuLoginError("官网会话不存在或已经结束，请重新打开")
    async with session.lock:
        if _now() >= session.expires_at:
            session.status = AccountStatus.offline
            session.message = "官网会话长时间未操作，已经自动关闭"
            await _close_browser(session)
            raise ZhihuLoginError(session.message)
        page = session.page
        timeout_ms = await browser_timeout_ms()
        try:
            if action == "click":
                if x is None or y is None:
                    raise ZhihuLoginError("点击位置无效")
                await page.mouse.click(x, y)
            elif action == "type":
                if not text:
                    raise ZhihuLoginError("请输入需要发送到官网的文字")
                await page.keyboard.insert_text(text)
            elif action == "key":
                allowed_keys = {
                    "Enter",
                    "Tab",
                    "Escape",
                    "Backspace",
                    "Delete",
                    "ArrowUp",
                    "ArrowDown",
                    "ArrowLeft",
                    "ArrowRight",
                    "PageUp",
                    "PageDown",
                    "Home",
                    "End",
                    "Space",
                    "Control+A",
                }
                if key not in allowed_keys:
                    raise ZhihuLoginError("该按键操作不受支持")
                await page.keyboard.press(key)
            elif action == "scroll":
                await page.mouse.wheel(0, delta_y or 0)
            elif action == "home":
                await page.goto(
                    "https://www.zhihu.com/",
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
            elif action == "back":
                await page.go_back(wait_until="domcontentloaded", timeout=timeout_ms)
            elif action == "forward":
                await page.go_forward(
                    wait_until="domcontentloaded", timeout=timeout_ms
                )
            elif action == "reload":
                await page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
            else:
                raise ZhihuLoginError("不支持的官网操作")
            await page.wait_for_timeout(700)
            session.status = await _browser_login_state(session)
            session.message = (
                "当前账号的知乎官网登录正常"
                if session.status == AccountStatus.online
                else "当前账号尚未登录，请在页面完成扫码或官方验证"
            )
            session.expires_at = _now() + WEBSITE_SESSION_TTL
            await _capture(session)
            return session
        except ZhihuLoginError:
            raise
        except Exception as exc:
            logger.exception(
                "Zhihu website action failed for account %s", session.account_id
            )
            raise ZhihuLoginError("知乎官网操作失败，请刷新官网页面后重试") from exc


def get_login_session(
    account_id: uuid.UUID, session_id: uuid.UUID
) -> ZhihuLoginSession:
    session = _sessions.get(session_id)
    if session is None or session.account_id != account_id:
        raise ZhihuLoginError("登录会话不存在或已经结束")
    return session


async def cancel_login_session(account_id: uuid.UUID, session_id: uuid.UUID) -> None:
    session = get_login_session(account_id, session_id)
    async with session.lock:
        if session.status != AccountStatus.online:
            session.status = AccountStatus.offline
            session.message = "登录已取消"
        await _close_browser(session)
        session.screenshot_path.unlink(missing_ok=True)
    _sessions.pop(session_id, None)
    expiry_task = _expiry_tasks.pop(session_id, None)
    if expiry_task is not None:
        expiry_task.cancel()


async def close_account_login_sessions(account_id: uuid.UUID) -> None:
    session_ids = [
        session_id
        for session_id, session in _sessions.items()
        if session.account_id == account_id
    ]
    for session_id in session_ids:
        session = _sessions.pop(session_id)
        expiry_task = _expiry_tasks.pop(session_id, None)
        if expiry_task is not None:
            expiry_task.cancel()
        async with session.lock:
            await _close_browser(session)
            session.screenshot_path.unlink(missing_ok=True)


async def close_all_login_sessions() -> None:
    session_ids = list(_sessions)
    for session_id in session_ids:
        session = _sessions.pop(session_id, None)
        expiry_task = _expiry_tasks.pop(session_id, None)
        if expiry_task is not None:
            expiry_task.cancel()
        if session is not None:
            async with session.lock:
                await _close_browser(session)
                session.screenshot_path.unlink(missing_ok=True)
