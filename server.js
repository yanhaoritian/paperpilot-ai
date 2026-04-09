const path = require("path");
const express = require("express");
const multer = require("multer");
const rateLimit = require("express-rate-limit");
const helmet = require("helmet");
const pdfParse = require("pdf-parse");
const dotenv = require("dotenv");

dotenv.config();

function parsePositiveInt(name, fallback, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") {
    return fallback;
  }
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, n));
}
const isProduction = () => process.env.NODE_ENV === "production";

const PORT = parsePositiveInt("PORT", 8787, { min: 1, max: 65535 });
const HOST = (process.env.HOST || "0.0.0.0").trim();
const OPENAI_API_KEY = process.env.OPENAI_API_KEY || "";
const DEFAULT_MODEL = process.env.DEFAULT_MODEL || "gpt-4.1-mini";
const PDF_MAX_MB = parsePositiveInt("PDF_MAX_UPLOAD_MB", 32, { min: 1, max: 100 });
const JSON_BODY_LIMIT_MB = parsePositiveInt("JSON_BODY_LIMIT_MB", 2, { min: 1, max: 10 });
const MAX_ABSTRACT_CHARS = parsePositiveInt("MAX_ABSTRACT_CHARS", 50000, { min: 2000, max: 120000 });
const MAX_TITLE_CHARS = parsePositiveInt("MAX_TITLE_CHARS", 500, { min: 100, max: 2000 });
const MAX_COMPARE_PAPERS = parsePositiveInt("MAX_COMPARE_PAPERS", 10, { min: 2, max: 20 });
const MAX_PAPER_ABSTRACT_IN_COMPARE = parsePositiveInt(
  "MAX_PAPER_ABSTRACT_IN_COMPARE",
  12000,
  { min: 1000, max: 50000 }
);
const HEALTH_VERBOSE = ["1", "true", "yes"].includes(String(process.env.HEALTH_VERBOSE || "").toLowerCase());

const RATE_WINDOW_MS = parsePositiveInt("RATE_LIMIT_WINDOW_MS", 15 * 60 * 1000, {
  min: 60 * 1000,
  max: 24 * 60 * 60 * 1000
});
const RATE_HEALTH_WINDOW_MS = parsePositiveInt("RATE_LIMIT_HEALTH_WINDOW_MS", 60 * 1000, {
  min: 10 * 1000,
  max: 60 * 60 * 1000
});
const RATE_HEALTH_MAX = parsePositiveInt("RATE_LIMIT_HEALTH_MAX", 120, { min: 10, max: 10000 });
const RATE_PDF_MAX = parsePositiveInt("RATE_LIMIT_PDF_MAX", 25, { min: 5, max: 200 });
const RATE_SUMMARIZE_MAX = parsePositiveInt("RATE_LIMIT_SUMMARIZE_MAX", 40, { min: 5, max: 500 });
const RATE_COMPARE_MAX = parsePositiveInt("RATE_LIMIT_COMPARE_MAX", 25, { min: 5, max: 500 });

const upload = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: PDF_MAX_MB * 1024 * 1024
  }
});

const app = express();

if (["1", "true", "yes"].includes(String(process.env.TRUST_PROXY || "").toLowerCase())) {
  app.set("trust proxy", 1);
}

app.use(
  helmet({
    contentSecurityPolicy: false
  })
);
app.use(express.json({ limit: `${JSON_BODY_LIMIT_MB}mb` }));

const healthLimiter = rateLimit({
  windowMs: RATE_HEALTH_WINDOW_MS,
  max: RATE_HEALTH_MAX,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "请求过于频繁，请稍后再试。" }
});

const pdfLimiter = rateLimit({
  windowMs: RATE_WINDOW_MS,
  max: RATE_PDF_MAX,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "PDF 抽取次数过多，请稍后再试。" }
});

const summarizeLimiter = rateLimit({
  windowMs: RATE_WINDOW_MS,
  max: RATE_SUMMARIZE_MAX,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "总结请求过于频繁，请稍后再试。" }
});

const compareLimiter = rateLimit({
  windowMs: RATE_WINDOW_MS,
  max: RATE_COMPARE_MAX,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "对比请求过于频繁，请稍后再试。" }
});

