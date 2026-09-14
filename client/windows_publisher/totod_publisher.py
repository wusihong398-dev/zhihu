"""TOTOD Windows local answer publisher.

Runs a visible Microsoft Edge profile per Zhihu account on the user's own PC.
It intentionally does not hide automation or bypass Zhihu security checks.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


APP_VERSION = "0.17.3"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "TOTODPublisher"
CONFIG_PATH = APP_DIR / "config.json"
PROFILE_DIR = APP_DIR / "profiles"
SCREENSHOT_DIR = APP_DIR / "screenshots"
ANSWER_EDITORS = (
    ".AnswerForm-editor .ProseMirror[contenteditable='true']",
    ".AnswerForm-editor [contenteditable='true']",
    ".ProseMirror[contenteditable='true']",
    "[contenteditable='true'][role='textbox']",
)
WRITE_BUTTONS = (
    "button:has-text('写回答')",
    "[role='button']:has-text('写回答')",
    "button:has-text('添加回答')",
    "button:has-text('回答问题')",
    "button:has-text('参与回答')",
)
PUBLISH_BUTTONS = (
    "button:has-text('发布回答')",
    "[role='button']:has-text('发布回答')",
    "button:has-text('提交回答')",
    ".AnswerForm button:has-text('发布')",
)


def find_supported_browser():
    """Return the first installed Edge/Chrome executable on Windows."""
    candidates = []
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
    program_files = os.environ.get("PROGRAMFILES")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if program_files_x86:
        candidates.extend(
            [
                ("Microsoft Edge", Path(program_files_x86) / "Microsoft/Edge/Application/msedge.exe"),
                ("Google Chrome", Path(program_files_x86) / "Google/Chrome/Application/chrome.exe"),
            ]
        )
    if program_files:
        candidates.extend(
            [
                ("Microsoft Edge", Path(program_files) / "Microsoft/Edge/Application/msedge.exe"),
                ("Google Chrome", Path(program_files) / "Google/Chrome/Application/chrome.exe"),
            ]
        )
    if local_app_data:
        candidates.extend(
            [
                ("Microsoft Edge", Path(local_app_data) / "Microsoft/Edge/Application/msedge.exe"),
                ("Google Chrome", Path(local_app_data) / "Google/Chrome/Application/chrome.exe"),
            ]
        )
    candidates.extend(
        [
            ("Microsoft Edge", Path(sys.executable).parent / "msedge.exe"),
            ("Google Chrome", Path(sys.executable).parent / "chrome.exe"),
        ]
    )
    for command, name in (
        ("msedge.exe", "Microsoft Edge"),
        ("msedge", "Microsoft Edge"),
        ("chrome.exe", "Google Chrome"),
        ("chrome", "Google Chrome"),
    ):
        resolved = shutil.which(command)
        if resolved:
            candidates.append((name, Path(resolved)))
    checked = set()
    for name, path in candidates:
        normalized = str(path).lower()
        if normalized in checked:
            continue
        checked.add(normalized)
        if path.is_file():
            return name, path
    return None


def api_request(server: str, token: str, path: str, method: str = "GET", body=None):
    url = f"{server.rstrip('/')}/api{path}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-TOTOD-Device-Token": token,
            "Content-Type": "application/json",
            "User-Agent": f"TOTOD-Windows-Publisher/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            payload = response.read()
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail")
        except Exception:
            detail = None
        raise RuntimeError(detail or f"服务器请求失败（HTTP {exc.code}）") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 TOTOD 服务器：{exc.reason}") from exc


def first_visible(page, selectors):
    for selector in selectors:
        try:
            items = page.locator(selector)
            for index in range(min(items.count(), 30)):
                item = items.nth(index)
                if item.is_visible(timeout=500):
                    return item
        except Exception:
            continue
    return None


def wait_visible(page, selectors, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        item = first_visible(page, selectors)
        if item is not None:
            return item
        page.wait_for_timeout(400)
    return None


class PublisherWorker(threading.Thread):
    def __init__(self, commands: queue.Queue, events: queue.Queue):
        super().__init__(daemon=True)
        self.commands = commands
        self.events = events
        self.running = False
        self.server = ""
        self.token = ""
        self.account_id = ""
        self.accounts = []
        self.contexts = {}
        self.playwright = None
        self.stopping = False

    def emit(self, kind, value):
        self.events.put((kind, value))

    def run(self):
        try:
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
            while not self.stopping:
                self.handle_commands()
                if self.running and self.account_id:
                    self.poll_once()
                time.sleep(1.5)
        except Exception as exc:
            self.emit("error", f"客户端启动失败：{exc}")
            self.emit("log", traceback.format_exc())
        finally:
            for context in list(self.contexts.values()):
                try:
                    context.close()
                except Exception:
                    pass
            if self.playwright:
                self.playwright.stop()

    def handle_commands(self):
        while True:
            try:
                command, payload = self.commands.get_nowait()
            except queue.Empty:
                return
            if command == "configure":
                self.server, self.token = payload
                self.load_accounts()
            elif command == "login":
                self.open_login(payload)
            elif command == "start":
                self.running = False
                self.account_id = ""
                try:
                    # The UI and worker live on different threads.  Refresh and
                    # retain the worker's own account snapshot before validating
                    # the selected account; otherwise the listener never starts.
                    if not self.load_accounts():
                        continue
                    account = next(
                        (item for item in self.accounts if item["id"] == payload), None
                    )
                    if not account or account.get("answer_publish_mode") != "local":
                        raise RuntimeError(
                            "该账号尚未启用本地发布，请在 TOTOD 后台编辑账号，"
                            "将回答发布方式改为“Windows 本地客户端”"
                        )
                    context = self.ensure_context(payload)
                    if not self.is_logged_in(context):
                        raise RuntimeError("该账号尚未在本地 Edge 登录知乎，请先点击“登录所选账号”")
                    self.account_id = payload
                    self.running = True
                    account_name = account.get("display_name") or payload[:8]
                    self.emit("log", f"开始监听账号：{account_name}（{payload[:8]}）")
                    self.emit("status", f"运行中：正在等待 {account_name} 的发布任务")
                except Exception as exc:
                    self.running = False
                    self.emit("error", str(exc))
            elif command == "stop":
                self.running = False
                self.emit("status", "已停止监听")
            elif command == "quit":
                self.stopping = True

    def load_accounts(self):
        try:
            accounts = api_request(
                self.server, self.token, "/local-publisher/client/accounts"
            ) or []
            self.accounts = accounts
            self.emit("accounts", accounts)
            local_count = sum(
                item.get("answer_publish_mode") == "local" for item in accounts
            )
            self.emit(
                "status",
                f"已连接服务器，共 {len(accounts)} 个账号，其中本地发布 {local_count} 个",
            )
            return True
        except Exception as exc:
            self.accounts = []
            self.emit("error", str(exc))
            return False

    def ensure_context(self, account_id):
        context = self.contexts.get(account_id)
        if context is not None:
            return context
        profile = PROFILE_DIR / account_id
        profile.mkdir(parents=True, exist_ok=True)
        browser = find_supported_browser()
        if browser is None:
            raise RuntimeError(
                "未找到 Microsoft Edge 或 Google Chrome。请先安装或更新其中一个浏览器，"
                "然后重新打开本客户端。"
            )
        browser_name, browser_path = browser
        self.emit("log", f"使用 {browser_name}：{browser_path}")
        context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            executable_path=str(browser_path),
            headless=False,
            locale="zh-CN",
            viewport={"width": 1440, "height": 960},
        )
        self.contexts[account_id] = context
        return context

    @staticmethod
    def is_logged_in(context):
        return any(cookie.get("name") == "z_c0" for cookie in context.cookies())

    def open_login(self, account_id):
        try:
            context = self.ensure_context(account_id)
            page = context.pages[0] if context.pages else context.new_page()
            page.bring_to_front()
            page.goto("https://www.zhihu.com/", wait_until="domcontentloaded", timeout=60000)
            self.emit("status", "知乎已在独立浏览器窗口打开；请登录后保持窗口开启")
        except Exception as exc:
            self.emit("error", f"打开浏览器失败：{exc}")

    def poll_once(self):
        try:
            account = urllib.parse.quote(self.account_id)
            task = api_request(
                self.server,
                self.token,
                f"/local-publisher/client/tasks/claim?account_id={account}",
                "POST",
            )
            if not task:
                return
            self.emit("status", f"正在发布：{task['question_title']}")
            self.emit("log", f"领取任务：{task['question_title']}")
            try:
                published_url = self.publish(task)
                api_request(
                    self.server,
                    self.token,
                    f"/local-publisher/client/tasks/{task['id']}/result",
                    "POST",
                    {"success": True, "published_url": published_url},
                )
                self.emit("log", f"发布成功：{published_url}")
            except Exception as exc:
                failure = str(exc)[:1800]
                api_request(
                    self.server,
                    self.token,
                    f"/local-publisher/client/tasks/{task['id']}/result",
                    "POST",
                    {"success": False, "error_message": failure},
                )
                self.emit("log", f"发布失败：{failure}")
            self.emit("status", "运行中：正在等待下一项任务")
        except Exception as exc:
            self.emit("error", str(exc))
            time.sleep(4)

    def publish(self, task):
        match = re.search(r"/question/(\d+)", task["question_url"])
        if not match:
            raise RuntimeError("问题链接格式不正确")
        question_id = match.group(1)
        context = self.ensure_context(task["account_id"])
        if not self.is_logged_in(context):
            raise RuntimeError("本地知乎登录已失效，请在客户端重新登录该账号")
        page = context.pages[0] if context.pages else context.new_page()
        page.bring_to_front()
        page.set_default_timeout(20000)
        responses = []
        page.on("response", lambda response: responses.append(response))
        page.goto(task["question_url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1800)
        body = page.locator("body").inner_text(timeout=5000)
        if any(text in body for text in ("安全验证", "验证码", "登录知乎")):
            raise RuntimeError("知乎要求登录或安全验证，请在打开的 Edge 窗口人工完成后重试")
        if any(text in body.replace(" ", "") for text in ("问题已关闭", "不能回答", "你已经回答过")):
            raise RuntimeError("该问题已关闭回答，或当前账号已经回答过该问题")
        editor = first_visible(page, ANSWER_EDITORS)
        if editor is None:
            button = wait_visible(page, WRITE_BUTTONS, 15)
            if button is None:
                raise RuntimeError("没有识别到“写回答”入口；请查看 Edge 页面确认问题是否允许回答")
            button.scroll_into_view_if_needed()
            button.click()
            editor = wait_visible(page, ANSWER_EDITORS, 12)
        if editor is None:
            raise RuntimeError("知乎回答编辑器没有正常打开")
        editor.click()
        try:
            editor.fill(task["content"])
        except Exception:
            editor.evaluate(
                """(node, text) => { node.focus(); node.innerText = text;
                node.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:text})); }""",
                task["content"],
            )
        page.wait_for_timeout(800)
        publish = wait_visible(page, PUBLISH_BUTTONS, 8)
        if publish is None or publish.is_disabled():
            raise RuntimeError("“发布回答”按钮不存在或不可用，请检查回答内容")
        responses.clear()
        publish.click()
        for _ in range(30):
            page.wait_for_timeout(500)
            url_match = re.search(rf"/question/{question_id}/answer/(\d+)", page.url)
            if url_match:
                return f"https://www.zhihu.com/question/{question_id}/answer/{url_match.group(1)}"
            for response in list(responses):
                if response.request.method.upper() not in {"POST", "PUT"} or "/answers" not in response.url:
                    continue
                try:
                    payload = response.json()
                except Exception:
                    continue
                message = payload.get("message") if isinstance(payload, dict) else None
                if response.status >= 400:
                    raise RuntimeError(f"知乎拒绝发布（HTTP {response.status}）：{message or '请查看页面提示'}")
                values = [payload, payload.get("data") if isinstance(payload, dict) else None]
                for value in values:
                    if isinstance(value, dict):
                        answer_id = str(value.get("id") or value.get("answer_id") or "")
                        if answer_id.isdigit():
                            return f"https://www.zhihu.com/question/{question_id}/answer/{answer_id}"
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        screenshot = SCREENSHOT_DIR / f"failure-{task['id']}.png"
        page.screenshot(path=str(screenshot), full_page=True)
        raise RuntimeError(f"知乎未返回回答编号，无法确认发布成功；截图已保存到 {screenshot}")


class App:
    def __init__(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self.root = tk.Tk()
        self.root.title(f"TOTOD Windows 本地发布客户端 v{APP_VERSION}")
        self.root.geometry("760x620")
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.worker = PublisherWorker(self.commands, self.events)
        self.accounts = []
        self.build_ui()
        self.load_config()
        self.worker.start()
        self.root.after(200, self.process_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self):
        root = ttk.Frame(self.root, padding=18)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text=f"TOTOD 本地发布客户端 v{APP_VERSION}", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="使用本机 Edge、常用网络和账号独立资料发布知乎回答").pack(anchor="w", pady=(2, 16))
        form = ttk.Frame(root)
        form.pack(fill="x")
        ttk.Label(form, text="服务器地址").grid(row=0, column=0, sticky="w")
        self.server = ttk.Entry(form)
        self.server.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(form, text="设备密钥").grid(row=0, column=1, sticky="w")
        self.token = ttk.Entry(form, show="•")
        self.token.grid(row=1, column=1, sticky="ew", padx=(0, 10))
        ttk.Button(form, text="保存并连接", command=self.connect).grid(row=1, column=2)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=2)
        ttk.Separator(root).pack(fill="x", pady=18)
        ttk.Label(root, text="知乎账号").pack(anchor="w")
        row = ttk.Frame(root)
        row.pack(fill="x", pady=(5, 10))
        self.account = ttk.Combobox(row, state="readonly")
        self.account.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(row, text="登录所选账号", command=self.login).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="刷新账号", command=self.connect).pack(side="left")
        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(controls, text="开始监听发布任务", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="停止监听", command=lambda: self.commands.put(("stop", None))).pack(side="left")
        self.status = tk.StringVar(value="请先填写服务器地址和设备密钥")
        ttk.Label(root, textvariable=self.status, foreground="#147a65").pack(anchor="w", pady=(0, 8))
        ttk.Label(root, text="运行日志").pack(anchor="w")
        self.log = tk.Text(root, height=20, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, pady=(5, 0))

    def load_config(self):
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            config = {"server": "https://totod.cn", "token": ""}
        self.server.insert(0, config.get("server", "https://totod.cn"))
        self.token.insert(0, config.get("token", ""))
        if config.get("token"):
            self.root.after(500, self.connect)

    def connect(self):
        server = self.server.get().strip().rstrip("/")
        token = self.token.get().strip()
        if not server or not token:
            messagebox.showerror("配置不完整", "请填写服务器地址和设备密钥")
            return
        CONFIG_PATH.write_text(json.dumps({"server": server, "token": token}, ensure_ascii=False), encoding="utf-8")
        self.status.set("正在连接服务器…")
        self.commands.put(("configure", (server, token)))

    def selected_id(self):
        index = self.account.current()
        return self.accounts[index]["id"] if 0 <= index < len(self.accounts) else ""

    def login(self):
        account_id = self.selected_id()
        if not account_id:
            messagebox.showwarning("请选择账号", "请先选择一个知乎账号")
            return
        self.commands.put(("login", account_id))

    def start(self):
        account_id = self.selected_id()
        if not account_id:
            messagebox.showwarning("请选择账号", "请先选择一个知乎账号")
            return
        self.commands.put(("start", account_id))

    def process_events(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "accounts":
                selected_id = self.selected_id()
                self.accounts = value
                self.account["values"] = [
                    f"{item['display_name']}  "
                    f"[{'本地发布' if item.get('answer_publish_mode') == 'local' else '需切换为本地发布'}]  "
                    f"({item['id'][:8]})"
                    for item in value
                ]
                if value:
                    selected_index = next(
                        (index for index, item in enumerate(value) if item["id"] == selected_id),
                        0,
                    )
                    self.account.current(selected_index)
            elif kind == "status":
                self.status.set(value)
            elif kind == "error":
                self.status.set(value)
                self.append_log(f"错误：{value}")
            elif kind == "log":
                self.append_log(value)
        self.root.after(200, self.process_events)

    def append_log(self, value):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {value}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def close(self):
        self.commands.put(("quit", None))
        self.root.after(150, self.root.destroy)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
