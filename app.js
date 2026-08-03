const TOKEN_KEY = "paperpilot_token";

const statusLine = document.getElementById("statusLine");
const authUserLabel = document.getElementById("authUserLabel");
const authUserAvatar = document.getElementById("authUserAvatar");
const logoutBtn = document.getElementById("logoutBtn");
const libraryList = document.getElementById("libraryList");
const libraryCount = document.getElementById("libraryCount");
const documentList = document.getElementById("documentList");
const documentCount = document.getElementById("documentCount");
const queryLibraryChecks = document.getElementById("queryLibraryChecks");
const queryScopeCount = document.getElementById("queryScopeCount");
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
const skillSelect = document.getElementById("skillSelect");
const ragQuestion = document.getElementById("ragQuestion");
const ragQueryBtn = document.getElementById("ragQueryBtn");
const ragClearChatBtn = document.getElementById("ragClearChatBtn");
const ragChatMessages = document.getElementById("ragChatMessages");
const chatDockDocLabel = document.getElementById("chatDockDocLabel");
const chatEmpty = document.getElementById("chatEmpty");
const conversationSelect = document.getElementById("conversationSelect");
const newConversationBtn = document.getElementById("newConversationBtn");
const deleteConversationBtn = document.getElementById("deleteConversationBtn");
const memoryToggleBtn = document.getElementById("memoryToggleBtn");
const healthLibraryState = document.getElementById("healthLibraryState");
const healthModelName = document.getElementById("healthModelName");
const healthRetrievalHit = document.getElementById("healthRetrievalHit");
const healthResponsePath = document.getElementById("healthResponsePath");
const serviceStatusPill = document.getElementById("serviceStatusPill");
const usageCostPill = document.getElementById("usageCostPill");
const usageCostSummary = document.getElementById("usageCostSummary");
const usageModal = document.getElementById("usageModal");
const usageModalBackdrop = document.getElementById("usageModalBackdrop");
const usageCloseBtn = document.getElementById("usageCloseBtn");
const usageRangeLabel = document.getElementById("usageRangeLabel");
const usageTotalTokens = document.getElementById("usageTotalTokens");
const usageInputTokens = document.getElementById("usageInputTokens");
const usageCachedTokens = document.getElementById("usageCachedTokens");
const usageOutputTokens = document.getElementById("usageOutputTokens");
const usageReasoningTokens = document.getElementById("usageReasoningTokens");
const usageEventSummary = document.getElementById("usageEventSummary");
const usageCostHeadline = document.getElementById("usageCostHeadline");
const usageCostList = document.getElementById("usageCostList");
const usageAccountingNote = document.getElementById("usageAccountingNote");
const usageOperationList = document.getElementById("usageOperationList");
const usageSkillList = document.getElementById("usageSkillList");

let token = localStorage.getItem(TOKEN_KEY) || "";
/** @type {Array<any>} */
let libraries = [];
let activeLibraryId = "";
/** @type {Set<string>} */
let selectedQueryLibraryIds = new Set();
let pollTimer = null;
let documentRefreshSequence = 0;
/** @type {Array<any>} */
let conversations = [];
let activeConversationId = "";
let activeConversationMemoryEnabled = true;
let streaming = false;
/** @type {Array<any>} */
let researchSkills = [];
let latestUsageSummary = null;

function apiUrl(path) {
  return path.startsWith("/") ? path : `/${path}`;
}

