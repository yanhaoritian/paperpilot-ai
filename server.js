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

function buildSummaryMessages(payload) {
  const personaGuidance = getPersonaGuidance(payload.personaKey, payload.personaLabel);
  const system = [
    "你是一个严谨的 AI 论文阅读助手。",
    "你必须只输出 JSON，不要输出额外解释。",
    "不同角色必须输出明显不同的内容重点和表达风格，不能只替换少量措辞。",
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
    "请输出中文结构化总结，要求：",
    "1) quickSummary 1-2 句，清晰可讲。",
    "2) innovations / risks / actions 各 3 条，简洁有决策价值。",
    "3) outline 给出 4 步汇报提纲。",
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

  const user = [
    `用户角色：${payload.personaLabel || payload.personaKey || "算法 / 研究"}`,
    "论文列表：",
    papersText,
    "",
    "请输出中文对比结论，重点强调方法差异、应用机会和下一步实验建议。"
  ].join("\n");

  return [
    { role: "system", content: system },
    { role: "user", content: user }
  ];
}

async function callChatCompletions(model, temperature, messages) {
  if (!OPENAI_API_KEY) {
    throw new Error("后端未配置 OPENAI_API_KEY。");
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
      baseUrl: OPENAI_BASE_URL
    });
  }
  return res.json({
    ...base,
    ready: Boolean(OPENAI_API_KEY)
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
