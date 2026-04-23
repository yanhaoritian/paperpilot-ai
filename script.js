const personas = {
  researcher: {
    title: "算法 / 研究视角总结",
    subtitle: "聚焦方法创新、实验结论和是否值得进一步复现。",
    summary: (title) => `${title} 通过注意力机制重构序列建模范式，在性能和训练效率之间找到了更优平衡。`,
    innovations: [
      "抛弃 RNN/CNN 主干，使用 self-attention 建模全局依赖。",
      "通过 multi-head attention 和 positional encoding 保留序列位置信息。",
      "在机器翻译任务上取得 SOTA，并显著缩短训练时间。"
    ],
    risks: [
      "需要重点确认模型在长序列和低资源场景下的稳定性。",
      "实验设置与当下硬件条件不同，复现时需重估资源成本。",
      "论文中的指标领先不代表对所有任务都有同等收益。"
    ],
    actions: [
      "优先阅读方法章节和 ablation study，确认创新点是否独立成立。",
      "记录实验配置、数据集和关键超参数，方便后续复现。",
      "对比同方向论文，判断这篇工作是增量优化还是范式变化。"
    ],
    outline: [
      "交代研究背景与序列建模的已有瓶颈。",
      "拆解核心模块，说明每个设计解决了什么问题。",
      "阅读实验结果时重点看对比基线和消融实验。",
      "给出是否值得复现、能复现到什么程度的判断。"
    ]
  },
  student: {
    title: "学生入门视角总结",
    subtitle: "聚焦理解门槛、基础概念和推荐阅读路径。",
    summary: (title) => `${title} 是理解现代大模型基础结构的必读论文，核心收获是理解注意力为什么重要。`,
    innovations: [
      "把复杂序列处理变得更直观，帮助初学者理解模型如何关注重点信息。",
      "这篇论文是很多后续大模型架构的起点，学习价值高。",
      "阅读后可以建立 attention、encoder、decoder 等核心概念框架。"
    ],
    risks: [
      "如果没有基础机器学习知识，第一次读会感觉抽象。",
      "符号和公式较多，建议配合可视化材料一起理解。",
      "只记结论不理解动机，后面学大模型会容易断层。"
    ],
    actions: [
      "先看摘要、图示和结论，再回到方法细节。",
      "边读边整理概念卡片，比如 self-attention、multi-head attention。",
      "读完后尝试用自己的话讲给别人听，检验是否真正理解。"
    ],
    outline: [
      "先讲论文背景：它为什么会出现。",
      "再讲最重要的概念：注意力机制是什么。",
      "然后解释 Transformer 结构是怎么组成的。",
      "最后总结这篇论文为什么值得所有 AI 学习者了解。"
    ]
  }
};

const titleInput = document.getElementById("paperTitle");
const abstractInput = document.getElementById("paperAbstract");
const goalSelect = document.getElementById("goalSelect");
const generateBtn = document.getElementById("generateBtn");
const exportBtn = document.getElementById("exportBtn");
const personaButtons = document.querySelectorAll(".persona-btn");
const modelSelect = document.getElementById("modelName");
const temperatureInput = document.getElementById("temperature");
const statusLine = document.getElementById("statusLine");

const pdfFileInput = document.getElementById("pdfFile");
const extractBtn = document.getElementById("extractBtn");
const addCompareBtn = document.getElementById("addCompareBtn");
const clearCompareBtn = document.getElementById("clearCompareBtn");
const compareBtn = document.getElementById("compareBtn");
const compareList = document.getElementById("compareList");
const compareTheme = document.getElementById("compareTheme");
const compareDiff = document.getElementById("compareDiff");
const compareOpportunity = document.getElementById("compareOpportunity");
const compareRecommendation = document.getElementById("compareRecommendation");

const docStatusLine = document.getElementById("docStatusLine");
const prdSummarizeBtn = document.getElementById("prdSummarizeBtn");
const prdSummaryOut = document.getElementById("prdSummaryOut");
const ragQuestion = document.getElementById("ragQuestion");
const ragQueryBtn = document.getElementById("ragQueryBtn");
const ragChatMessages = document.getElementById("ragChatMessages");
const ragClearChatBtn = document.getElementById("ragClearChatBtn");
const chatDockDocLabel = document.getElementById("chatDockDocLabel");
const ragPromptChips = document.getElementById("ragPromptChips");
const healthLibraryState = document.getElementById("healthLibraryState");
const healthModelName = document.getElementById("healthModelName");
const healthRetrievalHit = document.getElementById("healthRetrievalHit");
const healthResponsePath = document.getElementById("healthResponsePath");

const resultTitle = document.getElementById("resultTitle");
const resultSubtitle = document.getElementById("resultSubtitle");
const summaryCardLabel = document.getElementById("summaryCardLabel");
const innovationCardLabel = document.getElementById("innovationCardLabel");
const riskCardLabel = document.getElementById("riskCardLabel");
const actionCardLabel = document.getElementById("actionCardLabel");
const outlineCardTitle = document.getElementById("outlineCardTitle");
const quickSummary = document.getElementById("quickSummary");
const innovationList = document.getElementById("innovationList");
const riskList = document.getElementById("riskList");
const actionList = document.getElementById("actionList");
const meetingOutline = document.getElementById("meetingOutline");

