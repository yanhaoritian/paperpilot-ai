const TOKEN_KEY = "paperpilot_token";

const statusLine = document.getElementById("statusLine");
const authUserLabel = document.getElementById("authUserLabel");
const logoutBtn = document.getElementById("logoutBtn");
const libraryList = document.getElementById("libraryList");
const documentList = document.getElementById("documentList");
const queryLibraryChecks = document.getElementById("queryLibraryChecks");
const newLibraryName = document.getElementById("newLibraryName");
const createLibraryBtn = document.getElementById("createLibraryBtn");
const renameLibraryBtn = document.getElementById("renameLibraryBtn");
const deleteLibraryBtn = document.getElementById("deleteLibraryBtn");
const activeLibraryHint = document.getElementById("activeLibraryHint");
const pdfFile = document.getElementById("pdfFile");
const fileDrop = document.getElementById("fileDrop");
const fileDropTitle = document.getElementById("fileDropTitle");
const fileSelected = document.getElementById("fileSelected");
const uploadBtn = document.getElementById("uploadBtn");
const docStatusLine = document.getElementById("docStatusLine");
const modelSelect = document.getElementById("modelSelect");
const ragQuestion = document.getElementById("ragQuestion");
const ragQueryBtn = document.getElementById("ragQueryBtn");
const ragClearChatBtn = document.getElementById("ragClearChatBtn");
const ragChatMessages = document.getElementById("ragChatMessages");
const chatDockDocLabel = document.getElementById("chatDockDocLabel");
const chatEmpty = document.getElementById("chatEmpty");
const conversationSelect = document.getElementById("conversationSelect");
const newConversationBtn = document.getElementById("newConversationBtn");
const memoryToggleBtn = document.getElementById("memoryToggleBtn");
const healthLibraryState = document.getElementById("healthLibraryState");
const healthModelName = document.getElementById("healthModelName");
const healthRetrievalHit = document.getElementById("healthRetrievalHit");
const healthResponsePath = document.getElementById("healthResponsePath");

let token = localStorage.getItem(TOKEN_KEY) || "";
/** @type {Array<any>} */
let libraries = [];
let activeLibraryId = "";
/** @type {Set<string>} */
let selectedQueryLibraryIds = new Set();
let pollTimer = null;
/** @type {Array<any>} */
let conversations = [];
let activeConversationId = "";
let activeConversationMemoryEnabled = true;
let streaming = false;

function apiUrl(path) {
  return path.startsWith("/") ? path : `/${path}`;
}

