(() => {
  "use strict";

  const state = { token: sessionStorage.getItem("totod_token") || "", user: null, accounts: [], providers: [], keywords: [], keywordFolders: [], selectedKeywords: new Set(), keywordPage: 1, keywordPageSize: 100, keywordJob: null, page: "overview", pollTimer: null };
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
    window.clearTimeout(state.pollTimer);
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
    renderKeywordAccountOptions();
  }

  async function loadAccounts(silent = false) {
    try {
      state.accounts = await api("/accounts");
      renderAll();
      if (state.page === "keywords" && $("#keyword-account").value) await loadKeywordData(true);
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
    const select = $("#keyword-account");
    const previous = select.value;
    select.innerHTML = `<option value="">请先选择账号</option>${state.accounts.map((account) => `<option value="${account.id}">${escapeHtml(account.display_name)}</option>`).join("")}`;
    if (state.accounts.some((account) => account.id === previous)) select.value = previous;
    else if (state.accounts.length === 1) select.value = state.accounts[0].id;
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
    const accountId = $("#keyword-account").value;
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
      const query = filter === "unfiled" ? "&unfiled=true" : !["all", ""].includes(filter) ? `&folder_id=${encodeURIComponent(filter)}` : "";
      const offset = (state.keywordPage - 1) * state.keywordPageSize;
      const [job, folders, keywords] = await Promise.all([
        api(`/accounts/${accountId}/keyword-jobs/latest`),
        api(`/accounts/${accountId}/keyword-folders`),
        api(`/accounts/${accountId}/keywords?limit=${state.keywordPageSize}&offset=${offset}${query}`)
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
    const accountId = $("#keyword-account").value;
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
    const accountId = $("#keyword-account").value;
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
    const accountId = $("#keyword-account").value;
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
    const accountId = $("#keyword-account").value;
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
      const result = await api(`/accounts/${$("#keyword-account").value}/keywords/folder`, { method: "PATCH", body: JSON.stringify({ keyword_ids: ids, folder_id: $("#keyword-move-folder").value || null }) });
      state.selectedKeywords.clear();
      await loadKeywordData(true);
      toast(`已移动 ${result.affected_count} 个关键词`);
    } catch (error) { toast(error.message, "error"); }
  }

  async function deleteSelectedKeywords() {
    const ids = [...state.selectedKeywords];
    if (!ids.length || !window.confirm(`确定删除选中的 ${ids.length} 个关键词吗？`)) return;
    try {
      const result = await api(`/accounts/${$("#keyword-account").value}/keywords/bulk-delete`, { method: "POST", body: JSON.stringify({ keyword_ids: ids }) });
      state.selectedKeywords.clear();
      await loadKeywordData(true);
      toast(`已删除 ${result.affected_count} 个关键词`);
    } catch (error) { toast(error.message, "error"); }
  }

  function navigate(page) {
    state.page = page;
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
    $$(".page").forEach((item) => item.classList.toggle("active-page", item.id === `${page}-page`));
    const titles = { overview: "运行概览", accounts: "知乎账号", products: "推广商品", keywords: "关键词采集", "article-generate": "文章生成", articles: "文章列表", "article-publish": "自动发布", questions: "问题采集", answers: "回答列表", "auto-answer": "自动回答", schedules: "定时计划", logs: "运行日志", ai: "AI 配置", settings: "系统设置" };
    const kickers = { overview: "工作台", accounts: "账号与素材", products: "账号与素材", keywords: "关键词中心", "article-generate": "文章运营", articles: "文章运营", "article-publish": "文章运营", questions: "回答运营", answers: "回答运营", "auto-answer": "回答运营", schedules: "任务与系统", logs: "任务与系统", ai: "任务与系统", settings: "任务与系统" };
    $("#page-title").textContent = titles[page] || "运行概览";
    $("#page-kicker").textContent = kickers[page] || "工作台";
    $(".sidebar").classList.remove("open");
    if (page === "ai" && !state.providers.length) loadProviders(true);
    if (page === "keywords") loadKeywordData(true);
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
    $("#keyword-form").addEventListener("submit", startKeywordJob);
    $("#keyword-account").addEventListener("change", () => { $("#folder-filter").value = "all"; state.keywordPage = 1; state.selectedKeywords.clear(); loadKeywordData(true); });
    $("#keyword-refresh").addEventListener("click", () => loadKeywordData());
    $("#folder-filter").addEventListener("change", () => { state.keywordPage = 1; state.selectedKeywords.clear(); loadKeywordData(true); });
    $("#folder-create").addEventListener("click", createKeywordFolder);
    $("#folder-quick-create").addEventListener("click", createKeywordFolder);
    $("#folder-rename").addEventListener("click", renameKeywordFolder);
    $("#folder-delete").addEventListener("click", deleteKeywordFolder);
    $("#keyword-select-all").addEventListener("change", (event) => {
      (state.keywords.items || []).forEach((item) => event.currentTarget.checked ? state.selectedKeywords.add(item.id) : state.selectedKeywords.delete(item.id));
      renderKeywords();
    });
    $("#keyword-move").addEventListener("click", moveSelectedKeywords);
    $("#keyword-delete").addEventListener("click", deleteSelectedKeywords);
    $("#keyword-prev").addEventListener("click", () => { if (state.keywordPage > 1) { state.keywordPage -= 1; state.selectedKeywords.clear(); loadKeywordData(true); } });
    $("#keyword-next").addEventListener("click", () => { const totalPages = Math.max(1, Math.ceil((state.keywords.total || 0) / state.keywordPageSize)); if (state.keywordPage < totalPages) { state.keywordPage += 1; state.selectedKeywords.clear(); loadKeywordData(true); } });
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