let currentPersona = "researcher";
let latestResult = null;
let compareItems = [];
let latestComparison = null;
const resultCache = new Map();
let latestExtractedFullText = "";
const selectedCompareTitles = new Set();
let currentDocumentId = null;
let currentDocumentName = "";
/** @type {{ status: string, has_embeddings: boolean, chunk_count: number } | null} */
let lastDocumentMeta = null;
let libraryQueryableReady = false;
/** @type {Array<{ role: string, content: string }>} */
let ragTurnHistory = [];
const RAG_CLIENT_HISTORY_CAP = 20;
const sessionId = (globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(16).slice(2)}`).slice(
  0,
  80
);
/** @type {Map<string, any>} */
const answerFeedbackContext = new Map();

function fillList(target, items) {
  target.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    target.appendChild(li);
  });
}

function toArray(value, fallback) {
  if (Array.isArray(value) && value.length > 0) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  return fallback;
}

function updateStatus(text, isError = false) {
  statusLine.textContent = `提示：${text}`;
  statusLine.classList.toggle("error", isError);
}

function updateWorkspaceHealth({ retrievalHit, degraded } = {}) {
  if (healthModelName && modelSelect) {
    healthModelName.textContent = (modelSelect.value || "gpt-4.1-mini").trim() || "gpt-4.1-mini";
  }
  if (healthLibraryState) {
    healthLibraryState.textContent = libraryQueryableReady ? "可问答" : "未就绪";
  }
  if (healthRetrievalHit && Number.isFinite(Number(retrievalHit))) {
    healthRetrievalHit.textContent = String(Number(retrievalHit));
  }
  if (healthResponsePath && typeof degraded === "boolean") {
    healthResponsePath.textContent = degraded ? "降级/宽松召回" : "标准";
  }
}

let cachedMaxPdfMb = 32;

function updateModelOptions(models, defaultModel) {
  if (!modelSelect) {
    return;
  }
  const candidates = Array.isArray(models)
    ? models.map((item) => String(item).trim()).filter(Boolean)
    : [];
  const normalizedDefault = String(defaultModel || "").trim();
  const currentValue = (modelSelect.value || "").trim();
  const options = candidates.length > 0 ? candidates : [normalizedDefault || currentValue || "gpt-4.1-mini"];
  const uniqueOptions = Array.from(new Set(options));
  const selectedValue = uniqueOptions.includes(currentValue)
    ? currentValue
    : (uniqueOptions.includes(normalizedDefault) ? normalizedDefault : uniqueOptions[0]);

  modelSelect.innerHTML = "";
  uniqueOptions.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    if (model === selectedValue) {
      option.selected = true;
    }
    modelSelect.appendChild(option);
  });
}

async function syncConfigFromServer() {
  const hintEl = document.getElementById("pdfSizeHint");
  try {
    const response = await fetch(apiUrl("/api/health"));
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    const mb = data.maxPdfMb;
    if (Number.isFinite(mb) && mb > 0) {
      cachedMaxPdfMb = mb;
      if (hintEl) {
        hintEl.textContent = `提示：单个 PDF 请控制在 ${mb} MB 以内。提取后会回填摘要展示，分析时优先使用整篇文本。`;
      }
    }
    updateModelOptions(data.modelOptions, data.defaultModel);
    try {
      const libResp = await fetch(apiUrl("/api/library/status"));
      if (libResp.ok) {
        const libData = await libResp.json();
        libraryQueryableReady = Boolean(libData.has_queryable_docs);
      }
    } catch {
      /* ignore library status errors */
    }
    updateRagControls();
    updateChatDockLabel();
    updateWorkspaceHealth();
  } catch {
    /* 使用页面默认文案 */
  }
}

async function readErrorMessage(response) {
  const raw = await response.text();
  let msg = "请稍后再试。";
  try {
    const data = JSON.parse(raw);
    if (data && typeof data.error === "string" && data.error.trim()) {
      msg = data.error.trim();
    }
  } catch {
    if (raw && raw.length < 400) {
      msg = raw.trim().slice(0, 400);
    }
  }
  return msg;
}

function getPaperStateKey() {
  const title = titleInput.value.trim();
  const abstract = abstractInput.value.trim();
  const goal = goalSelect.value;
  return [title, abstract, goal, latestExtractedFullText].join("||");
}

function saveCurrentResultToCache() {
  if (!latestResult) {
    return;
  }
  resultCache.set(`${getPaperStateKey()}||${currentPersona}`, latestResult);
}

function loadCachedResult(personaKey = currentPersona) {
  return resultCache.get(`${getPaperStateKey()}||${personaKey}`) || null;
}

function clearResultCache() {
  resultCache.clear();
  latestResult = null;
  currentDocumentId = null;
  currentDocumentName = "";
  lastDocumentMeta = null;
  resetPrdRagUi();
  resetOutputToEmptyState();
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightAnswerExcerpt(wrap, excerpt) {
  if (!wrap) return;
  const bodyEl = wrap.querySelector(".chat-bubble__body");
  if (!bodyEl) return;
  const originalAnswer = String(bodyEl.getAttribute("data-answer-text") || bodyEl.textContent || "");
  const target = String(excerpt || "").trim();
  if (!target) {
    bodyEl.textContent = originalAnswer;
    return;
  }
  const idx = originalAnswer.indexOf(target);
  if (idx < 0) {
    bodyEl.textContent = originalAnswer;
    return;
  }
  const before = originalAnswer.slice(0, idx);
  const hit = originalAnswer.slice(idx, idx + target.length);
  const after = originalAnswer.slice(idx + target.length);
  bodyEl.innerHTML = `${escapeHtml(before)}<mark class="answer-highlight">${escapeHtml(hit)}</mark>${escapeHtml(after)}`;
}

function updatePromptChipsByIntent(intent) {
  if (!ragPromptChips) return;
  const targetIntent = String(intent || "").trim();
  const chips = Array.from(ragPromptChips.querySelectorAll(".prompt-chip"));
  chips.forEach((chip) => chip.classList.remove("prompt-chip--recommended"));
  if (!targetIntent) return;
  const matched = chips.find((chip) => String(chip.getAttribute("data-intent-key") || "") === targetIntent);
  if (!matched) return;
  matched.classList.add("prompt-chip--recommended");
  ragPromptChips.prepend(matched);
}

function updateRagControls() {
  if (!prdSummarizeBtn || !ragQueryBtn) {
    return;
  }
  const st = lastDocumentMeta?.status;
  const prdOk = Boolean(currentDocumentId) && (st === "ready" || st === "ready_no_embed");
  prdSummarizeBtn.disabled = !prdOk;
  const ragOk = libraryQueryableReady;
  ragQueryBtn.disabled = !ragOk;
}

function setChatPlaceholder() {
  if (!ragChatMessages) {
    return;
  }
  ragChatMessages.innerHTML =
    '<p class="chat-placeholder">文献入库并完成索引后，可在此基于整个文献库持续对话与追问。</p>';
}

function clearRagConversation({ resetDockLabel = false } = {}) {
  ragTurnHistory = [];
  setChatPlaceholder();
  if (resetDockLabel) {
    updateChatDockLabel();
  }
  updateWorkspaceHealth({ retrievalHit: 0 });
}

function updateChatDockLabel() {
  if (!chatDockDocLabel) {
    return;
  }
  if (!libraryQueryableReady) {
    chatDockDocLabel.textContent = "文献库未就绪";
    return;
  }
  if (!lastDocumentMeta || !currentDocumentId) {
    chatDockDocLabel.textContent = "文献库可用";
    return;
  }
  const name = currentDocumentName.trim();
  chatDockDocLabel.textContent = name ? `已入库：${name.slice(0, 20)}${name.length > 20 ? "…" : ""}` : "文献库已就绪";
}

function scrollChatToBottom() {
  if (!ragChatMessages) {
    return;
  }
  ragChatMessages.scrollTop = ragChatMessages.scrollHeight;
}

function appendChatUserBubble(text) {
  if (!ragChatMessages) {
    return;
  }
  const ph = ragChatMessages.querySelector(".chat-placeholder");
  if (ph) {
    ph.remove();
  }
  const div = document.createElement("div");
  div.className = "chat-bubble chat-bubble--user";
  div.textContent = text;
  ragChatMessages.appendChild(div);
  scrollChatToBottom();
}

function appendChatErrorBubble(message) {
  if (!ragChatMessages) {
    return;
  }
  const ph = ragChatMessages.querySelector(".chat-placeholder");
  if (ph) {
    ph.remove();
  }
  const div = document.createElement("div");
  div.className = "chat-bubble chat-bubble--assistant chat-bubble--error";
  const p = document.createElement("p");
  p.className = "chat-bubble__body";
  p.textContent = message;
  div.appendChild(p);
  ragChatMessages.appendChild(div);
  scrollChatToBottom();
}

const FEEDBACK_ERROR_TAG_OPTIONS = [
  { key: "intent_mismatch", label: "问题类型识别错" },
  { key: "retrieval_miss", label: "没检到关键证据" },
  { key: "retrieval_noise", label: "检索噪音大" },
  { key: "citation_invalid", label: "引用不可信" },
  { key: "answer_incomplete", label: "回答不完整" },
  { key: "answer_incorrect", label: "回答不正确" }
];

function collectFeedbackErrorTags(root) {
  if (!root) return [];
  return Array.from(root.querySelectorAll(".feedback-error-tag input[type='checkbox']:checked"))
    .map((el) => String(el.value || "").trim())
    .filter(Boolean)
    .slice(0, 5);
}

async function sendAnswerFeedback(answerId, vote, wrongQuestionType = false, errorTags = []) {
  const ctx = answerFeedbackContext.get(answerId);
  if (!ctx) {
    return;
  }
  await fetch(apiUrl("/api/feedback"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      answer_id: answerId,
      vote,
      wrong_question_type: Boolean(wrongQuestionType),
      error_tags: Array.isArray(errorTags) ? errorTags.slice(0, 5) : [],
      question: ctx.question,
      answer: ctx.answer,
      intent: ctx.intent,
      document_scope: ctx.document_scope,
      document_ids: ctx.document_ids,
      retrieval_hit_count: ctx.retrieval_hit_count,
      retrieval_top_scores: ctx.retrieval_top_scores,
      retrieval_score_gap: ctx.retrieval_score_gap,
      used_relaxed_threshold: ctx.used_relaxed_threshold,
      citation_count: ctx.citation_count,
      degraded: ctx.degraded,
      feedback_source: "explicit",
      client_time: new Date().toISOString(),
      session_id: sessionId
    })
  });
}

function appendChatAssistantBubble(payload, questionText) {
  if (!ragChatMessages) {
    return;
  }
  const ph = ragChatMessages.querySelector(".chat-placeholder");
  if (ph) {
    ph.remove();
  }
  const degraded = Boolean(payload.degraded);
  const result = payload.result || {};
  const answerId =
    (payload?.result?.answer_id && String(payload.result.answer_id).trim()) ||
    `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const documentIds = Array.isArray(result?.citations)
    ? result.citations.map((c) => c.document_id).filter(Boolean)
    : [];
  answerFeedbackContext.set(answerId, {
    question: String(questionText || "").trim(),
    answer: String(result.answer || "").trim(),
    intent: String(payload?.retrieval?.intent || "unknown"),
    document_scope: "library",
    document_ids: Array.from(new Set(documentIds)).slice(0, 30),
    retrieval_hit_count: Number(payload?.retrieval?.hit_count || 0),
    retrieval_top_scores: Array.isArray(payload?.retrieval?.scores)
      ? payload.retrieval.scores.map((s) => Number(s?.score)).filter((n) => Number.isFinite(n)).slice(0, 3)
      : [],
    retrieval_score_gap: (() => {
      const arr = Array.isArray(payload?.retrieval?.scores)
        ? payload.retrieval.scores.map((s) => Number(s?.score)).filter((n) => Number.isFinite(n))
        : [];
      return arr.length >= 2 ? Number((arr[0] - arr[1]).toFixed(4)) : null;
    })(),
    used_relaxed_threshold: Boolean(payload?.retrieval?.used_relaxed_threshold),
    citation_count: Array.isArray(result?.citations) ? result.citations.length : 0,
    degraded
  });
  const wrap = document.createElement("div");
  wrap.className = "chat-bubble chat-bubble--assistant";
  const answerText = String(result.answer || "");
  wrap.innerHTML = [
    degraded ? "<p><strong>提示：</strong>本次为证据不足或降级路径返回。</p>" : "",
    `<p class="chat-bubble__body" data-answer-text="${escapeHtml(answerText)}">${escapeHtml(answerText)}</p>`,
    `<p class="citation-meta">置信度：${escapeHtml(result.confidence || "N/A")}${
      result.out_of_scope ? " · 可能超出文献范围" : ""
    }</p>`,
    `<div class="answer-feedback" data-answer-id="${escapeHtml(answerId)}">
      <button type="button" class="feedback-btn" data-vote="up" title="有帮助">👍</button>
      <button type="button" class="feedback-btn" data-vote="down" title="没帮助">👎</button>
      <label class="feedback-tag">
        <input type="checkbox" class="feedback-wrong-type"> 问题类型错误
      </label>
      <div class="feedback-error-tags">
        ${FEEDBACK_ERROR_TAG_OPTIONS.map(
          (item) => `<label class="feedback-error-tag"><input type="checkbox" value="${escapeHtml(item.key)}"> ${escapeHtml(
            item.label
          )}</label>`
        ).join("")}
      </div>
    </div>`
  ].join("");
  const citations = Array.isArray(result.citations) ? result.citations : [];
  if (citations.length > 0) {
    const citeWrap = document.createElement("div");
    citeWrap.className = "chat-bubble__cites";
    const cards = citations
      .slice(0, 6)
      .map((c) => {
        const docName = c.document_name ? `文献：${escapeHtml(c.document_name)} · ` : "";
        const page =
          c.page_start != null
            ? `页码：${escapeHtml(c.page_start)}${
                c.page_end != null && c.page_end !== c.page_start ? `-${escapeHtml(c.page_end)}` : ""
              }`
            : "页码：未知";
        return `<article class="citation-card" role="button" tabindex="0" data-excerpt="${escapeHtml(c.excerpt || "")}">
          <p class="citation-meta">${docName}chunk：${escapeHtml(c.chunk_id || "N/A")} · ${page}</p>
          <p>${escapeHtml(c.excerpt || "").slice(0, 260)}</p>
        </article>`;
      })
      .join("");
    citeWrap.innerHTML = `<p class="citation-title">引用证据</p><div class="citation-stack">${cards}</div>`;
    wrap.appendChild(citeWrap);
  } else if (degraded) {
    const tips = document.createElement("div");
    tips.className = "chat-bubble__rewrite";
    tips.innerHTML = `<p class="citation-title">建议改写问题</p>
      <div class="rewrite-chip-row">
        <button type="button" class="rewrite-chip" data-q="请说明这篇论文的方法流程与关键模块。">问方法流程</button>
        <button type="button" class="rewrite-chip" data-q="请给出论文的数据来源、样本与实验设置。">问数据与实验</button>
        <button type="button" class="rewrite-chip" data-q="请总结作者的主要结论和证据。">问结论与证据</button>
      </div>`;
    wrap.appendChild(tips);
  }
  const feedbackRoot = wrap.querySelector(".answer-feedback");
  if (feedbackRoot) {
    const upBtn = feedbackRoot.querySelector('button[data-vote="up"]');
    const downBtn = feedbackRoot.querySelector('button[data-vote="down"]');
    const wrongTypeEl = feedbackRoot.querySelector(".feedback-wrong-type");
    const submit = async (vote) => {
      if (!upBtn || !downBtn) return;
      upBtn.disabled = true;
      downBtn.disabled = true;
      const errorTags = collectFeedbackErrorTags(feedbackRoot);
      try {
        await sendAnswerFeedback(answerId, vote, Boolean(wrongTypeEl?.checked), errorTags);
        feedbackRoot.classList.add("submitted");
      } catch (_error) {
        upBtn.disabled = false;
        downBtn.disabled = false;
      }
    };
    if (upBtn) upBtn.addEventListener("click", () => submit("up"));
    if (downBtn) downBtn.addEventListener("click", () => submit("down"));
  }
  ragChatMessages.appendChild(wrap);
  wrap.querySelectorAll(".rewrite-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!ragQuestion) return;
      ragQuestion.value = String(btn.getAttribute("data-q") || "").trim();
      ragQuestion.focus();
    });
  });
  wrap.querySelectorAll(".citation-card").forEach((card) => {
    const activate = () => {
      wrap.querySelectorAll(".citation-card").forEach((el) => el.classList.remove("citation-card--active"));
      card.classList.add("citation-card--active");
      highlightAnswerExcerpt(wrap, card.getAttribute("data-excerpt") || "");
    };
    card.addEventListener("click", activate);
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
  });
  updateWorkspaceHealth({
    retrievalHit: Number(payload?.retrieval?.hit_count || 0),
    degraded
  });
  updatePromptChipsByIntent(payload?.retrieval?.intent || "");
  scrollChatToBottom();
}