function setStatus(text, isError = false) {
  if (!statusLine) return;
  statusLine.textContent = text;
  statusLine.classList.toggle("is-error", Boolean(isError));
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function inlineFormat(escaped) {
  return escaped
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function normalizePipes(line) {
  return String(line || "").replace(/｜/g, "|");
}

function pipeCount(line) {
  return (normalizePipes(line).match(/\|/g) || []).length;
}

function isTableSep(line) {
  const t = normalizePipes(line).trim();
  // e.g. |---|---| or | :---: | --- |
  return t.includes("|") && t.includes("-") && /^[\s|.:-]+$/.test(t);
}

function isTableRow(line) {
  const t = normalizePipes(line).trim();
  return pipeCount(t) >= 2 && !isTableSep(t);
}

function looksLikeTableStart(lines, i) {
  if (!isTableRow(lines[i])) return false;
  if (i + 1 >= lines.length) return false;
  const next = lines[i + 1];
  return isTableSep(next) || isTableRow(next);
}

function renderMarkdownTable(rows) {
  const bodyRows = rows.filter((r) => !isTableSep(r));
  if (!bodyRows.length) return "";
  const cellsOf = (line) =>
    normalizePipes(line)
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => inlineFormat(escapeHtml(c.trim())));
  const head = cellsOf(bodyRows[0]);
  const rest = bodyRows.slice(1).map(cellsOf);
  const thead = `<thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead>`;
  const tbody = `<tbody>${rest
    .map((cells) => `<tr>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`)
    .join("")}</tbody>`;
  return `<div class="answer-table-wrap"><table class="answer-table">${thead}${tbody}</table></div>`;
}

/** Light Markdown → HTML for chat bubbles (paragraphs + compare tables). */
function formatAnswerHtml(text) {
  const raw = String(text || "").replace(/\r\n/g, "\n").trim();
  if (!raw) return "";
  const lines = raw.split("\n");
  const parts = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    // Markdown table block (with or without separator row)
    if (looksLikeTableStart(lines, i)) {
      const block = [];
      while (i < lines.length && (isTableRow(lines[i]) || isTableSep(lines[i]))) {
        block.push(lines[i]);
        i += 1;
      }
      parts.push(renderMarkdownTable(block));
      continue;
    }
    // Lone pipe-ish line that isn't a real table — still escape as paragraph
    // Decorative --- lines: skip
    if (/^\s*-{3,}\s*$/.test(line)) {
      i += 1;
      continue;
    }
    // Headings ### / ## / #
    const h = line.match(/^\s*#{1,4}\s+(.+)$/);
    if (h) {
      parts.push(`<h4 class="answer-h">${inlineFormat(escapeHtml(h[1]))}</h4>`);
      i += 1;
      continue;
    }
    // Numbered section like "一、共同点" without hashes
    const zhH = line.match(/^\s*([一二三四五六七八九十]+[、.．]\s*\S.+)$/);
    if (zhH && zhH[1].length < 40) {
      parts.push(`<h4 class="answer-h">${inlineFormat(escapeHtml(zhH[1]))}</h4>`);
      i += 1;
      continue;
    }
    // Paragraph: gather until blank / table / heading
    const buf = [];
    while (i < lines.length) {
      const L = lines[i];
      if (!L.trim()) break;
      if (/^\s*-{3,}\s*$/.test(L)) break;
      if (L.match(/^\s*#{1,4}\s+/)) break;
      if (looksLikeTableStart(lines, i)) break;
      buf.push(L.trim());
      i += 1;
    }
    if (buf.length) {
      parts.push(`<p>${inlineFormat(escapeHtml(buf.join(" ")))}</p>`);
    }
  }
  return parts.join("") || `<p>${inlineFormat(escapeHtml(raw))}</p>`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.json !== undefined) headers["Content-Type"] = "application/json";
  const resp = await fetch(apiUrl(path), {
    method: options.method || "GET",
    headers,
    body: options.json !== undefined ? JSON.stringify(options.json) : options.body
  });
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!resp.ok) {
    if (resp.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      location.replace("/login.html");
    }
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data;
}

function hideEmptyState() {
  if (chatEmpty) chatEmpty.hidden = true;
}

function clearChatDom() {
  if (!ragChatMessages) return;
  ragChatMessages.innerHTML = "";
  if (chatEmpty) {
    chatEmpty.hidden = false;
    ragChatMessages.appendChild(chatEmpty);
  }
}

function appendChat(role, html) {
  if (!ragChatMessages) return;
  hideEmptyState();
  const wrap = document.createElement("div");
  wrap.className = `bubble bubble--${role}`;
  wrap.innerHTML = html;
  ragChatMessages.appendChild(wrap);
  ragChatMessages.scrollTop = ragChatMessages.scrollHeight;
  return wrap;
}

function citationsHtml(citations) {
  if (!Array.isArray(citations) || citations.length === 0) return "";
  const cards = citations.slice(0, 12).map((c, index) => {
    const fileName = c.file_name || `来源 ${index + 1}`;
    const pageStart = Number.isFinite(Number(c.page_start)) ? Number(c.page_start) : null;
    const pageEnd = Number.isFinite(Number(c.page_end)) ? Number(c.page_end) : pageStart;
    const pageLabel = pageStart
      ? `第 ${pageStart}${pageEnd && pageEnd !== pageStart ? `–${pageEnd}` : ""} 页`
      : "页码不可用";
    const excerpt = String(c.excerpt || "").trim();
    const documentId = String(c.document_id || "").trim();
    const tag = documentId ? "button" : "div";
    const attrs = documentId
      ? ` type="button" data-open-document="${escapeHtml(documentId)}" data-page="${pageStart || ""}"`
      : "";
    return `<${tag} class="citation-card"${attrs}>
      <span class="citation-card__index">${index + 1}</span>
      <span class="citation-card__body">
        <strong>${escapeHtml(fileName)}</strong>
        <small>${escapeHtml(pageLabel)}${c.section_path ? ` · ${escapeHtml(c.section_path)}` : ""}</small>
        ${excerpt ? `<span>${escapeHtml(excerpt)}</span>` : ""}
      </span>
      ${documentId ? '<span class="citation-card__open">打开原文</span>' : ""}
    </${tag}>`;
  }).join("");
  return `<details class="citation-list" open>
    <summary>原文依据（${Math.min(citations.length, 12)}）</summary>
    <div class="citation-list__items">${cards}</div>
  </details>`;
}

function bindCitationActions(root) {
  if (!root) return;
  root.querySelectorAll("[data-open-document]").forEach((btn) => {
    if (btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      const documentId = btn.getAttribute("data-open-document") || "";
      const page = Number(btn.getAttribute("data-page") || 0);
      if (!documentId) return;
      const viewer = window.open("", "_blank");
      btn.disabled = true;
      try {
        const resp = await fetch(apiUrl(`/api/documents/${encodeURIComponent(documentId)}/file`), {
          headers: { Authorization: `Bearer ${token}` }
        });
        if (!resp.ok) {
          if (resp.status === 401) {
            localStorage.removeItem(TOKEN_KEY);
            location.replace("/login.html");
            return;
          }
          const payload = await resp.json().catch(() => null);
          throw new Error(payload?.detail || `无法打开原文（HTTP ${resp.status}）`);
        }
        const url = URL.createObjectURL(await resp.blob());
        const target = `${url}${page > 0 ? `#page=${page}` : ""}`;
        if (viewer) {
          viewer.location.href = target;
        } else {
          const link = document.createElement("a");
          link.href = target;
          link.target = "_blank";
          link.rel = "noopener";
          link.click();
        }
        setTimeout(() => URL.revokeObjectURL(url), 5 * 60 * 1000);
      } catch (e) {
        if (viewer) viewer.close();
        setStatus(e.message || "无法打开原文", true);
      } finally {
        btn.disabled = false;
      }
    });
  });
}

function renderAnswer(result) {
  const bubble = appendChat(
    "assistant",
    `${formatAnswerHtml(result.answer || "")}
     ${citationsHtml(result.citations)}
     <p class="bubble-meta">置信度 ${escapeHtml(result.confidence || "N/A")} · 命中 ${result.retrieval_hit ?? 0}</p>`
  );
  bindCitationActions(bubble);
  if (healthRetrievalHit) healthRetrievalHit.textContent = String(result.retrieval_hit ?? 0);
  if (healthResponsePath) healthResponsePath.textContent = result.degraded ? "降级" : "标准";
}

function renderStoredMessages(messages, truncated = false) {
  clearChatDom();
  if (truncated) {
    appendChat(
      "assistant",
      '<p class="memory-notice">当前仅显示最近 200 条消息；更早内容仍保存在会话中，并由滚动摘要和相关记忆参与后续问答。</p>'
    );
  }
  if (!messages?.length) return;
  for (const m of messages) {
    if (m.role === "user") {
      appendChat("user", `<p>${escapeHtml(m.content || "")}</p>`);
    } else {
      const meta = m.meta || {};
      const bubble = appendChat(
        "assistant",
        `${formatAnswerHtml(m.content || "")}
         ${citationsHtml(m.citations)}
         <p class="bubble-meta">置信度 ${escapeHtml(meta.confidence || "N/A")} · 命中 ${meta.retrieval_hit ?? 0}</p>`
      );
      bindCitationActions(bubble);
    }
  }
}

function updateMemoryControl(detail = null) {
  if (!memoryToggleBtn) return;
  const current =
    detail || conversations.find((item) => item.id === activeConversationId);
  if (!current) {
    memoryToggleBtn.disabled = true;
    memoryToggleBtn.textContent = "长记忆：—";
    memoryToggleBtn.classList.remove("is-on");
    memoryToggleBtn.title = "请先选择会话";
    return;
  }
  activeConversationMemoryEnabled = current.memory_enabled !== false;
  memoryToggleBtn.disabled = streaming;
  memoryToggleBtn.textContent = activeConversationMemoryEnabled ? "长记忆：开" : "长记忆：关";
  memoryToggleBtn.classList.toggle("is-on", activeConversationMemoryEnabled);
  const revision = Number(current.memory_revision || 0);
  const summarized = Number(current.summarized_message_count || 0);
  const entries = Number(current.memory_entry_count || 0);
  memoryToggleBtn.title = activeConversationMemoryEnabled
    ? `已摘要至消息 ${summarized}；记忆版本 ${revision}；情景记忆 ${entries} 条`
    : "长记忆已关闭，派生摘要和情景记忆已清除";
}

function renderConversationSelect() {
  if (!conversationSelect) return;
  conversationSelect.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = conversations.length ? "选择会话…" : "暂无历史会话";
  conversationSelect.appendChild(blank);
  for (const c of conversations) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.title || "未命名";
    if (c.id === activeConversationId) opt.selected = true;
    conversationSelect.appendChild(opt);
  }
}