function normalizeBaseUrl(url) {
  const cleaned = (url || "https://api.openai.com/v1").trim().replace(/\/$/, "");
  if (/\/v\d+$/i.test(cleaned)) {
    return cleaned;
  }
  return `${cleaned}/v1`;
}

const OPENAI_BASE_URL = normalizeBaseUrl(process.env.OPENAI_BASE_URL);

function parseModelJson(content) {
  if (!content) {
    throw new Error("模型未返回可解析内容。");
  }
  const fenced = content.match(/```json\s*([\s\S]*?)\s*```/i);
  const jsonText = fenced?.[1] || content;
  return JSON.parse(jsonText);
}

function decodeFileName(fileName) {
  if (!fileName) {
    return "uploaded-paper.pdf";
  }
  try {
    return Buffer.from(fileName, "latin1").toString("utf8");
  } catch (_error) {
    return fileName;
  }
}

function normalizeExtractedText(text) {
  return (text || "")
    .replace(/\r/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function extractAbstractSection(text) {
  const normalized = normalizeExtractedText(text);
  if (!normalized) {
    return "";
  }

  const abstractMatch = normalized.match(
    /(?:^|\n)\s*(abstract|摘\s*要)\s*[\n:：]*([\s\S]{80,4000}?)(?=\n\s*(keywords?|index terms|introduction|1[\.\s]+introduction|i\.\s*introduction|关键词)\b|$)/i
  );

  if (abstractMatch?.[2]) {
    return abstractMatch[2].replace(/\n+/g, " ").trim();
  }

  const lines = normalized
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const abstractLineIndex = lines.findIndex((line) => /^(abstract|摘\s*要)$/i.test(line));
  if (abstractLineIndex >= 0) {
    const nextLines = lines.slice(abstractLineIndex + 1, abstractLineIndex + 9).join(" ");
    return nextLines.trim();
  }

  return "";
}

function normalizeList(value, fallback) {
  if (Array.isArray(value) && value.length > 0) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  return fallback;
}

function sanitizeSummary(raw) {
  return {
    quickSummary: String(raw?.quickSummary || "暂无总结").trim(),
    innovations: normalizeList(raw?.innovations, ["暂无"]),
    risks: normalizeList(raw?.risks, ["暂无"]),
    actions: normalizeList(raw?.actions, ["暂无"]),
    outline: normalizeList(raw?.outline, ["暂无"]),
    confidence: String(raw?.confidence || "N/A").trim(),
    readOrder: String(raw?.readOrder || "先摘要后方法").trim()
  };
}

function sanitizeComparison(raw) {
  return {
    commonTheme: String(raw?.commonTheme || "暂无共同主题").trim(),
    differences: normalizeList(raw?.differences, ["暂无"]),
    opportunities: normalizeList(raw?.opportunities, ["暂无"]),
    recommendations: normalizeList(raw?.recommendations, ["暂无"])
  };
}

function validateSummarizePayload(body) {
  const title = String(body?.paperTitle || "").trim();
  const abstract = String(body?.paperAbstract || "").trim();
  if (title.length > MAX_TITLE_CHARS) {
    return `标题过长（最多 ${MAX_TITLE_CHARS} 字）。`;
  }
  if (abstract.length > MAX_ABSTRACT_CHARS) {
    return `摘要过长（最多 ${MAX_ABSTRACT_CHARS} 字）。`;
  }
  return null;
}

function validateComparePayload(body) {
  const papers = Array.isArray(body?.papers) ? body.papers : [];
  if (papers.length < 2) {
    return "至少需要 2 篇论文进行对比。";
  }
  if (papers.length > MAX_COMPARE_PAPERS) {
    return `对比最多支持 ${MAX_COMPARE_PAPERS} 篇论文。`;
  }
  for (let i = 0; i < papers.length; i++) {
    const p = papers[i];
    const t = String(p?.title || "").trim();
    const a = String(p?.abstract || "").trim();
    if (t.length > MAX_TITLE_CHARS) {
      return `第 ${i + 1} 篇标题过长（最多 ${MAX_TITLE_CHARS} 字）。`;
    }
    if (a.length > MAX_PAPER_ABSTRACT_IN_COMPARE) {
      return `第 ${i + 1} 篇摘要过长（最多 ${MAX_PAPER_ABSTRACT_IN_COMPARE} 字）。`;
    }
  }
  return null;
}

function getPersonaGuidance(personaKey, personaLabel) {
  if (personaKey === "student" || /学生/.test(personaLabel || "")) {
    return [
      "当前角色是学生入门者。",
      "请降低术语密度，尽量解释动机、核心概念和推荐阅读顺序。",
      "innovations 字段应更像“核心概念 / 关键理解点”，不要写得像论文评审意见。",
      "risks 字段应强调理解门槛、容易混淆之处和前置知识缺口。",
      "actions 字段应给出学习动作，例如先读哪一部分、如何做笔记、如何复述。",
      "outline 应适合给同学或自己讲解，结构从背景、概念、结构、意义展开。"
    ].join("\n");
  }

  return [
    "当前角色是算法 / 研究读者。",
    "请突出方法创新、实验设置、结论边界和复现价值。",
    "innovations 字段应强调技术贡献、与基线相比的新意和方法变化。",
    "risks 字段应强调实验可信度、适用边界、复现难点和资源成本。",
    "actions 字段应给出研究动作，例如优先读的方法章节、需要关注的实验、是否值得复现。",
    "outline 应适合组会或研究汇报，结构从问题、方法、实验、结论展开。"
  ].join("\n");
}

function getGoalGuidance(goalKey) {
  const g = String(goalKey || "").trim();
  if (g === "meeting") {
    return [
      "【阅读目标：组会 / 面试讲解】",
      "quickSummary 要像开场白：30 秒内说清「解决了什么问题 + 凭什么信 + 一句价值」。",
      "innovations 每条必须能口头复述，禁止堆叠论文式编号术语。",
      "risks 写「听众最可能追问的三点」以及你可如何简洁回应。",
      "actions 聚焦预演：先练哪一段、如何控制时间、如何准备一张核心图。",
      "outline 必须按「讲解顺序」四步：抓注意力→讲清方法故事→实验怎么支撑→收尾与开放问题；禁止写成论文章节目录或与「是否值得读」结构雷同。"
    ].join("\n");
  }
  if (g === "application") {
    return [
      "【阅读目标：实际应用与落地启发】",
      "quickSummary 要点出潜在用户/场景、边界，少强调纯理论排名。",
      "innovations 写成「可迁移的能力或机制」，对应到能解决什么业务/工程问题。",
      "risks 强调数据、算力、合规、运维、与真实环境的 gap；避免只写学术局限。",
      "actions 给出可执行下一步：小规模验证、需什么数据、衡量什么指标、何处可能踩坑。",
      "outline 按「场景→方案要点→实施条件→风险与指标」四步，禁止与讲解稿或决策稿结构雷同。"
    ].join("\n");
  }
  return [
    "【阅读目标：快速判断是否值得深入阅读】",
    "quickSummary 第一句必须给出明确倾向：建议深读 / 可泛读 / 可跳过，并一句理由。",
    "innovations 只列与「是否继续投入时间」最直接相关的要点，避免全面铺开。",
    "risks 写「若误判会继续读的最大代价」：时间、方向或资源上的损失。",
    "actions 以「若读：最先翻哪一节；若不读：可转向哪类替代读物或关键词」为主。",
    "outline 四步必须是：结论快照→关键证据→主要疑点→是否继续读；禁止与组会讲解或落地分析的结构相同。"
  ].join("\n");
}

function getCompareGoalNudge(goalKey) {
  const g = String(goalKey || "").trim();
  if (g === "meeting") {
    return "对比要便于口头汇报：各篇讲解切入点、串联顺序、听众可能问的跨篇问题。";
  }
  if (g === "application") {
    return "对比要突出落地：哪篇更适合产品/工程试点、场景差异、接入成本与主要风险。";
  }
  return "对比要服务取舍决策：优先深读顺序、可泛读篇目、主要取舍依据与放弃理由。";
}

function buildSummaryMessages(payload) {
  const personaGuidance = getPersonaGuidance(payload.personaKey, payload.personaLabel);
  const goalGuidance = getGoalGuidance(payload.goal);
  const system = [
    "你是一个严谨的 AI 论文阅读助手。",
    "你必须只输出 JSON，不要输出额外解释。",
    "不同角色、不同阅读目标必须输出明显不同的内容重点与结构；切换目标时禁止套用另一目标的提纲逻辑。",
    "JSON schema:",
    "{",
    '  "quickSummary": "string",',
    '  "innovations": ["string", "string", "string"],',
    '  "risks": ["string", "string", "string"],',
    '  "actions": ["string", "string", "string"],',
    '  "outline": ["string", "string", "string", "string"],',
    '  "confidence": "string",',
    '  "readOrder": "string"',
    "}"
  ].join("\n");

  const user = [
    `论文标题：${payload.paperTitle || "未提供"}`,
    `论文摘要：${payload.paperAbstract || "未提供"}`,
    `用户角色：${payload.personaLabel || payload.personaKey || "算法 / 研究"}`,
    `阅读目标：${payload.goalLabel || payload.goal || "快速判断"}`,
    "",
    "角色约束：",
    personaGuidance,
    "",
    "阅读目标约束（必须与角色约束同时满足，且本约束优先决定 outline 的结构）：",
    goalGuidance,
    "",
    "请输出中文结构化总结，要求：",
    "1) quickSummary 1-2 句，清晰可讲。",
    "2) innovations / risks / actions 各 3 条，严格符合上述阅读目标侧重点。",
    "3) outline 恰好 4 步，结构必须与所选阅读目标一致。",
    "4) confidence 例如 88%。",
    "5) readOrder 输出推荐阅读顺序。"
  ].join("\n");

  return [
    { role: "system", content: system },
    { role: "user", content: user }
  ];
}

function buildCompareMessages(payload) {
  const papers = Array.isArray(payload.papers) ? payload.papers : [];
  const papersText = papers
    .map((paper, index) => `${index + 1}. 标题：${paper.title}\n摘要：${paper.abstract}`)
    .join("\n\n");

  const system = [
    "你是一个 AI 论文对比分析助手。",
    "你必须只输出 JSON，不要输出额外解释。",
    "JSON schema:",
    "{",
    '  "commonTheme": "string",',
    '  "differences": ["string", "string", "string"],',
    '  "opportunities": ["string", "string", "string"],',
    '  "recommendations": ["string", "string", "string"]',
    "}"
  ].join("\n");

  const goalLine = `${payload.goalLabel || payload.goal || "快速判断"}`;
  const compareNudge = getCompareGoalNudge(payload.goal);

  const user = [
    `用户角色：${payload.personaLabel || payload.personaKey || "算法 / 研究"}`,
    `阅读目标：${goalLine}`,
    `对比侧重点：${compareNudge}`,
    "",
    "论文列表：",
    papersText,
    "",
    "请输出中文对比结论；differences / opportunities / recommendations 三条列表均须体现上述阅读目标，不要写成泛泛方法比较。"
  ].join("\n");

  return [
    { role: "system", content: system },
    { role: "user", content: user }
  ];
}

async function callChatCompletions(model, temperature, messages) {
  if (!OPENAI_API_KEY) {
    throw new Error("总结功能暂时不可用，请稍后再试。");
  }
  const response = await fetch(`${OPENAI_BASE_URL}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${OPENAI_API_KEY}`
    },
    body: JSON.stringify({
      model: model || DEFAULT_MODEL,
      temperature: Number.isFinite(temperature) ? temperature : 0.3,
      response_format: { type: "json_object" },
      messages
    })
  });

  if (!response.ok) {
    const errorText = await response.text();
    console.error("[callChatCompletions] upstream error", response.status, errorText.slice(0, 500));
    if (isProduction()) {
      throw new Error("模型服务暂时不可用，请稍后重试。");
    }
    throw new Error(
      `模型调用失败 (${response.status}) [${OPENAI_BASE_URL}/chat/completions]：${errorText.slice(0, 250)}`
    );
  }
  const data = await response.json();
  const content = data?.choices?.[0]?.message?.content;
  try {
    return parseModelJson(content);
  } catch (parseErr) {
    console.error("[callChatCompletions] parse error", parseErr);
    if (isProduction()) {
      throw new Error("模型返回格式异常，请稍后重试。");
    }
    throw parseErr;
  }
}