function compactNumber(value) {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 100_000 ? 0 : 1)}K`;
  return String(n);
}

const USAGE_OPERATION_LABELS = {
  answer_generate: "模型回答",
  query_rewrite: "问题改写",
  query_embedding: "查询向量化",
  document_embedding: "文档向量化",
  memory_query_embedding: "记忆检索向量化",
  memory_embedding: "长期记忆向量化",
  memory_summary: "长期记忆摘要",
  rerank: "语义精排",
  agent_plan: "Agent 规划",
  contextual_prefix: "上下文前缀",
  vision_page: "PDF 视觉解析",
  response_cache_hit: "回答缓存命中",
  unattributed: "未归属"
};

function fullNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function costText(currencies, digits = 6) {
  const rows = Array.isArray(currencies) ? currencies : [];
  if (!rows.length) return "未定价";
  return rows
    .map((row) => `${row.currency} ${Number(row.cost || 0).toFixed(digits)}`)
    .join(" · ");
}

function breakdownAccountingText(row) {
  const parts = [];
  if (Array.isArray(row.currencies) && row.currencies.length) {
    parts.push(costText(row.currencies));
  }
  if (row.unpriced_events) parts.push(`${fullNumber(row.unpriced_events)} 次待定价`);
  if (row.local_events) parts.push(`${fullNumber(row.local_events)} 次本地`);
  if (row.cache_hits) parts.push(`${fullNumber(row.cache_hits)} 次缓存`);
  return parts.length ? parts.join(" · ") : "无计费金额";
}

function renderUsageBreakdown(container, rows, labelForKey) {
  if (!container) return;
  const values = Array.isArray(rows) ? rows.slice(0, 10) : [];
  if (!values.length) {
    container.innerHTML = '<p class="usage-empty">尚无可展示的调用记录。</p>';
    return;
  }
  container.innerHTML = values.map((row) => {
    const label = labelForKey(row.key);
    return `
      <div class="usage-breakdown-row">
        <span class="usage-breakdown-row__name" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
        <span class="usage-breakdown-row__value">
          <strong>${escapeHtml(compactNumber(row.total_tokens))} tokens</strong>
          <small>${escapeHtml(breakdownAccountingText(row))} · ${fullNumber(row.events)} 次事件</small>
        </span>
      </div>`;
  }).join("");
}

function renderUsagePanel(usage) {
  if (!usage) return;
  const costs = Array.isArray(usage.costs) ? usage.costs : [];
  if (usageRangeLabel) usageRangeLabel.textContent = `最近 ${usage.days || 30} 天 · 按当前账号归集`;
  if (usageTotalTokens) usageTotalTokens.textContent = fullNumber(usage.total_tokens);
  if (usageInputTokens) usageInputTokens.textContent = fullNumber(usage.input_tokens);
  if (usageCachedTokens) usageCachedTokens.textContent = fullNumber(usage.cached_input_tokens);
  if (usageOutputTokens) usageOutputTokens.textContent = fullNumber(usage.output_tokens);
  if (usageReasoningTokens) usageReasoningTokens.textContent = `推理 ${fullNumber(usage.reasoning_tokens)}`;
  if (usageEventSummary) {
    usageEventSummary.textContent = `${fullNumber(usage.events)} 次事件 · 精确 ${fullNumber(usage.provider_reported_events)} · 估算 ${fullNumber(usage.estimated_events)}`;
  }

  if (usageCostHeadline) {
    if (costs.length === 1) usageCostHeadline.textContent = costText(costs, 6);
    else if (costs.length > 1) usageCostHeadline.textContent = `${costs.length} 个币种分别核算`;
    else if (usage.unpriced_events) usageCostHeadline.textContent = "有用量待配置单价";
    else usageCostHeadline.textContent = usage.events ? "暂无计费金额" : "尚无调用记录";
  }
  if (usageCostList) {
    usageCostList.innerHTML = costs.length
      ? costs.map((row) => `
          <span class="usage-cost-chip">
            ${escapeHtml(row.currency)}
            <strong>${Number(row.cost || 0).toFixed(6)}</strong>
          </span>`).join("")
      : '<span class="usage-cost-chip">Token 已记录，金额待价格配置</span>';
  }

  if (usageAccountingNote) {
    const notes = [];
    if (usage.unpriced_events) notes.push(`${fullNumber(usage.unpriced_events)} 次可计费调用尚未配置匹配单价`);
    if (usage.estimated_events) notes.push(`${fullNumber(usage.estimated_events)} 次调用使用 Token 估算值`);
    if (usage.local_events) notes.push(`${fullNumber(usage.local_events)} 次本地向量计算不产生供应商费用`);
    if (usage.cache_hits) notes.push(`${fullNumber(usage.cache_hits)} 次回答命中缓存`);
    if (usage.failed_events) notes.push(`${fullNumber(usage.failed_events)} 次失败调用不计入金额`);
    usageAccountingNote.textContent = notes.length
      ? `${notes.join("；")}。`
      : "精确 Token 来自供应商 usage；历史金额按调用发生时的价格版本保留。";
  }

  renderUsageBreakdown(
    usageOperationList,
    usage.by_operation,
    (key) => USAGE_OPERATION_LABELS[key] || key
  );
  renderUsageBreakdown(
    usageSkillList,
    usage.by_skill,
    (key) => researchSkills.find((skill) => skill.id === key)?.title || (key === "unattributed" ? "系统后台任务" : key)
  );
}

async function openUsagePanel() {
  if (!usageModal) return;
  usageModal.hidden = false;
  document.body.classList.add("is-usage-open");
  usageCostPill?.setAttribute("aria-expanded", "true");
  usageCloseBtn?.focus();
  if (latestUsageSummary) renderUsagePanel(latestUsageSummary);
  await refreshUsageSummary();
}

function closeUsagePanel() {
  if (!usageModal || usageModal.hidden) return;
  usageModal.hidden = true;
  document.body.classList.remove("is-usage-open");
  usageCostPill?.setAttribute("aria-expanded", "false");
  usageCostPill?.focus();
}

async function refreshResearchSkills() {
  if (!skillSelect) return;
  researchSkills = await api("/api/research/skills");
  const previous = skillSelect.value || "auto";
  skillSelect.innerHTML = '<option value="auto">自动选择</option>';
  for (const skill of researchSkills) {
    const option = document.createElement("option");
    option.value = skill.id;
    option.textContent = skill.title;
    option.title = skill.description || "";
    skillSelect.appendChild(option);
  }
  skillSelect.value = researchSkills.some((skill) => skill.id === previous) ? previous : "auto";
  if (latestUsageSummary) renderUsagePanel(latestUsageSummary);
}

async function refreshUsageSummary() {
  if (!usageCostSummary) return;
  try {
    const usage = await api("/api/research/usage?days=30");
    latestUsageSummary = usage;
    const costs = Array.isArray(usage.costs) ? usage.costs : [];
    if (costs.length === 1) {
      const row = costs[0];
      usageCostSummary.textContent = `${row.currency} ${Number(row.cost || 0).toFixed(3)}`;
    } else if (costs.length > 1) {
      usageCostSummary.textContent = `${costs.length} 币种`;
    } else {
      usageCostSummary.textContent = usage.total_tokens ? `${compactNumber(usage.total_tokens)} tokens` : "暂无";
    }
    if (usageCostPill) {
      const priced = costs.map((row) => `${row.currency} ${Number(row.cost || 0).toFixed(6)}`).join("；");
      usageCostPill.title = [
        `近 30 天 ${compactNumber(usage.total_tokens)} tokens`,
        priced || "尚未配置模型单价",
        `供应商精确 ${usage.provider_reported_events || 0} 次；估算 ${usage.estimated_events || 0} 次`,
        `本地计算 ${usage.local_events || 0} 次；缓存命中 ${usage.cache_hits || 0} 次`,
        `待定价 ${usage.unpriced_events || 0} 次；失败 ${usage.failed_events || 0} 次`
      ].join("；");
    }
    renderUsagePanel(usage);
  } catch {
    usageCostSummary.textContent = "—";
    if (usageAccountingNote && usageModal && !usageModal.hidden) {
      usageAccountingNote.textContent = "用量明细加载失败，请稍后重试。";
    }
  }
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
      const skillLabel = meta.skill_title || meta.skill_id || "科研问答";
      const bubble = appendChat(
        "assistant",
        `${formatAnswerHtml(m.content || "")}
         ${citationsHtml(m.citations)}
         <p class="bubble-meta">${escapeHtml(skillLabel)} · 置信度 ${escapeHtml(meta.confidence || "N/A")} · 命中 ${meta.retrieval_hit ?? 0}</p>`
      );
      bindCitationActions(bubble);
    }
  }
}

function updateMemoryControl(detail = null) {
  const current =
    detail || conversations.find((item) => item.id === activeConversationId);
  if (deleteConversationBtn) {
    deleteConversationBtn.disabled = streaming || !current;
    deleteConversationBtn.title = current
      ? `删除会话「${current.title || "未命名"}」`
      : "请先选择会话";
  }
  if (!memoryToggleBtn) return;
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
  let sawError = false;
  let sawDone = false;
  let lastErrorDetail = "";
  let donePayload = null;
  try {
    while (true) {
      let chunk;
      try {
        chunk = await reader.read();
      } catch (error) {
        if (error?.name === "AbortError") {
          throw new Error("回答请求已取消。");
        }
        throw new Error("回答连接已中断，请检查网络后重试。");
      }
      const { done, value } = chunk;
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
        if (event === "error") {
          sawError = true;
          lastErrorDetail = String(data?.detail || "流式生成出错");
        } else if (event === "done") {
          sawDone = true;
          donePayload = data;
          if (data?.ok === false) sawError = true;
        }
        const fn = handlers[event];
        if (fn) fn(data);
      }
    }
    if (!sawDone) {
      throw new Error(
        sawError && lastErrorDetail
          ? `回答生成失败：${lastErrorDetail}`
          : "回答连接已中断：服务端未发送完成信号，请重试。"
      );
    }
    return { sawError, sawDone, done: donePayload, errorDetail: lastErrorDetail };
  } finally {
    reader.releaseLock();
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
  if (queryScopeCount) queryScopeCount.textContent = String(n);
  if (chatDockDocLabel) {
    chatDockDocLabel.textContent = n > 0 ? `已选 ${n} 个知识库参与检索` : "请先勾选左侧知识库";
  }
  if (ragQueryBtn) ragQueryBtn.disabled = n === 0;
}

function renderLibraries() {
  if (!libraryList) return;
  if (libraryCount) libraryCount.textContent = String(libraries.length);
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

function documentIndexDisplay(document) {
  const state = String(document.status || "pending");
  if (state === "pending") {
    return {
      text: document.status_detail === "queued_reindex" ? "等待重新索引" : "等待索引",
      action: "排队中",
      canReindex: false
    };
  }
  if (state === "processing") {
    return { text: "正在解析并建立索引", action: "索引中", canReindex: false };
  }
  if (state === "failed") {
    const detail = document.status_detail
      ? ` · ${String(document.status_detail).slice(0, 80)}`
      : "";
    return { text: `索引失败${detail}`, action: "重索引", canReindex: true };
  }
  if (state === "ready" && Number(document.page_count || 0) > 0) {
    return {
      text: `可检索 · ${Number(document.page_count)} 页`,
      action: "重索引",
      canReindex: true
    };
  }
  if (state === "ready") {
    return { text: "索引异常 · 未生成可检索内容", action: "重索引", canReindex: true };
  }
  return { text: state, action: "重索引", canReindex: true };
}

async function refreshDocuments() {
  if (!documentList) return;
  const refreshSequence = ++documentRefreshSequence;
  const libraryId = activeLibraryId;
  if (!libraryId) {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if (documentCount) documentCount.textContent = "0";
    documentList.innerHTML = "<li class='muted'>请先选择知识库。</li>";
    return;
  }
  const docs = await api(`/api/libraries/${libraryId}/documents`);
  if (refreshSequence !== documentRefreshSequence || libraryId !== activeLibraryId) return;
  if (documentCount) documentCount.textContent = String(docs.length);
  documentList.innerHTML = "";
  if (!docs.length) {
    documentList.innerHTML = "<li class='muted'>当前库暂无文档。</li>";
  }
  for (const d of docs) {
    const indexDisplay = documentIndexDisplay(d);
    const li = document.createElement("li");
    li.className = `doc-item is-${escapeHtml(String(d.status || "pending"))}`;
    li.innerHTML = `<div class="doc-row">
      <div class="doc-meta">
        <strong>${escapeHtml(d.file_name)}</strong>
        <span>${escapeHtml(indexDisplay.text)}</span>
      </div>
      <button type="button" class="btn btn-ghost btn-sm" data-reindex="${escapeHtml(d.id)}" ${indexDisplay.canReindex ? "" : "disabled"}>${escapeHtml(indexDisplay.action)}</button>
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
    }, 2500);
  }
  if (!pending && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
    refreshUsageSummary().catch(() => {});
  }
}