async function refreshConversations() {
  conversations = await api("/api/conversations");
  if (activeConversationId && !conversations.some((c) => c.id === activeConversationId)) {
    activeConversationId = "";
  }
  renderConversationSelect();
  updateMemoryControl();
}

async function ensureConversation() {
  if (activeConversationId) return activeConversationId;
  const library_ids = [...selectedQueryLibraryIds];
  const conv = await api("/api/conversations", {
    method: "POST",
    json: { library_ids, title: "新对话" }
  });
  activeConversationId = conv.id;
  activeConversationMemoryEnabled = conv.memory_enabled !== false;
  await refreshConversations();
  return activeConversationId;
}

async function loadConversation(id) {
  if (!id) {
    activeConversationId = "";
    clearChatDom();
    renderConversationSelect();
    return;
  }
  const detail = await api(`/api/conversations/${id}`);
  activeConversationId = detail.id;
  if (Array.isArray(detail.library_ids) && detail.library_ids.length) {
    selectedQueryLibraryIds = new Set(detail.library_ids.filter((x) => libraries.some((l) => l.id === x)));
    renderLibraries();
  }
  renderStoredMessages(detail.messages || [], Boolean(detail.messages_truncated));
  renderConversationSelect();
  updateMemoryControl(detail);
}

async function parseSseStream(resp, handlers) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const block of parts) {
      let event = "message";
      const dataLines = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      let data = null;
      try {
        data = JSON.parse(dataLines.join("\n"));
      } catch {
        continue;
      }
      const fn = handlers[event];
      if (fn) fn(data);
    }
  }
}