app.get("/api/health", healthLimiter, (_req, res) => {
  const base = { ok: true };
  if (HEALTH_VERBOSE) {
    return res.json({
      ...base,
      hasApiKey: Boolean(OPENAI_API_KEY),
      defaultModel: DEFAULT_MODEL,
      baseUrl: OPENAI_BASE_URL,
      maxPdfMb: PDF_MAX_MB
    });
  }
  return res.json({
    ...base,
    ready: Boolean(OPENAI_API_KEY),
    maxPdfMb: PDF_MAX_MB
  });
});

app.post("/api/extract-pdf", pdfLimiter, upload.single("pdf"), async (req, res) => {
  try {
    if (!req.file) {
      return res.status(400).json({ error: "缺少 pdf 文件。" });
    }
    const data = await pdfParse(req.file.buffer);
    const text = normalizeExtractedText(data.text || "");

    if (!text) {
      return res.status(422).json({
        error: "未从 PDF 中提取到可用文本，可能是扫描版 PDF、图片型 PDF，或当前解析器不支持该文件格式。"
      });
    }

    const decodedFileName = decodeFileName(req.file.originalname);
    const abstract = extractAbstractSection(text);

    if (!abstract) {
      return res.status(422).json({
        error: "未定位到论文摘要部分。当前仅会自动回填 Abstract 段，建议手动复制摘要或换一份带可选中文本层的 PDF。"
      });
    }

    return res.json({
      fileName: decodedFileName,
      pages: data.numpages || 0,
      characters: text.length,
      abstract,
      text: abstract
    });
  } catch (error) {
    return res.status(500).json({ error: `PDF 解析失败：${error.message}` });
  }
});