function resetPrdRagUi() {
  if (prdSummaryOut) {
    prdSummaryOut.classList.add("muted");
    prdSummaryOut.textContent = "生成后将在此展示结构化字段。";
  }
  if (ragQuestion) {
    ragQuestion.value = "";
  }
  if (docStatusLine) {
    docStatusLine.textContent = "尚未解析：完成解析后将在此显示入库状态与分块信息。";
  }
  clearRagConversation({ resetDockLabel: true });
  updateRagControls();
}

function renderPrdSummary(result) {
  if (!prdSummaryOut) {
    return;
  }
  prdSummaryOut.classList.remove("muted");
  const keywords = Array.isArray(result.keywords) ? result.keywords : [];
  const kw = keywords.map((k) => `<span class="keyword-chip">${escapeHtml(k)}</span>`).join("");
  prdSummaryOut.innerHTML = [
    `<div class="prd-block"><h5>研究问题</h5><p>${escapeHtml(result.researchQuestion)}</p></div>`,
    `<div class="prd-block"><h5>方法</h5><p>${escapeHtml(result.methods)}</p></div>`,
    `<div class="prd-block"><h5>结论</h5><p>${escapeHtml(result.conclusions)}</p></div>`,
    `<div class="prd-block"><h5>关键词</h5><div class="keyword-row">${
      kw || '<span class="keyword-chip">—</span>'
    }</div></div>`
  ].join("");
}