function updateActiveHint() {
  const lib = libraries.find((l) => l.id === activeLibraryId);
  if (activeLibraryHint) {
    activeLibraryHint.textContent = lib
      ? `当前库：${lib.name}（上传将写入此库）`
      : "选择一个库以上传论文。";
  }
}

function updateQueryReady() {
  const n = selectedQueryLibraryIds.size;
  if (chatDockDocLabel) {
    chatDockDocLabel.textContent = n > 0 ? `已选 ${n} 个知识库参与检索` : "请先勾选左侧知识库";
  }
  if (ragQueryBtn) ragQueryBtn.disabled = n === 0;
}

function renderLibraries() {
  if (!libraryList) return;
  libraryList.innerHTML = "";
  if (!libraries.length) {
    libraryList.innerHTML = "<li class='muted'>暂无知识库，先创建一个。</li>";
  }
  for (const lib of libraries) {
    const li = document.createElement("li");
    li.className = "lib-item" + (lib.id === activeLibraryId ? " is-active" : "");
    li.innerHTML = `<button type="button" class="lib-pick" data-id="${escapeHtml(lib.id)}">
      <strong>${escapeHtml(lib.name)}</strong>
      <span>${lib.document_count || 0} 篇文献</span>
    </button>`;
    libraryList.appendChild(li);
  }
  libraryList.querySelectorAll("[data-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeLibraryId = btn.getAttribute("data-id") || "";
      renderLibraries();
      updateActiveHint();
      refreshDocuments();
    });
  });

  if (queryLibraryChecks) {
    queryLibraryChecks.innerHTML = "";
    for (const lib of libraries) {
      const on = selectedQueryLibraryIds.has(lib.id);
      const label = document.createElement("label");
      label.className = "check-chip" + (on ? " is-on" : "");
      label.innerHTML = `<input type="checkbox" data-lib="${escapeHtml(lib.id)}" ${on ? "checked" : ""}>
        <span>${escapeHtml(lib.name)}</span>`;
      const input = label.querySelector("input");
      input.addEventListener("change", () => {
        if (input.checked) selectedQueryLibraryIds.add(lib.id);
        else selectedQueryLibraryIds.delete(lib.id);
        label.classList.toggle("is-on", input.checked);
        updateQueryReady();
      });
      queryLibraryChecks.appendChild(label);
    }
  }
  updateActiveHint();
  updateQueryReady();
}

