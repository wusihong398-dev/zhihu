(function promptLibraryModule() {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = { kind: "article", folders: [], articles: [], answers: [], selectedId: "" };

  function escapeHtml(value) { const node = document.createElement("div"); node.textContent = value ?? ""; return node.innerHTML; }
  function toast(message, type = "success") { const node = document.createElement("div"); node.className = `toast ${type}`; node.textContent = message; $("#toast-region").appendChild(node); setTimeout(() => node.remove(), 4500); }
  async function api(path, options = {}) {
    const token = sessionStorage.getItem("totod_token") || "";
    const response = await fetch(`/api${path}`, { credentials: "same-origin", headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) }, ...options });
    if (!response.ok) { let detail = `请求失败（HTTP ${response.status}）`; try { const body = await response.json(); detail = body.detail || detail; } catch (_) {} throw new Error(detail); }
    if (response.status === 204) return null;
    return response.json();
  }
  function setBusy(button, busy, label) { if (busy) { button.dataset.label = button.textContent; button.textContent = label; button.disabled = true; } else { button.textContent = button.dataset.label || button.textContent; button.disabled = false; } }
  function currentItems() { return state.kind === "article" ? state.articles : state.answers; }
  function activeFolderId() { const value = $("#prompt-library-folder-filter").value; return ["all", "unfiled"].includes(value) ? "" : value; }
  function visibleItems() {
    const folder = $("#prompt-library-folder-filter").value;
    const query = $("#prompt-library-search").value.trim().toLowerCase();
    return currentItems().filter((item) => {
      const folderMatch = folder === "all" || (folder === "unfiled" ? !item.folder_id : item.folder_id === folder);
      const text = state.kind === "article" ? `${item.name} ${item.title_prompt} ${item.content_prompt}` : `${item.name} ${item.prompt}`;
      return folderMatch && (!query || text.toLowerCase().includes(query));
    });
  }
  function renderFolders() {
    const filter = $("#prompt-library-folder-filter");
    const editor = $("#prompt-library-folder");
    const oldFilter = filter.value || "all";
    const oldEditor = editor.value;
    const options = state.folders.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}（${item.template_count}）</option>`).join("");
    filter.innerHTML = `<option value="all">全部模板</option><option value="unfiled">未归档</option>${options}`;
    editor.innerHTML = `<option value="">未归档</option>${state.folders.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("")}`;
    filter.value = ["all", "unfiled"].includes(oldFilter) || state.folders.some((item) => item.id === oldFilter) ? oldFilter : "all";
    if (state.folders.some((item) => item.id === oldEditor)) editor.value = oldEditor;
    const hasFolder = Boolean(activeFolderId());
    $("#prompt-library-folder-rename").disabled = !hasFolder;
    $("#prompt-library-folder-delete").disabled = !hasFolder;
    $("#prompt-library-folder-count").textContent = `${state.folders.length} 个`;
    $("#prompt-library-article-count").textContent = state.articles.length;
    $("#prompt-library-answer-count").textContent = state.answers.length;
  }
  function renderList() {
    const items = visibleItems();
    $("#prompt-library-list").innerHTML = items.map((item) => `<button class="prompt-library-item ${item.id === state.selectedId ? "active" : ""}" type="button" data-template-id="${item.id}"><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.folder_name || "未归档")}</small></span><i>${state.kind === "article" ? "标题＋正文" : "回答"}</i></button>`).join("");
    $("#prompt-library-list").hidden = !items.length;
    $("#prompt-library-empty").hidden = Boolean(items.length);
    $$(".prompt-library-item", $("#prompt-library-list")).forEach((button) => button.addEventListener("click", () => selectTemplate(button.dataset.templateId)));
  }
  function renderEditor() {
    const item = currentItems().find((entry) => entry.id === state.selectedId);
    $("#prompt-library-editor-title").textContent = item ? `编辑：${item.name}` : `新建${state.kind === "article" ? "文章" : "回答"}提示词模板`;
    $("#prompt-library-article-fields").hidden = state.kind !== "article";
    $("#prompt-library-answer-fields").hidden = state.kind !== "answer";
    $("#prompt-library-save-current").disabled = !item;
    $("#prompt-library-template-rename").disabled = !item;
    $("#prompt-library-template-delete").disabled = !item;
  }
  function selectTemplate(id) {
    const item = currentItems().find((entry) => entry.id === id);
    if (!item) return;
    state.selectedId = id;
    $("#prompt-library-name").value = item.name;
    $("#prompt-library-folder").value = item.folder_id || "";
    if (state.kind === "article") {
      $("#prompt-library-title-prompt").value = item.title_prompt;
      $("#prompt-library-content-prompt").value = item.content_prompt;
    } else $("#prompt-library-answer-prompt").value = item.prompt;
    $("#prompt-library-error").textContent = "";
    renderList(); renderEditor();
  }
  function newTemplate() {
    state.selectedId = "";
    $("#prompt-library-name").value = "";
    $("#prompt-library-folder").value = activeFolderId();
    if (state.kind === "article") {
      $("#prompt-library-title-prompt").value = $("#article-title-prompt")?.value || "围绕{关键词}拟一个自然、有吸引力、适合知乎阅读的标题。";
      $("#prompt-library-content-prompt").value = $("#article-content-prompt")?.value || "围绕{关键词}写一篇专业、自然、有实际帮助的知乎文章。";
    } else $("#prompt-library-answer-prompt").value = $("#answer-prompt")?.value || "请先专业、真实地回答问题，再结合商品资料自然说明适用场景。";
    $("#prompt-library-error").textContent = "";
    renderList(); renderEditor();
  }
  function formPayload() {
    const name = $("#prompt-library-name").value.trim();
    if (!name) throw new Error("请填写模板名称");
    const folder_id = $("#prompt-library-folder").value || null;
    if (state.kind === "article") {
      const title_prompt = $("#prompt-library-title-prompt").value.trim();
      const content_prompt = $("#prompt-library-content-prompt").value.trim();
      if (!title_prompt || !content_prompt) throw new Error("标题提示词和正文提示词不能为空");
      return { name, folder_id, title_prompt, content_prompt };
    }
    const prompt = $("#prompt-library-answer-prompt").value.trim();
    if (!prompt) throw new Error("回答提示词不能为空");
    return { name, folder_id, prompt };
  }
  async function load(silent = true) {
    const failures = [];
    const [folders, articles, answers] = await Promise.allSettled([api("/article-prompt-folders"), api("/article-prompt-templates"), api("/answer-prompt-templates")]);
    state.folders = folders.status === "fulfilled" ? folders.value : [];
    state.articles = articles.status === "fulfilled" ? articles.value.items : [];
    state.answers = answers.status === "fulfilled" ? answers.value.items : [];
    if (folders.status === "rejected") failures.push(`文件夹：${folders.reason.message}`);
    if (articles.status === "rejected") failures.push(`文章模板：${articles.reason.message}`);
    if (answers.status === "rejected") failures.push(`回答模板：${answers.reason.message}`);
    renderFolders();
    if (state.selectedId && !currentItems().some((item) => item.id === state.selectedId)) state.selectedId = "";
    renderList(); renderEditor();
    if (failures.length) toast(`部分数据加载失败：${failures.join("；")}`, "error");
    else if (!silent) toast("提示词模板已刷新");
  }
  async function save(asNew) {
    const button = asNew ? $("#prompt-library-save-new") : $("#prompt-library-save-current");
    $("#prompt-library-error").textContent = "";
    let body;
    try { body = formPayload(); } catch (error) { $("#prompt-library-error").textContent = error.message; return; }
    if (!asNew && !state.selectedId) return;
    setBusy(button, true, "保存中…");
    try {
      const base = state.kind === "article" ? "/article-prompt-templates" : "/answer-prompt-templates";
      const saved = await api(asNew ? base : `${base}/${state.selectedId}`, { method: asNew ? "POST" : "PATCH", body: JSON.stringify(body) });
      state.selectedId = saved.id;
      await load(true);
      selectTemplate(saved.id);
      toast(asNew ? "提示词模板已创建" : "当前提示词模板已保存");
    } catch (error) { $("#prompt-library-error").textContent = error.message; }
    finally { setBusy(button, false); renderEditor(); }
  }
  async function renameTemplate() {
    const item = currentItems().find((entry) => entry.id === state.selectedId);
    if (!item) return;
    const name = window.prompt("请输入新的模板名称", item.name);
    if (!name?.trim() || name.trim() === item.name) return;
    try {
      const base = state.kind === "article" ? "/article-prompt-templates" : "/answer-prompt-templates";
      await api(`${base}/${item.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
      await load(true); selectTemplate(item.id); toast("模板已重命名");
    } catch (error) { toast(error.message, "error"); }
  }
  async function deleteTemplate() {
    const item = currentItems().find((entry) => entry.id === state.selectedId);
    if (!item || !window.confirm(`确定删除模板“${item.name}”吗？`)) return;
    try {
      const base = state.kind === "article" ? "/article-prompt-templates" : "/answer-prompt-templates";
      await api(`${base}/${item.id}`, { method: "DELETE" });
      state.selectedId = ""; await load(true); newTemplate(); toast("模板已删除");
    } catch (error) { toast(error.message, "error"); }
  }
  async function createFolder() {
    const name = window.prompt("请输入新模板文件夹名称");
    if (!name?.trim()) return;
    try {
      const folder = await api("/article-prompt-folders", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
      await load(true);
      $("#prompt-library-folder-filter").value = folder.id;
      $("#prompt-library-folder").value = folder.id;
      renderFolders(); renderList(); toast("模板文件夹已创建");
    } catch (error) { toast(error.message, "error"); }
  }
  async function renameFolder() {
    const id = activeFolderId();
    const folder = state.folders.find((item) => item.id === id);
    if (!folder) return;
    const name = window.prompt("请输入新的文件夹名称", folder.name);
    if (!name?.trim() || name.trim() === folder.name) return;
    try {
      await api(`/article-prompt-folders/${id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
      await load(true); $("#prompt-library-folder-filter").value = id; renderFolders(); renderList(); toast("文件夹已重命名");
    } catch (error) { toast(error.message, "error"); }
  }
  async function deleteFolder() {
    const id = activeFolderId();
    const folder = state.folders.find((item) => item.id === id);
    if (!folder || !window.confirm(`删除文件夹“${folder.name}”？其中模板会保留并移到未归档。`)) return;
    try {
      await api(`/article-prompt-folders/${id}`, { method: "DELETE" });
      state.selectedId = ""; await load(true); $("#prompt-library-folder-filter").value = "all"; renderFolders(); renderList(); newTemplate(); toast("文件夹已删除，模板已保留");
    } catch (error) { toast(error.message, "error"); }
  }
  function switchKind(kind) {
    state.kind = kind; state.selectedId = "";
    $$("[data-prompt-kind]").forEach((button) => button.classList.toggle("active", button.dataset.promptKind === kind));
    renderList(); newTemplate();
  }
  async function activate() { await load(true); if (!state.selectedId) newTemplate(); }
  function bind() {
    $("#prompt-library-folder-create").addEventListener("click", createFolder);
    $("#prompt-library-folder-rename").addEventListener("click", renameFolder);
    $("#prompt-library-folder-delete").addEventListener("click", deleteFolder);
    $("#prompt-library-folder-filter").addEventListener("change", () => { state.selectedId = ""; renderFolders(); renderList(); newTemplate(); });
    $("#prompt-library-search").addEventListener("input", renderList);
    $$("[data-prompt-kind]").forEach((button) => button.addEventListener("click", () => switchKind(button.dataset.promptKind)));
    $("#prompt-library-new-template").addEventListener("click", newTemplate);
    $("#prompt-library-save-new").addEventListener("click", () => save(true));
    $("#prompt-library-save-current").addEventListener("click", () => save(false));
    $("#prompt-library-template-rename").addEventListener("click", renameTemplate);
    $("#prompt-library-template-delete").addEventListener("click", deleteTemplate);
  }
  window.TotodPromptLibrary = { activate, refresh: () => load(false) };
  document.addEventListener("DOMContentLoaded", bind);
})();