function resetOutputToEmptyState() {
  const persona = personas[currentPersona];
  resultTitle.textContent = persona.title;
  resultSubtitle.textContent = persona.subtitle;
  updateOutputLabels();
  quickSummary.textContent = "上传论文或输入摘要后，点击「生成当前视角总结」。";
  innovationList.innerHTML = "<li>生成后将在这里展示关键创新点。</li>";
  riskList.innerHTML = "<li>生成后将在这里展示实验结论与风险提示。</li>";
  actionList.innerHTML = "<li>生成后将在这里展示阅读建议与应用方向。</li>";
  meetingOutline.innerHTML = "<li>生成后将在这里展示汇报提纲。</li>";
}

function updateOutputLabels() {
  if (currentPersona === "student") {
    summaryCardLabel.textContent = "通俗总结";
    innovationCardLabel.textContent = "核心概念";
    riskCardLabel.textContent = "理解难点";
    actionCardLabel.textContent = "阅读建议";
    outlineCardTitle.textContent = "讲解提纲生成";
    return;
  }

  summaryCardLabel.textContent = "一句话总结";
  innovationCardLabel.textContent = "核心创新";
  riskCardLabel.textContent = "实验与风险";
  actionCardLabel.textContent = "落地启发";
  outlineCardTitle.textContent = "汇报提纲生成";
}