async function refreshLibraries() {
  libraries = await api("/api/libraries");
  if (!activeLibraryId && libraries[0]) activeLibraryId = libraries[0].id;
  if (activeLibraryId && !libraries.some((l) => l.id === activeLibraryId)) {
    activeLibraryId = libraries[0]?.id || "";
  }
  selectedQueryLibraryIds = new Set(
    [...selectedQueryLibraryIds].filter((id) => libraries.some((l) => l.id === id))
  );
  if (selectedQueryLibraryIds.size === 0 && activeLibraryId) {
    selectedQueryLibraryIds.add(activeLibraryId);
  }
  renderLibraries();
  await refreshDocuments();
}

async function refreshDocuments() {
  if (!documentList) return;
  if (!activeLibraryId) {
    documentList.innerHTML = "<li class='muted'>请先选择知识库。</li>";
    return;
  }
  const docs = await api(`/api/libraries/${activeLibraryId}/documents`);
  documentList.innerHTML = "";
  if (!docs.length) {
    documentList.innerHTML = "<li class='muted'>当前库暂无文档。</li>";
  }
  for (const d of docs) {
    const li = document.createElement("li");
    li.className = "doc-item";
    li.innerHTML = `<div class="doc-row">
      <div class="doc-meta">
        <strong>${escapeHtml(d.file_name)}</strong>
        <span>${escapeHtml(d.status)}${d.status_detail ? " · " + escapeHtml(String(d.status_detail).slice(0, 80)) : ""} · ${d.page_count || 0} 页</span>
      </div>
      <button type="button" class="btn btn-ghost btn-sm" data-reindex="${escapeHtml(d.id)}">重索引</button>
      <button type="button" class="btn btn-ghost btn-sm btn-danger" data-del="${escapeHtml(d.id)}">删</button>
    </div>`;
    documentList.appendChild(li);
  }
  documentList.querySelectorAll("[data-reindex]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-reindex");
      if (!id) return;
      try {
        await api(`/api/documents/${id}/reindex`, { method: "POST" });
        setStatus("已排队重新索引。");
        await refreshDocuments();
      } catch (e) {
        setStatus(e.message, true);
      }
    });
  });
  documentList.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-del");
      if (!id || !confirm("确认删除该文档？")) return;
      try {
        await api(`/api/documents/${id}`, { method: "DELETE" });
        setStatus("文档已删除。");
        await refreshLibraries();
      } catch (e) {
        setStatus(e.message, true);
      }
    });
  });

  const pending = docs.some((d) => d.status === "pending" || d.status === "processing");
  if (pending && !pollTimer) {
    pollTimer = setInterval(() => {
      refreshDocuments().catch(() => {});
      refreshLibraries().catch(() => {});
    }, 2500);
  }
  if (!pending && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function refreshHealth() {
  try {
    const h = await api("/api/health");
    if (healthLibraryState) {
      healthLibraryState.textContent = !h.database
        ? "数据库异常"
        : h.worker_alive === false
          ? "Worker 离线"
          : h.has_api_key
            ? "可用"
            : "缺 Key";
    }
  } catch {
    if (healthLibraryState) healthLibraryState.textContent = "离线";
  }
  if (healthModelName && modelSelect) {
    healthModelName.textContent = modelSelect.value || "—";
  }
}

logoutBtn?.addEventListener("click", () => {
  localStorage.removeItem(TOKEN_KEY);
  location.replace("/login.html");
});

createLibraryBtn?.addEventListener("click", async () => {
  const name = (newLibraryName.value || "").trim();
  if (!name) {
    setStatus("请输入知识库名称。", true);
    return;
  }
  try {
    const lib = await api("/api/libraries", { method: "POST", json: { name } });
    newLibraryName.value = "";
    activeLibraryId = lib.id;
    selectedQueryLibraryIds.add(lib.id);
    setStatus(`已创建「${lib.name}」。`);
    await refreshLibraries();
  } catch (e) {
    setStatus(e.message, true);
  }
});

renameLibraryBtn?.addEventListener("click", async () => {
  if (!activeLibraryId) {
    setStatus("请先选择知识库。", true);
    return;
  }
  const name = prompt("新的知识库名称");
  if (!name || !name.trim()) return;
  try {
    await api(`/api/libraries/${activeLibraryId}`, { method: "PATCH", json: { name: name.trim() } });
    setStatus("已重命名。");
    await refreshLibraries();
  } catch (e) {
    setStatus(e.message, true);
  }
});

deleteLibraryBtn?.addEventListener("click", async () => {
  if (!activeLibraryId) {
    setStatus("请先选择知识库。", true);
    return;
  }
  if (!confirm("确认删除该知识库及其中全部文档？")) return;
  try {
    await api(`/api/libraries/${activeLibraryId}`, { method: "DELETE" });
    selectedQueryLibraryIds.delete(activeLibraryId);
    activeLibraryId = "";
    setStatus("知识库已删除。");
    await refreshLibraries();
  } catch (e) {
    setStatus(e.message, true);
  }
});

function formatBytes(n) {
  if (!Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function showSelectedFile(file) {
  if (!file) {
    fileDrop?.classList.remove("has-file");
    if (fileDropTitle) fileDropTitle.textContent = "选择或拖入 PDF";
    if (fileSelected) {
      fileSelected.hidden = true;
      fileSelected.textContent = "";
    }
    if (docStatusLine) docStatusLine.textContent = "尚未选择文件。";
    return;
  }
  fileDrop?.classList.add("has-file");
  if (fileDropTitle) fileDropTitle.textContent = "已选择文件";
  if (fileSelected) {
    fileSelected.hidden = false;
    fileSelected.textContent = `${file.name}（${formatBytes(file.size)}）`;
  }
  if (docStatusLine) {
    docStatusLine.textContent = `已选：${file.name}。请点击「上传并索引」写入当前知识库。`;
  }
  setStatus(`已选择 ${file.name}，可上传。`);
}

function assignPdfFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    setStatus("请选择 PDF 文件。", true);
    return;
  }
  // Reflect into the file input via DataTransfer so upload uses the same file
  const dt = new DataTransfer();
  dt.items.add(file);
  if (pdfFile) pdfFile.files = dt.files;
  showSelectedFile(file);
}

pdfFile?.addEventListener("change", () => {
  const file = pdfFile.files?.[0] || null;
  showSelectedFile(file);
});

["dragenter", "dragover"].forEach((evName) => {
  fileDrop?.addEventListener(evName, (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    fileDrop.classList.add("is-dragover");
  });
});
["dragleave", "drop"].forEach((evName) => {
  fileDrop?.addEventListener(evName, (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    fileDrop.classList.remove("is-dragover");
  });
});
fileDrop?.addEventListener("drop", (ev) => {
  const file = ev.dataTransfer?.files?.[0];
  if (file) assignPdfFile(file);
});

uploadBtn?.addEventListener("click", async () => {
  if (!activeLibraryId) {
    setStatus("请先选择知识库。", true);
    return;
  }
  const file = pdfFile?.files?.[0];
  if (!file) {
    setStatus("请选择 PDF 文件。", true);
    return;
  }
  const fd = new FormData();
  fd.append("file", file);
  setStatus("正在上传…");
  if (docStatusLine) docStatusLine.textContent = `正在上传「${file.name}」…`;
  try {
    const doc = await api(`/api/libraries/${activeLibraryId}/documents`, { method: "POST", body: fd });
    setStatus(`已入库：${doc.file_name}（${doc.status}）`);
    if (docStatusLine) {
      docStatusLine.textContent = `已入库：${doc.file_name} · ${doc.status}。可继续选择下一篇上传。`;
    }
    if (pdfFile) pdfFile.value = "";
    showSelectedFile(null);
    await refreshLibraries();
  } catch (e) {
    setStatus(e.message, true);
  }
});

ragQueryBtn?.addEventListener("click", async () => {
  if (streaming) return;
  const question = (ragQuestion.value || "").trim();
  if (!question) {
    setStatus("请输入问题。", true);
    return;
  }
  const library_ids = [...selectedQueryLibraryIds];
  if (!library_ids.length) {
    setStatus("请至少勾选一个知识库。", true);
    return;
  }
  appendChat("user", `<p>${escapeHtml(question)}</p>`);
  ragQuestion.value = "";
  streaming = true;
  if (ragQueryBtn) ragQueryBtn.disabled = true;
  updateMemoryControl();

  const bubble = appendChat(
    "assistant",
    `<p class="bubble-status is-active">正在检索并生成回答…</p>
     <div class="stream-text answer-body"></div>
     <div class="stream-citations"></div>
     <p class="bubble-meta"></p>`
  );
  const statusEl = bubble.querySelector(".bubble-status");
  const textEl = bubble.querySelector(".stream-text");
  const citationsEl = bubble.querySelector(".stream-citations");
  const metaEl = bubble.querySelector(".bubble-meta");

  const setBubbleStatus = (text, active = true) => {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    statusEl.classList.toggle("is-active", Boolean(active && text));
    statusEl.hidden = !text;
  };

  try {
    const convId = await ensureConversation();
    const resp = await fetch(apiUrl(`/api/conversations/${convId}/messages`), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream"
      },
      body: JSON.stringify({
        question,
        library_ids,
        model: (modelSelect?.value || "").trim() || null,
        temperature: 0.2
      })
    });
    if (!resp.ok) {
      const t = await resp.text();
      let detail = t;
      try {
        detail = JSON.parse(t).detail || t;
      } catch {
        /* keep */
      }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }

    let answer = "";
    await parseSseStream(resp, {
      status: (data) => {
        setBubbleStatus(data.text || "处理中…", true);
      },
      tool: (data) => {
        const name = data.name || "tool";
        if (data.phase === "start") {
          setBubbleStatus(`调用工具 ${name}…`, true);
        } else if (data.phase === "done") {
          setBubbleStatus(`${name}：${data.summary || "完成"}`, true);
        }
      },
      meta: (data) => {
        if (data.retrieval_hit != null && healthRetrievalHit) {
          healthRetrievalHit.textContent = String(data.retrieval_hit);
        }
        if (data.memory && memoryToggleBtn) {
          const memory = data.memory;
          const details = [];
          if (memory.query_rewritten) details.push("已改写检索问题");
          if (memory.summary_used) details.push("已使用滚动摘要");
          if (memory.recalled_count) details.push(`召回 ${memory.recalled_count} 条旧记忆`);
          if (details.length) memoryToggleBtn.title = details.join("；");
        }
      },
      token: (data) => {
        if (answer === "" && statusEl) {
          setBubbleStatus("", false);
        }
        answer += data.text || "";
        if (textEl) textEl.innerHTML = formatAnswerHtml(answer);
        if (ragChatMessages) ragChatMessages.scrollTop = ragChatMessages.scrollHeight;
      },
      citations: (data) => {
        setBubbleStatus("", false);
        if (citationsEl) {
          citationsEl.innerHTML = citationsHtml(data.citations || []);
          bindCitationActions(citationsEl);
        }
        if (metaEl) {
          metaEl.textContent = `置信度 ${data.confidence || "N/A"} · 命中 ${data.retrieval_hit ?? 0}`;
        }
        if (healthRetrievalHit) healthRetrievalHit.textContent = String(data.retrieval_hit ?? 0);
        if (healthResponsePath) healthResponsePath.textContent = data.degraded ? "降级" : "Agent";
      },
      error: (data) => {
        setBubbleStatus(data.detail || "流式生成出错", true);
        setStatus(data.detail || "流式生成出错", true);
      },
      done: () => {
        setBubbleStatus("", false);
        setStatus("已回复。");
      }
    });
    await refreshConversations();
  } catch (e) {
    setBubbleStatus("", false);
    if (textEl && !textEl.textContent.trim()) textEl.innerHTML = formatAnswerHtml(e.message);
    else appendChat("assistant", formatAnswerHtml(e.message));
    setStatus(e.message, true);
  } finally {
    streaming = false;
    updateQueryReady();
    updateMemoryControl();
  }
});

