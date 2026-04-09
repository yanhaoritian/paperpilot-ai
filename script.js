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
const modelInput = document.getElementById("modelName");
const temperatureInput = document.getElementById("temperature");
const fallbackToggle = document.getElementById("fallbackToggle");
const backendUrlInput = document.getElementById("backendUrl");
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
  statusLine.textContent = `状态：${text}`;
  statusLine.classList.toggle("error", isError);
}

function getPaperStateKey() {
  const title = titleInput.value.trim();
  const abstract = abstractInput.value.trim();
  const goal = goalSelect.value;
  return [title, abstract, goal].join("||");
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
  resetOutputToEmptyState();
}

function resetOutputToEmptyState() {
  const persona = personas[currentPersona];
  resultTitle.textContent = persona.title;
  resultSubtitle.textContent = persona.subtitle;
  updateOutputLabels();
  quickSummary.textContent = "上传论文或输入摘要后，点击“使用 AI 生成结果”。";
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

function getBackendUrl() {
  return (backendUrlInput.value || "").trim().replace(/\/$/, "");
}

/** 公网同源部署时留空后端地址，请求发到当前站点 */
function apiUrl(path) {
  const base = getBackendUrl();
  const p = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${p}` : p;
}

function buildFallbackResult() {
  const persona = personas[currentPersona];
  const title = titleInput.value.trim() || "这篇论文";
  const abstract = abstractInput.value.trim();
  const goal = goalSelect.value;
  let prefix = "如果目标是快速判断价值，这篇论文最核心的结论是：";
  if (goal === "meeting") {
    prefix = "如果目标是用于组会或面试表达，这篇论文最值得你强调的是：";
  } else if (goal === "application") {
    prefix = "如果目标是寻找实际应用方向，这篇论文最重要的启发是：";
  }

  const abstractHint = abstract.length > 180
    ? "摘要显示这是一篇偏基础架构创新的工作，适合优先理解其方法变化与影响范围。"
    : "当前输入较短，系统会优先根据标题和阅读目标生成高层总结。";

  return {
    quickSummary: `${prefix}${persona.summary(title)} ${abstractHint}`,
    innovations: persona.innovations,
    risks: persona.risks,
    actions: persona.actions,
    outline: persona.outline,
    confidence: "90%",
    readOrder: "先看摘要与结论，再看方法与实验"
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
    li.textContent = `${index + 1}. ${item.title}`;
    compareList.appendChild(li);
  });
}

function setLoading(button, loadingText, isLoading) {
  button.disabled = isLoading;
  button.textContent = isLoading ? loadingText : button.dataset.defaultText;
}

function getActivePersonaLabel() {
  return document.querySelector(".persona-btn.active")?.textContent?.trim() || "算法 / 研究";
}

function getSummaryPayload() {
  return {
    paperTitle: titleInput.value.trim() || "未命名论文",
    paperAbstract: abstractInput.value.trim() || "未提供摘要",
    personaKey: currentPersona,
    personaLabel: getActivePersonaLabel(),
    goal: goalSelect.value,
    goalLabel: goalSelect.options[goalSelect.selectedIndex]?.text || "快速判断",
    model: (modelInput.value || "").trim(),
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
    const errorText = await response.text();
    throw new Error(`${path} 调用失败 (${response.status})：${errorText.slice(0, 220)}`);
  }
  return response.json();
}

async function renderResult() {
  setLoading(generateBtn, "生成中，请稍候...", true);
  updateStatus("正在调用后端生成总结...");
  try {
    const payload = getSummaryPayload();
    const data = await postJson("/api/summarize", payload);
    latestResult = sanitizeSummary(data.result);
    saveCurrentResultToCache();
    applyResult(latestResult);
    updateStatus("已完成 AI 生成");
  } catch (error) {
    if (fallbackToggle.checked) {
      latestResult = buildFallbackResult();
      saveCurrentResultToCache();
      applyResult(latestResult);
      updateStatus(`AI 调用失败，已切换本地结果：${error.message}`, true);
    } else {
      updateStatus(error.message, true);
    }
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
  setLoading(extractBtn, "抽取中...", true);
  updateStatus("正在提取 PDF 文本...");
  try {
    const formData = new FormData();
    formData.append("pdf", file);
    const response = await fetch(apiUrl("/api/extract-pdf"), {
      method: "POST",
      body: formData
    });
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`PDF 抽取失败 (${response.status})：${errorText.slice(0, 220)}`);
    }
    const data = await response.json();
    if (!data.abstract || !data.abstract.trim()) {
      throw new Error("PDF 已上传，但没有定位到摘要部分。");
    }
    clearResultCache();
    abstractInput.value = data.abstract;
    if (data.fileName && data.fileName.trim()) {
      titleInput.value = data.fileName.replace(/\.pdf$/i, "");
    }
    updateStatus(`已完成文本抽取（约 ${data.characters || 0} 字符）`);
  } catch (error) {
    updateStatus(error.message, true);
  } finally {
    setLoading(extractBtn, "抽取中...", false);
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
  }
  renderCompareList();
  updateStatus(`已加入对比列表，当前共 ${compareItems.length} 篇。`);
}

function clearCompare() {
  compareItems = [];
  latestComparison = null;
  renderCompareList();
  applyComparison(buildFallbackComparison());
  updateStatus("已清空对比列表。");
}

async function generateCompare() {
  if (compareItems.length < 2) {
    updateStatus("请先加入至少 2 篇论文再做对比。", true);
    return;
  }
  setLoading(compareBtn, "对比中...", true);
  updateStatus("正在生成多篇论文对比...");
  try {
    const payload = {
      papers: compareItems,
      personaKey: currentPersona,
      personaLabel: getActivePersonaLabel(),
      model: (modelInput.value || "").trim(),
      temperature: Number.parseFloat(temperatureInput.value || "0.3")
    };
    const data = await postJson("/api/compare", payload);
    latestComparison = sanitizeComparison(data.result);
    applyComparison(latestComparison);
    updateStatus("已完成论文对比分析。");
  } catch (error) {
    if (fallbackToggle.checked) {
      latestComparison = buildFallbackComparison();
      applyComparison(latestComparison);
      updateStatus(`对比失败，已使用本地结果：${error.message}`, true);
    } else {
      updateStatus(error.message, true);
    }
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
abstractInput.addEventListener("input", clearResultCache);
goalSelect.addEventListener("change", clearResultCache);

extractBtn.dataset.defaultText = extractBtn.textContent;
generateBtn.dataset.defaultText = generateBtn.textContent;
compareBtn.dataset.defaultText = compareBtn.textContent;

extractBtn.addEventListener("click", extractFromPdf);
generateBtn.addEventListener("click", renderResult);
addCompareBtn.addEventListener("click", addCurrentPaperToCompare);
clearCompareBtn.addEventListener("click", clearCompare);
compareBtn.addEventListener("click", generateCompare);
exportBtn.addEventListener("click", exportMarkdown);

resetOutputToEmptyState();
applyComparison(buildFallbackComparison());
renderCompareList();
