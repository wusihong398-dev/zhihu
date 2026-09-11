(() => {
  "use strict";

  const state = { token: sessionStorage.getItem("totod_token") || "", user: null, accounts: [], page: "overview" };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[character]);

  function toast(message, type = "success") {
    const item = document.createElement("div");
    item.className = `toast ${type}`;
    item.textContent = message;
    $("#toast-region").append(item);
    window.setTimeout(() => item.remove(), 3600);
  }

  async function api(path, options = {}) {
    const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const response = await fetch(`/api${path}`, { ...options, headers });
    if (response.status === 401 && path !== "/auth/login") {
      logout("登录已过期，请重新登录");
      throw new Error("登录已过期");
    }
    let data = null;
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join("；") : data.detail;
      throw new Error(detail || `请求失败（${response.status}）`);
    }
    return data;
  }

  function setBusy(button, busy, busyText = "处理中…") {
    if (!button) return;
    if (busy) {
      button.dataset.label = button.textContent;
      button.textContent = busyText;
      button.disabled = true;
    } else {
      button.textContent = button.dataset.label || button.textContent;
      button.disabled = false;
    }
  }

  function showApp() {
    $("#login-view").hidden = true;
    $("#app-view").hidden = false;
    const username = state.user?.username || "管理员";
    $("#user-name").textContent = username;
    $("#user-avatar").textContent = username.slice(0, 1).toUpperCase();
  }

  function showLogin(message = "") {
    $("#app-view").hidden = true;
    $("#login-view").hidden = false;
    $("#login-error").textContent = message;
    window.setTimeout(() => $("#username").focus(), 0);
  }

  function logout(message = "") {
    state.token = "";
    state.user = null;
    state.accounts = [];
    sessionStorage.removeItem("totod_token");
    showLogin(message);
  }

  function accountSummary(account) {
    return `<article class="account-summary">
      <div class="account-summary-head"><div><h4>${escapeHtml(account.display_name)}</h4><p>${escapeHtml(account.remark || "暂无备注")}</p></div><span class="badge ${account.enabled ? "" : "off"}">${account.enabled ? "已启用" : "已停用"}</span></div>
      <div class="quota-row"><div class="quota-box"><small>每日文章</small><strong>${account.daily_article_limit}</strong></div><div class="quota-box"><small>每日回答</small><strong>${account.daily_answer_limit}</strong></div></div>
    </article>`;
  }

  function renderOverview() {
    const enabled = state.accounts.filter((account) => account.enabled);
    $("#metric-accounts").textContent = state.accounts.length;
    $("#metric-enabled").textContent = `${enabled.length} 个已启用`;
    $("#metric-articles").textContent = enabled.reduce((sum, account) => sum + account.daily_article_limit, 0);
    $("#metric-answers").textContent = enabled.reduce((sum, account) => sum + account.daily_answer_limit, 0);
    const items = state.accounts.slice(0, 6);
    $("#overview-accounts").innerHTML = items.map(accountSummary).join("");
    $("#overview-accounts").hidden = items.length === 0;
    $("#overview-empty").hidden = items.length !== 0;
  }

  function accountRow(account) {
    return `<div class="account-row" data-account-id="${account.id}">
      <div class="account-name"><strong>${escapeHtml(account.display_name)}</strong><small>${escapeHtml(account.remark || "暂无备注")}</small></div>
      <label class="inline-field"><input class="article-input" aria-label="${escapeHtml(account.display_name)}每日文章数量" type="number" min="0" max="100" value="${account.daily_article_limit}"><span>篇/天</span></label>
      <label class="inline-field"><input class="answer-input" aria-label="${escapeHtml(account.display_name)}每日回答数量" type="number" min="0" max="200" value="${account.daily_answer_limit}"><span>个/天</span></label>
      <label class="switch" title="启用或停用账号"><input class="enabled-input" type="checkbox" ${account.enabled ? "checked" : ""} aria-label="启用${escapeHtml(account.display_name)}"><i></i></label>
      <div class="row-actions"><button class="button button-ghost save-account">保存设置</button></div>
    </div>`;
  }

  function renderAccounts() {
    const query = $("#account-search").value.trim().toLowerCase();
    const filtered = state.accounts.filter((account) => `${account.display_name} ${account.remark}`.toLowerCase().includes(query));
    $("#account-count").textContent = `共 ${state.accounts.length} 个账号`;
    const table = $("#accounts-table");
    table.innerHTML = filtered.length ? `<div class="account-row header"><span>账号</span><span>每日文章</span><span>每日回答</span><span>状态</span><span></span></div>${filtered.map(accountRow).join("")}` : "";
    $("#accounts-empty").hidden = state.accounts.length !== 0 || query !== "";
    $$(".save-account", table).forEach((button) => button.addEventListener("click", saveAccount));
  }

  function renderAll() {
    renderOverview();
    renderAccounts();
  }

  async function loadAccounts(silent = false) {
    try {
      state.accounts = await api("/accounts");
      renderAll();
      if (!silent) toast("账号数据已刷新");
    } catch (error) {
      if (state.token) toast(error.message, "error");
    }
  }

  async function saveAccount(event) {
    const button = event.currentTarget;
    const row = button.closest(".account-row");
    const accountId = row.dataset.accountId;
    const payload = {
      daily_article_limit: Number($(".article-input", row).value),
      daily_answer_limit: Number($(".answer-input", row).value),
      enabled: $(".enabled-input", row).checked
    };
    setBusy(button, true, "保存中…");
    try {
      await api(`/accounts/${accountId}`, { method: "PATCH", body: JSON.stringify(payload) });
      await loadAccounts(true);
      toast("账号设置已保存");
    } catch (error) {
      toast(error.message, "error");
      setBusy(button, false);
    }
  }

  function openAccountDialog() {
    $("#account-form").reset();
    $("#article-limit").value = 3;
    $("#answer-limit").value = 5;
    $("#account-error").textContent = "";
    $("#account-dialog").hidden = false;
    window.setTimeout(() => $("#display-name").focus(), 0);
  }

  function closeAccountDialog() { $("#account-dialog").hidden = true; }

  async function createAccount(event) {
    event.preventDefault();
    const submit = $("#account-submit");
    const payload = {
      display_name: $("#display-name").value.trim(),
      remark: $("#remark").value.trim(),
      daily_article_limit: Number($("#article-limit").value),
      daily_answer_limit: Number($("#answer-limit").value),
      timezone: "Asia/Shanghai"
    };
    setBusy(submit, true, "保存中…");
    $("#account-error").textContent = "";
    try {
      await api("/accounts", { method: "POST", body: JSON.stringify(payload) });
      closeAccountDialog();
      await loadAccounts(true);
      toast("知乎账号已添加");
    } catch (error) {
      $("#account-error").textContent = error.message;
    } finally {
      setBusy(submit, false);
    }
  }

  function navigate(page) {
    state.page = page;
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
    $$(".page").forEach((item) => item.classList.toggle("active-page", item.id === `${page}-page`));
    $("#page-title").textContent = page === "accounts" ? "知乎账号" : "运行概览";
    $(".sidebar").classList.remove("open");
  }

  async function login(event) {
    event.preventDefault();
    const button = $("#login-button");
    $("#login-error").textContent = "";
    setBusy(button, true, "正在登录…");
    try {
      const data = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: $("#username").value.trim(), password: $("#password").value })
      });
      state.token = data.access_token;
      state.user = data.user;
      sessionStorage.setItem("totod_token", state.token);
      $("#password").value = "";
      showApp();
      await loadAccounts(true);
      toast("登录成功");
    } catch (error) {
      $("#login-error").textContent = error.message;
    } finally {
      setBusy(button, false);
    }
  }

  async function boot() {
    $("#login-form").addEventListener("submit", login);
    $("#logout-button").addEventListener("click", () => logout());
    $("#refresh-button").addEventListener("click", () => loadAccounts());
    $("#account-form").addEventListener("submit", createAccount);
    $("#account-search").addEventListener("input", renderAccounts);
    $("#dialog-close").addEventListener("click", closeAccountDialog);
    $("#dialog-cancel").addEventListener("click", closeAccountDialog);
    $("#account-dialog").addEventListener("click", (event) => { if (event.target.id === "account-dialog") closeAccountDialog(); });
    $("#menu-button").addEventListener("click", () => $(".sidebar").classList.toggle("open"));
    $$('[data-open-account]').forEach((button) => button.addEventListener("click", openAccountDialog));
    $$(".nav-item").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.page)));
    $$('[data-page-link]').forEach((button) => button.addEventListener("click", () => navigate(button.dataset.pageLink)));
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeAccountDialog(); });

    try {
      const version = await api("/version");
      $("#sidebar-version").textContent = `版本 ${version.version}`;
    } catch { $("#sidebar-version").textContent = "版本检测失败"; }

    if (!state.token) return showLogin();
    try {
      state.user = await api("/auth/me");
      showApp();
      await loadAccounts(true);
    } catch { showLogin("请重新登录"); }
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