ragQuestion?.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    ragQueryBtn?.click();
  }
});

newConversationBtn?.addEventListener("click", async () => {
  try {
    const library_ids = [...selectedQueryLibraryIds];
    const conv = await api("/api/conversations", {
      method: "POST",
      json: { library_ids, title: "新对话" }
    });
    activeConversationId = conv.id;
    activeConversationMemoryEnabled = conv.memory_enabled !== false;
    clearChatDom();
    await refreshConversations();
    setStatus("已新建对话。");
  } catch (e) {
    setStatus(e.message, true);
  }
});

memoryToggleBtn?.addEventListener("click", async () => {
  if (!activeConversationId || streaming) return;
  try {
    if (activeConversationMemoryEnabled) {
      const approved = window.confirm(
        "关闭长记忆会清除该会话派生的滚动摘要和情景记忆；原始聊天消息不会删除。是否继续？"
      );
      if (!approved) return;
      await api(`/api/conversations/${activeConversationId}/memory`, {
        method: "DELETE"
      });
      activeConversationMemoryEnabled = false;
      setStatus("已关闭并清除该会话的派生长记忆；原始消息仍保留。");
    } else {
      await api(`/api/conversations/${activeConversationId}`, {
        method: "PATCH",
        json: { memory_enabled: true }
      });
      activeConversationMemoryEnabled = true;
      setStatus("已开启长记忆，将在后续对话中自动建立摘要。");
    }
    await refreshConversations();
    updateMemoryControl();
  } catch (e) {
    setStatus(e.message, true);
  }
});