/** 服务地址留空时，请求发到当前网页所在站点 */
function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  return p;
}

function buildFallbackResult() {
  const persona = personas[currentPersona];
  const title = titleInput.value.trim() || "这篇论文";
  const abstract = abstractInput.value.trim();
  const goal = goalSelect.value;
  let prefix = "【是否值得深读】结论倾向：";
  if (goal === "meeting") {
    prefix = "【组会/面试】30 秒开场：";
  } else if (goal === "application") {
    prefix = "【落地启发】场景与价值：";
  }

  const abstractHint = abstract.length > 180
    ? "摘要信息较完整，以下结构与当前阅读目标对齐。"
    : "摘要较短，以下为按阅读目标整理的占位结构；连接 AI 后可生成更贴合原文的条目。";

  let outline;
  let actions;
  let risks;
  let readOrder;
  if (goal === "meeting") {
    outline = [
      "开场：问题背景与这篇工作的「一句话故事」",
      "核心方法：用生活化类比讲清关键设计（控制在一分钟）",
      "实验：哪条结果最能说服听众，以及一句局限",
      "收尾：可复述的结论 + 预设 1～2 个听众追问"
    ];
    actions = [
      "计时演练全文讲解，控制在目标时长内。",
      "准备一张方法示意图，能脱离论文讲清楚。",
      "列出三个最可能被问的问题并写出口头提纲。"
    ];
    risks = [
      "术语过多会让非小院线听众掉队，准备降级说法。",
      "实验设定与听众直觉场景不符时，需主动交代前提。",
      "若被问「和某某经典工作关系」，用对比一句接住。"
    ];
    readOrder = "先定讲解故事线，再精读方法图与主实验表";
  } else if (goal === "application") {
    outline = [
      "目标场景：谁在用、解决什么痛点",
      "方案要点：核心能力与可复用的技术组件",
      "实施条件：数据、算力、集成方式与迭代周期",
      "风险与指标：上线后如何衡量成功、已知坑位"
    ];
    actions = [
      "列出两个最小可行验证场景并定义成功指标。",
      "对照现有方案估算增量成本与依赖（数据/人力）。",
      "标注一条「论文有、现实可能没有」的前提。"
    ];
    risks = [
      "论文 benchmark 与真实负载/数据分布可能不一致。",
      "合规、安全或运维成本在文中往往一笔带过。",
      "效果提升是否覆盖集成与维护成本需单独算账。"
    ];
    readOrder = "先抓摘要中的任务定义，再对照实验中的设定是否贴近你的场景";
  } else {
    outline = [
      "结论快照：建议深读 / 泛读 / 跳过的倾向与理由",
      "关键证据：支撑上述判断的一条方法线与一条实验结果",
      "主要疑点：继续读前必须澄清的薄弱点",
      "是否继续：若读从哪一节切入，不读可转向哪些关键词或综述"
    ];
    actions = [
      "用 10 分钟扫方法与主实验，验证摘要是否夸大。",
      "若决定不读：保存标题与关键词供日后主题检索。",
      "若决定深读：列出要在笔记里回答的三个问题。"
    ];
    risks = [
      "时间与选题机会成本：深读后价值不及预期。",
      "领域前提不熟导致误判贡献或创新边界。",
      "追热点论文但实验不可复现或数据未公开。"
    ];
    readOrder = "摘要→结论→主实验；可疑再读方法细节";
  }

  return {
    quickSummary: `${prefix}${persona.summary(title)} ${abstractHint}`,
    innovations: persona.innovations,
    risks,
    actions,
    outline,
    confidence: "90%",
    readOrder
  };
}