app.post("/api/summarize", summarizeLimiter, async (req, res) => {
  try {
    const payload = req.body || {};
    const invalid = validateSummarizePayload(payload);
    if (invalid) {
      return res.status(400).json({ error: invalid });
    }
    const model = payload.model || DEFAULT_MODEL;
    const temperature = Number(payload.temperature);
    const messages = buildSummaryMessages(payload);
    const raw = await callChatCompletions(model, temperature, messages);
    return res.json({ result: sanitizeSummary(raw) });
  } catch (error) {
    return res.status(500).json({ error: error.message });
  }
});

app.post("/api/compare", compareLimiter, async (req, res) => {
  try {
    const payload = req.body || {};
    const invalid = validateComparePayload(payload);
    if (invalid) {
      return res.status(400).json({ error: invalid });
    }
    const model = payload.model || DEFAULT_MODEL;
    const temperature = Number(payload.temperature);
    const messages = buildCompareMessages(payload);
    const raw = await callChatCompletions(model, temperature, messages);
    return res.json({ result: sanitizeComparison(raw) });
  } catch (error) {
    return res.status(500).json({ error: error.message });
  }
});

app.use(express.static(path.resolve(__dirname)));

app.use((err, req, res, next) => {
  if (err instanceof multer.MulterError) {
    if (err.code === "LIMIT_FILE_SIZE") {
      return res.status(413).json({ error: `PDF 超过大小限制（最大 ${PDF_MAX_MB}MB）。` });
    }
    return res.status(400).json({ error: "文件上传异常，请重试。" });
  }
  if (err.type === "entity.too.large") {
    return res.status(413).json({ error: `请求体过大（最大 ${JSON_BODY_LIMIT_MB}MB）。` });
  }
  return next(err);
});

app.use((err, req, res, _next) => {
  console.error(err);
  const status = Number.isInteger(err.status) ? err.status : 500;
  res.status(status).json({ error: isProduction() ? "服务异常，请稍后重试。" : err.message });
});

app.listen(PORT, HOST, () => {
  const hint = HOST === "0.0.0.0" ? `http://localhost:${PORT}` : `http://${HOST}:${PORT}`;
  console.log(`PaperPilot server listening on ${hint} (bind ${HOST}:${PORT})`);
});