conversationSelect?.addEventListener("change", async () => {
  const id = conversationSelect.value || "";
  try {
    await loadConversation(id);
  } catch (e) {
    setStatus(e.message, true);
  }
});

ragClearChatBtn?.addEventListener("click", async () => {
  try {
    const library_ids = [...selectedQueryLibraryIds];
    const conv = await api("/api/conversations", {
      method: "POST",
      json: { library_ids, title: "新对话" }
    });
    activeConversationId = conv.id;
    activeConversationMemoryEnabled = conv.memory_enabled !== false;
    clearChatDom();
    await refreshConversations();
    setStatus("已新开对话。");
  } catch (e) {
    clearChatDom();
    activeConversationId = "";
    setStatus(e.message, true);
  }
});

modelSelect?.addEventListener("change", () => {
  if (healthModelName) healthModelName.textContent = modelSelect.value || "—";
});

(async function boot() {
  if (!token) {
    location.replace("/login.html");
    return;
  }
  try {
    const me = await api("/api/auth/me");
    if (authUserLabel) authUserLabel.textContent = me.username || "用户";
    await refreshHealth();
    await refreshLibraries();
    await refreshConversations();
    if (conversations[0]) {
      await loadConversation(conversations[0].id);
    }
  } catch {
    location.replace("/login.html");
  }
})();