function buildFallbackComparison() {
  const titles = compareItems.map((item) => item.title).join("、");
  return {
    commonTheme: `这些论文都在探索如何更高效地利用模型能力服务真实任务。当前对比集合：${titles || "未命名论文"}`,
    differences: [
      "方法路径不同：有的重架构，有的重训练技巧，有的重数据或任务设计。",
      "实验评价维度不同：有的强调准确率，有的强调效率和成本。",
      "应用成熟度不同：从基础研究到可直接复用之间存在明显跨度。"
    ],
    opportunities: [
      "可把共通能力整理成统一方法框架，减少重复阅读和验证成本。",
      "根据方法差异设计分层实验，先验证低成本高收益方向。",
      "在真实场景里同时追踪效果和成本，避免只看 benchmark。"
    ],
    recommendations: [
      "先选 1 篇最贴近当前研究目标的论文做小范围验证。",
      "建立统一对比模板，记录假设、指标和结论。",
      "每周迭代一次优先级，持续更新研究路线图。"
    ]
  };
}

function sanitizeSummary(raw) {
  const fallback = buildFallbackResult();
  return {
    quickSummary: String(raw?.quickSummary || fallback.quickSummary).trim(),
    innovations: toArray(raw?.innovations, fallback.innovations),
    risks: toArray(raw?.risks, fallback.risks),
    actions: toArray(raw?.actions, fallback.actions),
    outline: toArray(raw?.outline, fallback.outline),
    confidence: String(raw?.confidence || fallback.confidence).trim(),
    readOrder: String(raw?.readOrder || fallback.readOrder).trim()
  };
}

function sanitizeComparison(raw) {
  const fallback = buildFallbackComparison();
  return {
    commonTheme: String(raw?.commonTheme || fallback.commonTheme).trim(),
    differences: toArray(raw?.differences, fallback.differences),
    opportunities: toArray(raw?.opportunities, fallback.opportunities),
    recommendations: toArray(raw?.recommendations, fallback.recommendations)
  };
}

function applyResult(result) {
  const persona = personas[currentPersona];
  resultTitle.textContent = persona.title;
  resultSubtitle.textContent = persona.subtitle;
  updateOutputLabels();
  quickSummary.textContent = result.quickSummary;
  fillList(innovationList, result.innovations);
  fillList(riskList, result.risks);
  fillList(actionList, result.actions);
  fillList(meetingOutline, result.outline);
}

function applyComparison(result) {
  compareTheme.textContent = result.commonTheme;
  fillList(compareDiff, result.differences);
  fillList(compareOpportunity, result.opportunities);
  fillList(compareRecommendation, result.recommendations);
}

function renderCompareList() {
  if (compareItems.length === 0) {
    compareList.innerHTML = "<li>暂无论文，请先加入至少 2 篇。</li>";
    return;
  }
  compareList.innerHTML = "";
  compareItems.forEach((item, index) => {
    const li = document.createElement("li");
    const label = document.createElement("label");
    label.className = "compare-item-label";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "compare-item-checkbox";
    checkbox.checked = selectedCompareTitles.has(item.title);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        selectedCompareTitles.add(item.title);
      } else {
        selectedCompareTitles.delete(item.title);
      }
    });
    const text = document.createElement("span");
    text.textContent = `${index + 1}. ${item.title}`;
    label.appendChild(checkbox);
    label.appendChild(text);
    li.appendChild(label);
    compareList.appendChild(li);
  });
}

function setLoading(button, loadingText, isLoading) {
  button.disabled = isLoading;
  button.textContent = isLoading ? loadingText : button.dataset.defaultText;
  if (button.classList.contains("workflow-primary-btn")) {
    button.classList.toggle("is-loading", isLoading);
  }
}

function getActivePersonaLabel() {
  return document.querySelector(".persona-btn.active")?.textContent?.trim() || "算法 / 研究";
}

function getSummaryPayload() {
  const displayAbstract = abstractInput.value.trim();
  return {
    paperTitle: titleInput.value.trim() || "未命名论文",
    paperAbstract: displayAbstract || "未提供摘要",
    paperFullText: latestExtractedFullText || displayAbstract || "",
    personaKey: currentPersona,
    personaLabel: getActivePersonaLabel(),
    goal: goalSelect.value,
    goalLabel: goalSelect.options[goalSelect.selectedIndex]?.text || "快速判断",
    model: (modelSelect.value || "").trim(),
    temperature: Number.parseFloat(temperatureInput.value || "0.3")
  };
}

async function postJson(path, payload) {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    const msg = await readErrorMessage(response);
    throw new Error(msg);
  }
  return response.json();
}

async function renderResult() {
  setLoading(generateBtn, "生成中，请稍候...", true);
  updateStatus("正在生成总结，请稍候…");
  try {
    const payload = getSummaryPayload();
    const data = await postJson("/api/summarize", payload);
    latestResult = sanitizeSummary(data.result);
    saveCurrentResultToCache();
    applyResult(latestResult);
    updateStatus("总结已生成。");
  } catch (error) {
    updateStatus(error.message, true);
  } finally {
    setLoading(generateBtn, "生成中，请稍候...", false);
  }
}

