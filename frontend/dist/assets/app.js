(() => {
  "use strict";

  const state = { token: sessionStorage.getItem("totod_token") || "", user: null, users: [], editingUserId: null, accounts: [], editingAccountId: null, products: [], editingProductId: null, providers: [], keywordAccountId: "", keywords: { items: [], total: 0 }, keywordFolders: [], selectedKeywords: new Set(), keywordPage: 1, keywordPageSize: 100, keywordJob: null, recycledKeywords: { items: [], total: 0 }, selectedRecycledKeywords: new Set(), recycleKeywordPage: 1, mediaFolders: [], media: { items: [], total: 0 }, selectedMedia: new Set(), mediaPage: 1, mediaPageSize: 100, mediaUploadRunning: false, articles: { items: [], total: 0 }, selectedArticles: new Set(), articlePage: 1, articlePageSize: 100, editingArticle: null, articleGenerateKeywords: [], selectedArticleKeywords: new Set(), articleGenerateProducts: [], promptFolders: [], promptTemplates: [], generationJob: null, publishJob: null, articleJobTimers: { generate: null, publish: null }, page: "overview", pollTimer: null, articleSearchTimer: null, zhihuLoginTimer: null, zhihuLogin: null, zhihuBrowserBusy: false, zhihuScreenshotUrl: "", zhihuScreenshotVersion: -1 };
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
    const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
    const headers = { ...(options.body && !isFormData ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) };
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

  async function apiBlob(path) {
    const headers = state.token ? { Authorization: `Bearer ${state.token}` } : {};
    const response = await fetch(`/api${path}`, { headers, cache: "no-store" });
    if (response.status === 401) {
      logout("登录已过期，请重新登录");
      throw new Error("登录已过期");
    }
    if (!response.ok) {
      let message = `请求失败（${response.status}）`;
      try { message = (await response.json()).detail || message; } catch { /* 图片接口可能不返回 JSON */ }
      throw new Error(message);
    }
    return response.blob();
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
    const isAdmin = state.user?.role === "admin";
    $("#user-role-label").textContent = isAdmin ? "系统管理员" : "授权用户";
    $$(".admin-only").forEach((element) => { element.hidden = !isAdmin; });
  }

  function showLogin(message = "") {
    $("#app-view").hidden = true;
    $("#login-view").hidden = false;
    $("#login-error").textContent = message;
    window.setTimeout(() => $("#username").focus(), 0);
  }

  function logout(message = "") {
    window.clearTimeout(state.pollTimer);
    window.clearTimeout(state.zhihuLoginTimer);
    window.clearTimeout(state.articleJobTimers.generate);
    window.clearTimeout(state.articleJobTimers.publish);
    if (state.zhihuScreenshotUrl) URL.revokeObjectURL(state.zhihuScreenshotUrl);
    state.zhihuLogin = null;
    state.zhihuScreenshotUrl = "";
    state.token = "";
    state.user = null;
    state.users = [];
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
    const statusMap = {
      pending_login: ["待登录", "pending"], online: ["已登录", ""], offline: ["已离线", "off"],
      verification_required: ["需要验证", "warning"], restricted: ["账号受限", "danger"], paused: ["已暂停", "off"]
    };
    const [statusLabel, statusClass] = statusMap[account.status] || ["未知", "off"];
    return `<div class="account-row" data-account-id="${account.id}">
      <div class="account-name"><strong>${escapeHtml(account.display_name)}</strong><small>${escapeHtml(account.remark || "暂无备注")}</small></div>
      <label class="inline-field"><input class="article-input" aria-label="${escapeHtml(account.display_name)}每日文章数量" type="number" min="0" max="100" value="${account.daily_article_limit}"><span>篇/天</span></label>
      <label class="inline-field"><input class="answer-input" aria-label="${escapeHtml(account.display_name)}每日回答数量" type="number" min="0" max="200" value="${account.daily_answer_limit}"><span>个/天</span></label>
      <span class="badge login-status ${statusClass}">${statusLabel}</span>
      <label class="switch" title="启用或停用账号"><input class="enabled-input" type="checkbox" ${account.enabled ? "checked" : ""} aria-label="启用${escapeHtml(account.display_name)}"><i></i></label>
      <div class="row-actions"><button class="button ${account.status === "online" ? "button-ghost" : "button-primary"} login-account" type="button">${account.status === "online" ? "重新登录" : "扫码登录"}</button><button class="button ${account.status === "online" ? "button-primary" : "button-ghost"} open-website-account" type="button">登录官网</button><button class="button button-ghost save-account" type="button">保存设置</button><button class="button button-ghost edit-account" type="button">编辑</button><button class="button button-ghost danger-text delete-account" type="button">删除</button></div>
    </div>`;
  }

  function renderAccounts() {
    const query = $("#account-search").value.trim().toLowerCase();
    const filtered = state.accounts.filter((account) => `${account.display_name} ${account.remark}`.toLowerCase().includes(query));
    $("#account-count").textContent = `共 ${state.accounts.length} 个账号`;
    const table = $("#accounts-table");
    table.innerHTML = filtered.length ? `<div class="account-row header"><span>账号</span><span>每日文章</span><span>每日回答</span><span>知乎登录</span><span>启用</span><span>账号操作</span></div>${filtered.map(accountRow).join("")}` : "";
    $("#accounts-empty").hidden = state.accounts.length !== 0 || query !== "";
    $$(".save-account", table).forEach((button) => button.addEventListener("click", saveAccount));
    $$(".login-account", table).forEach((button) => button.addEventListener("click", openZhihuLogin));
    $$(".open-website-account", table).forEach((button) => button.addEventListener("click", openZhihuWebsite));
    $$(".edit-account", table).forEach((button) => button.addEventListener("click", editAccount));
    $$(".delete-account", table).forEach((button) => button.addEventListener("click", deleteAccount));
  }

  function renderAll() {
    renderOverview();
    renderAccounts();
    renderKeywordAccountOptions();
    renderProductAccountOptions();
    renderArticleAccountOptions();
  }

  async function loadAccounts(silent = false) {
    try {
      state.accounts = await api("/accounts");
      renderAll();
      if (["keyword-collect", "keywords", "keyword-recycle"].includes(state.page) && state.keywordAccountId) await loadKeywordData(true);
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

  function setZhihuLoginMessage(session) {
    const labelMap = { pending_login: "等待扫码", online: "登录成功", offline: "登录已结束", verification_required: "需要人工验证" };
    $("#zhihu-login-state").textContent = session.mode === "website" && session.status === "online" ? "官网已登录" : labelMap[session.status] || "正在连接";
    $("#zhihu-login-message").textContent = session.message || "请稍候";
    $("#zhihu-browser-url").textContent = session.page_url || "正在打开知乎官网";
    $("#zhihu-login-state").className = `badge login-status ${session.status === "online" ? "" : session.status === "verification_required" ? "warning" : session.status === "offline" ? "danger" : "pending"}`;
  }

  async function refreshZhihuLoginScreenshot(force = false) {
    const session = state.zhihuLogin;
    if (!session || (!force && session.screenshot_version === state.zhihuScreenshotVersion)) return;
    const blob = await apiBlob(`/accounts/${session.account_id}/login-session/${session.session_id}/screenshot?v=${session.screenshot_version}`);
    if (state.zhihuScreenshotUrl) URL.revokeObjectURL(state.zhihuScreenshotUrl);
    state.zhihuScreenshotUrl = URL.createObjectURL(blob);
    state.zhihuScreenshotVersion = session.screenshot_version;
    $("#zhihu-login-screenshot").src = state.zhihuScreenshotUrl;
    $("#zhihu-login-screenshot").hidden = false;
    $("#zhihu-login-loading").hidden = true;
  }

  function scheduleZhihuLoginPoll() {
    window.clearTimeout(state.zhihuLoginTimer);
    state.zhihuLoginTimer = window.setTimeout(pollZhihuLogin, 2500);
  }

  async function pollZhihuLogin() {
    const current = state.zhihuLogin;
    if (!current || $("#zhihu-login-dialog").hidden) return;
    try {
      const session = await api(`/accounts/${current.account_id}/login-session/${current.session_id}`);
      if (!state.zhihuLogin || state.zhihuLogin.session_id !== session.session_id) return;
      state.zhihuLogin = session;
      setZhihuLoginMessage(session);
      await refreshZhihuLoginScreenshot();
      if (session.status === "online") {
        await loadAccounts(true);
        toast(session.mode === "website" ? "当前账号的知乎官网已打开" : "知乎账号登录成功，独立登录状态已保存");
        return;
      }
      if (session.status === "offline") return;
      scheduleZhihuLoginPoll();
    } catch (error) {
      $("#zhihu-login-message").textContent = error.message;
      $("#zhihu-login-state").textContent = "连接失败";
      $("#zhihu-login-state").className = "badge login-status danger";
    }
  }

  async function openZhihuLogin(event) {
    const button = event.currentTarget;
    const row = button.closest(".account-row");
    const accountId = row.dataset.accountId;
    const account = state.accounts.find((item) => item.id === accountId);
    setBusy(button, true, "打开登录页…");
    try {
      const session = await api(`/accounts/${accountId}/login-session`, { method: "POST" });
      await showZhihuBrowserSession(session, account, "login");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  async function openZhihuWebsite(event) {
    const button = event.currentTarget;
    const row = button.closest(".account-row");
    const accountId = row.dataset.accountId;
    const account = state.accounts.find((item) => item.id === accountId);
    setBusy(button, true, "打开官网…");
    try {
      const session = await api(`/accounts/${accountId}/website-session`, { method: "POST" });
      await showZhihuBrowserSession(session, account, "website");
      if (session.status === "online") await loadAccounts(true);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  async function showZhihuBrowserSession(session, account, mode) {
    state.zhihuLogin = session;
    state.zhihuScreenshotVersion = -1;
    $("#zhihu-login-dialog").dataset.mode = mode;
    $("#zhihu-login-title").textContent = `${mode === "website" ? "知乎官网" : "登录知乎"} · ${account?.display_name || "账号"}`;
    $("#zhihu-browser-controls").hidden = mode !== "website";
    $("#zhihu-login-help").textContent = mode === "website"
      ? "这是当前账号在服务器中的独立知乎官网。直接点击网页画面操作；先点输入位置，再在下方输入文字并发送。关闭窗口不会退出知乎账号。"
      : "请使用知乎手机 App 扫码。二维码和登录状态仅属于当前账号；系统不会向网页返回 Cookie。若知乎要求安全验证，请按官方提示人工完成。";
    $("#zhihu-login-screenshot").classList.toggle("interactive", mode === "website");
    $("#zhihu-login-screenshot").hidden = true;
    $("#zhihu-login-loading").hidden = false;
    $("#zhihu-login-dialog").hidden = false;
    setZhihuLoginMessage(session);
    await refreshZhihuLoginScreenshot(true);
    if (session.status !== "online" && session.status !== "offline") scheduleZhihuLoginPoll();
    else if (session.status === "online") await loadAccounts(true);
  }

  async function sendZhihuBrowserAction(payload, button = null) {
    const session = state.zhihuLogin;
    if (!session || session.mode !== "website" || state.zhihuBrowserBusy) return;
    state.zhihuBrowserBusy = true;
    $("#zhihu-login-screenshot").classList.add("busy");
    if (button) setBusy(button, true, "处理中…");
    try {
      const updated = await api(`/accounts/${session.account_id}/login-session/${session.session_id}/browser-action`, { method: "POST", body: JSON.stringify(payload) });
      if (!state.zhihuLogin || state.zhihuLogin.session_id !== updated.session_id) return;
      state.zhihuLogin = updated;
      setZhihuLoginMessage(updated);
      await refreshZhihuLoginScreenshot(true);
      await loadAccounts(true);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      state.zhihuBrowserBusy = false;
      $("#zhihu-login-screenshot").classList.remove("busy");
      if (button) setBusy(button, false);
    }
  }

  function clickZhihuWebsite(event) {
    const session = state.zhihuLogin;
    const image = event.currentTarget;
    if (!session || session.mode !== "website" || !image.naturalWidth || state.zhihuBrowserBusy) return;
    const bounds = image.getBoundingClientRect();
    const scale = Math.min(bounds.width / image.naturalWidth, bounds.height / image.naturalHeight);
    const renderedWidth = image.naturalWidth * scale;
    const renderedHeight = image.naturalHeight * scale;
    const left = bounds.left + (bounds.width - renderedWidth) / 2;
    const top = bounds.top + (bounds.height - renderedHeight) / 2;
    const x = (event.clientX - left) / scale;
    const y = (event.clientY - top) / scale;
    if (x < 0 || y < 0 || x > image.naturalWidth || y > image.naturalHeight) return;
    sendZhihuBrowserAction({ action: "click", x, y });
  }

  function sendZhihuBrowserText() {
    const input = $("#zhihu-browser-text");
    if (!input.value) return toast("请先输入文字", "error");
    const text = input.value;
    input.value = "";
    sendZhihuBrowserAction({ action: "type", text }, $("#zhihu-browser-send"));
  }

  function closeZhihuLoginDialog() {
    window.clearTimeout(state.zhihuLoginTimer);
    const session = state.zhihuLogin;
    if (session) {
      api(`/accounts/${session.account_id}/login-session/${session.session_id}`, { method: "DELETE" }).catch(() => {});
    }
    state.zhihuLogin = null;
    state.zhihuBrowserBusy = false;
    state.zhihuScreenshotVersion = -1;
    if (state.zhihuScreenshotUrl) URL.revokeObjectURL(state.zhihuScreenshotUrl);
    state.zhihuScreenshotUrl = "";
    $("#zhihu-login-screenshot").removeAttribute("src");
    $("#zhihu-login-screenshot").classList.remove("interactive", "busy");
    $("#zhihu-login-dialog").hidden = true;
  }

  function openZhihuLoginScreenshot() {
    if (!state.zhihuScreenshotUrl) {
      toast("登录页面截图尚未生成", "error");
      return;
    }
    window.open(state.zhihuScreenshotUrl, "_blank", "noopener,noreferrer");
  }

  function openAccountDialog(account = null) {
    const editing = account && account.id ? account : null;
    state.editingAccountId = editing?.id || null;
    $("#account-form").reset();
    $("#dialog-title").textContent = editing ? "编辑知乎账号" : "添加知乎账号";
    $("#account-submit").textContent = editing ? "保存修改" : "保存账号";
    $("#display-name").value = editing?.display_name || "";
    $("#remark").value = editing?.remark || "";
    $("#article-limit").value = editing?.daily_article_limit ?? 3;
    $("#answer-limit").value = editing?.daily_answer_limit ?? 5;
    $("#account-timezone").value = editing?.timezone || "Asia/Shanghai";
    $("#account-error").textContent = "";
    $("#account-dialog").hidden = false;
    window.setTimeout(() => $("#display-name").focus(), 0);
  }

  function closeAccountDialog() { $("#account-dialog").hidden = true; state.editingAccountId = null; }

  function editAccount(event) {
    const accountId = event.currentTarget.closest(".account-row").dataset.accountId;
    openAccountDialog(state.accounts.find((item) => item.id === accountId));
  }

  async function deleteAccount(event) {
    const accountId = event.currentTarget.closest(".account-row").dataset.accountId;
    const account = state.accounts.find((item) => item.id === accountId);
    if (!account || !window.confirm(`确定删除知乎账号“${account.display_name}”吗？\n\n该账号下的登录资料、商品、关键词和文章将一并删除，且无法恢复。`)) return;
    const button = event.currentTarget;
    setBusy(button, true, "删除中…");
    try {
      await api(`/accounts/${accountId}`, { method: "DELETE" });
      await loadAccounts(true);
      toast("知乎账号及其独立数据已删除");
    } catch (error) {
      toast(error.message, "error");
      setBusy(button, false);
    }
  }

  async function createAccount(event) {
    event.preventDefault();
    const submit = $("#account-submit");
    const payload = {
      display_name: $("#display-name").value.trim(),
      remark: $("#remark").value.trim(),
      daily_article_limit: Number($("#article-limit").value),
      daily_answer_limit: Number($("#answer-limit").value),
      timezone: $("#account-timezone").value.trim() || "Asia/Shanghai"
    };
    setBusy(submit, true, "保存中…");
    $("#account-error").textContent = "";
    try {
      const editing = Boolean(state.editingAccountId);
      await api(editing ? `/accounts/${state.editingAccountId}` : "/accounts", { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) });
      closeAccountDialog();
      await loadAccounts(true);
      toast(editing ? "知乎账号已修改，登录状态保持不变" : "知乎账号已添加");
    } catch (error) {
      $("#account-error").textContent = error.message;
    } finally {
      setBusy(submit, false);
    }
  }

  function renderProductAccountOptions() {
    const select = $("#product-account");
    const formSelect = $("#product-form-account");
    const previous = select.value;
    const options = state.accounts.map((account) => `<option value="${account.id}">${escapeHtml(account.display_name)}</option>`).join("");
    select.innerHTML = `<option value="">请先选择账号</option>${options}`;
    formSelect.innerHTML = `<option value="">请选择账号</option>${options}`;
    if (state.accounts.some((account) => account.id === previous)) select.value = previous;
    else if (state.accounts.length) select.value = state.accounts[0].id;
  }

  function productCard(product) {
    const points = product.selling_points.split(/\r?\n/).map((item) => item.trim()).filter(Boolean).slice(0, 3);
    const details = points.length ? points.map((point) => `<li>${escapeHtml(point)}</li>`).join("") : `<li>${escapeHtml(product.description || "尚未填写商品卖点")}</li>`;
    const link = product.promotion_url
      ? `<a href="${escapeHtml(product.promotion_url)}" target="_blank" rel="noopener noreferrer">打开推广链接 ↗</a>`
      : `<span>尚未设置推广链接</span>`;
    return `<article class="product-card" data-product-id="${product.id}">
      <div class="product-card-head"><div><span>${escapeHtml(product.category || "未分类")}</span><h3>${escapeHtml(product.name)}</h3></div><button class="badge product-toggle ${product.enabled ? "" : "off"}" type="button">${product.enabled ? "已启用" : "已停用"}</button></div>
      <p class="product-description">${escapeHtml(product.description || "暂无商品简介")}</p>
      <div class="product-card-section"><strong>核心卖点</strong><ul>${details}</ul></div>
      <div class="product-audience"><span>目标人群</span><p>${escapeHtml(product.target_audience || "尚未设置")}</p></div>
      <div class="product-card-foot">${link}<div><button class="button button-ghost product-edit" type="button">编辑</button><button class="button button-ghost danger-text product-delete" type="button">删除</button></div></div>
    </article>`;
  }

  function renderProducts() {
    const query = $("#product-search").value.trim().toLowerCase();
    const filtered = state.products.filter((product) => `${product.name} ${product.category} ${product.description} ${product.selling_points}`.toLowerCase().includes(query));
    $("#product-count").textContent = query ? `找到 ${filtered.length} / ${state.products.length} 个商品` : `共 ${state.products.length} 个商品`;
    $("#product-list").innerHTML = filtered.map(productCard).join("");
    $("#product-list").hidden = filtered.length === 0;
    $("#product-empty").hidden = filtered.length !== 0;
    $("#product-empty h3").textContent = query ? "没有匹配的推广商品" : $("#product-account").value ? "还没有推广商品" : "请先选择知乎账号";
    $("#product-empty p").textContent = query ? "请更换搜索词，或添加新的推广商品。" : $("#product-account").value ? "添加该账号需要推广的商品资料，后续生成内容时即可直接选择。" : "推广商品按账号独立保存，请先选择一个知乎账号。";
    $$(".product-edit", $("#product-list")).forEach((button) => button.addEventListener("click", () => openProductDialog(state.products.find((product) => product.id === button.closest(".product-card").dataset.productId))));
    $$(".product-delete", $("#product-list")).forEach((button) => button.addEventListener("click", () => deleteProduct(button.closest(".product-card").dataset.productId)));
    $$(".product-toggle", $("#product-list")).forEach((button) => button.addEventListener("click", () => toggleProduct(button.closest(".product-card").dataset.productId)));
  }

  async function loadProducts(silent = false) {
    const accountId = $("#product-account").value;
    if (!accountId) {
      state.products = [];
      renderProducts();
      return;
    }
    try {
      const result = await api(`/accounts/${accountId}/products?limit=500`);
      state.products = result.items;
      renderProducts();
      if (!silent) toast("推广商品已刷新");
    } catch (error) { toast(error.message, "error"); }
  }

  function openProductDialog(product = null) {
    if (!state.accounts.length) return toast("请先添加知乎账号", "error");
    $("#product-form").reset();
    state.editingProductId = product?.id || null;
    $("#product-dialog-title").textContent = product ? "编辑推广商品" : "添加推广商品";
    $("#product-form-account").value = product?.account_id || $("#product-account").value || state.accounts[0].id;
    $("#product-form-account").disabled = Boolean(product);
    $("#product-name").value = product?.name || "";
    $("#product-category").value = product?.category || "";
    $("#product-description").value = product?.description || "";
    $("#product-selling-points").value = product?.selling_points || "";
    $("#product-target-audience").value = product?.target_audience || "";
    $("#product-url").value = product?.promotion_url || "";
    $("#product-requirements").value = product?.content_requirements || "";
    $("#product-forbidden").value = product?.forbidden_terms || "";
    $("#product-enabled").checked = product ? product.enabled : true;
    $("#product-error").textContent = "";
    $("#product-dialog").hidden = false;
    window.setTimeout(() => $("#product-name").focus(), 0);
  }

  function closeProductDialog() {
    $("#product-dialog").hidden = true;
    state.editingProductId = null;
  }

  async function saveProduct(event) {
    event.preventDefault();
    const submit = $("#product-submit");
    const accountId = $("#product-form-account").value;
    if (!accountId) return $("#product-error").textContent = "请选择知乎账号";
    const payload = {
      name: $("#product-name").value.trim(),
      category: $("#product-category").value.trim(),
      description: $("#product-description").value.trim(),
      selling_points: $("#product-selling-points").value.trim(),
      target_audience: $("#product-target-audience").value.trim(),
      promotion_url: $("#product-url").value.trim(),
      content_requirements: $("#product-requirements").value.trim(),
      forbidden_terms: $("#product-forbidden").value.trim(),
      enabled: $("#product-enabled").checked
    };
    setBusy(submit, true, "保存中…");
    $("#product-error").textContent = "";
    try {
      const wasEditing = Boolean(state.editingProductId);
      if (wasEditing) await api(`/accounts/${accountId}/products/${state.editingProductId}`, { method: "PATCH", body: JSON.stringify(payload) });
      else await api(`/accounts/${accountId}/products`, { method: "POST", body: JSON.stringify(payload) });
      $("#product-account").value = accountId;
      closeProductDialog();
      await loadProducts(true);
      toast(wasEditing ? "推广商品已更新" : "推广商品已添加");
    } catch (error) { $("#product-error").textContent = error.message; }
    finally { setBusy(submit, false); }
  }

  async function toggleProduct(productId) {
    const product = state.products.find((item) => item.id === productId);
    if (!product) return;
    try {
      await api(`/accounts/${product.account_id}/products/${product.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !product.enabled }) });
      await loadProducts(true);
      toast(product.enabled ? "商品已停用" : "商品已启用");
    } catch (error) { toast(error.message, "error"); }
  }

  async function deleteProduct(productId) {
    const product = state.products.find((item) => item.id === productId);
    if (!product || !window.confirm(`确定删除推广商品“${product.name}”吗？`)) return;
    try {
      await api(`/accounts/${product.account_id}/products/${product.id}`, { method: "DELETE" });
      await loadProducts(true);
      toast("推广商品已删除");
    } catch (error) { toast(error.message, "error"); }
  }

  const formatDateTime = (value) => value ? new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)) : "长期有效";

  function userAuthorization(user) {
    if (!user.is_active) return { label: "已停用", className: "off" };
    if (user.expires_at && new Date(user.expires_at) <= new Date()) return { label: "已到期", className: "expired" };
    return { label: "授权有效", className: "" };
  }

  function renderUsers() {
    const query = $("#user-search").value.trim().toLowerCase();
    const filtered = state.users.filter((user) => user.username.toLowerCase().includes(query));
    const now = Date.now();
    const sevenDays = now + 7 * 24 * 60 * 60 * 1000;
    const active = state.users.filter((user) => user.is_active && (!user.expires_at || new Date(user.expires_at).getTime() > now));
    const expiring = active.filter((user) => user.expires_at && new Date(user.expires_at).getTime() <= sevenDays);
    $("#metric-users").textContent = state.users.length;
    $("#metric-users-active").textContent = active.length;
    $("#metric-users-expiring").textContent = expiring.length;
    $("#metric-user-accounts").textContent = state.users.reduce((sum, user) => sum + user.account_count, 0);
    $("#user-count").textContent = `共 ${state.users.length} 个用户`;
    $("#users-table").innerHTML = filtered.length ? `<div class="user-row header"><span>登录用户</span><span>授权状态</span><span>到期时间</span><span>知乎账号</span><span>最近登录</span><span>操作</span></div>${filtered.map((user) => {
      const authorization = userAuthorization(user);
      return `<div class="user-row" data-user-id="${user.id}"><div class="account-name"><strong>${escapeHtml(user.username)}</strong><small>创建于 ${formatDateTime(user.created_at)}</small></div><span class="badge ${authorization.className}">${authorization.label}</span><span class="user-date">${formatDateTime(user.expires_at)}</span><strong>${user.account_count} 个</strong><span class="user-date">${user.last_login_at ? formatDateTime(user.last_login_at) : "尚未登录"}</span><div class="row-actions"><button class="button button-ghost user-edit" type="button">编辑授权</button><button class="button button-ghost user-reset-password" type="button">重置密码</button></div></div>`;
    }).join("")}` : "";
    $("#users-empty").hidden = filtered.length !== 0;
    $("#users-empty h3").textContent = query ? "没有匹配的用户" : "还没有普通用户";
    $("#users-empty p").textContent = query ? "请更换搜索用户名。" : "添加用户后，对方可以使用自己的账号和数据空间。";
    $$(".user-edit", $("#users-table")).forEach((button) => button.addEventListener("click", () => openUserDialog(state.users.find((user) => user.id === button.closest(".user-row").dataset.userId))));
    $$(".user-reset-password", $("#users-table")).forEach((button) => button.addEventListener("click", () => resetManagedUserPassword(button.closest(".user-row").dataset.userId)));
  }

  async function loadUsers(silent = false) {
    if (state.user?.role !== "admin") return;
    try {
      state.users = await api("/users");
      renderUsers();
      if (!silent) toast("用户数据已刷新");
    } catch (error) { toast(error.message, "error"); }
  }

  function toLocalDateTimeInput(value) {
    const date = value ? new Date(value) : new Date(Date.now() + 30 * 24 * 60 * 60 * 1000);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  function openUserDialog(user = null) {
    $("#user-form").reset();
    state.editingUserId = user?.id || null;
    $("#user-dialog-title").textContent = user ? "编辑用户授权" : "添加用户";
    $("#managed-username").value = user?.username || "";
    $("#managed-username").disabled = Boolean(user);
    $("#managed-password-field").hidden = false;
    $("#managed-password").required = !user;
    $("#managed-password-label").textContent = user ? "新密码（留空则不修改）" : "初始密码";
    $("#managed-password").placeholder = user ? "需要重置时填写，至少 12 位" : "至少 12 位";
    $("#managed-expires").value = user ? (user.expires_at ? toLocalDateTimeInput(user.expires_at) : "") : toLocalDateTimeInput();
    $("#managed-active").checked = user ? user.is_active : true;
    $("#user-error").textContent = "";
    $("#user-dialog").hidden = false;
    window.setTimeout(() => $(user ? "#managed-expires" : "#managed-username").focus(), 0);
  }

  function closeUserDialog() {
    $("#user-dialog").hidden = true;
    state.editingUserId = null;
  }

  async function saveManagedUser(event) {
    event.preventDefault();
    const submit = $("#user-submit");
    const expiration = $("#managed-expires").value;
    const payload = { expires_at: expiration ? new Date(expiration).toISOString() : null, is_active: $("#managed-active").checked };
    const wasEditing = Boolean(state.editingUserId);
    if (!wasEditing) {
      payload.username = $("#managed-username").value.trim();
      payload.password = $("#managed-password").value;
    }
    setBusy(submit, true, "保存中…");
    $("#user-error").textContent = "";
    try {
      if (wasEditing) {
        const userId = state.editingUserId;
        await api(`/users/${userId}`, { method: "PATCH", body: JSON.stringify(payload) });
        if ($("#managed-password").value) await api(`/users/${userId}/reset-password`, { method: "POST", body: JSON.stringify({ password: $("#managed-password").value }) });
      }
      else await api("/users", { method: "POST", body: JSON.stringify(payload) });
      closeUserDialog();
      await loadUsers(true);
      toast(wasEditing ? "用户授权已更新" : "用户已创建");
    } catch (error) { $("#user-error").textContent = error.message; }
    finally { setBusy(submit, false); }
  }

  function resetManagedUserPassword(userId) {
    const user = state.users.find((item) => item.id === userId);
    if (!user) return;
    openUserDialog(user);
    window.setTimeout(() => $("#managed-password").focus(), 0);
  }

  function providerCard(provider) {
    const options = [...new Set([provider.model, ...provider.models])]
      .map((model) => `<option value="${escapeHtml(model)}">`)
      .join("");
    const resultClass = provider.last_test_ok === true ? "ok" : provider.last_test_ok === false ? "fail" : "";
    const resultText = provider.last_test_message || "保存后可测试 API Key 和模型是否可用。";
    return `<article class="provider-card" data-provider="${provider.provider}">
      <div class="provider-head"><span class="provider-logo">${provider.provider === "openai" ? "O" : provider.provider === "deepseek" ? "D" : "火"}</span><div><h3>${escapeHtml(provider.display_name)}</h3><p>${escapeHtml(provider.base_url)}</p></div><span class="badge provider-state ${provider.enabled ? "" : "off"}">${provider.enabled ? "已启用" : "未启用"}</span></div>
      <div class="provider-body">
        <label>API Key</label><input class="provider-key" type="password" autocomplete="new-password" placeholder="${provider.has_api_key ? "留空则保留已保存密钥" : "请输入平台 API Key"}">
        <p class="saved-key">${provider.has_api_key ? `已保存：${escapeHtml(provider.masked_key || "••••••••")}` : "尚未保存密钥"}</p>
        <label>文字模型</label><input class="provider-model" list="models-${provider.provider}" value="${escapeHtml(provider.model)}"><datalist id="models-${provider.provider}">${options}</datalist>
        <div class="provider-actions"><button class="button button-ghost test-provider">测试连接</button><button class="button button-primary save-provider">保存配置</button></div>
        <p class="provider-result ${resultClass}">${escapeHtml(resultText)}</p>
      </div>
    </article>`;
  }

  function renderProviders() {
    $("#provider-grid").innerHTML = state.providers.map(providerCard).join("");
    $$(".save-provider").forEach((button) => button.addEventListener("click", saveProvider));
    $$(".test-provider").forEach((button) => button.addEventListener("click", testProvider));
  }

  async function loadProviders(silent = false) {
    try {
      state.providers = await api("/ai/providers");
      renderProviders();
      if (!silent) toast("AI 平台配置已刷新");
    } catch (error) { toast(error.message, "error"); }
  }

  async function saveProvider(event) {
    const button = event.currentTarget;
    const card = button.closest(".provider-card");
    const provider = card.dataset.provider;
    const apiKey = $(".provider-key", card).value.trim();
    const model = $(".provider-model", card).value.trim();
    if (!model) return toast("模型名称不能为空", "error");
    setBusy(button, true, "保存中…");
    try {
      await api(`/ai/providers/${provider}`, { method: "PUT", body: JSON.stringify({ api_key: apiKey || null, model, enabled: true }) });
      await loadProviders(true);
      toast("AI 配置已加密保存");
    } catch (error) { toast(error.message, "error"); setBusy(button, false); }
  }

  async function testProvider(event) {
    const button = event.currentTarget;
    const card = button.closest(".provider-card");
    const provider = card.dataset.provider;
    const apiKey = $(".provider-key", card).value.trim();
    const model = $(".provider-model", card).value.trim();
    const result = $(".provider-result", card);
    setBusy(button, true, "测试中…");
    result.className = "provider-result";
    result.textContent = "正在连接平台，最长等待 30 秒…";
    try {
      const data = await api(`/ai/providers/${provider}/test`, { method: "POST", body: JSON.stringify({ api_key: apiKey || null, model }) });
      result.classList.add(data.ok ? "ok" : "fail");
      result.textContent = `${data.message}（${data.latency_ms}ms）`;
      toast(data.message, data.ok ? "success" : "error");
    } catch (error) { result.classList.add("fail"); result.textContent = error.message; toast(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  function renderKeywordAccountOptions() {
    if (!state.accounts.some((account) => account.id === state.keywordAccountId)) {
      state.keywordAccountId = state.accounts.length === 1 ? state.accounts[0].id : "";
    }
    const options = `<option value="">请选择知乎账号</option>${state.accounts.map((account) => `<option value="${account.id}">${escapeHtml(account.display_name)}</option>`).join("")}`;
    ["#keyword-account", "#keyword-library-account", "#keyword-recycle-account"].forEach((selector) => {
      const select = $(selector);
      select.innerHTML = options;
      select.value = state.keywordAccountId;
    });
    renderRecycleSettings();
  }

  function selectKeywordAccount(accountId) {
    state.keywordAccountId = accountId;
    ["#keyword-account", "#keyword-library-account", "#keyword-recycle-account"].forEach((selector) => { $(selector).value = accountId; });
    $("#folder-filter").value = "all";
    state.keywordPage = 1;
    state.recycleKeywordPage = 1;
    state.selectedKeywords.clear();
    state.selectedRecycledKeywords.clear();
    renderRecycleSettings();
    loadKeywordData(true);
    if (state.page === "keyword-recycle") loadRecycledKeywords(true);
  }

  function renderRecycleSettings() {
    const account = state.accounts.find((item) => item.id === state.keywordAccountId);
    $("#keyword-auto-recycle").checked = account?.recycle_keywords_after_use ?? true;
    $("#keyword-auto-restore").checked = account?.auto_restore_keywords ?? false;
    $("#keyword-restore-threshold").value = account?.keyword_restore_threshold ?? 20;
    $("#keyword-recycle-settings-save").disabled = !account;
    $("#keyword-auto-restore-now").disabled = !account;
  }

  function renderKeywordJob() {
    const job = state.keywordJob;
    $("#job-empty").hidden = Boolean(job);
    $("#job-progress").hidden = !job;
    if (!job) return;
    const labels = { pending: "等待中", running: "采集中", completed: "已完成", partial: "部分完成", failed: "失败" };
    const badge = $("#job-status");
    badge.textContent = labels[job.status] || job.status;
    badge.className = `badge ${["failed", "partial"].includes(job.status) ? "off" : ""}`;
    $("#job-count").textContent = `${job.collected_count} / ${job.target_count}`;
    $("#job-progress-bar").style.width = `${Math.min(100, Math.round(job.collected_count / job.target_count * 100))}%`;
    $("#job-current").textContent = job.current_keyword || "—";
    $("#job-searched").textContent = job.searched_count;
    $("#job-seed").textContent = job.seed_keyword;
    $("#job-message").textContent = job.error_message || (job.status === "running" ? "正在从搜索页面提取相关关键词…" : "");
  }

  function folderOptions(prefix = "") {
    return state.keywordFolders.map((folder) => `<option value="${folder.id}">${prefix}${escapeHtml(folder.name)}（${folder.keyword_count}）</option>`).join("");
  }

  function renderKeywordFolders() {
    const jobFolder = $("#keyword-folder").value;
    const filter = $("#folder-filter").value;
    const moveFolder = $("#keyword-move-folder").value;
    $("#keyword-folder").innerHTML = `<option value="">未归档</option>${folderOptions()}`;
    $("#folder-filter").innerHTML = `<option value="all">全部关键词</option><option value="unfiled">未归档</option>${folderOptions()}`;
    $("#keyword-move-folder").innerHTML = `<option value="">移动到未归档</option>${folderOptions("移动到：")}`;
    if (state.keywordFolders.some((folder) => folder.id === jobFolder)) $("#keyword-folder").value = jobFolder;
    if (["all", "unfiled"].includes(filter) || state.keywordFolders.some((folder) => folder.id === filter)) $("#folder-filter").value = filter;
    if (state.keywordFolders.some((folder) => folder.id === moveFolder)) $("#keyword-move-folder").value = moveFolder;
    const editable = !["all", "unfiled", ""].includes($("#folder-filter").value);
    $("#folder-rename").disabled = !editable;
    $("#folder-delete").disabled = !editable;
  }

  function updateKeywordSelection() {
    const visibleIds = (state.keywords.items || []).map((item) => item.id);
    const selectedVisible = visibleIds.filter((id) => state.selectedKeywords.has(id));
    $("#keyword-selected-count").textContent = `已选 ${state.selectedKeywords.size} 个`;
    $("#keyword-select-all").checked = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
    $("#keyword-select-all").indeterminate = selectedVisible.length > 0 && selectedVisible.length < visibleIds.length;
    $("#keyword-move").disabled = state.selectedKeywords.size === 0;
    $("#keyword-recycle").disabled = state.selectedKeywords.size === 0;
    $("#keyword-delete").disabled = state.selectedKeywords.size === 0;
  }

  function renderKeywordPagination() {
    const total = state.keywords.total || 0;
    const totalPages = Math.max(1, Math.ceil(total / state.keywordPageSize));
    $("#keyword-pagination").hidden = total === 0;
    $("#keyword-page-info").textContent = `第 ${state.keywordPage} / ${totalPages} 页 · 每页 100 个 · 共 ${total} 个`;
    $("#keyword-prev").disabled = state.keywordPage <= 1;
    $("#keyword-next").disabled = state.keywordPage >= totalPages;
  }

  function renderKeywords() {
    $("#keyword-total").textContent = `${state.keywords.total || 0} 个`;
    const items = state.keywords.items || [];
    const folderNames = new Map(state.keywordFolders.map((folder) => [folder.id, folder.name]));
    $("#keyword-list").innerHTML = items.map((item) => `<article class="keyword-item ${state.selectedKeywords.has(item.id) ? "selected" : ""}" data-keyword-id="${item.id}"><label class="keyword-check"><input type="checkbox" ${state.selectedKeywords.has(item.id) ? "checked" : ""} aria-label="选择${escapeHtml(item.keyword)}"><span></span></label><div><strong title="${escapeHtml(item.keyword)}">${escapeHtml(item.keyword)}</strong><small><span class="source-${item.source}">${item.source === "baidu" ? "百度" : item.source === "google" ? "谷歌" : "其他"}</span><span>${escapeHtml(folderNames.get(item.folder_id) || "未归档")}</span><span>第 ${item.depth} 层</span></small></div></article>`).join("");
    $("#keyword-list").hidden = items.length === 0;
    $("#keyword-empty").hidden = items.length !== 0;
    $$(".keyword-check input", $("#keyword-list")).forEach((input) => input.addEventListener("change", (event) => {
      const item = event.currentTarget.closest(".keyword-item");
      if (event.currentTarget.checked) state.selectedKeywords.add(item.dataset.keywordId);
      else state.selectedKeywords.delete(item.dataset.keywordId);
      item.classList.toggle("selected", event.currentTarget.checked);
      updateKeywordSelection();
    }));
    updateKeywordSelection();
    renderKeywordPagination();
  }

  function scheduleJobPoll() {
    window.clearTimeout(state.pollTimer);
    if (state.keywordJob && ["pending", "running"].includes(state.keywordJob.status)) {
      state.pollTimer = window.setTimeout(() => loadKeywordData(true), 2500);
    }
  }

  async function loadKeywordData(silent = false) {
    const accountId = state.keywordAccountId;
    window.clearTimeout(state.pollTimer);
    if (!accountId) {
      state.keywordJob = null;
      state.keywords = { items: [], total: 0 };
      state.keywordFolders = [];
      state.selectedKeywords.clear();
      renderKeywordFolders(); renderKeywordJob(); renderKeywords(); return;
    }
    try {
      const filter = $("#folder-filter").value;
      const params = new URLSearchParams();
      if (filter === "unfiled") params.set("unfiled", "true");
      else if (!["all", ""].includes(filter)) params.set("folder_id", filter);
      if ($("#keyword-search").value.trim()) params.set("q", $("#keyword-search").value.trim());
      const offset = (state.keywordPage - 1) * state.keywordPageSize;
      const [job, folders, keywords] = await Promise.all([
        api(`/accounts/${accountId}/keyword-jobs/latest`),
        api(`/accounts/${accountId}/keyword-folders`),
        api(`/accounts/${accountId}/keywords?limit=${state.keywordPageSize}&offset=${offset}&${params}`)
      ]);
      const totalPages = Math.max(1, Math.ceil(keywords.total / state.keywordPageSize));
      if (state.keywordPage > totalPages) {
        state.keywordPage = totalPages;
        return loadKeywordData(silent);
      }
      state.keywordJob = job;
      state.keywordFolders = folders;
      state.keywords = keywords;
      const visibleIds = new Set(keywords.items.map((item) => item.id));
      state.selectedKeywords = new Set([...state.selectedKeywords].filter((id) => visibleIds.has(id)));
      renderKeywordFolders(); renderKeywordJob(); renderKeywords(); scheduleJobPoll();
      if (!silent) toast("关键词数据已刷新");
    } catch (error) { toast(error.message, "error"); }
  }

  async function startKeywordJob(event) {
    event.preventDefault();
    const button = $("#keyword-start");
    const accountId = state.keywordAccountId;
    $("#keyword-error").textContent = "";
    if (!accountId) { $("#keyword-error").textContent = "请先选择知乎账号"; return; }
    setBusy(button, true, "正在创建任务…");
    try {
      state.keywordJob = await api(`/accounts/${accountId}/keyword-jobs`, {
        method: "POST",
        body: JSON.stringify({ seed_keyword: $("#seed-keyword").value.trim(), source: $("#keyword-source").value, target_count: Number($("#keyword-target").value), folder_id: $("#keyword-folder").value || null })
      });
      renderKeywordJob(); scheduleJobPoll(); toast("关键词采集任务已开始");
    } catch (error) { $("#keyword-error").textContent = error.message; }
    finally { setBusy(button, false); }
  }

  async function createKeywordFolder() {
    const accountId = state.keywordAccountId;
    if (!accountId) return toast("请先选择知乎账号", "error");
    const name = window.prompt("请输入新文件夹名称");
    if (!name?.trim()) return;
    try {
      const folder = await api(`/accounts/${accountId}/keyword-folders`, { method: "POST", body: JSON.stringify({ name: name.trim() }) });
      await loadKeywordData(true);
      $("#keyword-folder").value = folder.id;
      toast("关键词文件夹已创建");
    } catch (error) { toast(error.message, "error"); }
  }

  async function renameKeywordFolder() {
    const accountId = state.keywordAccountId;
    const folderId = $("#folder-filter").value;
    const folder = state.keywordFolders.find((item) => item.id === folderId);
    if (!folder) return;
    const name = window.prompt("请输入新的文件夹名称", folder.name);
    if (!name?.trim() || name.trim() === folder.name) return;
    try {
      await api(`/accounts/${accountId}/keyword-folders/${folderId}`, { method: "PUT", body: JSON.stringify({ name: name.trim() }) });
      await loadKeywordData(true);
      toast("文件夹已重命名");
    } catch (error) { toast(error.message, "error"); }
  }

  async function deleteKeywordFolder() {
    const accountId = state.keywordAccountId;
    const folderId = $("#folder-filter").value;
    const folder = state.keywordFolders.find((item) => item.id === folderId);
    if (!folder || !window.confirm(`删除文件夹“${folder.name}”？其中的关键词会保留并移到未归档。`)) return;
    try {
      await api(`/accounts/${accountId}/keyword-folders/${folderId}`, { method: "DELETE" });
      $("#folder-filter").value = "all";
      await loadKeywordData(true);
      toast("文件夹已删除，关键词已保留");
    } catch (error) { toast(error.message, "error"); }
  }

  async function moveSelectedKeywords() {
    const ids = [...state.selectedKeywords];
    if (!ids.length) return;
    try {
      const result = await api(`/accounts/${state.keywordAccountId}/keywords/folder`, { method: "PATCH", body: JSON.stringify({ keyword_ids: ids, folder_id: $("#keyword-move-folder").value || null }) });
      state.selectedKeywords.clear();
      await loadKeywordData(true);
      toast(`已移动 ${result.affected_count} 个关键词`);
    } catch (error) { toast(error.message, "error"); }
  }

  async function deleteSelectedKeywords() {
    const ids = [...state.selectedKeywords];
    if (!ids.length || !window.confirm(`确定删除选中的 ${ids.length} 个关键词吗？`)) return;
    try {
      const result = await api(`/accounts/${state.keywordAccountId}/keywords/bulk-delete`, { method: "POST", body: JSON.stringify({ keyword_ids: ids }) });
      state.selectedKeywords.clear();
      await loadKeywordData(true);
      toast(`已删除 ${result.affected_count} 个关键词`);
    } catch (error) { toast(error.message, "error"); }
  }

  async function recycleSelectedKeywords() {
    const ids = [...state.selectedKeywords];
    if (!ids.length) return;
    try {
      const result = await api(`/accounts/${state.keywordAccountId}/keywords/bulk-recycle`, { method: "POST", body: JSON.stringify({ keyword_ids: ids }) });
      state.selectedKeywords.clear();
      await loadKeywordData(true);
      toast(`已将 ${result.affected_count} 个关键词转入回收库`);
    } catch (error) { toast(error.message, "error"); }
  }

  function updateRecycleKeywordSelection() {
    const visibleIds = (state.recycledKeywords.items || []).map((item) => item.id);
    const selectedVisible = visibleIds.filter((id) => state.selectedRecycledKeywords.has(id));
    $("#recycle-keyword-selected-count").textContent = `已选 ${state.selectedRecycledKeywords.size} 个`;
    $("#recycle-keyword-select-all").checked = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
    $("#recycle-keyword-select-all").indeterminate = selectedVisible.length > 0 && selectedVisible.length < visibleIds.length;
    $("#recycle-keyword-restore").disabled = state.selectedRecycledKeywords.size === 0;
    $("#recycle-keyword-delete").disabled = state.selectedRecycledKeywords.size === 0;
  }

  function renderRecycledKeywords() {
    const items = state.recycledKeywords.items || [];
    $("#recycle-keyword-total").textContent = `${state.recycledKeywords.total || 0} 个`;
    $("#recycle-keyword-list").innerHTML = items.map((item) => `<article class="keyword-item recycled ${state.selectedRecycledKeywords.has(item.id) ? "selected" : ""}" data-keyword-id="${item.id}"><label class="keyword-check"><input type="checkbox" ${state.selectedRecycledKeywords.has(item.id) ? "checked" : ""} aria-label="选择${escapeHtml(item.keyword)}"><span></span></label><div><strong title="${escapeHtml(item.keyword)}">${escapeHtml(item.keyword)}</strong><small><span>已使用 ${item.used_count || 0} 次</span><span>回收于 ${item.recycled_at ? formatDateTime(item.recycled_at) : "—"}</span><span>${item.source === "baidu" ? "百度" : item.source === "google" ? "谷歌" : "其他"}</span></small></div></article>`).join("");
    $("#recycle-keyword-list").hidden = items.length === 0;
    $("#recycle-keyword-empty").hidden = items.length !== 0;
    $$(".keyword-check input", $("#recycle-keyword-list")).forEach((input) => input.addEventListener("change", (event) => {
      const id = event.currentTarget.closest(".keyword-item").dataset.keywordId;
      if (event.currentTarget.checked) state.selectedRecycledKeywords.add(id);
      else state.selectedRecycledKeywords.delete(id);
      event.currentTarget.closest(".keyword-item").classList.toggle("selected", event.currentTarget.checked);
      updateRecycleKeywordSelection();
    }));
    const pages = Math.max(1, Math.ceil((state.recycledKeywords.total || 0) / state.keywordPageSize));
    $("#recycle-keyword-pagination").hidden = !state.recycledKeywords.total;
    $("#recycle-keyword-page-info").textContent = `第 ${state.recycleKeywordPage} / ${pages} 页 · 每页 100 个 · 共 ${state.recycledKeywords.total || 0} 个`;
    $("#recycle-keyword-prev").disabled = state.recycleKeywordPage <= 1;
    $("#recycle-keyword-next").disabled = state.recycleKeywordPage >= pages;
    updateRecycleKeywordSelection();
  }

  async function loadRecycledKeywords(silent = false) {
    if (!state.keywordAccountId) {
      state.recycledKeywords = { items: [], total: 0 };
      state.selectedRecycledKeywords.clear();
      renderRecycledKeywords();
      return;
    }
    try {
      const offset = (state.recycleKeywordPage - 1) * state.keywordPageSize;
      const params = new URLSearchParams({ recycled: "true", limit: String(state.keywordPageSize), offset: String(offset) });
      if ($("#recycle-keyword-search").value.trim()) params.set("q", $("#recycle-keyword-search").value.trim());
      const data = await api(`/accounts/${state.keywordAccountId}/keywords?${params}`);
      const pages = Math.max(1, Math.ceil(data.total / state.keywordPageSize));
      if (state.recycleKeywordPage > pages) { state.recycleKeywordPage = pages; return loadRecycledKeywords(silent); }
      state.recycledKeywords = data;
      const visible = new Set(data.items.map((item) => item.id));
      state.selectedRecycledKeywords = new Set([...state.selectedRecycledKeywords].filter((id) => visible.has(id)));
      renderRecycledKeywords();
      renderRecycleSettings();
      if (!silent) toast("回收关键词库已刷新");
    } catch (error) { toast(error.message, "error"); }
  }

  async function restoreSelectedKeywords() {
    const ids = [...state.selectedRecycledKeywords];
    if (!ids.length) return;
    try {
      const result = await api(`/accounts/${state.keywordAccountId}/keywords/bulk-restore`, { method: "POST", body: JSON.stringify({ keyword_ids: ids }) });
      state.selectedRecycledKeywords.clear();
      await Promise.all([loadRecycledKeywords(true), loadKeywordData(true)]);
      toast(`已恢复 ${result.affected_count} 个关键词，可再次用于生成文章`);
    } catch (error) { toast(error.message, "error"); }
  }

  async function deleteRecycledKeywords() {
    const ids = [...state.selectedRecycledKeywords];
    if (!ids.length || !window.confirm(`确定永久删除回收库中的 ${ids.length} 个关键词吗？`)) return;
    try {
      const result = await api(`/accounts/${state.keywordAccountId}/keywords/bulk-delete`, { method: "POST", body: JSON.stringify({ keyword_ids: ids }) });
      state.selectedRecycledKeywords.clear();
      await loadRecycledKeywords(true);
      toast(`已永久删除 ${result.affected_count} 个关键词`);
    } catch (error) { toast(error.message, "error"); }
  }

  async function saveKeywordRecycleSettings() {
    if (!state.keywordAccountId) return;
    try {
      await api(`/accounts/${state.keywordAccountId}`, { method: "PATCH", body: JSON.stringify({ recycle_keywords_after_use: $("#keyword-auto-recycle").checked, auto_restore_keywords: $("#keyword-auto-restore").checked, keyword_restore_threshold: Number($("#keyword-restore-threshold").value) }) });
      await loadAccounts(true);
      toast("关键词循环规则已保存");
    } catch (error) { toast(error.message, "error"); }
  }

  async function autoRestoreKeywordsNow() {
    if (!state.keywordAccountId) return;
    try {
      const result = await api(`/accounts/${state.keywordAccountId}/keywords/auto-restore`, { method: "POST" });
      await Promise.all([loadRecycledKeywords(true), loadKeywordData(true)]);
      toast(result.affected_count ? `已按规则恢复 ${result.affected_count} 个关键词` : "正常关键词数量已达到设定值");
    } catch (error) { toast(error.message, "error"); }
  }

  function mediaFolderOptions(prefix = "") {
    return state.mediaFolders.map((folder) => `<option value="${folder.id}">${escapeHtml(prefix + folder.name)}（${folder.image_count} 张）</option>`).join("");
  }

  function renderMediaFolderOptions() {
    const filter = $("#media-folder-filter");
    const upload = $("#media-upload-folder");
    const generator = $("#article-local-image-folder");
    const filterValue = filter.value;
    const uploadValue = upload.value;
    const generatorValue = generator.value;
    filter.innerHTML = `<option value="all">全部文件</option><option value="unfiled">未归档</option>${mediaFolderOptions()}`;
    upload.innerHTML = `<option value="">未归档</option>${mediaFolderOptions()}`;
    generator.innerHTML = `<option value="">全部图片库</option>${mediaFolderOptions()}`;
    if (["all", "unfiled", ...state.mediaFolders.map((item) => item.id)].includes(filterValue)) filter.value = filterValue;
    if (state.mediaFolders.some((item) => item.id === uploadValue)) upload.value = uploadValue;
    if (state.mediaFolders.some((item) => item.id === generatorValue)) generator.value = generatorValue;
    const hasFolder = state.mediaFolders.some((item) => item.id === filter.value);
    $("#media-folder-rename").disabled = !hasFolder;
    $("#media-folder-delete").disabled = !hasFolder;
  }

  function formatBytes(value) {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }

  function updateMediaSelection() {
    $("#media-selected-count").textContent = `已选 ${state.selectedMedia.size} 个`;
    $("#media-delete-selected").disabled = state.selectedMedia.size === 0;
    const visible = state.media.items || [];
    $("#media-select-all").checked = visible.length > 0 && visible.every((item) => state.selectedMedia.has(item.id));
  }

  function renderMedia() {
    const items = state.media.items || [];
    $("#media-total").textContent = `共 ${state.media.total || 0} 个文件`;
    $("#media-empty").hidden = items.length > 0;
    $("#media-list").innerHTML = items.map((item) => {
      const preview = item.kind === "image"
        ? `<div class="media-preview"><img src="${escapeHtml(item.public_url)}" alt="${escapeHtml(item.original_name)}" loading="lazy"></div>`
        : `<div class="media-preview archive">▣</div>`;
      const archiveAction = item.kind === "archive"
        ? `<button class="button button-primary media-extract" data-media-id="${item.id}" ${item.extracted ? "disabled" : ""}>${item.extracted ? "已解压" : "解压"}</button>`
        : "";
      return `<article class="media-card ${state.selectedMedia.has(item.id) ? "selected" : ""}" data-media-id="${item.id}"><label class="media-card-check"><input type="checkbox" ${state.selectedMedia.has(item.id) ? "checked" : ""}></label>${preview}<div class="media-card-body"><strong title="${escapeHtml(item.original_name)}">${escapeHtml(item.original_name)}</strong><small>${item.kind === "image" ? "图片" : "压缩包"} · ${formatBytes(item.size_bytes)} · ${escapeHtml(item.folder_name || "未归档")}</small><div class="media-card-actions">${archiveAction}<button class="button button-ghost danger-text media-delete-one" data-media-id="${item.id}">删除</button></div></div></article>`;
    }).join("");
    $$(".media-card-check input", $("#media-list")).forEach((input) => input.addEventListener("change", (event) => {
      const id = event.currentTarget.closest(".media-card").dataset.mediaId;
      if (event.currentTarget.checked) state.selectedMedia.add(id); else state.selectedMedia.delete(id);
      renderMedia();
    }));
    $$(".media-extract", $("#media-list")).forEach((button) => button.addEventListener("click", () => extractMediaArchive(button.dataset.mediaId, button)));
    $$(".media-delete-one", $("#media-list")).forEach((button) => button.addEventListener("click", () => deleteMedia([button.dataset.mediaId])));
    const pages = Math.max(1, Math.ceil((state.media.total || 0) / state.mediaPageSize));
    $("#media-pagination").hidden = !state.media.total;
    $("#media-page-info").textContent = `第 ${state.mediaPage} / ${pages} 页 · 每页 100 个`;
    $("#media-prev").disabled = state.mediaPage <= 1;
    $("#media-next").disabled = state.mediaPage >= pages;
    updateMediaSelection();
  }

  async function loadMedia(silent = false) {
    try {
      const folder = $("#media-folder-filter").value;
      const params = new URLSearchParams({ limit: String(state.mediaPageSize), offset: String((state.mediaPage - 1) * state.mediaPageSize) });
      if (folder === "unfiled") params.set("unfiled", "true"); else if (!["all", ""].includes(folder)) params.set("folder_id", folder);
      if ($("#media-kind-filter").value) params.set("kind", $("#media-kind-filter").value);
      if ($("#media-search").value.trim()) params.set("q", $("#media-search").value.trim());
      const [folders, media] = await Promise.all([api("/local-media/folders"), api(`/local-media?${params}`)]);
      state.mediaFolders = folders;
      state.media = media;
      const pages = Math.max(1, Math.ceil(media.total / state.mediaPageSize));
      if (state.mediaPage > pages) { state.mediaPage = pages; return loadMedia(silent); }
      const visible = new Set(media.items.map((item) => item.id));
      state.selectedMedia = new Set([...state.selectedMedia].filter((id) => visible.has(id)));
      renderMediaFolderOptions();
      renderMedia();
      if (!silent) toast("本地图片库已刷新");
    } catch (error) { toast(error.message, "error"); }
  }

  function setMediaUploadProgress({ percent, currentIndex, total, currentName, currentPercent, remaining, succeeded, failed, status = "uploading" }) {
    const panel = $("#media-upload-progress");
    const safePercent = Math.max(0, Math.min(100, Math.round(percent)));
    const safeCurrentPercent = Math.max(0, Math.min(100, Math.round(currentPercent)));
    const labels = { uploading: "正在上传", completed: "上传完成", partial: "部分失败", failed: "上传失败" };
    panel.hidden = false;
    panel.dataset.status = status;
    panel.setAttribute("aria-valuenow", String(safePercent));
    $("#media-upload-state").textContent = labels[status] || labels.uploading;
    $("#media-upload-percent").textContent = `${safePercent}%`;
    $("#media-upload-count").textContent = `${currentIndex} / ${total}`;
    $("#media-upload-progress-bar").style.width = `${safePercent}%`;
    $("#media-upload-current").textContent = currentName || "—";
    $("#media-upload-current").title = currentName || "";
    $("#media-upload-current-percent").textContent = `${safeCurrentPercent}%`;
    $("#media-upload-remaining").textContent = `${remaining} 个`;
    $("#media-upload-result").textContent = `成功 ${succeeded} · 失败 ${failed}`;
  }

  function uploadSingleMediaFile(file, folderId, onProgress) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", "/api/local-media/upload");
      request.responseType = "json";
      if (state.token) request.setRequestHeader("Authorization", `Bearer ${state.token}`);
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
      });
      request.addEventListener("load", () => {
        let data = request.response || {};
        if (typeof data === "string") {
          try { data = JSON.parse(data); } catch { data = {}; }
        }
        if (request.status === 401) {
          logout("登录已过期，请重新登录");
          const error = new Error("登录已过期");
          error.authExpired = true;
          reject(error);
          return;
        }
        if (request.status < 200 || request.status >= 300) {
          const detail = Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join("；") : data.detail;
          reject(new Error(detail || `${file.name} 上传失败（${request.status || "网络错误"}）`));
          return;
        }
        onProgress(1);
        resolve(data);
      });
      request.addEventListener("error", () => reject(new Error(`${file.name} 上传时网络连接失败`)));
      request.addEventListener("abort", () => reject(new Error(`${file.name} 上传已取消`)));
      const form = new FormData();
      form.append("files", file);
      if (folderId) form.append("folder_id", folderId);
      request.send(form);
    });
  }

  async function uploadMedia(event) {
    event.preventDefault();
    if (state.mediaUploadRunning) return;
    const files = [...$("#media-upload-files").files];
    if (!files.length) return $("#media-upload-error").textContent = "请选择图片或压缩包";
    const folderId = $("#media-upload-folder").value;
    const button = $("#media-upload-submit");
    const fileInput = $("#media-upload-files");
    const folderSelect = $("#media-upload-folder");
    const weights = files.map((file) => Math.max(1, file.size));
    const totalWeight = weights.reduce((sum, size) => sum + size, 0);
    let completedWeight = 0;
    let succeeded = 0;
    const failures = [];
    $("#media-upload-error").textContent = "";
    state.mediaUploadRunning = true;
    fileInput.disabled = true;
    folderSelect.disabled = true;
    setBusy(button, true, "正在上传…");
    setMediaUploadProgress({ percent: 0, currentIndex: 0, total: files.length, currentName: "准备上传", currentPercent: 0, remaining: files.length, succeeded: 0, failed: 0 });
    try {
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        const currentWeight = weights[index];
        const renderProgress = (ratio) => setMediaUploadProgress({
          percent: (completedWeight + currentWeight * ratio) / totalWeight * 100,
          currentIndex: index + 1,
          total: files.length,
          currentName: file.name,
          currentPercent: ratio * 100,
          remaining: files.length - index - 1,
          succeeded,
          failed: failures.length,
        });
        renderProgress(0);
        try {
          const result = await uploadSingleMediaFile(file, folderId, renderProgress);
          succeeded += result.uploaded_count || 1;
        } catch (error) {
          failures.push({ name: file.name, message: error.message });
          if (error.authExpired) return;
        }
        completedWeight += currentWeight;
        renderProgress(1);
      }
      fileInput.value = "";
      await loadMedia(true);
      const status = failures.length === files.length ? "failed" : failures.length ? "partial" : "completed";
      setMediaUploadProgress({ percent: 100, currentIndex: files.length, total: files.length, currentName: "全部文件已处理", currentPercent: 100, remaining: 0, succeeded, failed: failures.length, status });
      if (failures.length) {
        const details = failures.slice(0, 5).map((item) => `${item.name}：${item.message}`).join("；");
        $("#media-upload-error").textContent = `有 ${failures.length} 个文件上传失败：${details}${failures.length > 5 ? "；其余失败文件请分批重试" : ""}`;
        toast(`上传结束：成功 ${succeeded} 个，失败 ${failures.length} 个`, failures.length === files.length ? "error" : "success");
      } else {
        toast(`已上传 ${succeeded} 个文件`);
      }
    } finally {
      state.mediaUploadRunning = false;
      fileInput.disabled = false;
      folderSelect.disabled = false;
      setBusy(button, false);
    }
  }

  async function createMediaFolder() {
    const name = window.prompt("请输入图片文件夹名称");
    if (!name?.trim()) return;
    try { const folder = await api("/local-media/folders", { method: "POST", body: JSON.stringify({ name: name.trim() }) }); await loadMedia(true); $("#media-folder-filter").value = folder.id; await loadMedia(true); toast("图片文件夹已创建"); }
    catch (error) { toast(error.message, "error"); }
  }

  async function renameMediaFolder() {
    const folder = state.mediaFolders.find((item) => item.id === $("#media-folder-filter").value);
    if (!folder) return;
    const name = window.prompt("请输入新的图片文件夹名称", folder.name);
    if (!name?.trim() || name.trim() === folder.name) return;
    try { await api(`/local-media/folders/${folder.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) }); await loadMedia(true); $("#media-folder-filter").value = folder.id; toast("图片文件夹已改名"); }
    catch (error) { toast(error.message, "error"); }
  }

  async function deleteMediaFolder() {
    const folder = state.mediaFolders.find((item) => item.id === $("#media-folder-filter").value);
    if (!folder || !window.confirm(`确定删除文件夹“${folder.name}”吗？文件会保留并移到未归档。`)) return;
    try { await api(`/local-media/folders/${folder.id}`, { method: "DELETE" }); $("#media-folder-filter").value = "unfiled"; await loadMedia(true); toast("文件夹已删除，原文件已移到未归档"); }
    catch (error) { toast(error.message, "error"); }
  }

  async function extractMediaArchive(id, button) {
    setBusy(button, true, "解压中…");
    try { const result = await api(`/local-media/${id}/extract`, { method: "POST" }); await loadMedia(true); toast(`解压完成：新增 ${result.extracted_count} 张图片，跳过 ${result.skipped_count} 个文件`); }
    catch (error) { toast(error.message, "error"); setBusy(button, false); }
  }

  async function deleteMedia(ids) {
    if (!ids.length || !window.confirm(`确定删除所选 ${ids.length} 个文件吗？删除后无法恢复。`)) return;
    try { const result = await api("/local-media/bulk-delete", { method: "POST", body: JSON.stringify({ asset_ids: ids }) }); state.selectedMedia.clear(); await loadMedia(true); toast(`已删除 ${result.affected_count} 个文件`); }
    catch (error) { toast(error.message, "error"); }
  }

  function renderArticleAccountOptions() {
    const generate = $("#article-generate-account");
    const filter = $("#article-account-filter");
    const generateValue = generate.value;
    const filterValue = filter.value;
    const options = state.accounts.map((account) => `<option value="${account.id}">${escapeHtml(account.display_name)}</option>`).join("");
    generate.innerHTML = `<option value="">请选择账号</option>${options}`;
    filter.innerHTML = state.accounts.length
      ? options
      : `<option value="">暂无知乎账号</option>`;
    filter.disabled = state.accounts.length === 0;
    $("#article-sync").disabled = state.accounts.length === 0;
    if (state.accounts.some((account) => account.id === generateValue)) generate.value = generateValue;
    else if (state.accounts.length === 1) generate.value = state.accounts[0].id;
    if (state.accounts.some((account) => account.id === filterValue)) filter.value = filterValue;
    else if (state.accounts.length) filter.value = state.accounts[0].id;
  }

  function updateArticleGenerateEstimate() {
    const keywordCount = state.selectedArticleKeywords.size;
    const perKeyword = Number($("#articles-per-keyword").value) || 0;
    const total = keywordCount * perKeyword;
    $("#article-generate-estimate").textContent = `将生成 ${total} 篇`;
    $("#article-generate-estimate").className = `badge ${total ? "" : "off"}`;
    $("#article-selected-summary").textContent = `已选 ${keywordCount} 个关键词`;
  }

  function renderArticleKeywordOptions() {
    const query = $("#article-keyword-search").value.trim().toLowerCase();
    const filtered = state.articleGenerateKeywords.filter((item) => item.keyword.toLowerCase().includes(query));
    $("#article-keyword-count").textContent = `显示 ${filtered.length} / ${state.articleGenerateKeywords.length} 个`;
    $("#article-keyword-options").innerHTML = filtered.map((item) => `<label class="article-keyword-option ${state.selectedArticleKeywords.has(item.id) ? "selected" : ""}" data-keyword-id="${item.id}"><input type="checkbox" ${state.selectedArticleKeywords.has(item.id) ? "checked" : ""}><span><strong>${escapeHtml(item.keyword)}</strong><small>${item.source === "baidu" ? "百度" : item.source === "google" ? "谷歌" : "其他"} · 第 ${item.depth} 层</small></span></label>`).join("");
    $("#article-keyword-options").hidden = filtered.length === 0;
    $("#article-keyword-empty").hidden = filtered.length !== 0;
    if (!filtered.length) $("#article-keyword-empty p").textContent = state.articleGenerateKeywords.length ? "没有匹配的关键词" : "当前账号或文件夹没有关键词";
    const visibleIds = filtered.map((item) => item.id);
    const selectedVisible = visibleIds.filter((id) => state.selectedArticleKeywords.has(id));
    $("#article-keyword-select-all").checked = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
    $("#article-keyword-select-all").indeterminate = selectedVisible.length > 0 && selectedVisible.length < visibleIds.length;
    $$(".article-keyword-option input", $("#article-keyword-options")).forEach((input) => input.addEventListener("change", (event) => {
      const option = event.currentTarget.closest(".article-keyword-option");
      if (event.currentTarget.checked) state.selectedArticleKeywords.add(option.dataset.keywordId);
      else state.selectedArticleKeywords.delete(option.dataset.keywordId);
      option.classList.toggle("selected", event.currentTarget.checked);
      renderArticleKeywordOptions();
      updateArticleGenerateEstimate();
    }));
    updateArticleGenerateEstimate();
  }

  function renderArticleGeneratorOptions() {
    const folder = $("#article-generate-folder");
    const oldFolder = folder.value;
    folder.innerHTML = `<option value="all">全部关键词</option><option value="unfiled">未归档</option>${state.keywordFolders.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}（${item.keyword_count}）</option>`).join("")}`;
    if (["all", "unfiled"].includes(oldFolder) || state.keywordFolders.some((item) => item.id === oldFolder)) folder.value = oldFolder;
    const product = $("#article-generate-product");
    const oldProduct = product.value;
    const enabledProducts = state.articleGenerateProducts.filter((item) => item.enabled);
    product.innerHTML = `<option value="">请选择启用的商品</option>${enabledProducts.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("")}`;
    if (enabledProducts.some((item) => item.id === oldProduct)) product.value = oldProduct;
    else if (enabledProducts.length === 1) product.value = enabledProducts[0].id;
    const provider = $("#article-generate-provider");
    const oldProvider = provider.value;
    const enabledProviders = state.providers.filter((item) => item.enabled && item.has_api_key);
    const unavailableProviders = state.providers.filter((item) => !item.enabled || !item.has_api_key);
    provider.innerHTML = `<option value="">请选择已配置的平台</option>${enabledProviders.map((item) => `<option value="${item.provider}">${escapeHtml(item.display_name)} · ${escapeHtml(item.model)}</option>`).join("")}${unavailableProviders.map((item) => `<option value="" disabled>${escapeHtml(item.display_name)}（请先到 AI 配置保存密钥）</option>`).join("")}`;
    if (enabledProviders.some((item) => item.provider === oldProvider)) provider.value = oldProvider;
    else if (enabledProviders.length === 1) provider.value = enabledProviders[0].provider;
  }

  function renderPromptLibrary() {
    const folderSelect = $("#prompt-folder-filter");
    const oldFolder = folderSelect.value;
    folderSelect.innerHTML = `<option value="">全部文件夹</option>${state.promptFolders.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}（${item.template_count}）</option>`).join("")}`;
    if (state.promptFolders.some((item) => item.id === oldFolder)) folderSelect.value = oldFolder;
    const selectedTemplate = $("#prompt-template-select").value;
    const visible = folderSelect.value ? state.promptTemplates.filter((item) => item.folder_id === folderSelect.value) : state.promptTemplates;
    $("#prompt-template-select").innerHTML = `<option value="">当前未使用已保存模板</option>${visible.map((item) => `<option value="${item.id}">${escapeHtml(item.folder_name ? `${item.folder_name} / ${item.name}` : item.name)}</option>`).join("")}`;
    if (visible.some((item) => item.id === selectedTemplate)) $("#prompt-template-select").value = selectedTemplate;
    const hasFolder = Boolean(folderSelect.value);
    const hasTemplate = Boolean($("#prompt-template-select").value);
    $("#prompt-folder-rename").disabled = !hasFolder;
    $("#prompt-folder-delete").disabled = !hasFolder;
    $("#prompt-template-update").disabled = !hasTemplate;
    if (!hasTemplate) $("#prompt-template-update").textContent = "保存当前模板";
    $("#prompt-template-rename").disabled = !hasTemplate;
    $("#prompt-template-delete").disabled = !hasTemplate;
  }

  async function loadPromptLibrary() {
    try {
      const [folders, templates] = await Promise.all([api("/article-prompt-folders"), api("/article-prompt-templates")]);
      state.promptFolders = folders;
      state.promptTemplates = templates.items;
      renderPromptLibrary();
    } catch (error) { toast(error.message, "error"); }
  }

  function selectPromptTemplate() {
    const template = state.promptTemplates.find((item) => item.id === $("#prompt-template-select").value);
    $("#prompt-template-update").textContent = "保存当前模板";
    if (template) {
      $("#article-title-prompt").value = template.title_prompt;
      $("#article-content-prompt").value = template.content_prompt;
      toast(`已切换到模板：${template.name}`);
    }
    renderPromptLibrary();
  }

  async function createPromptFolder() {
    const name = window.prompt("新建提示词文件夹名称");
    if (!name?.trim()) return;
    try {
      const folder = await api("/article-prompt-folders", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
      await loadPromptLibrary();
      $("#prompt-folder-filter").value = folder.id;
      renderPromptLibrary();
      toast("提示词文件夹已创建");
    } catch (error) { toast(error.message, "error"); }
  }

  async function renamePromptFolder() {
    const folder = state.promptFolders.find((item) => item.id === $("#prompt-folder-filter").value);
    if (!folder) return;
    const name = window.prompt("修改文件夹名称", folder.name);
    if (!name?.trim() || name.trim() === folder.name) return;
    try {
      await api(`/article-prompt-folders/${folder.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
      await loadPromptLibrary();
      $("#prompt-folder-filter").value = folder.id;
      renderPromptLibrary();
      toast("提示词文件夹已重命名");
    } catch (error) { toast(error.message, "error"); }
  }

  async function deletePromptFolder() {
    const folder = state.promptFolders.find((item) => item.id === $("#prompt-folder-filter").value);
    if (!folder || !window.confirm(`删除文件夹“${folder.name}”？模板会保留并移到未归档。`)) return;
    try {
      await api(`/article-prompt-folders/${folder.id}`, { method: "DELETE" });
      $("#prompt-folder-filter").value = "";
      await loadPromptLibrary();
      toast("文件夹已删除，模板已保留");
    } catch (error) { toast(error.message, "error"); }
  }

  async function savePromptTemplate() {
    const name = window.prompt("提示词模板名称");
    if (!name?.trim()) return;
    const selectedFolder = state.promptFolders.find((item) => item.id === $("#prompt-folder-filter").value);
    const folderName = window.prompt("保存到文件夹（可留空；输入新名称会自动创建）", selectedFolder?.name || "") || "";
    try {
      const template = await api("/article-prompt-templates", { method: "POST", body: JSON.stringify({
        name: name.trim(), folder_name: folderName.trim() || null,
        title_prompt: $("#article-title-prompt").value.trim(), content_prompt: $("#article-content-prompt").value.trim()
      }) });
      await loadPromptLibrary();
      if (template.folder_id) $("#prompt-folder-filter").value = template.folder_id;
      renderPromptLibrary();
      $("#prompt-template-select").value = template.id;
      renderPromptLibrary();
      $("#prompt-template-update").textContent = "保存当前模板";
      toast("提示词模板已保存");
    } catch (error) { toast(error.message, "error"); }
  }

  async function updatePromptTemplate() {
    const id = $("#prompt-template-select").value;
    if (!id) return;
    const template = state.promptTemplates.find((item) => item.id === id);
    const titlePrompt = $("#article-title-prompt").value.trim();
    const contentPrompt = $("#article-content-prompt").value.trim();
    if (!titlePrompt || !contentPrompt) {
      toast("标题提示词和正文提示词不能为空", "error");
      return;
    }
    const button = $("#prompt-template-update");
    button.disabled = true;
    button.textContent = "保存中…";
    try {
      await api(`/article-prompt-templates/${id}`, { method: "PATCH", body: JSON.stringify({
        title_prompt: titlePrompt, content_prompt: contentPrompt
      }) });
      await loadPromptLibrary();
      $("#prompt-template-select").value = id;
      renderPromptLibrary();
      button.textContent = "保存当前模板";
      toast(`已保存当前模板：${template?.name || "当前模板"}`);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.textContent = "保存当前模板";
      button.disabled = !$("#prompt-template-select").value;
    }
  }

  function markPromptTemplateChanged() {
    if (!$("#prompt-template-select").value) return;
    $("#prompt-template-update").textContent = "保存当前模板（有修改）";
  }

  async function renamePromptTemplate() {
    const template = state.promptTemplates.find((item) => item.id === $("#prompt-template-select").value);
    if (!template) return;
    const name = window.prompt("修改模板名称", template.name);
    if (!name?.trim() || name.trim() === template.name) return;
    try {
      await api(`/article-prompt-templates/${template.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
      await loadPromptLibrary();
      $("#prompt-template-select").value = template.id;
      renderPromptLibrary();
      toast("模板已重命名");
    } catch (error) { toast(error.message, "error"); }
  }

  async function deletePromptTemplate() {
    const template = state.promptTemplates.find((item) => item.id === $("#prompt-template-select").value);
    if (!template || !window.confirm(`确定删除提示词模板“${template.name}”吗？`)) return;
    try {
      await api(`/article-prompt-templates/${template.id}`, { method: "DELETE" });
      await loadPromptLibrary();
      toast("提示词模板已删除");
    } catch (error) { toast(error.message, "error"); }
  }

  async function loadArticleGenerator(resetSelection = false) {
    await loadPromptLibrary();
    const accountId = $("#article-generate-account").value;
    if (!accountId) {
      state.articleGenerateKeywords = [];
      state.articleGenerateProducts = [];
      state.keywordFolders = [];
      state.selectedArticleKeywords.clear();
      renderArticleGeneratorOptions();
      renderArticleKeywordOptions();
      return;
    }
    if (resetSelection) state.selectedArticleKeywords.clear();
    const folder = $("#article-generate-folder").value;
    const folderQuery = folder === "unfiled" ? "&unfiled=true" : !["all", ""].includes(folder) ? `&folder_id=${encodeURIComponent(folder)}` : "";
    try {
      const [folders, keywords, products, providers, mediaFolders] = await Promise.all([
        api(`/accounts/${accountId}/keyword-folders`),
        api(`/accounts/${accountId}/keywords?limit=500${folderQuery}`),
        api(`/accounts/${accountId}/products?limit=500`),
        api("/ai/providers"),
        api("/local-media/folders")
      ]);
      state.keywordFolders = folders;
      state.articleGenerateKeywords = keywords.items;
      state.articleGenerateProducts = products.items;
      state.providers = providers;
      state.mediaFolders = mediaFolders;
      const available = new Set(keywords.items.map((item) => item.id));
      state.selectedArticleKeywords = new Set([...state.selectedArticleKeywords].filter((id) => available.has(id)));
      renderArticleGeneratorOptions();
      renderMediaFolderOptions();
      renderArticleKeywordOptions();
    } catch (error) { toast(error.message, "error"); }
  }

  async function generateArticles(event) {
    event.preventDefault();
    const button = $("#article-generate-submit");
    const accountId = $("#article-generate-account").value;
    const providerName = $("#article-generate-provider").value;
    const provider = state.providers.find((item) => item.provider === providerName);
    const keywordIds = [...state.selectedArticleKeywords];
    const articlesPerKeyword = Number($("#articles-per-keyword").value);
    const total = keywordIds.length * articlesPerKeyword;
    $("#article-generate-error").textContent = "";
    if (!accountId) return $("#article-generate-error").textContent = "请选择知乎账号";
    if (!keywordIds.length) return $("#article-generate-error").textContent = "请至少选择一个关键词";
    if (!$("#article-generate-product").value) return $("#article-generate-error").textContent = "请选择推广商品";
    if (!provider) return $("#article-generate-error").textContent = "请先在 AI 配置中保存并启用平台";
    if (total > 50) return $("#article-generate-error").textContent = "单次最多生成 50 篇文章，请减少关键词或每词篇数";
    button.disabled = true;
    $("#article-generate-progress").textContent = "任务创建后可查看百分比，也可暂停、继续或停止。";
    try {
      const result = await api(`/accounts/${accountId}/article-jobs/generate`, {
        method: "POST",
        body: JSON.stringify({
          keyword_ids: keywordIds,
          product_id: $("#article-generate-product").value,
          provider: provider.provider,
          model: provider.model,
          articles_per_keyword: articlesPerKeyword,
          min_length: Number($("#article-min-length").value),
          max_length: Number($("#article-max-length").value),
          title_prompt: $("#article-title-prompt").value.trim(),
          content_prompt: $("#article-content-prompt").value.trim(),
          local_image_folder_id: $("#article-local-image-folder").value || null,
          output_mode: $("#article-output-mode").value
        })
      });
      state.generationJob = result;
      renderArticleJob("generate", result);
      scheduleArticleJobPoll("generate", result.id);
      toast("文章生成任务已开始");
    } catch (error) {
      $("#article-generate-error").textContent = error.message;
      $("#article-generate-progress").textContent = "任务未创建，请根据错误提示检查设置";
      button.disabled = false;
    }
  }

  const articleJobStatusLabels = { pending: "等待中", running: "执行中", paused: "已暂停", stopped: "已停止", completed: "已完成", failed: "任务失败" };
  const activeArticleJobStatuses = new Set(["pending", "running", "paused"]);

  function renderJobAt(prefix, job, container) {
    if (!container) return;
    container.hidden = !job && !container.classList.contains("standalone");
    if (!job) return;
    $(`#${prefix}-status`).textContent = articleJobStatusLabels[job.status] || job.status;
    $(`#${prefix}-status`).className = `badge ${job.status === "failed" || job.status === "stopped" ? "danger" : job.status === "paused" ? "warning" : ""}`;
    $(`#${prefix}-percent`).textContent = `${job.progress_percent}%`;
    $(`#${prefix}-count`).textContent = `${job.completed_count} / ${job.total_count} · 成功 ${job.success_count} · 失败 ${job.failed_count}`;
    $(`#${prefix}-bar`).style.width = `${job.progress_percent}%`;
    $(`#${prefix}-current`).textContent = job.current_item || (job.status === "completed" ? "全部处理完成" : job.status === "stopped" ? "任务已停止" : "等待下一项");
    $(`#${prefix}-message`).textContent = job.error_message || "";
    $(`#${prefix}-pause`).disabled = !["pending", "running"].includes(job.status);
    $(`#${prefix}-resume`).disabled = job.status !== "paused";
    $(`#${prefix}-stop`).disabled = !activeArticleJobStatuses.has(job.status);
  }

  function renderArticleJob(type, job) {
    if (type === "generate") {
      state.generationJob = job;
      renderJobAt("article-generation", job, $("#article-generation-job"));
      $("#article-generate-submit").disabled = Boolean(job && activeArticleJobStatuses.has(job.status));
      if (job) $("#article-generate-progress").textContent = `${articleJobStatusLabels[job.status]}：${job.progress_percent}%`;
      return;
    }
    state.publishJob = job;
    renderJobAt("article-publish", job, $("#article-publish-job"));
    renderJobAt("article-publish-page", job, $("#article-publish-job-page"));
  }

  function scheduleArticleJobPoll(type, id) {
    window.clearTimeout(state.articleJobTimers[type]);
    state.articleJobTimers[type] = window.setTimeout(() => pollArticleJob(type, id), 1200);
  }

  async function pollArticleJob(type, id) {
    try {
      const before = type === "generate" ? state.generationJob : state.publishJob;
      const job = await api(`/article-jobs/${id}`);
      renderArticleJob(type, job);
      if (!before || before.completed_count !== job.completed_count) await loadArticles(true);
      if (activeArticleJobStatuses.has(job.status)) return scheduleArticleJobPoll(type, id);
      await loadAccounts(true);
      toast(`${type === "generate" ? "文章生成" : "文章发布"}${articleJobStatusLabels[job.status]}：成功 ${job.success_count} 篇，失败 ${job.failed_count} 篇`, job.failed_count || job.status === "failed" ? "error" : "success");
    } catch (error) { toast(error.message, "error"); }
  }

  async function loadLatestArticleJob(type) {
    try {
      const job = await api(`/article-jobs/latest?type=${type}`);
      renderArticleJob(type, job);
      if (job && activeArticleJobStatuses.has(job.status)) scheduleArticleJobPoll(type, job.id);
    } catch (error) { toast(error.message, "error"); }
  }

  async function controlArticleJob(type, action) {
    const job = type === "generate" ? state.generationJob : state.publishJob;
    if (!job) return;
    try {
      const updated = await api(`/article-jobs/${job.id}/${action}`, { method: "POST" });
      renderArticleJob(type, updated);
      if (activeArticleJobStatuses.has(updated.status)) scheduleArticleJobPoll(type, updated.id);
      toast(action === "pause" ? "任务已暂停；当前正在处理的一篇完成后生效" : action === "resume" ? "任务已继续" : "已请求停止；当前正在处理的一篇完成后停止");
    } catch (error) { toast(error.message, "error"); }
  }

  function articleStatusLabel(value) {
    return { draft: "草稿", ready: "待发布", published: "已发布", failed: "失败" }[value] || value;
  }

  function selectedArticleStatus() {
    return $("[data-status].active")?.dataset.status || "draft";
  }

  function articleActionButtons(item) {
    const publishing = Boolean(state.publishJob && activeArticleJobStatuses.has(state.publishJob.status));
    const publish = item.status !== "published" ? `<button class="button button-primary article-publish" type="button" ${publishing ? "disabled" : ""}>发布</button>` : "";
    const view = item.status === "published" && item.published_url ? `<a class="button button-ghost article-view" href="${escapeHtml(item.published_url)}" target="_blank" rel="noopener noreferrer">查看</a>` : "";
    return `${publish}${view}<button class="button button-ghost article-edit" type="button">编辑</button><button class="button button-ghost danger-text article-delete" type="button">删除</button>`;
  }

  function updateArticleSelection() {
    const visibleIds = (state.articles.items || []).map((item) => item.id);
    const selectedVisible = visibleIds.filter((id) => state.selectedArticles.has(id));
    $("#article-selected-count").textContent = `已选 ${state.selectedArticles.size} 篇`;
    $("#article-select-all").checked = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
    $("#article-select-all").indeterminate = selectedVisible.length > 0 && selectedVisible.length < visibleIds.length;
    $("#article-mark-ready").disabled = state.selectedArticles.size === 0;
    $("#article-bulk-delete").disabled = state.selectedArticles.size === 0;
    const selectedItems = (state.articles.items || []).filter((item) => state.selectedArticles.has(item.id));
    $("#article-bulk-publish").disabled = !selectedItems.length || selectedItems.some((item) => item.status === "published") || Boolean(state.publishJob && activeArticleJobStatuses.has(state.publishJob.status));
  }

  function renderArticles() {
    const items = state.articles.items || [];
    const accountId = $("#article-account-filter").value;
    const account = state.accounts.find((item) => item.id === accountId);
    const category = { draft: "草稿箱", ready: "待发布", published: "已发布", failed: "发布失败" }[selectedArticleStatus()];
    $("#article-total").textContent = `${account ? account.display_name : "当前账号"} · ${category} ${state.articles.total || 0} 篇`;
    $("#article-list").innerHTML = items.map((item) => {
      const failure = item.status === "failed"
        ? `<span class="article-failure-reason"><b>失败原因：</b>${escapeHtml(item.error_message || "系统没有返回具体原因，请重新发布后查看")}</span>`
        : `<small>${item.content_length} 字${item.error_message ? ` · ${escapeHtml(item.error_message)}` : ""}</small>`;
      const publishTime = item.publish_attempted_at ? formatDateTime(item.publish_attempted_at) : "—";
      return `<div class="content-table article-row" data-article-id="${item.id}"><div class="article-title-cell"><input type="checkbox" ${state.selectedArticles.has(item.id) ? "checked" : ""} aria-label="选择${escapeHtml(item.title)}"><label><strong title="${escapeHtml(item.title)}">${escapeHtml(item.title || "未命名文章")}</strong>${failure}</label></div><span class="article-cell-muted">${escapeHtml(item.account_name)}</span><span class="article-cell-muted">${escapeHtml(item.keyword_text || "手动创建")}</span><span class="badge article-status ${item.status}">${articleStatusLabel(item.status)}</span><span class="article-cell-muted">${publishTime}</span><div class="article-actions">${articleActionButtons(item)}</div></div>`;
    }).join("");
    $("#article-empty").hidden = items.length !== 0;
    $("#article-empty h3").textContent = account ? "该账号还没有文章" : "请先添加知乎账号";
    $("#article-empty p").textContent = account ? `“${account.display_name}”暂时没有符合当前筛选条件的文章。` : "文章将按知乎账号独立归类，请先添加一个知乎账号。";
    $$(".article-title-cell input", $("#article-list")).forEach((input) => input.addEventListener("change", (event) => {
      const id = event.currentTarget.closest(".article-row").dataset.articleId;
      if (event.currentTarget.checked) state.selectedArticles.add(id); else state.selectedArticles.delete(id);
      updateArticleSelection();
    }));
    $$(".article-edit", $("#article-list")).forEach((button) => button.addEventListener("click", () => openArticleDialog(state.articles.items.find((item) => item.id === button.closest(".article-row").dataset.articleId))));
    $$(".article-publish", $("#article-list")).forEach((button) => button.addEventListener("click", publishArticle));
    $$(".article-delete", $("#article-list")).forEach((button) => button.addEventListener("click", () => deleteArticle(button.closest(".article-row").dataset.articleId)));
    const totalPages = Math.max(1, Math.ceil((state.articles.total || 0) / state.articlePageSize));
    $("#article-pagination").hidden = !state.articles.total;
    $("#article-page-info").textContent = `第 ${state.articlePage} / ${totalPages} 页 · 每页 100 篇 · 共 ${state.articles.total || 0} 篇`;
    $("#article-prev").disabled = state.articlePage <= 1;
    $("#article-next").disabled = state.articlePage >= totalPages;
    updateArticleSelection();
  }

  async function loadArticles(silent = false) {
    const params = new URLSearchParams({ limit: state.articlePageSize, offset: (state.articlePage - 1) * state.articlePageSize });
    const accountId = $("#article-account-filter").value;
    if (!accountId) {
      state.articles = { items: [], total: 0 };
      state.selectedArticles.clear();
      renderArticles();
      return;
    }
    const articleStatus = selectedArticleStatus();
    const query = $("#article-search").value.trim();
    params.set("account_id", accountId);
    params.set("status", articleStatus);
    if (query) params.set("q", query);
    try {
      const articles = await api(`/articles?${params.toString()}`);
      const totalPages = Math.max(1, Math.ceil(articles.total / state.articlePageSize));
      if (state.articlePage > totalPages) { state.articlePage = totalPages; return loadArticles(silent); }
      state.articles = articles;
      const visible = new Set(articles.items.map((item) => item.id));
      state.selectedArticles = new Set([...state.selectedArticles].filter((id) => visible.has(id)));
      renderArticles();
      if (!silent) toast("文章列表已刷新");
    } catch (error) { toast(error.message, "error"); }
  }

  async function syncPublishedArticles() {
    const accountId = $("#article-account-filter").value;
    const account = state.accounts.find((item) => item.id === accountId);
    if (!account) return toast("请先选择知乎账号", "error");
    if (!window.confirm(`确定从“${account.display_name}”的知乎创作中心同步文章吗？\n\n系统只会按完整标题匹配本地失败记录，不会导入无关文章，也不会重新发布。`)) return;
    const button = $("#article-sync");
    setBusy(button, true, "同步中…");
    try {
      const result = await api(`/accounts/${accountId}/articles/sync`, { method: "POST" });
      if (result.matched_count > 0) {
        $$('[data-status]').forEach((item) => item.classList.toggle("active", item.dataset.status === "published"));
        state.articlePage = 1;
        state.selectedArticles.clear();
      }
      await loadArticles(true);
      const unmatched = result.unmatched_failed_count ? `，仍有 ${result.unmatched_failed_count} 篇失败记录未匹配` : "";
      const unchanged = result.already_synced_count ? `，已同步 ${result.already_synced_count} 篇` : "";
      toast(`同步完成：读取 ${result.scanned_count} 篇，修正 ${result.matched_count} 篇${unchanged}${unmatched}`, result.unmatched_failed_count ? "error" : "success");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  function openArticleDialog(article) {
    if (!article) return;
    state.editingArticle = article;
    $("#article-edit-title").value = article.title;
    $("#article-edit-content").value = article.content;
    $("#article-edit-status").value = article.status;
    updateArticleEditLength();
    $("#article-edit-error").textContent = article.error_message || "";
    $("#article-dialog").hidden = false;
  }

  function closeArticleDialog() { $("#article-dialog").hidden = true; state.editingArticle = null; }
  function updateArticleEditLength() { $("#article-edit-length").textContent = `${$("#article-edit-content").value.replace(/\s/g, "").length} 字`; }

  async function saveArticle(event) {
    event.preventDefault();
    if (!state.editingArticle) return;
    const button = $("#article-submit");
    setBusy(button, true, "保存中…");
    try {
      await api(`/accounts/${state.editingArticle.account_id}/articles/${state.editingArticle.id}`, { method: "PATCH", body: JSON.stringify({ title: $("#article-edit-title").value.trim(), content: $("#article-edit-content").value, status: $("#article-edit-status").value }) });
      closeArticleDialog();
      await loadArticles(true);
      toast("文章已保存");
    } catch (error) { $("#article-edit-error").textContent = error.message; }
    finally { setBusy(button, false); }
  }

  async function deleteArticle(articleId) {
    const article = state.articles.items.find((item) => item.id === articleId);
    if (!article || !window.confirm(`确定删除文章“${article.title}”吗？`)) return;
    try {
      await api(`/accounts/${article.account_id}/articles/${article.id}`, { method: "DELETE" });
      await loadArticles(true); toast("文章已删除");
    } catch (error) { toast(error.message, "error"); }
  }

  async function publishArticle(event) {
    const articleId = event.currentTarget.closest(".article-row").dataset.articleId;
    const article = state.articles.items.find((item) => item.id === articleId);
    if (!article || !window.confirm(`确定使用“${article.account_name}”发布文章“${article.title}”吗？`)) return;
    await startPublishJob([article.id]);
  }

  async function startPublishJob(ids = [...state.selectedArticles]) {
    if (!ids.length) return;
    try {
      const job = await api("/article-jobs/publish", { method: "POST", body: JSON.stringify({ article_ids: ids }) });
      state.selectedArticles.clear();
      renderArticleJob("publish", job);
      renderArticles();
      scheduleArticleJobPoll("publish", job.id);
      toast(`已开始发布 ${ids.length} 篇文章`);
    } catch (error) { toast(error.message, "error"); }
  }

  async function publishSelectedArticles() {
    const ids = [...state.selectedArticles];
    if (!ids.length || !window.confirm(`确定将选中的 ${ids.length} 篇文章加入真实发布队列吗？`)) return;
    await startPublishJob(ids);
  }

  async function bulkArticleStatus() {
    const ids = [...state.selectedArticles];
    if (!ids.length) return;
    try {
      const result = await api("/articles/bulk-status", { method: "POST", body: JSON.stringify({ article_ids: ids, status: "ready" }) });
      state.selectedArticles.clear(); await loadArticles(true); toast(`已将 ${result.affected_count} 篇文章转为待发布`);
    } catch (error) { toast(error.message, "error"); }
  }

  async function bulkDeleteArticles() {
    const ids = [...state.selectedArticles];
    if (!ids.length || !window.confirm(`确定删除选中的 ${ids.length} 篇文章吗？`)) return;
    try {
      const result = await api("/articles/bulk-delete", { method: "POST", body: JSON.stringify({ article_ids: ids }) });
      state.selectedArticles.clear(); await loadArticles(true); toast(`已删除 ${result.affected_count} 篇文章`);
    } catch (error) { toast(error.message, "error"); }
  }

  function setNavigationActive(page, articleStatus = "", answerStatus = "") {
    $$(".nav-item").forEach((item) => {
      let active = item.dataset.page === page;
      if (active && page === "articles") active = item.dataset.articleStatus === (articleStatus || selectedArticleStatus());
      if (active && page === "answers") active = item.dataset.answerStatus === (answerStatus || "draft");
      item.classList.toggle("active", active);
      if (active) item.closest("details")?.setAttribute("open", "");
    });
  }

  function navigate(page, options = {}) {
    if (["users", "settings"].includes(page) && state.user?.role !== "admin") page = "overview";
    state.page = page;
    const articleStatus = options.articleStatus || (page === "articles" ? selectedArticleStatus() : "");
    const answerStatus = options.answerStatus || "";
    if (page === "articles" && articleStatus) {
      $$('[data-status]').forEach((item) => item.classList.toggle("active", item.dataset.status === articleStatus));
    }
    if (page === "answers" && answerStatus) {
      const answerTab = $(`[data-answer-status="${answerStatus}"]`);
      if (answerTab && !answerTab.classList.contains("active")) answerTab.click();
    }
    setNavigationActive(page, articleStatus, answerStatus);
    $$(".page").forEach((item) => item.classList.toggle("active-page", item.id === `${page}-page`));
    const articleTitles = { draft: "草稿文章", ready: "待发布文章", published: "已发布文章", failed: "发布失败文章" };
    const answerTitles = { draft: "草稿回答", ready: "待发布回答", published: "已发布回答", failed: "发布失败回答" };
    const titles = { overview: "运行概览", accounts: "知乎账号", products: "推广商品", "keyword-collect": "关键词采集", keywords: "关键词列表", "keyword-recycle": "回收关键词库", "local-media": "本地图片库", "article-generate": "生成文章", articles: articleTitles[articleStatus] || "文章列表", "article-publish": "发布任务", questions: "问题采集与列表", answers: answerTitles[answerStatus] || "回答列表", "auto-answer": "自动回答", schedules: "定时计划", logs: "运行日志", ai: "AI 配置", users: "用户管理", settings: "系统设置" };
    const kickers = { overview: "工作台", accounts: "账号", products: "账号", ai: "账号", "keyword-collect": "关键词", keywords: "关键词", "keyword-recycle": "关键词", "local-media": "文章", "article-generate": "文章", articles: "文章", "article-publish": "文章", questions: "问答", answers: "问答", "auto-answer": "问答", schedules: "任务与系统", logs: "任务与系统", users: "任务与系统", settings: "任务与系统" };
    $("#page-title").textContent = titles[page] || "运行概览";
    $("#page-kicker").textContent = kickers[page] || "工作台";
    $(".sidebar").classList.remove("open");
    if (page === "ai") loadProviders(true);
    if (["keyword-collect", "keywords"].includes(page)) loadKeywordData(true);
    if (page === "keyword-recycle") loadRecycledKeywords(true);
    if (page === "local-media") loadMedia(true);
    if (page === "products") loadProducts(true);
    if (page === "users") loadUsers(true);
    if (page === "article-generate") { loadArticleGenerator(false); loadLatestArticleJob("generate"); }
    if (page === "articles") { loadArticles(true); loadLatestArticleJob("publish"); }
    if (page === "article-publish") loadLatestArticleJob("publish");
  }

  function refreshCurrentPage() {
    if (state.page === "products") return loadProducts();
    if (["keyword-collect", "keywords"].includes(state.page)) return loadKeywordData();
    if (state.page === "keyword-recycle") return loadRecycledKeywords();
    if (state.page === "local-media") return loadMedia();
    if (state.page === "ai") return loadProviders();
    if (state.page === "users") return loadUsers();
    if (state.page === "article-generate") return loadArticleGenerator(false);
    if (state.page === "articles") return loadArticles();
    return loadAccounts();
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
    $("#refresh-button").addEventListener("click", refreshCurrentPage);
    $("#account-form").addEventListener("submit", createAccount);
    $("#account-search").addEventListener("input", renderAccounts);
    $("#user-search").addEventListener("input", renderUsers);
    $("#user-add").addEventListener("click", () => openUserDialog());
    $("#user-empty-add").addEventListener("click", () => openUserDialog());
    $("#user-form").addEventListener("submit", saveManagedUser);
    $("#user-dialog-close").addEventListener("click", closeUserDialog);
    $("#user-dialog-cancel").addEventListener("click", closeUserDialog);
    $("#user-dialog").addEventListener("click", (event) => { if (event.target.id === "user-dialog") closeUserDialog(); });
    $("#product-account").addEventListener("change", () => { $("#product-search").value = ""; loadProducts(true); });
    $("#product-search").addEventListener("input", renderProducts);
    $("#product-add").addEventListener("click", () => openProductDialog());
    $("#product-empty-add").addEventListener("click", () => openProductDialog());
    $("#product-form").addEventListener("submit", saveProduct);
    $("#product-dialog-close").addEventListener("click", closeProductDialog);
    $("#product-dialog-cancel").addEventListener("click", closeProductDialog);
    $("#product-dialog").addEventListener("click", (event) => { if (event.target.id === "product-dialog") closeProductDialog(); });
    $("#keyword-form").addEventListener("submit", startKeywordJob);
    ["#keyword-account", "#keyword-library-account", "#keyword-recycle-account"].forEach((selector) => $(selector).addEventListener("change", (event) => selectKeywordAccount(event.currentTarget.value)));
    $("#keyword-refresh").addEventListener("click", () => loadKeywordData());
    $("#folder-filter").addEventListener("change", () => { state.keywordPage = 1; state.selectedKeywords.clear(); loadKeywordData(true); });
    $("#keyword-search").addEventListener("input", () => { window.clearTimeout(state.keywordSearchTimer); state.keywordSearchTimer = window.setTimeout(() => { state.keywordPage = 1; state.selectedKeywords.clear(); loadKeywordData(true); }, 300); });
    $("#folder-create").addEventListener("click", createKeywordFolder);
    $("#folder-quick-create").addEventListener("click", createKeywordFolder);
    $("#folder-rename").addEventListener("click", renameKeywordFolder);
    $("#folder-delete").addEventListener("click", deleteKeywordFolder);
    $("#keyword-select-all").addEventListener("change", (event) => {
      (state.keywords.items || []).forEach((item) => event.currentTarget.checked ? state.selectedKeywords.add(item.id) : state.selectedKeywords.delete(item.id));
      renderKeywords();
    });
    $("#keyword-move").addEventListener("click", moveSelectedKeywords);
    $("#keyword-recycle").addEventListener("click", recycleSelectedKeywords);
    $("#keyword-delete").addEventListener("click", deleteSelectedKeywords);
    $("#keyword-prev").addEventListener("click", () => { if (state.keywordPage > 1) { state.keywordPage -= 1; state.selectedKeywords.clear(); loadKeywordData(true); } });
    $("#keyword-next").addEventListener("click", () => { const totalPages = Math.max(1, Math.ceil((state.keywords.total || 0) / state.keywordPageSize)); if (state.keywordPage < totalPages) { state.keywordPage += 1; state.selectedKeywords.clear(); loadKeywordData(true); } });
    $("#recycle-keyword-select-all").addEventListener("change", (event) => { (state.recycledKeywords.items || []).forEach((item) => event.currentTarget.checked ? state.selectedRecycledKeywords.add(item.id) : state.selectedRecycledKeywords.delete(item.id)); renderRecycledKeywords(); });
    $("#recycle-keyword-search").addEventListener("input", () => { window.clearTimeout(state.recycleKeywordSearchTimer); state.recycleKeywordSearchTimer = window.setTimeout(() => { state.recycleKeywordPage = 1; state.selectedRecycledKeywords.clear(); loadRecycledKeywords(true); }, 300); });
    $("#recycle-keyword-restore").addEventListener("click", restoreSelectedKeywords);
    $("#recycle-keyword-delete").addEventListener("click", deleteRecycledKeywords);
    $("#keyword-recycle-settings-save").addEventListener("click", saveKeywordRecycleSettings);
    $("#keyword-auto-restore-now").addEventListener("click", autoRestoreKeywordsNow);
    $("#recycle-keyword-prev").addEventListener("click", () => { if (state.recycleKeywordPage > 1) { state.recycleKeywordPage -= 1; state.selectedRecycledKeywords.clear(); loadRecycledKeywords(true); } });
    $("#recycle-keyword-next").addEventListener("click", () => { const pages = Math.max(1, Math.ceil((state.recycledKeywords.total || 0) / state.keywordPageSize)); if (state.recycleKeywordPage < pages) { state.recycleKeywordPage += 1; state.selectedRecycledKeywords.clear(); loadRecycledKeywords(true); } });
    $("#media-upload-form").addEventListener("submit", uploadMedia);
    $("#media-folder-create").addEventListener("click", createMediaFolder);
    $("#media-folder-rename").addEventListener("click", renameMediaFolder);
    $("#media-folder-delete").addEventListener("click", deleteMediaFolder);
    $("#media-folder-filter").addEventListener("change", () => { state.mediaPage = 1; state.selectedMedia.clear(); loadMedia(true); });
    $("#media-kind-filter").addEventListener("change", () => { state.mediaPage = 1; state.selectedMedia.clear(); loadMedia(true); });
    $("#media-search").addEventListener("input", () => { window.clearTimeout(state.mediaSearchTimer); state.mediaSearchTimer = window.setTimeout(() => { state.mediaPage = 1; state.selectedMedia.clear(); loadMedia(true); }, 300); });
    $("#media-select-all").addEventListener("change", (event) => { (state.media.items || []).forEach((item) => event.currentTarget.checked ? state.selectedMedia.add(item.id) : state.selectedMedia.delete(item.id)); renderMedia(); });
    $("#media-delete-selected").addEventListener("click", () => deleteMedia([...state.selectedMedia]));
    $("#media-prev").addEventListener("click", () => { if (state.mediaPage > 1) { state.mediaPage -= 1; state.selectedMedia.clear(); loadMedia(true); } });
    $("#media-next").addEventListener("click", () => { const pages = Math.max(1, Math.ceil((state.media.total || 0) / state.mediaPageSize)); if (state.mediaPage < pages) { state.mediaPage += 1; state.selectedMedia.clear(); loadMedia(true); } });
    $("#article-generate-account").addEventListener("change", () => { $("#article-generate-folder").value = "all"; $("#article-keyword-search").value = ""; loadArticleGenerator(true); });
    $("#article-generate-folder").addEventListener("change", () => { $("#article-keyword-search").value = ""; loadArticleGenerator(true); });
    $("#prompt-folder-filter").addEventListener("change", renderPromptLibrary);
    $("#prompt-template-select").addEventListener("change", selectPromptTemplate);
    $("#prompt-folder-create").addEventListener("click", createPromptFolder);
    $("#prompt-folder-rename").addEventListener("click", renamePromptFolder);
    $("#prompt-folder-delete").addEventListener("click", deletePromptFolder);
    $("#prompt-template-save").addEventListener("click", savePromptTemplate);
    $("#prompt-template-update").addEventListener("click", updatePromptTemplate);
    $("#article-title-prompt").addEventListener("input", markPromptTemplateChanged);
    $("#article-content-prompt").addEventListener("input", markPromptTemplateChanged);
    $("#prompt-template-rename").addEventListener("click", renamePromptTemplate);
    $("#prompt-template-delete").addEventListener("click", deletePromptTemplate);
    $("#article-keyword-search").addEventListener("input", renderArticleKeywordOptions);
    $("#article-keyword-select-all").addEventListener("change", (event) => {
      const query = $("#article-keyword-search").value.trim().toLowerCase();
      state.articleGenerateKeywords.filter((item) => item.keyword.toLowerCase().includes(query)).forEach((item) => event.currentTarget.checked ? state.selectedArticleKeywords.add(item.id) : state.selectedArticleKeywords.delete(item.id));
      renderArticleKeywordOptions();
    });
    $("#articles-per-keyword").addEventListener("input", updateArticleGenerateEstimate);
    $("#article-generate-form").addEventListener("submit", generateArticles);
    $("#article-output-mode").addEventListener("change", () => { $("#article-generate-progress").textContent = $("#article-output-mode").value === "immediate" ? "生成成功后立即发布；请确认知乎账号已经登录" : "生成成功后保存到草稿库"; });
    $("#article-generation-pause").addEventListener("click", () => controlArticleJob("generate", "pause"));
    $("#article-generation-resume").addEventListener("click", () => controlArticleJob("generate", "resume"));
    $("#article-generation-stop").addEventListener("click", () => controlArticleJob("generate", "stop"));
    $("#article-account-filter").addEventListener("change", () => { state.articlePage = 1; state.selectedArticles.clear(); loadArticles(true); });
    $("#article-sync").addEventListener("click", syncPublishedArticles);
    $$('[data-status]').forEach((button) => button.addEventListener("click", () => {
      $$('[data-status]').forEach((item) => item.classList.toggle("active", item === button));
      setNavigationActive("articles", button.dataset.status, "");
      $("#page-title").textContent = ({ draft: "草稿文章", ready: "待发布文章", published: "已发布文章", failed: "发布失败文章" })[button.dataset.status];
      state.articlePage = 1;
      state.selectedArticles.clear();
      loadArticles(true);
    }));
    $("#article-search").addEventListener("input", () => { window.clearTimeout(state.articleSearchTimer); state.articleSearchTimer = window.setTimeout(() => { state.articlePage = 1; state.selectedArticles.clear(); loadArticles(true); }, 300); });
    $("#article-select-all").addEventListener("change", (event) => { (state.articles.items || []).forEach((item) => event.currentTarget.checked ? state.selectedArticles.add(item.id) : state.selectedArticles.delete(item.id)); updateArticleSelection(); renderArticles(); });
    $("#article-mark-ready").addEventListener("click", bulkArticleStatus);
    $("#article-bulk-publish").addEventListener("click", publishSelectedArticles);
    $("#article-bulk-delete").addEventListener("click", bulkDeleteArticles);
    [["#article-publish-pause", "pause"], ["#article-publish-resume", "resume"], ["#article-publish-stop", "stop"], ["#article-publish-page-pause", "pause"], ["#article-publish-page-resume", "resume"], ["#article-publish-page-stop", "stop"]].forEach(([selector, action]) => $(selector).addEventListener("click", () => controlArticleJob("publish", action)));
    $("#article-prev").addEventListener("click", () => { if (state.articlePage > 1) { state.articlePage -= 1; state.selectedArticles.clear(); loadArticles(true); } });
    $("#article-next").addEventListener("click", () => { const pages = Math.max(1, Math.ceil((state.articles.total || 0) / state.articlePageSize)); if (state.articlePage < pages) { state.articlePage += 1; state.selectedArticles.clear(); loadArticles(true); } });
    $("#article-form").addEventListener("submit", saveArticle);
    $("#article-edit-content").addEventListener("input", updateArticleEditLength);
    $("#article-dialog-close").addEventListener("click", closeArticleDialog);
    $("#article-dialog-cancel").addEventListener("click", closeArticleDialog);
    $("#article-dialog").addEventListener("click", (event) => { if (event.target.id === "article-dialog") closeArticleDialog(); });
    $("#dialog-close").addEventListener("click", closeAccountDialog);
    $("#dialog-cancel").addEventListener("click", closeAccountDialog);
    $("#account-dialog").addEventListener("click", (event) => { if (event.target.id === "account-dialog") closeAccountDialog(); });
    $("#zhihu-login-close").addEventListener("click", closeZhihuLoginDialog);
    $("#zhihu-login-done").addEventListener("click", closeZhihuLoginDialog);
    $("#zhihu-login-open").addEventListener("click", openZhihuLoginScreenshot);
    $("#zhihu-login-refresh").addEventListener("click", pollZhihuLogin);
    $("#zhihu-login-screenshot").addEventListener("click", clickZhihuWebsite);
    $("#zhihu-browser-send").addEventListener("click", sendZhihuBrowserText);
    $("#zhihu-browser-text").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); sendZhihuBrowserText(); } });
    $$("[data-browser-action]").forEach((button) => button.addEventListener("click", () => sendZhihuBrowserAction({ action: button.dataset.browserAction }, button)));
    $$("[data-browser-key]").forEach((button) => button.addEventListener("click", () => sendZhihuBrowserAction({ action: "key", key: button.dataset.browserKey }, button)));
    $$("[data-browser-scroll]").forEach((button) => button.addEventListener("click", () => sendZhihuBrowserAction({ action: "scroll", delta_y: Number(button.dataset.browserScroll) }, button)));
    $("#zhihu-login-dialog").addEventListener("click", (event) => { if (event.target.id === "zhihu-login-dialog") closeZhihuLoginDialog(); });
    $("#menu-button").addEventListener("click", () => $(".sidebar").classList.toggle("open"));
    $$('[data-open-account]').forEach((button) => button.addEventListener("click", openAccountDialog));
    $$(".nav-item").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.page, { articleStatus: button.dataset.articleStatus, answerStatus: button.dataset.answerStatus })));
    $$('[data-page-link]').forEach((button) => button.addEventListener("click", () => navigate(button.dataset.pageLink)));
    $$(".nav-section").forEach((section) => section.addEventListener("toggle", () => { if (!section.open) return; $$(".nav-section").forEach((other) => { if (other !== section) other.open = false; }); }));
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeAccountDialog(); closeProductDialog(); closeUserDialog(); closeArticleDialog(); closeZhihuLoginDialog(); } });

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
