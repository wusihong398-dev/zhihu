(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    accounts: [], providers: [], products: [],
    questions: { items: [], total: 0 }, questionPage: 1, selectedQuestions: new Set(),
    answers: { items: [], total: 0 }, answerPage: 1, answerStatus: "draft", selectedAnswers: new Set(),
    editingAnswer: null, generationJob: null, publishJob: null, pollTimer: null,
  };
  const terminal = new Set(["completed", "failed", "stopped"]);

  function escapeHtml(value) { const node = document.createElement("div"); node.textContent = value ?? ""; return node.innerHTML; }
  function toast(message, type = "success") { const node = document.createElement("div"); node.className = `toast ${type}`; node.textContent = message; $("#toast-region").appendChild(node); setTimeout(() => node.remove(), 4500); }
  async function api(path, options = {}) {
    const token = sessionStorage.getItem("totod_token") || "";
    const response = await fetch(`/api${path}`, { credentials: "same-origin", headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.headers || {}) }, ...options });
    if (!response.ok) { let detail = `请求失败（HTTP ${response.status}）`; try { const body = await response.json(); detail = body.detail || detail; if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join("；"); } catch (_) {} throw new Error(detail); }
    if (response.status === 204) return null;
    return response.json();
  }
  function setBusy(button, busy, text) { if (!button) return; if (busy) { button.dataset.label = button.textContent; button.textContent = text; button.disabled = true; } else { button.textContent = button.dataset.label || button.textContent; button.disabled = false; } }
  function formatTime(value) { if (!value) return "—"; return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)); }
  function accountOptions() { return state.accounts.map((item) => `<option value="${item.id}">${escapeHtml(item.display_name)}</option>`).join(""); }

  async function loadBase() {
    try {
      const [accounts, providers] = await Promise.all([api("/accounts"), api("/ai/providers")]);
      state.accounts = accounts;
      state.providers = providers.filter((item) => item.enabled && item.has_api_key);
      const options = `<option value="">请选择知乎账号</option>${accountOptions()}`;
      ["#question-collect-account", "#question-list-account", "#answer-list-account", "#auto-answer-account"].forEach((id) => { const select = $(id); const old = select.value; select.innerHTML = options; if (accounts.some((item) => item.id === old)) select.value = old; else if (accounts.length === 1) select.value = accounts[0].id; });
      const provider = $("#answer-generate-provider"); provider.innerHTML = state.providers.length ? state.providers.map((item) => `<option value="${item.provider}" data-model="${escapeHtml(item.model)}">${escapeHtml(item.display_name)} · ${escapeHtml(item.model)}</option>`).join("") : `<option value="">请先在AI配置启用平台</option>`;
      const initial = $("#question-list-account").value;
      if (initial) { $("#question-collect-account").value = initial; await loadQuestionAccount(initial); }
      const answerAccount = $("#answer-list-account").value;
      if (answerAccount) await loadAnswers(true);
      const autoAccount = $("#auto-answer-account").value;
      if (autoAccount) await loadAutoSummary();
    } catch (error) { if (!/401|登录/.test(error.message)) toast(error.message, "error"); }
  }

  async function loadQuestionAccount(accountId) {
    if (!accountId) return;
    ["#question-collect-account", "#question-list-account"].forEach((id) => { if ($(`${id} option[value="${accountId}"]`)) $(id).value = accountId; });
    try {
      const products = await api(`/accounts/${accountId}/products?limit=500`);
      state.products = products.items.filter((item) => item.enabled);
      $("#answer-generate-product").innerHTML = state.products.length ? state.products.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("") : `<option value="">请先添加启用的推广商品</option>`;
    } catch (error) { toast(error.message, "error"); }
    state.questionPage = 1; state.selectedQuestions.clear(); await loadQuestions(true);
  }

  async function collectQuestions(event) {
    event.preventDefault();
    const accountId = $("#question-collect-account").value;
    const keywords = $("#question-keywords").value.split(/[\n,，;；]+/).map((item) => item.trim()).filter(Boolean);
    if (!accountId || !keywords.length) return toast("请选择账号并填写采集关键词", "error");
    const button = $("#question-collect-submit"); $("#question-collect-error").textContent = ""; setBusy(button, true, "正在搜索知乎…");
    try {
      const result = await api(`/accounts/${accountId}/questions/collect`, { method: "POST", body: JSON.stringify({ keywords, target_count: Number($("#question-target").value) }) });
      $("#question-list-account").value = accountId; state.questionPage = 1; await loadQuestions(true);
      toast(`采集完成：新增 ${result.added_count} 个，自动去重 ${result.duplicate_count} 个`);
    } catch (error) { $("#question-collect-error").textContent = error.message; toast(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  async function loadQuestions(silent = false) {
    const accountId = $("#question-list-account").value;
    if (!accountId) { state.questions = { items: [], total: 0 }; renderQuestions(); return; }
    const params = new URLSearchParams({ limit: "100", offset: String((state.questionPage - 1) * 100) });
    const q = $("#question-search").value.trim(); if (q) params.set("q", q);
    try { state.questions = await api(`/accounts/${accountId}/questions?${params}`); const pages = Math.max(1, Math.ceil(state.questions.total / 100)); if (state.questionPage > pages) { state.questionPage = pages; return loadQuestions(silent); } renderQuestions(); if (!silent) toast("问题库已刷新"); }
    catch (error) { toast(error.message, "error"); }
  }
  function renderQuestions() {
    const list = $("#question-list"); list.innerHTML = state.questions.items.map((item) => `<div class="qa-table qa-question-row ${state.selectedQuestions.has(item.id) ? "qa-row-selected" : ""}" data-question-id="${item.id}"><div class="qa-title-cell"><input class="question-check" type="checkbox" ${state.selectedQuestions.has(item.id) ? "checked" : ""}><div><strong title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</strong><small>${escapeHtml(item.excerpt || item.url)}</small></div></div><span class="qa-cell">${escapeHtml(item.keyword_text || "—")}</span><span class="qa-cell">${item.answer_count || 0}</span><span class="badge ${item.status === "answered" ? "ok" : "off"}">${item.status === "answered" ? "已生成" : item.status === "ignored" ? "已忽略" : "待处理"}</span><div class="qa-actions"><a class="button button-ghost" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">查看</a></div></div>`).join("");
    $("#question-empty").hidden = state.questions.total > 0; $("#question-total").textContent = `共 ${state.questions.total} 个问题`;
    const pages = Math.max(1, Math.ceil(state.questions.total / 100)); $("#question-pagination").hidden = !state.questions.total; $("#question-page-info").textContent = `第 ${state.questionPage} / ${pages} 页 · 每页100个`; $("#question-prev").disabled = state.questionPage <= 1; $("#question-next").disabled = state.questionPage >= pages;
    $$(".question-check", list).forEach((box) => box.addEventListener("change", () => { const id = box.closest("[data-question-id]").dataset.questionId; box.checked ? state.selectedQuestions.add(id) : state.selectedQuestions.delete(id); renderQuestions(); }));
    updateQuestionSelection();
  }
  function updateQuestionSelection() { const count = state.selectedQuestions.size; $("#question-selected-count").textContent = `已选 ${count} 个`; $("#answer-generate-submit").disabled = !count; $("#question-delete-selected").disabled = !count; const visible = state.questions.items.map((item) => item.id); $("#question-select-all").checked = visible.length > 0 && visible.every((id) => state.selectedQuestions.has(id)); }
  async function deleteSelectedQuestions() { if (!state.selectedQuestions.size || !confirm(`确定删除所选 ${state.selectedQuestions.size} 个问题及其回答吗？`)) return; const accountId = $("#question-list-account").value; try { await api(`/accounts/${accountId}/questions/bulk-delete`, { method: "POST", body: JSON.stringify({ question_ids: [...state.selectedQuestions] }) }); state.selectedQuestions.clear(); await loadQuestions(true); toast("所选问题已删除"); } catch (error) { toast(error.message, "error"); } }

  async function generateAnswers() {
    const accountId = $("#question-list-account").value;
    const productId = $("#answer-generate-product").value;
    const providerSelect = $("#answer-generate-provider"); const provider = providerSelect.value; const model = providerSelect.selectedOptions[0]?.dataset.model || "";
    if (!state.selectedQuestions.size || !productId || !provider) return toast("请选择问题、推广商品和已配置的AI平台", "error");
    const button = $("#answer-generate-submit"); setBusy(button, true, "正在创建任务…"); $("#answer-generate-error").textContent = "";
    try {
      state.generationJob = await api(`/accounts/${accountId}/answer-jobs/generate`, { method: "POST", body: JSON.stringify({ question_ids: [...state.selectedQuestions], product_id: productId, provider, model, min_length: Number($("#answer-min-length").value), max_length: Number($("#answer-max-length").value), prompt: $("#answer-prompt").value, ready_after_generate: $("#answer-ready-after-generate").checked }) });
      pollJobs(); toast("回答生成任务已开始");
    } catch (error) { $("#answer-generate-error").textContent = error.message; setBusy(button, false); }
  }

  async function loadAnswers(silent = false) {
    const accountId = $("#answer-list-account").value;
    if (!accountId) { state.answers = { items: [], total: 0 }; renderAnswers(); return; }
    const params = new URLSearchParams({ limit: "100", offset: String((state.answerPage - 1) * 100), status: state.answerStatus }); const q = $("#answer-search").value.trim(); if (q) params.set("q", q);
    try { state.answers = await api(`/accounts/${accountId}/answers?${params}`); const pages = Math.max(1, Math.ceil(state.answers.total / 100)); if (state.answerPage > pages) { state.answerPage = pages; return loadAnswers(silent); } renderAnswers(); if (!silent) toast("回答列表已刷新"); }
    catch (error) { toast(error.message, "error"); }
  }
  function answerStatusLabel(value) { return ({ draft: "草稿", ready: "待发布", published: "已发布", failed: "失败" })[value] || value; }
  function renderAnswers() {
    const list = $("#answer-list"); list.innerHTML = state.answers.items.map((item) => { const time = item.published_at || item.publish_attempted_at; const action = item.status === "published" && item.published_url ? `<a class="button button-ghost" href="${escapeHtml(item.published_url)}" target="_blank" rel="noopener">查看</a>` : `<button class="button button-primary answer-publish-one">发布</button>`; return `<div class="qa-table qa-answer-row ${state.selectedAnswers.has(item.id) ? "qa-row-selected" : ""}" data-answer-id="${item.id}"><div class="qa-title-cell"><input class="answer-check" type="checkbox" ${state.selectedAnswers.has(item.id) ? "checked" : ""}><div><strong title="${escapeHtml(item.question_title)}">${escapeHtml(item.question_title)}</strong><small>${item.content_length}字 · ${escapeHtml(item.keyword_text || "无关键词")}${item.error_message ? `<b class="qa-failure">失败原因：${escapeHtml(item.error_message)}</b>` : ""}</small></div></div><span class="qa-cell">${escapeHtml(item.product_name || "—")}</span><span class="badge qa-status ${item.status}">${answerStatusLabel(item.status)}</span><span class="qa-cell">${formatTime(time)}</span><div class="qa-actions">${action}<button class="button button-ghost answer-edit">编辑</button><button class="button button-ghost danger-text answer-delete">删除</button></div></div>`; }).join("");
    $("#answer-empty").hidden = state.answers.total > 0; $("#answer-total").textContent = `共 ${state.answers.total} 个回答`;
    const pages = Math.max(1, Math.ceil(state.answers.total / 100)); $("#answer-pagination").hidden = !state.answers.total; $("#answer-page-info").textContent = `第 ${state.answerPage} / ${pages} 页 · 每页100个`; $("#answer-prev").disabled = state.answerPage <= 1; $("#answer-next").disabled = state.answerPage >= pages;
    $$(".answer-check", list).forEach((box) => box.addEventListener("change", () => { const id = box.closest("[data-answer-id]").dataset.answerId; box.checked ? state.selectedAnswers.add(id) : state.selectedAnswers.delete(id); renderAnswers(); }));
    $$(".answer-edit", list).forEach((button) => button.addEventListener("click", () => openAnswerDialog(button.closest("[data-answer-id]").dataset.answerId)));
    $$(".answer-delete", list).forEach((button) => button.addEventListener("click", () => deleteAnswer(button.closest("[data-answer-id]").dataset.answerId)));
    $$(".answer-publish-one", list).forEach((button) => button.addEventListener("click", () => startPublish([button.closest("[data-answer-id]").dataset.answerId])));
    updateAnswerSelection();
  }
  function updateAnswerSelection() { const count = state.selectedAnswers.size; $("#answer-selected-count").textContent = `已选 ${count} 个`; ["#answer-publish-selected", "#answer-mark-ready", "#answer-delete-selected"].forEach((id) => $(id).disabled = !count); const visible = state.answers.items.map((item) => item.id); $("#answer-select-all").checked = visible.length > 0 && visible.every((id) => state.selectedAnswers.has(id)); }

  function openAnswerDialog(id) { const item = state.answers.items.find((answer) => answer.id === id); if (!item) return; state.editingAnswer = item; $("#answer-edit-question").textContent = item.question_title; $("#answer-edit-question").href = item.question_url; $("#answer-edit-content").value = item.content; $("#answer-edit-status").value = item.status === "published" ? "draft" : item.status; $("#answer-edit-status").disabled = item.status === "published"; $("#answer-edit-length").textContent = `${item.content.replace(/\s/g, "").length} 字`; $("#answer-edit-error").textContent = item.error_message || ""; $("#answer-dialog").hidden = false; }
  function closeAnswerDialog() { $("#answer-dialog").hidden = true; state.editingAnswer = null; }
  async function saveAnswer(event) { event.preventDefault(); if (!state.editingAnswer) return; const button = $("#answer-edit-submit"); setBusy(button, true, "保存中…"); try { const body = { content: $("#answer-edit-content").value }; if (!$("#answer-edit-status").disabled) body.status = $("#answer-edit-status").value; await api(`/accounts/${state.editingAnswer.account_id}/answers/${state.editingAnswer.id}`, { method: "PATCH", body: JSON.stringify(body) }); closeAnswerDialog(); await loadAnswers(true); toast("回答已保存"); } catch (error) { $("#answer-edit-error").textContent = error.message; } finally { setBusy(button, false); } }
  async function deleteAnswer(id) { if (!confirm("确定删除这个回答吗？")) return; const accountId = $("#answer-list-account").value; try { await api(`/accounts/${accountId}/answers/${id}`, { method: "DELETE" }); state.selectedAnswers.delete(id); await loadAnswers(true); toast("回答已删除"); } catch (error) { toast(error.message, "error"); } }
  async function bulkAnswer(path, body, message) { const accountId = $("#answer-list-account").value; try { await api(`/accounts/${accountId}/answers/${path}`, { method: "POST", body: JSON.stringify(body) }); state.selectedAnswers.clear(); await loadAnswers(true); toast(message); } catch (error) { toast(error.message, "error"); } }

  async function startPublish(ids) { if (!ids.length) return; try { state.publishJob = await api("/answer-jobs/publish/start", { method: "POST", body: JSON.stringify({ answer_ids: ids }) }); $("#answer-job-inline").hidden = false; pollJobs(); toast("回答发布任务已开始"); } catch (error) { toast(error.message, "error"); } }
  function renderJob(job, prefix) { const empty = !job; $(`#${prefix}-status`).textContent = empty ? "暂无任务" : ({ pending: "等待中", running: "运行中", paused: "已暂停", stopped: "已停止", completed: "已完成", failed: "任务失败" })[job.status]; $(`#${prefix}-percent`).textContent = `${empty ? 0 : job.progress_percent}%`; $(`#${prefix}-count`).textContent = empty ? "0 / 0" : `${job.completed_count} / ${job.total_count} · 成功 ${job.success_count} · 失败 ${job.failed_count}`; $(`#${prefix}-bar`).style.width = `${empty ? 0 : job.progress_percent}%`; $(`#${prefix}-current`).textContent = empty ? "暂无任务" : job.current_item || "全部处理完成"; $(`#${prefix}-message`).textContent = empty ? "" : job.error_message || ""; const active = job && !terminal.has(job.status); $(`#${prefix}-pause`).disabled = !active || job.status !== "running"; $(`#${prefix}-resume`).disabled = !active || !["paused", "pending"].includes(job.status); $(`#${prefix}-stop`).disabled = !active; }
  async function pollJobs() {
    if (state.pollTimer) return;
    const tick = async () => {
      try {
        if (state.generationJob && !terminal.has(state.generationJob.status)) { state.generationJob = await api(`/answer-jobs/${state.generationJob.id}`); $("#question-selected-count").textContent = `生成 ${state.generationJob.progress_percent}% · 成功${state.generationJob.success_count} · 失败${state.generationJob.failed_count}`; if (terminal.has(state.generationJob.status)) { setBusy($("#answer-generate-submit"), false); state.selectedQuestions.clear(); await loadQuestions(true); toast(state.generationJob.failed_count ? "回答生成完成，部分失败请到回答列表查看" : "回答生成完成", state.generationJob.failed_count ? "error" : "success"); } }
        if (state.publishJob && !terminal.has(state.publishJob.status)) { state.publishJob = await api(`/answer-jobs/${state.publishJob.id}`); renderJob(state.publishJob, "answer-job"); renderJob(state.publishJob, "auto-job"); if (terminal.has(state.publishJob.status)) { await loadAnswers(true); await loadAutoSummary(); toast(state.publishJob.failed_count ? "发布任务完成，失败原因已保存" : "回答发布完成", state.publishJob.failed_count ? "error" : "success"); } }
      } catch (error) { toast(error.message, "error"); }
      if ((!state.generationJob || terminal.has(state.generationJob.status)) && (!state.publishJob || terminal.has(state.publishJob.status))) { clearInterval(state.pollTimer); state.pollTimer = null; }
    };
    await tick(); if ((state.generationJob && !terminal.has(state.generationJob.status)) || (state.publishJob && !terminal.has(state.publishJob.status))) state.pollTimer = setInterval(tick, 1200);
  }
  async function loadLatestPublish(accountId) { if (!accountId) return; try { state.publishJob = await api(`/answer-jobs/latest?account_id=${accountId}&type=publish`); if (state.publishJob) { $("#answer-job-inline").hidden = false; renderJob(state.publishJob, "answer-job"); renderJob(state.publishJob, "auto-job"); if (!terminal.has(state.publishJob.status)) pollJobs(); } else { renderJob(null, "auto-job"); } } catch (error) { toast(error.message, "error"); } }
  async function controlJob(action) { if (!state.publishJob) return; try { state.publishJob = await api(`/answer-jobs/${state.publishJob.id}/${action}`, { method: "POST" }); renderJob(state.publishJob, "answer-job"); renderJob(state.publishJob, "auto-job"); if (action === "resume") pollJobs(); } catch (error) { toast(error.message, "error"); } }

  async function loadAutoSummary() { const accountId = $("#auto-answer-account").value; $("#auto-answer-run").disabled = !accountId; if (!accountId) return; try { const data = await api(`/accounts/${accountId}/auto-answer/summary`); $("#auto-daily-limit").textContent = data.daily_limit; $("#auto-attempted").textContent = data.attempted_today; $("#auto-remaining").textContent = data.remaining_today; $("#auto-ready").textContent = data.ready_count; $("#auto-answer-run").disabled = !data.remaining_today || !data.ready_count; await loadLatestPublish(accountId); } catch (error) { toast(error.message, "error"); } }
  async function runAutoAnswer() { const accountId = $("#auto-answer-account").value; const account = state.accounts.find((item) => item.id === accountId); if (!account || !confirm(`确定按“${account.display_name}”的今日剩余额度发布待发布回答吗？`)) return; const button = $("#auto-answer-run"); setBusy(button, true, "正在创建队列…"); try { const result = await api(`/accounts/${accountId}/auto-answer/run`, { method: "POST" }); if (!result.job) return toast(result.attempted_today >= result.daily_limit ? "今日回答额度已用完" : "没有待发布回答", "error"); state.publishJob = result.job; renderJob(state.publishJob, "auto-job"); pollJobs(); toast(`已加入 ${result.queued_count} 个回答`); } catch (error) { toast(error.message, "error"); } finally { setBusy(button, false); } }

  async function activate(page) { if (!state.accounts.length) await loadBase(); if (page === "questions") { const id = $("#question-list-account").value; if (id) await loadQuestionAccount(id); } if (page === "answers") { await loadAnswers(true); const id = $("#answer-list-account").value; if (id) await loadLatestPublish(id); } if (page === "auto-answer") await loadAutoSummary(); }
  function bind() {
    $("#question-collect-form").addEventListener("submit", collectQuestions);
    $("#question-collect-account").addEventListener("change", (event) => loadQuestionAccount(event.target.value));
    $("#question-list-account").addEventListener("change", (event) => loadQuestionAccount(event.target.value));
    $("#question-search").addEventListener("input", () => { clearTimeout(state.questionSearchTimer); state.questionSearchTimer = setTimeout(() => { state.questionPage = 1; loadQuestions(true); }, 300); });
    $("#question-select-all").addEventListener("change", (event) => { state.questions.items.forEach((item) => event.target.checked ? state.selectedQuestions.add(item.id) : state.selectedQuestions.delete(item.id)); renderQuestions(); });
    $("#question-delete-selected").addEventListener("click", deleteSelectedQuestions); $("#answer-generate-submit").addEventListener("click", generateAnswers);
    $("#question-prev").addEventListener("click", () => { state.questionPage--; loadQuestions(true); }); $("#question-next").addEventListener("click", () => { state.questionPage++; loadQuestions(true); });
    $("#answer-list-account").addEventListener("change", async () => { state.answerPage = 1; state.selectedAnswers.clear(); await loadAnswers(true); await loadLatestPublish($("#answer-list-account").value); });
    $("#answer-search").addEventListener("input", () => { clearTimeout(state.answerSearchTimer); state.answerSearchTimer = setTimeout(() => { state.answerPage = 1; loadAnswers(true); }, 300); });
    $$("[data-answer-status]").forEach((button) => button.addEventListener("click", () => { $$("[data-answer-status]").forEach((item) => item.classList.toggle("active", item === button)); state.answerStatus = button.dataset.answerStatus; state.answerPage = 1; state.selectedAnswers.clear(); $$(".nav-item[data-page='answers']").forEach((item) => item.classList.toggle("active", item.dataset.answerStatus === state.answerStatus)); const title = $("#page-title"); if (title) title.textContent = ({ draft: "草稿回答", ready: "待发布回答", published: "已发布回答", failed: "发布失败回答" })[state.answerStatus]; loadAnswers(true); }));
    $("#answer-select-all").addEventListener("change", (event) => { state.answers.items.forEach((item) => event.target.checked ? state.selectedAnswers.add(item.id) : state.selectedAnswers.delete(item.id)); renderAnswers(); });
    $("#answer-publish-selected").addEventListener("click", () => startPublish([...state.selectedAnswers])); $("#answer-mark-ready").addEventListener("click", () => bulkAnswer("bulk-status", { answer_ids: [...state.selectedAnswers], status: "ready" }, "已转入待发布")); $("#answer-delete-selected").addEventListener("click", () => { if (confirm(`确定删除所选 ${state.selectedAnswers.size} 个回答吗？`)) bulkAnswer("bulk-delete", { answer_ids: [...state.selectedAnswers] }, "所选回答已删除"); });
    $("#answer-prev").addEventListener("click", () => { state.answerPage--; loadAnswers(true); }); $("#answer-next").addEventListener("click", () => { state.answerPage++; loadAnswers(true); });
    $("#answer-dialog-close").addEventListener("click", closeAnswerDialog); $("#answer-dialog-cancel").addEventListener("click", closeAnswerDialog); $("#answer-edit-form").addEventListener("submit", saveAnswer); $("#answer-edit-content").addEventListener("input", () => $("#answer-edit-length").textContent = `${$("#answer-edit-content").value.replace(/\s/g, "").length} 字`);
    ["answer-job", "auto-job"].forEach((prefix) => { $(`#${prefix}-pause`).addEventListener("click", () => controlJob("pause")); $(`#${prefix}-resume`).addEventListener("click", () => controlJob("resume")); $(`#${prefix}-stop`).addEventListener("click", () => controlJob("stop")); });
    $("#auto-answer-account").addEventListener("change", loadAutoSummary); $("#auto-answer-run").addEventListener("click", runAutoAnswer);
    $$("[data-page], [data-page-link]").forEach((button) => button.addEventListener("click", () => { const page = button.dataset.page || button.dataset.pageLink; if (["questions", "answers", "auto-answer"].includes(page)) setTimeout(() => activate(page), 50); }));
    $("#login-form").addEventListener("submit", () => setTimeout(loadBase, 900)); $("#refresh-button").addEventListener("click", () => { const active = $(".nav-item.active")?.dataset.page; if (["questions", "answers", "auto-answer"].includes(active)) setTimeout(() => activate(active), 100); });
  }
  document.addEventListener("DOMContentLoaded", () => { bind(); loadBase(); });
})();