async function extractFromPdf() {
  const file = pdfFileInput.files?.[0];
  if (!file) {
    updateStatus("请先选择 PDF 文件。", true);
    return;
  }
  const maxBytes = cachedMaxPdfMb * 1024 * 1024;
  if (file.size > maxBytes) {
    updateStatus(`文件超过当前上限（${cachedMaxPdfMb} MB），请选用更小的 PDF 或压缩后再试。`, true);
    return;
  }
  setLoading(extractBtn, "解析并入库中...", true);
  updateStatus("正在解析 PDF、分块并构建向量索引（可能需要数秒）…");
  try {
    const formData = new FormData();
    formData.append("pdf", file);
    const response = await fetch(apiUrl("/api/documents"), {
      method: "POST",
      body: formData
    });
    const raw = await response.text();
    let data = {};
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      data = {};
    }
    if (!response.ok) {
      currentDocumentId = null;
      lastDocumentMeta = null;
      updateRagControls();
      const msg =
        (typeof data.error === "string" && data.error.trim()) ||
        (raw && raw.length < 500 ? raw.trim() : "") ||
        "解析或入库失败，请稍后再试。";
      throw new Error(msg);
    }
    const extractedFullText = (data.fullText || data.text || "").trim();
    const extractedAbstract = (data.abstract || "").trim();
    if (!extractedFullText) {
      throw new Error("PDF 已上传，但未提取到可用于分析的文本。");
    }
    clearResultCache();
    latestExtractedFullText = extractedFullText;
    abstractInput.value = extractedAbstract || extractedFullText.slice(0, 1200);
    if (data.fileName && data.fileName.trim()) {
      titleInput.value = data.fileName.replace(/\.pdf$/i, "");
    }
    currentDocumentId = data.document_id || null;
    currentDocumentName = (data.fileName || "").replace(/\.pdf$/i, "").trim();
    lastDocumentMeta = {
      status: data.status || "unknown",
      has_embeddings: Boolean(data.has_embeddings),
      chunk_count: Number(data.chunk_count) || 0
    };
    libraryQueryableReady = libraryQueryableReady || Boolean(data.has_embeddings);
    updateRagControls();
    updateChatDockLabel();
    const ragHint = data.has_embeddings
      ? "已加入文献库，可与库中所有文献联合对话。"
      : "未检测到向量索引：配置 OPENAI_API_KEY 后重新解析，方可使用底部对话。";
    if (docStatusLine) {
      docStatusLine.textContent = `已入库：${currentDocumentName || "未命名文献"} · 分块 ${lastDocumentMeta.chunk_count} · ${ragHint}`;
    }
    updateStatus("解析完成：摘要已回填；PRD 总结在上方，文献对话请用屏幕底部输入框。");
    if (ragQuestion && data.has_embeddings) {
      requestAnimationFrame(() => ragQuestion.focus());
    }
  } catch (error) {
    updateStatus(error.message, true);
  } finally {
    setLoading(extractBtn, "解析并入库中...", false);
  }
}

async function runPrdSummarize() {
  if (!currentDocumentId) {
    updateStatus("请先完成 PDF 解析入库。", true);
    return;
  }
  setLoading(prdSummarizeBtn, "生成中…", true);
  updateStatus("正在生成 PRD 结构化总结…");
  try {
    const payload = {
      model: (modelSelect.value || "").trim(),
      temperature: Number.parseFloat(temperatureInput.value || "0.3")
    };
    const data = await postJson(`/api/documents/${encodeURIComponent(currentDocumentId)}/summarize`, payload);
    renderPrdSummary(data.result || {});
    const hint = data.cached ? "（命中缓存）" : "";
    const warn = data.degraded ? ` 降级提示：${data.warning || ""}` : "";
    updateStatus(`结构化总结已更新${hint}。${warn}`.trim());
  } catch (error) {
    updateStatus(error.message, true);
  } finally {
    setLoading(prdSummarizeBtn, "生成中…", false);
  }
}