async function refreshHealth() {
  try {
    const h = await api("/api/health");
    let state = "ok";
    if (healthLibraryState) {
      if (!h.database) {
        healthLibraryState.textContent = "数据库异常";
        state = "error";
      } else if (h.worker_alive === false) {
        healthLibraryState.textContent = "Worker 离线";
        state = "warning";
      } else if (!h.has_api_key) {
        healthLibraryState.textContent = "缺少模型配置";
        state = "warning";
      } else {
        healthLibraryState.textContent = "运行正常";
      }
    }
    if (serviceStatusPill) serviceStatusPill.dataset.state = state;
  } catch {
    if (healthLibraryState) healthLibraryState.textContent = "离线";
    if (serviceStatusPill) serviceStatusPill.dataset.state = "error";
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
    docStatusLine.textContent = `已选：${file.name}。请点击「添加并索引」写入当前知识库。`;
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
    const isReady = doc.status === "ready" && Number(doc.page_count || 0) > 0;
    setStatus(
      isReady
        ? `文档已存在且可检索：${doc.file_name}`
        : `已上传：${doc.file_name}，索引任务已排队。`
    );
    if (pdfFile) pdfFile.value = "";
    showSelectedFile(null);
    if (docStatusLine) docStatusLine.textContent = isReady
      ? `「${doc.file_name}」已在当前知识库中，可直接检索。`
      : `「${doc.file_name}」正在后台建立索引；完成后论文库会自动更新，无需点击重索引。`;
    await refreshLibraries();
  } catch (e) {
    if (docStatusLine) docStatusLine.textContent = `上传失败：${e.message}`;
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
  if (healthResponsePath) healthResponsePath.textContent = "处理中";
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
  let resolvedSkillTitle = "";
  let streamFailed = false;
  let streamFailureMessage = "";

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
        temperature: 0.2,
        skill_id: skillSelect?.value || "auto"
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
        if (data.skill) {
          resolvedSkillTitle = data.skill.title || data.skill.id || "科研问答";
          if (skillSelect) skillSelect.title = data.skill.description || resolvedSkillTitle;
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
          const skillTitle = data.skill?.title || resolvedSkillTitle;
          metaEl.textContent = `${skillTitle ? `${skillTitle} · ` : ""}置信度 ${data.confidence || "N/A"} · 命中 ${data.retrieval_hit ?? 0}`;
        }
        if (healthRetrievalHit) healthRetrievalHit.textContent = String(data.retrieval_hit ?? 0);
        if (healthResponsePath) healthResponsePath.textContent = data.degraded ? "降级" : "Agent";
      },
      error: (data) => {
        streamFailed = true;
        streamFailureMessage = data.detail || "流式生成出错";
        if (healthResponsePath) healthResponsePath.textContent = "异常";
        setBubbleStatus(streamFailureMessage, false);
        setStatus(streamFailureMessage, true);
      },
      done: (data) => {
        if (data?.ok === false) {
          streamFailed = true;
          streamFailureMessage = data.detail || streamFailureMessage || "回答生成失败，请重试。";
          if (healthResponsePath) healthResponsePath.textContent = "异常";
          setBubbleStatus(streamFailureMessage, false);
          setStatus(streamFailureMessage, true);
          return;
        }
        if (streamFailed) {
          setBubbleStatus(streamFailureMessage || "回答生成失败，请重试。", false);
          return;
        }
        setBubbleStatus("", false);
        setStatus("已回复。");
        refreshUsageSummary().catch(() => {});
      }
    });
    await refreshConversations();
  } catch (e) {
    const message = e?.message || "回答连接已中断，请重试。";
    if (healthResponsePath) {
      healthResponsePath.textContent = message.includes("连接") ? "中断" : "异常";
    }
    if (!streamFailed) {
      streamFailed = true;
      streamFailureMessage = message;
      setBubbleStatus(message, false);
      if (textEl && !textEl.textContent.trim()) textEl.innerHTML = formatAnswerHtml(message);
      else appendChat("assistant", formatAnswerHtml(message));
    } else {
      setBubbleStatus(streamFailureMessage || message, false);
    }
    setStatus(streamFailureMessage || message, true);
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

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!ragQuestion) return;
    ragQuestion.value = button.getAttribute("data-prompt") || "";
    ragQuestion.focus();
    setStatus(
      selectedQueryLibraryIds.size
        ? "示例问题已填入，可以直接发送。"
        : "示例问题已填入，请先选择检索知识库。"
    );
  });
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