async function runRagQuery() {
  if (!ragQueryBtn || ragQueryBtn.disabled) {
    return;
  }
  if (!libraryQueryableReady) {
    updateStatus("文献库尚未就绪，请先上传并完成索引。", true);
    return;
  }
  const q = (ragQuestion?.value || "").trim();
  if (!q) {
    updateStatus("请先输入要提问的内容。", true);
    return;
  }
  const historyForApi = ragTurnHistory.map((m) => ({ role: m.role, content: m.content }));
  appendChatUserBubble(q);
  if (ragQuestion) {
    ragQuestion.value = "";
  }
  setLoading(ragQueryBtn, "发送中…", true);
  updateStatus("正在检索相关片段并生成回答…");
  try {
    const payload = {
      question: q,
      history: historyForApi,
      model: (modelSelect.value || "").trim(),
      temperature: Number.parseFloat(temperatureInput.value || "0.3")
    };
    const response = await fetch(apiUrl("/api/library/query"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const raw = await response.text();
    let data = {};
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      data = {};
    }
    if (!response.ok) {
      const msg =
        (typeof data.error === "string" && data.error.trim()) ||
        (raw && raw.length < 500 ? raw.trim() : "") ||
        "问答请求失败，请稍后再试。";
      throw new Error(msg);
    }
    appendChatAssistantBubble(data, q);
    const ans = String(data?.result?.answer || "").trim();
    ragTurnHistory.push({ role: "user", content: q });
    ragTurnHistory.push({ role: "assistant", content: ans || "（无文本回答）" });
    if (ragTurnHistory.length > RAG_CLIENT_HISTORY_CAP) {
      ragTurnHistory = ragTurnHistory.slice(-RAG_CLIENT_HISTORY_CAP);
    }
    updateStatus("已回复，可继续追问。");
  } catch (error) {
    appendChatErrorBubble(error.message || "请求失败");
    updateStatus(error.message, true);
  } finally {
    setLoading(ragQueryBtn, "发送中…", false);
  }
}

function addCurrentPaperToCompare() {
  const title = titleInput.value.trim();
  const text = abstractInput.value.trim();
  if (!title || !text) {
    updateStatus("加入对比前请至少填写标题和摘要。", true);
    return;
  }
  const exists = compareItems.some((item) => item.title === title);
  if (!exists) {
    compareItems.push({
      title,
      abstract: text
    });
    selectedCompareTitles.delete(title);
  }
  renderCompareList();
  updateStatus(`已加入对比列表，当前共 ${compareItems.length} 篇。`);
}

function clearCompare() {
  if (selectedCompareTitles.size === 0) {
    updateStatus("请先勾选要删除的文献。", true);
    return;
  }
  const beforeCount = compareItems.length;
  compareItems = compareItems.filter((item) => !selectedCompareTitles.has(item.title));
  const removedCount = beforeCount - compareItems.length;
  selectedCompareTitles.clear();
  latestComparison = null;
  renderCompareList();
  applyComparison(buildFallbackComparison());
  updateStatus(`已删除 ${removedCount} 篇文献，当前剩余 ${compareItems.length} 篇。`);
}

async function generateCompare() {
  if (compareItems.length < 2) {
    updateStatus("请先加入至少 2 篇论文再做对比。", true);
    return;
  }
  setLoading(compareBtn, "对比中...", true);
    updateStatus("正在生成多篇对比，请稍候…");
  try {
    const payload = {
      papers: compareItems,
      personaKey: currentPersona,
      personaLabel: getActivePersonaLabel(),
      goal: goalSelect.value,
      goalLabel: goalSelect.options[goalSelect.selectedIndex]?.text || "快速判断",
      model: (modelSelect.value || "").trim(),
      temperature: Number.parseFloat(temperatureInput.value || "0.3")
    };
    const data = await postJson("/api/compare", payload);
    latestComparison = sanitizeComparison(data.result);
    applyComparison(latestComparison);
    updateStatus("对比结果已生成。");
  } catch (error) {
    updateStatus(error.message, true);
  } finally {
    setLoading(compareBtn, "对比中...", false);
  }
}

function exportMarkdown() {
  const persona = personas[currentPersona];
  const summary = latestResult || buildFallbackResult();
  const comparison = latestComparison || buildFallbackComparison();
  const title = titleInput.value.trim() || "未命名论文";
  const goalLabel = goalSelect.options[goalSelect.selectedIndex]?.text || "快速判断";
  const compareText = compareItems.length === 0 ? "暂无" : compareItems.map((item) => item.title).join("、");

  const content = [
    `# ${title} - 文献总结与对比`,
    "",
    `- 角色视角：${persona.title}`,
    `- 阅读目标：${goalLabel}`,
    `- 对比论文：${compareText}`,
    "",
    "## 一句话总结",
    summary.quickSummary,
    "",
    "## 核心创新",
    ...summary.innovations.map((item) => `- ${item}`),
    "",
    "## 实验与风险",
    ...summary.risks.map((item) => `- ${item}`),
    "",
    "## 落地启发",
    ...summary.actions.map((item) => `- ${item}`),
    "",
    "## 汇报提纲",
    ...summary.outline.map((item, index) => `${index + 1}. ${item}`),
    "",
    "## 多篇论文对比",
    `- 共同主题：${comparison.commonTheme}`,
    "",
    "### 核心差异",
    ...comparison.differences.map((item) => `- ${item}`),
    "",
    "### 应用机会",
    ...comparison.opportunities.map((item) => `- ${item}`),
    "",
    "### 下一步建议",
    ...comparison.recommendations.map((item) => `- ${item}`),
    "",
    "## 附加信息",
    `- 摘要可信度：${summary.confidence}`,
    `- 推荐阅读顺序：${summary.readOrder}`
  ].join("\n");

  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${title.replace(/[\\/:*?"<>|]/g, "_")}_report.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  updateStatus("已导出 Markdown 报告。");
}

personaButtons.forEach((button) => {
  button.addEventListener("click", () => {
    saveCurrentResultToCache();
    personaButtons.forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    currentPersona = button.dataset.persona;
    latestResult = loadCachedResult();
    if (latestResult) {
      applyResult(latestResult);
    } else {
      resetOutputToEmptyState();
    }
    updateStatus("已切换视角，可重新生成。");
  });
});

titleInput.addEventListener("input", clearResultCache);
abstractInput.addEventListener("input", () => {
  latestExtractedFullText = "";
  clearResultCache();
});
goalSelect.addEventListener("change", clearResultCache);

extractBtn.dataset.defaultText = extractBtn.textContent;
generateBtn.dataset.defaultText = generateBtn.textContent;
compareBtn.dataset.defaultText = compareBtn.textContent;
if (prdSummarizeBtn) {
  prdSummarizeBtn.dataset.defaultText = prdSummarizeBtn.textContent.trim();
}
if (ragQueryBtn) {
  ragQueryBtn.dataset.defaultText = ragQueryBtn.textContent.trim();
}

extractBtn.addEventListener("click", extractFromPdf);
if (prdSummarizeBtn) {
  prdSummarizeBtn.addEventListener("click", runPrdSummarize);
}
if (ragQueryBtn) {
  ragQueryBtn.addEventListener("click", runRagQuery);
}
if (ragClearChatBtn) {
  ragClearChatBtn.addEventListener("click", () => {
    clearRagConversation({ resetDockLabel: false });
    updateStatus("已清空本轮对话记录（未解绑文献）。");
  });
}
if (ragQuestion) {
  ragQuestion.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    runRagQuery();
  });
}
if (ragPromptChips) {
  ragPromptChips.querySelectorAll(".prompt-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      if (!ragQuestion) return;
      ragQuestion.value = String(chip.getAttribute("data-q") || "").trim();
      ragQuestion.focus();
    });
  });
}
if (modelSelect) {
  modelSelect.addEventListener("change", () => updateWorkspaceHealth());
}
generateBtn.addEventListener("click", renderResult);
addCompareBtn.addEventListener("click", addCurrentPaperToCompare);
clearCompareBtn.addEventListener("click", clearCompare);
compareBtn.addEventListener("click", generateCompare);
exportBtn.addEventListener("click", exportMarkdown);

resetPrdRagUi();
resetOutputToEmptyState();
applyComparison(buildFallbackComparison());
renderCompareList();
updateChatDockLabel();
updateWorkspaceHealth();

syncConfigFromServer();