deleteConversationBtn?.addEventListener("click", async () => {
  if (!activeConversationId || streaming) return;
  const conversationId = activeConversationId;
  const currentIndex = conversations.findIndex((item) => item.id === conversationId);
  const current = conversations[currentIndex];
  const title = current?.title || "未命名";
  const approved = window.confirm(
    `确认删除会话“${title}”？该会话的聊天消息和长记忆将永久删除，无法撤销；Token 与费用统计会继续保留。`
  );
  if (!approved) return;

  deleteConversationBtn.disabled = true;
  try {
    await api(`/api/conversations/${conversationId}`, { method: "DELETE" });
    activeConversationId = "";
    activeConversationMemoryEnabled = true;
    clearChatDom();
    await refreshConversations();
    const fallbackIndex = Math.max(0, currentIndex);
    const fallback = conversations[Math.min(fallbackIndex, conversations.length - 1)];
    if (fallback) {
      await loadConversation(fallback.id);
    } else {
      renderConversationSelect();
      updateMemoryControl();
    }
    setStatus(`会话“${title}”已删除。`);
  } catch (e) {
    await refreshConversations().catch(() => {});
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

skillSelect?.addEventListener("change", () => {
  if (!skillSelect) return;
  const skill = researchSkills.find((item) => item.id === skillSelect.value);
  skillSelect.title = skill?.description || "系统将根据问题自动选择科研模式";
});

usageCostPill?.addEventListener("click", () => {
  openUsagePanel().catch(() => {});
});

usageCloseBtn?.addEventListener("click", closeUsagePanel);
usageModalBackdrop?.addEventListener("click", closeUsagePanel);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && usageModal && !usageModal.hidden) {
    closeUsagePanel();
  }
});

(async function boot() {
  if (!token) {
    location.replace("/login.html");
    return;
  }
  try {
    const me = await api("/api/auth/me");
    const username = me.username || "研究者";
    if (authUserLabel) authUserLabel.textContent = username;
    if (authUserAvatar) authUserAvatar.textContent = username.trim().slice(0, 1) || "研";
    await refreshHealth();
    await refreshResearchSkills();
    await refreshUsageSummary();
    await refreshLibraries();
    await refreshConversations();
    if (conversations[0]) {
      await loadConversation(conversations[0].id);
    }
  } catch {
    location.replace("/login.html");
  }
})();
