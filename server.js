const path = require("path");
const crypto = require("crypto");
const fs = require("fs/promises");
const express = require("express");
const multer = require("multer");
const rateLimit = require("express-rate-limit");
const helmet = require("helmet");
const pdfParse = require("pdf-parse");
const dotenv = require("dotenv");
const rag = require("./rag");
const { compileRagGraph, runRagGraph } = require("./graph/ragGraph");

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
const MAX_FULLTEXT_CHARS = parsePositiveInt("MAX_FULLTEXT_CHARS", 600000, { min: 5000, max: 2000000 });
const MAX_TITLE_CHARS = parsePositiveInt("MAX_TITLE_CHARS", 500, { min: 100, max: 2000 });
const MAX_COMPARE_PAPERS = parsePositiveInt("MAX_COMPARE_PAPERS", 10, { min: 2, max: 20 });
const MAX_PAPER_ABSTRACT_IN_COMPARE = parsePositiveInt(
  "MAX_PAPER_ABSTRACT_IN_COMPARE",
  12000,
  { min: 1000, max: 50000 }
);
const HEALTH_VERBOSE = ["1", "true", "yes"].includes(String(process.env.HEALTH_VERBOSE || "").toLowerCase());

const EMBEDDING_MODEL = (process.env.EMBEDDING_MODEL || "text-embedding-3-small").trim();
const CHUNK_TARGET_CHARS = parsePositiveInt("CHUNK_TARGET_CHARS", 2000, { min: 500, max: 8000 });
const CHUNK_MIN_CHARS = parsePositiveInt("CHUNK_MIN_CHARS", 450, { min: 200, max: CHUNK_TARGET_CHARS - 1 });
const CHUNK_OVERLAP_RATIO = Number(process.env.CHUNK_OVERLAP_RATIO || "0.15");
const RAG_TOP_K = parsePositiveInt("RAG_TOP_K", 6, { min: 1, max: 24 });
const RAG_MIN_SIMILARITY = Number(process.env.RAG_MIN_SIMILARITY || "0.22");
const RAG_RELAXED_MIN_SIMILARITY = Number(process.env.RAG_RELAXED_MIN_SIMILARITY || "0.08");
const MAX_CHUNKS = parsePositiveInt("MAX_CHUNKS", 280, { min: 20, max: 800 });
const PRD_SUMMARY_MAX_CHARS = parsePositiveInt("PRD_SUMMARY_MAX_CHARS", 24000, { min: 4000, max: 120000 });
const DOC_PROFILE_MAX_CHARS = parsePositiveInt("DOC_PROFILE_MAX_CHARS", 80000, { min: 8000, max: 300000 });
const EMBED_BATCH_SIZE = parsePositiveInt("EMBED_BATCH_SIZE", 64, { min: 8, max: 128 });
const RATE_DOCUMENT_QUERY_MAX = parsePositiveInt("RATE_LIMIT_DOCUMENT_QUERY_MAX", 60, { min: 5, max: 500 });
const RAG_HISTORY_MAX_MESSAGES = parsePositiveInt("RAG_HISTORY_MAX_MESSAGES", 20, { min: 0, max: 40 });

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
const ANALYSIS_TEXT_MAX_CHARS = parsePositiveInt("ANALYSIS_TEXT_MAX_CHARS", 22000, { min: 3000, max: 120000 });
const RESPONSE_CACHE_TTL_MS = parsePositiveInt("RESPONSE_CACHE_TTL_MS", 10 * 60 * 1000, {
  min: 10 * 1000,
  max: 24 * 60 * 60 * 1000
});

const ENABLE_LANGGRAPH_CHECKPOINTS = ["1", "true", "yes"].includes(
  String(process.env.LANGGRAPH_CHECKPOINTS || "").toLowerCase()
);
const LANGGRAPH_CHECKPOINT_FILE = (process.env.LANGGRAPH_CHECKPOINT_FILE || "").trim();

function parseModelOptions(raw) {
  const text = String(raw || "").trim();
  if (!text) {
    return [DEFAULT_MODEL];
  }
  const items = text
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const unique = Array.from(new Set(items));
  return unique.length > 0 ? unique : [DEFAULT_MODEL];
}

const MODEL_OPTIONS = parseModelOptions(process.env.MODEL_OPTIONS);
const responseCache = new Map();
/** @type {Map<string, any>} */
const documents = new Map();
/** @type {Map<string, string>} */
const documentHashIndex = new Map();
/** @type {Array<{ role: string, content: string }>} */
const libraryChatHistory = [];
const FEEDBACK_DIR = path.resolve(__dirname, "data");
const FEEDBACK_FILE = path.join(FEEDBACK_DIR, "feedback.jsonl");
const LIBRARY_STORE_FILE = path.join(FEEDBACK_DIR, "library-store.json");
let persistTimer = null;
let persistInFlight = false;

function normalizeLoadedDoc(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const id = String(raw.id || "").trim();
  const fileHash = String(raw.fileHash || "").trim();
  if (!id || !fileHash) {
    return null;
  }
  const chunks = Array.isArray(raw.chunks) ? raw.chunks : [];
  const embeddings = Array.isArray(raw.embeddings) ? raw.embeddings : null;
  return {
    id,
    fileHash,
    fileName: String(raw.fileName || "uploaded-paper.pdf").trim() || "uploaded-paper.pdf",
    pages: Number.isFinite(Number(raw.pages)) ? Number(raw.pages) : 0,
    fullText: String(raw.fullText || ""),
    abstract: String(raw.abstract || ""),
    chunks,
    embeddings,
    status: String(raw.status || "ready_no_embed"),
    statusDetail: raw.statusDetail == null ? null : String(raw.statusDetail),
    error: raw.error == null ? null : String(raw.error),
    summaryPrd: raw.summaryPrd && typeof raw.summaryPrd === "object" ? raw.summaryPrd : null,
    deepProfile: raw.deepProfile && typeof raw.deepProfile === "object" ? raw.deepProfile : null,
    deepProfileUpdatedAt: Number.isFinite(Number(raw.deepProfileUpdatedAt)) ? Number(raw.deepProfileUpdatedAt) : null,
    chatHistory: sanitizeRagHistory(raw.chatHistory),
    createdAt: Number.isFinite(Number(raw.createdAt)) ? Number(raw.createdAt) : Date.now(),
    locationNote:
      String(raw.locationNote || "").trim() ||
      "页码通常按字符在全文中的位置占页数比例估算；若与 PDF 阅读器页码不一致，请以 PDF 为准并对照 excerpt。"
  };
}

async function persistLibraryStateNow() {
  if (persistInFlight) {
    return;
  }
  persistInFlight = true;
  try {
    await fs.mkdir(FEEDBACK_DIR, { recursive: true });
    const payload = {
      savedAt: new Date().toISOString(),
      documents: Array.from(documents.values()),
      libraryChatHistory: sanitizeRagHistory(libraryChatHistory)
    };
    await fs.writeFile(LIBRARY_STORE_FILE, JSON.stringify(payload), "utf8");
  } catch (error) {
    console.error("[persist] library state save failed:", error.message || error);
  } finally {
    persistInFlight = false;
  }
}

function schedulePersistLibraryState() {
  if (persistTimer) {
    clearTimeout(persistTimer);
  }
  persistTimer = setTimeout(() => {
    persistTimer = null;
    void persistLibraryStateNow();
  }, 400);
}

async function loadLibraryStateFromDisk() {
  try {
    const rawText = await fs.readFile(LIBRARY_STORE_FILE, "utf8");
    if (!rawText.trim()) {
      return;
    }
    const payload = JSON.parse(rawText);
    const docsIn = Array.isArray(payload?.documents) ? payload.documents : [];
    let loadedCount = 0;
    for (const row of docsIn) {
      const doc = normalizeLoadedDoc(row);
      if (!doc) {
        continue;
      }
      documents.set(doc.id, doc);
      documentHashIndex.set(doc.fileHash, doc.id);
      loadedCount += 1;
    }
    const history = sanitizeRagHistory(payload?.libraryChatHistory);
    if (history.length > 0) {
      libraryChatHistory.splice(0, libraryChatHistory.length, ...history);
    }
    console.log(`[persist] loaded ${loadedCount} document(s) from disk.`);
  } catch (error) {
    if (error && error.code === "ENOENT") {
      return;
    }
    console.error("[persist] library state load failed:", error.message || error);
  }
}

async function resetLibraryState() {
  documents.clear();
  documentHashIndex.clear();
  libraryChatHistory.splice(0, libraryChatHistory.length);
  responseCache.clear();
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  try {
    await fs.unlink(LIBRARY_STORE_FILE);
  } catch (error) {
    if (!error || error.code !== "ENOENT") {
      throw error;
    }
  }
}

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

const documentQueryLimiter = rateLimit({
  windowMs: RATE_WINDOW_MS,
  max: RATE_DOCUMENT_QUERY_MAX,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "文献问答请求过于频繁，请稍后再试。" }
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

function findFirstMatchIndex(text, patterns) {
  let best = -1;
  patterns.forEach((pattern) => {
    const match = pattern.exec(text);
    if (!match) {
      return;
    }
    const idx = match.index;
    if (idx >= 0 && (best < 0 || idx < best)) {
      best = idx;
    }
  });
  return best;
}

function tidyAbstractText(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .replace(/\s*-\s*/g, "-")
    .trim();
}

function countMatches(text, regex) {
  const m = String(text || "").match(regex);
  return m ? m.length : 0;
}

function detectPrimaryLanguage(text) {
  const sample = String(text || "").slice(0, 8000);
  const zh = countMatches(sample, /[\u4e00-\u9fff]/g);
  const en = countMatches(sample, /[A-Za-z]/g);
  if (zh === 0 && en === 0) {
    return "unknown";
  }
  return zh >= en * 0.65 ? "zh" : "en";
}

function uniqueByValue(items, keyFn) {
  const seen = new Set();
  const out = [];
  for (const item of items) {
    const key = keyFn(item);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    out.push(item);
  }
  return out;
}

function collectAbstractCandidates(normalized, boundaryPatterns) {
  const headingDefs = [
    // 中文标题不能用 \b（JS 的单词边界不适配中文），否则会漏匹配“摘要”
    { label: "zh", regex: /(?:^|\n)\s*(?:摘\s*要|中文摘要)\s*[:：]?\s*/gi },
    { label: "en", regex: /(?:^|\n)\s*abstract\s*[:：]?\s*/gi },
    { label: "en", regex: /(?:^|\n)\s*summary\s*[:：]?\s*/gi }
  ];
  const candidates = [];
  for (const def of headingDefs) {
    let match;
    while ((match = def.regex.exec(normalized)) !== null) {
      const headingIdx = match.index;
      const bodyStart = headingIdx + match[0].length;
      const bodyText = normalized.slice(bodyStart);
      const boundaryOffset = findFirstMatchIndex(bodyText, boundaryPatterns);
      const rawCandidate = boundaryOffset >= 0 ? bodyText.slice(0, boundaryOffset) : bodyText.slice(0, 3200);
      const candidate = tidyAbstractText(rawCandidate);
      if (candidate.length >= 80 && candidate.length <= 3600) {
        candidates.push({
          label: def.label,
          value: candidate,
          offset: headingIdx
        });
      }
    }
  }
  return uniqueByValue(candidates, (c) => c.value);
}

function scoreAbstractQuality(candidate, fullText) {
  const text = String(candidate || "").trim();
  if (!text) {
    return 0;
  }
  let score = 0;
  const len = text.length;
  if (len >= 120 && len <= 3200) {
    score += 35;
  } else if (len >= 80 && len <= 5000) {
    score += 20;
  } else {
    score += 5;
  }
  const lang = detectPrimaryLanguage(fullText || "");
  const zh = countMatches(text, /[\u4e00-\u9fff]/g);
  const en = countMatches(text, /[A-Za-z]/g);
  if (lang === "zh") {
    score += zh >= en * 0.6 ? 30 : 5;
  } else if (lang === "en") {
    score += en >= zh * 0.8 ? 20 : 6;
  } else {
    score += 10;
  }
  if (/^(abstract|summary)\b/i.test(text)) {
    score -= 8;
  }
  if (/^(摘\s*要|中文摘要)/.test(text)) {
    score += 6;
  }
  if (/关键词|key words?|index terms/i.test(text)) {
    score -= 3;
  }
  return score;
}

function chooseBetterAbstract(previousAbstract, nextAbstract, fullText) {
  const prev = String(previousAbstract || "").trim();
  const next = String(nextAbstract || "").trim();
  if (!prev) {
    return next;
  }
  if (!next) {
    return prev;
  }
  const prevScore = scoreAbstractQuality(prev, fullText);
  const nextScore = scoreAbstractQuality(next, fullText);
  if (nextScore > prevScore) {
    return next;
  }
  if (nextScore === prevScore && next !== prev) {
    return next;
  }
  return prev;
}

function extractAbstractSection(text) {
  const normalized = normalizeExtractedText(text);
  if (!normalized) {
    return "";
  }
  const boundaryPatterns = [
    /\n\s*(keywords?|key words|index terms)\s*[:：]/i,
    /\n\s*(关键词|关键字)\s*[:：]/i,
    /\n\s*plain language summary\b/i,
    /\n\s*(\d{1,2}[\.\s]+)?introduction\b/i,
    /\n\s*[ivx]+\s*[\.\)]?\s*introduction\b/i,
    /\n\s*引言\s*[:：]?/i,
    /\n\s*\d{1,2}[\.\s]+[A-Z][^\n]{0,80}\n/
  ];

  const candidates = collectAbstractCandidates(normalized, boundaryPatterns);
  if (candidates.length > 0) {
    const lang = detectPrimaryLanguage(normalized);
    const preferredLabel = lang === "zh" ? "zh" : "en";
    const preferred = candidates
      .filter((c) => c.label === preferredLabel)
      .sort((a, b) => a.offset - b.offset);
    if (preferred.length > 0) {
      return preferred[0].value;
    }
    return candidates.sort((a, b) => a.offset - b.offset)[0].value;
  }

  // 兜底：取开头较短段落，避免误把 Introduction 整段当作摘要。
  const paragraphs = normalized
    .split(/\n{2,}/)
    .map((p) => tidyAbstractText(p))
    .filter(Boolean);
  for (const para of paragraphs.slice(0, 8)) {
    if (para.length >= 120 && para.length <= 2200 && !/^(\d+[\.\s]+)?introduction\b/i.test(para)) {
      return para;
    }
  }
  return "";
}

function getPreferredPaperText(fullText, abstract) {
  const normalizedFullText = normalizeExtractedText(fullText || "");
  if (normalizedFullText) {
    return normalizedFullText;
  }
  return normalizeExtractedText(abstract || "");
}

function normalizeList(value, fallback) {
  if (Array.isArray(value) && value.length > 0) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  return fallback;
}

function clipTextForAnalysis(text, maxChars) {
  const source = String(text || "").trim();
  if (!source) {
    return "";
  }
  if (source.length <= maxChars) {
    return source;
  }
  const headLen = Math.floor(maxChars * 0.7);
  const tailLen = Math.max(0, maxChars - headLen);
  const head = source.slice(0, headLen).trim();
  const tail = tailLen > 0 ? source.slice(-tailLen).trim() : "";
  return tail
    ? `${head}\n\n[...中间内容已省略以提升速度...]\n\n${tail}`
    : head;
}

function makeCacheKey(prefix, payload) {
  const hash = crypto.createHash("sha256").update(JSON.stringify(payload)).digest("hex");
  return `${prefix}:${hash}`;
}

function getCachedValue(key) {
  const hit = responseCache.get(key);
  if (!hit) {
    return null;
  }
  if (Date.now() > hit.expiresAt) {
    responseCache.delete(key);
    return null;
  }
  return hit.value;
}

function setCachedValue(key, value) {
  responseCache.set(key, {
    value,
    expiresAt: Date.now() + RESPONSE_CACHE_TTL_MS
  });
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
  const fullText = String(body?.paperFullText || "").trim();
  if (title.length > MAX_TITLE_CHARS) {
    return `标题过长（最多 ${MAX_TITLE_CHARS} 字）。`;
  }
  if (abstract.length > MAX_ABSTRACT_CHARS) {
    return `摘要过长（最多 ${MAX_ABSTRACT_CHARS} 字）。`;
  }
  if (fullText.length > MAX_FULLTEXT_CHARS) {
    return `论文全文过长（最多 ${MAX_FULLTEXT_CHARS} 字）。`;
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
  const abstractText = String(payload.paperAbstract || "未提供").trim();
  const fullText = String(payload.paperFullText || "").trim();
  const analysisText = clipTextForAnalysis(fullText || abstractText, ANALYSIS_TEXT_MAX_CHARS);
  const system = [
    "你是一个严谨的 AI 论文阅读助手。",
    "你必须只输出 JSON，不要输出额外解释。",
    "不同角色、不同阅读目标必须输出明显不同的内容重点与结构；切换目标时禁止套用另一目标的提纲逻辑。",
    "如果同时提供摘要和全文，分析必须优先依据全文，不可只凭摘要做结论。",
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
    `论文摘要（用于展示）：${abstractText || "未提供"}`,
    `用于分析的文本（优先全文）：${analysisText || "未提供"}`,
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

async function embedTextsBatch(inputs) {
  if (!OPENAI_API_KEY) {
    throw new Error("缺少 OPENAI_API_KEY，无法生成向量索引。");
  }
  const trimmed = inputs.map((t) => String(t || "").trim()).filter(Boolean);
  if (trimmed.length === 0) {
    return [];
  }
  const response = await fetch(`${OPENAI_BASE_URL}/embeddings`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${OPENAI_API_KEY}`
    },
    body: JSON.stringify({
      model: EMBEDDING_MODEL,
      input: trimmed
    })
  });
  if (!response.ok) {
    const errorText = await response.text();
    console.error("[embedTextsBatch] upstream error", response.status, errorText.slice(0, 500));
    if (isProduction()) {
      throw new Error("向量服务暂时不可用，请稍后重试。");
    }
    throw new Error(
      `Embedding 调用失败 (${response.status}) [${OPENAI_BASE_URL}/embeddings]：${errorText.slice(0, 250)}`
    );
  }
  const data = await response.json();
  const rows = Array.isArray(data?.data) ? data.data : [];
  const sorted = rows.slice().sort((a, b) => Number(a.index) - Number(b.index));
  return sorted.map((row) => row.embedding).filter(Array.isArray);
}

async function translateTextToChinese(text, label) {
  const source = String(text || "").trim();
  if (!source) {
    return "";
  }
  if (detectPrimaryLanguage(source) === "zh") {
    return source;
  }
  if (!OPENAI_API_KEY) {
    return source;
  }
  const system = [
    "你是专业翻译助手。",
    "请把给定文本准确翻译为简体中文。",
    "保留原意、术语、数字和专有名词；不要添加解释。",
    "只输出 JSON。"
  ].join("\n");
  const user = [
    `文本类型：${label || "文本"}`,
    "请翻译为简体中文：",
    source
  ].join("\n");
  try {
    const raw = await callChatCompletions(DEFAULT_MODEL, 0.1, [
      { role: "system", content: `${system}\nJSON schema:\n{"translatedText":"string"}` },
      { role: "user", content: user }
    ]);
    const translated = String(raw?.translatedText || "").trim();
    return translated || source;
  } catch (_error) {
    return source;
  }
}

function buildPrdSummaryMessages(doc) {
  const analysisText = clipTextForAnalysis(doc.fullText || "", PRD_SUMMARY_MAX_CHARS);
  const system = [
    "你是一个严谨的学术文献阅读助手。",
    "你必须只输出 JSON，不要输出额外解释。",
    "输出必须为简体中文。",
    "只能依据用户提供的论文文本进行总结；若文本信息不足，请在对应字段明确说明「文本中未明确提及」。",
    "JSON schema:",
    "{",
    '  "researchQuestion": "string",',
    '  "methods": "string",',
    '  "conclusions": "string",',
    '  "keywords": ["string", "string", "string", "string", "string"]',
    "}"
  ].join("\n");

  const user = [
    `论文文件名：${doc.fileName || "未提供"}`,
    `页数（解析器报告）：${doc.pages || 0}`,
    "",
    "论文文本（用于分析）：",
    analysisText || "未提供"
  ].join("\n");

  return [
    { role: "system", content: system },
    { role: "user", content: user }
  ];
}

function sanitizePrdSummary(raw) {
  const keywords = normalizeList(raw?.keywords, []);
  const safeKeywords = keywords.length > 0 ? keywords.slice(0, 12) : ["文本中未提取到关键词"];
  return {
    researchQuestion: String(raw?.researchQuestion || "文本中未明确提及研究问题。").trim(),
    methods: String(raw?.methods || "文本中未明确提及方法。").trim(),
    conclusions: String(raw?.conclusions || "文本中未明确提及结论。").trim(),
    keywords: safeKeywords
  };
}

function buildDeepProfileMessages(doc) {
  const analysisText = clipTextForAnalysis(doc.fullText || "", DOC_PROFILE_MAX_CHARS);
  const system = [
    "你是学术论文深度解读助手。",
    "请基于提供的全文内容提炼“通篇理解画像”，用于后续问答。",
    "输出必须为简体中文 JSON，不要输出额外解释。",
    "JSON schema:",
    "{",
    '  "oneParagraph": "string",',
    '  "keyTerms": ["string"],',
    '  "methodFlow": ["string"],',
    '  "dataSource": "string",',
    '  "experimentDesign": "string",',
    '  "limitations": ["string"],',
    '  "contributions": ["string"]',
    "}"
  ].join("\n");
  const user = [
    `论文文件名：${doc.fileName || "未提供"}`,
    "请给出通篇理解画像，尤其要明确数据来源、方法流程和实验设计。",
    "",
    "论文全文（截断后）：",
    analysisText || "未提供"
  ].join("\n");
  return [
    { role: "system", content: system },
    { role: "user", content: user }
  ];
}

function sanitizeDeepProfile(raw) {
  return {
    oneParagraph: String(raw?.oneParagraph || "暂无全局画像").trim(),
    keyTerms: normalizeList(raw?.keyTerms, ["暂无"]),
    methodFlow: normalizeList(raw?.methodFlow, ["暂无"]),
    dataSource: String(raw?.dataSource || "文本中未明确提及数据来源。").trim(),
    experimentDesign: String(raw?.experimentDesign || "文本中未明确提及实验设计。").trim(),
    limitations: normalizeList(raw?.limitations, ["文本中未明确提及局限性。"]),
    contributions: normalizeList(raw?.contributions, ["文本中未明确提及贡献点。"])
  };
}

async function ensureDocDeepProfile(doc) {
  if (!doc || typeof doc !== "object") {
    return null;
  }
  if (doc.deepProfile) {
    return doc.deepProfile;
  }
  if (!OPENAI_API_KEY) {
    return null;
  }
  if (!(doc.fullText || "").trim()) {
    return null;
  }
  try {
    const raw = await callChatCompletions(DEFAULT_MODEL, 0.2, buildDeepProfileMessages(doc));
    doc.deepProfile = sanitizeDeepProfile(raw);
    doc.deepProfileUpdatedAt = Date.now();
    schedulePersistLibraryState();
    return doc.deepProfile;
  } catch (_error) {
    return null;
  }
}

function sanitizeRagHistory(raw) {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") {
      continue;
    }
    const role = String(row.role || "").trim();
    if (role !== "user" && role !== "assistant") {
      continue;
    }
    const content = String(row.content || "")
      .trim()
      .slice(0, 2400);
    if (!content) {
      continue;
    }
    out.push({ role, content });
  }
  const max = RAG_HISTORY_MAX_MESSAGES > 0 ? RAG_HISTORY_MAX_MESSAGES : 0;
  return max > 0 ? out.slice(-max) : [];
}

function appendDocChatHistory(doc, question, answer) {
  if (!doc || typeof doc !== "object") {
    return;
  }
  if (!Array.isArray(doc.chatHistory)) {
    doc.chatHistory = [];
  }
  const q = String(question || "").trim().slice(0, 2400);
  const a = String(answer || "").trim().slice(0, 2400);
  if (q) {
    doc.chatHistory.push({ role: "user", content: q });
  }
  if (a) {
    doc.chatHistory.push({ role: "assistant", content: a });
  }
  const max = RAG_HISTORY_MAX_MESSAGES > 0 ? RAG_HISTORY_MAX_MESSAGES : 0;
  if (max > 0 && doc.chatHistory.length > max) {
    doc.chatHistory = doc.chatHistory.slice(-max);
  }
  schedulePersistLibraryState();
}

function buildQuestionKeywords(question) {
  const q = String(question || "").trim().toLowerCase();
  if (!q) {
    return [];
  }
  const explicit = [];
  if (/数据来源|资料来源|来源|数据集|样本|采集|观测|实验数据|数据库/i.test(q)) {
    explicit.push("数据", "来源", "资料", "数据集", "采集", "观测", "样本", "实验");
  }
  if (/研究区|区域|地点|剖面|测线|盆地|断裂带|井|台站/i.test(q)) {
    explicit.push("研究区", "区域", "地点", "剖面", "测线", "盆地", "断裂带", "台站");
  }
  // 追问「结果/结论/发现」时补充学术语，避免只命中引言背景段
  if (
    /结果怎样|主要结果|研究结论|论文结论|结论是什么|得出什么|主要发现|表明|说明|讨论|反演|认识|特征|极性|电性|缝合/i.test(
      q
    )
  ) {
    explicit.push(
      "结论",
      "讨论",
      "结果",
      "表明",
      "显示",
      "反演",
      "认识",
      "解释",
      "特征",
      "电性结构",
      "缝合带",
      "俯冲"
    );
  }
  const extracted = (q.match(/[\u4e00-\u9fffA-Za-z0-9]{2,}/g) || []).filter((t) => t.length >= 2);
  const stop = new Set(["这篇", "文章", "论文", "什么", "如何", "是否", "以及", "主要", "进行", "关于"]);
  const merged = Array.from(new Set([...explicit, ...extracted])).filter((t) => !stop.has(t));
  return merged.slice(0, 24);
}

function inferQuestionIntent(question) {
  const q = String(question || "").toLowerCase();
  const rules = [
    { intent: "research_problem", patterns: [/研究问题|要解决什么|核心问题|问题定义|problem statement|research question/i] },
    // 须在 experiment 之前：「结果怎样」等易被误判为实验类，扩展问句会偏向实验设置而非结论讨论
    {
      intent: "findings",
      patterns: [
        /结果怎样|主要结果|研究结论|论文结论|结论是什么|得出什么结论|主要发现|论文的发现|文章的结果|这篇.*结果|文章.*结果|论文.*结果/i,
        /表明了什么|说明了什么|讨论认|结果与讨论|反演得到了|反演结果|三维反演.*结果|认识.*特征|写得怎样|写得如何/i,
        /findings?\b|main results?\b|conclusions?\b/i
      ]
    },
    { intent: "method", patterns: [/方法|模型|算法|流程|框架|思路|技术路线|how does|approach|methodology/i] },
    { intent: "innovation", patterns: [/创新|新意|贡献|亮点|改进点|contribution|novelty/i] },
    { intent: "data_source", patterns: [/数据来源|资料来源|数据集|样本|采集|观测|数据库|study area|dataset|source/i] },
    // 不含单独「结果」二字，避免与 findings 冲突；实验指标类仍用「实验结果」「指标」等
    { intent: "experiment", patterns: [/实验|对比|指标|评价|消融|ablation|benchmark|metric|实验结果|指标结果/i] },
    { intent: "limitation", patterns: [/局限|不足|缺点|假设|边界|适用范围|limitation|future work/i] },
    { intent: "application", patterns: [/应用|落地|场景|工程|实践|部署|application|use case/i] }
  ];
  for (const r of rules) {
    if (r.patterns.some((p) => p.test(q))) {
      return r.intent;
    }
  }
  return "general";
}

function intentKeywords(intent) {
  const map = {
    research_problem: ["研究问题", "问题", "目标", "挑战", "背景", "动机"],
    findings: [
      "结论",
      "讨论",
      "结果",
      "表明",
      "显示",
      "反演",
      "认识",
      "解释",
      "特征",
      "电性",
      "缝合",
      "俯冲"
    ],
    method: ["方法", "模型", "算法", "流程", "框架", "步骤"],
    innovation: ["创新", "贡献", "新", "改进", "优势"],
    data_source: ["数据", "来源", "数据集", "样本", "采集", "观测", "研究区", "剖面", "测线"],
    experiment: ["实验", "对比", "指标", "评价", "消融"],
    limitation: ["局限", "不足", "假设", "边界", "适用"],
    application: ["应用", "场景", "工程", "部署", "落地"]
  };
  return map[intent] || [];
}

function buildExpandedQuestions(question, intent) {
  const q = String(question || "").trim();
  const expansions = [q];
  if (!q) return expansions;
  const map = {
    research_problem: ["这篇论文主要要解决什么科学问题？", "作者关注的核心问题与研究目标是什么？"],
    findings: [
      "论文在讨论或结论部分得出的主要认识与结果是什么？",
      "反演或主要分析得到了什么电性结构或地质解释？",
      "作者对研究区构造或俯冲极性等得出了什么结论？"
    ],
    method: ["论文的核心方法流程是什么？", "关键模型/算法步骤如何设计？"],
    innovation: ["与已有方法相比，主要创新贡献是什么？", "这篇工作的新意体现在哪些方面？"],
    data_source: ["论文的数据来源、数据集或采集方式是什么？", "研究区/样本/观测资料来自哪里？"],
    experiment: ["实验设置、对比基线与评价指标是什么？", "实验结果如何支撑结论？"],
    limitation: ["论文明确提到的局限性和适用边界是什么？", "有哪些前提假设或不足？"],
    application: ["论文可能的应用场景和落地方向是什么？", "工程实践中的可用性如何？"]
  };
  for (const e of map[intent] || []) {
    if (!expansions.includes(e)) expansions.push(e);
  }
  return expansions.slice(0, 4);
}

function isDataSourceIntent(intent, question) {
  if (intent === "data_source") {
    return true;
  }
  return /数据来源|资料来源|采集单位|采集|观测|数据集|样本|研究区|测线|剖面/i.test(String(question || ""));
}

function organizationHintScore(text) {
  const t = String(text || "");
  if (!t) return 0;
  const orgPatterns = [
    /中国.{0,18}(大学|学院|研究院|研究所|中心|局|院)/g,
    /[^\s]{2,24}(大学|学院|研究院|研究所|中心|实验室|公司|研究站)/g,
    /(institute|university|academy|laboratory|center)/gi
  ];
  let score = 0;
  for (const p of orgPatterns) {
    const m = t.match(p);
    if (m) score += m.length * 2;
  }
  if (/采集|观测|测量|记录|资料|数据库|数据中心|survey|acquisition|observation/i.test(t)) {
    score += 3;
  }
  return score;
}

function dataSourceRuleHitsForDoc(doc, topK) {
  if (!doc || !Array.isArray(doc.chunks)) {
    return [];
  }
  const scored = doc.chunks
    .map((chunk, index) => ({
      index,
      score: organizationHintScore(chunk.text)
    }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score);
  return scored.slice(0, topK);
}

/** 结论/讨论/反演结果等段落常含用语，用于 findings 意图下补充召回 */
function findingsDiscussionHintScore(text) {
  const t = String(text || "");
  if (!t) return 0;
  let score = 0;
  const strong = [
    /结果表明/,
    /研究显示/,
    /综上/,
    /主要结论/,
    /讨论认为/,
    /主要认识/,
    /反演得到/,
    /反演结果/,
    /电性结构/,
    /三维反演/,
    /缝合带/,
    /俯冲.*极性/,
    /认识如下/,
    /结论表明/,
    /据此认为/,
    /揭示.*特征/
  ];
  for (const p of strong) {
    if (p.test(t)) score += 4;
  }
  if (/结论|结果与讨论|讨论部分|本章小结|研究得出/i.test(t)) score += 2;
  return score;
}

function findingsRuleHitsForDoc(doc, topK) {
  if (!doc || !Array.isArray(doc.chunks)) {
    return [];
  }
  const scored = doc.chunks
    .map((chunk, index) => ({
      index,
      score: findingsDiscussionHintScore(chunk.text)
    }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score);
  return scored.slice(0, topK);
}

function keywordScore(text, terms) {
  const source = String(text || "").toLowerCase();
  if (!source || !Array.isArray(terms) || terms.length === 0) {
    return 0;
  }
  let score = 0;
  for (const term of terms) {
    if (!term) continue;
    if (source.includes(String(term).toLowerCase())) {
      score += 1;
    }
  }
  return score;
}

function keywordSearchDocChunks(doc, terms, topK) {
  if (!doc || !Array.isArray(doc.chunks) || terms.length === 0) {
    return [];
  }
  const scored = doc.chunks
    .map((chunk, index) => ({ index, score: keywordScore(chunk.text, terms) }))
    .filter((r) => r.score > 0)
    .sort((a, b) => b.score - a.score);
  return scored.slice(0, topK);
}

function mergeDocHits(semanticHits, keywordHits, topK) {
  const merged = [];
  const seen = new Set();
  for (const row of semanticHits || []) {
    if (!seen.has(row.index)) {
      merged.push(row);
      seen.add(row.index);
    }
  }
  for (const row of keywordHits || []) {
    if (!seen.has(row.index)) {
      merged.push({
        index: row.index,
        score: Math.min(0.25, 0.05 + row.score * 0.03)
      });
      seen.add(row.index);
    }
  }
  merged.sort((a, b) => b.score - a.score);
  return merged.slice(0, topK);
}

function appendLibraryChatHistory(question, answer) {
  const q = String(question || "").trim().slice(0, 2400);
  const a = String(answer || "").trim().slice(0, 2400);
  if (q) {
    libraryChatHistory.push({ role: "user", content: q });
  }
  if (a) {
    libraryChatHistory.push({ role: "assistant", content: a });
  }
  const max = RAG_HISTORY_MAX_MESSAGES > 0 ? RAG_HISTORY_MAX_MESSAGES : 0;
  if (max > 0 && libraryChatHistory.length > max) {
    libraryChatHistory.splice(0, libraryChatHistory.length - max);
  }
  schedulePersistLibraryState();
}

function toIsoDate(ts) {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) {
    return "unknown";
  }
  return d.toISOString().slice(0, 10);
}

async function appendFeedbackRecord(record) {
  const payload = { ...record, server_received_at: new Date().toISOString() };
  await fs.mkdir(FEEDBACK_DIR, { recursive: true });
  await fs.appendFile(FEEDBACK_FILE, `${JSON.stringify(payload)}\n`, "utf8");
}

function getQueryableDocs() {
  return Array.from(documents.values()).filter(
    (doc) => doc.status === "ready" && Array.isArray(doc.embeddings) && doc.embeddings.length > 0 && doc.chunks.length > 0
  );
}

function looksLikeBroadSummaryQuestion(question) {
  const q = String(question || "").toLowerCase();
  const broadHints = [
    "主要讲",
    "讲了什么",
    "总结",
    "概述",
    "内容是什么",
    "介绍一下",
    "核心内容",
    "结果怎样",
    "结论怎样",
    "主要结论",
    "论文结果",
    "文章结果",
    "得出什么",
    "what is this paper about",
    "main idea",
    "summary"
  ];
  return broadHints.some((token) => q.includes(token));
}

function buildRagQueryMessages(doc, question, retrieved, history) {
  const locationNote = doc.locationNote || "";
  const profile = doc.deepProfile;
  const profileText = profile
    ? [
        `【通篇理解画像】`,
        `全局概述：${profile.oneParagraph}`,
        `关键术语：${(profile.keyTerms || []).join("、")}`,
        `方法流程：${(profile.methodFlow || []).join(" -> ")}`,
        `数据来源：${profile.dataSource}`,
        `实验设计：${profile.experimentDesign}`,
        `局限性：${(profile.limitations || []).join("；")}`,
        `贡献：${(profile.contributions || []).join("；")}`
      ].join("\n")
    : "（暂无通篇画像）";
  const contextBlocks = retrieved
    .map((row) => {
      const ch = doc.chunks[row.index];
      if (!ch) {
        return "";
      }
      const pageLine =
        ch.page_start != null
          ? `页码约 ${ch.page_start}${ch.page_end != null && ch.page_end !== ch.page_start ? `–${ch.page_end}` : ""}`
          : "页码不可用";
      return [
        `[chunk_id=${ch.chunk_id}]`,
        `[段落序号=${ch.paragraph_index}]`,
        `[${pageLine}]`,
        ch.text
      ].join("\n");
    })
    .filter(Boolean)
    .join("\n\n---\n\n");

  const intent = inferQuestionIntent(question);
  const system = [
    "你是一个基于检索证据的论文问答助手。",
    "你必须只输出 JSON，不要输出额外解释。",
    "answer 必须使用简体中文回答。",
    "回答时同时参考「通篇理解画像」与 Context，但事实、数据和结论必须能在 Context 中找到依据。",
    "若 Context 无法回答问题，请将 answer 明确写成无法从文献中得出可靠结论，并说明原因；citations 可为空数组。",
    "若用户问题与论文主题明显无关，将 out_of_scope 设为 true，answer 简短说明并引导用户围绕论文提问。",
    "citations 中的 chunk_id 必须来自 Context 中标注的 chunk_id；excerpt 必须是 Context 对应片段中的原文子串（可适当缩短但不得改写事实）。",
    "如果问题是数据来源/实验设置/创新点/局限性等常见论文阅读问题，请优先提炼对应证据后再回答。",
    "JSON schema:",
    "{",
    '  "answer": "string",',
    '  "citations": [',
    '    { "chunk_id": "string", "excerpt": "string", "page_start": null, "page_end": null, "paragraph_index": 0 }',
    "  ],",
    '  "confidence": "string",',
    '  "out_of_scope": false',
    "}"
  ].join("\n");

  const historyLines =
    history && history.length > 0
      ? [
          "【上文对话（仅用于理解指代、省略与多轮衔接；事实与数据必须以 Context 为准，不得仅凭上文记忆编造）】",
          ...history.map((h) => `${h.role === "user" ? "用户" : "助手"}：${h.content}`)
        ].join("\n")
      : "";

  const user = [
    `论文文件名：${doc.fileName || "未提供"}`,
    `位置说明：${locationNote}`,
    "",
    profileText,
    "",
    historyLines ? `${historyLines}\n` : "",
    `问题类型：${intent}`,
    `当前问题：${question}`,
    "",
    "Context（仅供引用）：",
    contextBlocks || "（空：无可用检索片段）"
  ]
    .filter((line) => line !== "")
    .join("\n");

  return [
    { role: "system", content: system },
    { role: "user", content: user }
  ];
}

function buildLibraryRagQueryMessages(question, retrieved, history) {
  const intent = inferQuestionIntent(question);
  const profileByDoc = new Map();
  for (const row of retrieved) {
    if (!profileByDoc.has(row.doc.id) && row.doc.deepProfile) {
      const p = row.doc.deepProfile;
      profileByDoc.set(
        row.doc.id,
        `文献：${row.doc.fileName}\n概述：${p.oneParagraph}\n方法：${(p.methodFlow || []).join(" -> ")}\n数据来源：${p.dataSource}`
      );
    }
  }
  const profileBlocks =
    profileByDoc.size > 0 ? Array.from(profileByDoc.values()).join("\n\n---\n\n") : "（暂无通篇画像）";
  const contextBlocks = retrieved
    .map((row) => {
      const pageLine =
        row.chunk.page_start != null
          ? `页码约 ${row.chunk.page_start}${
              row.chunk.page_end != null && row.chunk.page_end !== row.chunk.page_start ? `–${row.chunk.page_end}` : ""
            }`
          : "页码不可用";
      return [
        `[ref_id=${row.ref_id}]`,
        `[文献=${row.doc.fileName}]`,
        `[chunk_id=${row.chunk.chunk_id}]`,
        `[段落序号=${row.chunk.paragraph_index}]`,
        `[${pageLine}]`,
        row.chunk.text
      ].join("\n");
    })
    .join("\n\n---\n\n");

  const system = [
    "你是一个基于检索证据的文献库问答助手。",
    "你必须只输出 JSON，不要输出额外解释。",
    "answer 必须使用简体中文回答。",
    "只能使用 Context 回答，禁止编造事实。",
    "回答可综合多篇文献，但必须在 citations 中给出引用。",
    "回答时可参考「文献通篇画像」，但关键事实仍需在 Context 找到对应证据。",
    "如果问题属于创新点、方法、数据来源、实验、局限性、应用等论文常见问题，请先归纳证据再给结论。",
    "若无法回答，请明确说明证据不足。",
    "JSON schema:",
    "{",
    '  "answer": "string",',
    '  "citations": [',
    '    { "ref_id": "string", "excerpt": "string", "page_start": null, "page_end": null, "paragraph_index": 0 }',
    "  ],",
    '  "confidence": "string",',
    '  "out_of_scope": false',
    "}"
  ].join("\n");

  const historyLines =
    history && history.length > 0
      ? [
          "【上文对话（仅用于理解多轮衔接；事实必须以 Context 为准）】",
          ...history.map((h) => `${h.role === "user" ? "用户" : "助手"}：${h.content}`)
        ].join("\n")
      : "";

  const user = [
    historyLines ? `${historyLines}\n` : "",
    `问题类型：${intent}`,
    `当前问题：${question}`,
    "",
    "文献通篇画像（高层理解）：",
    profileBlocks,
    "",
    "Context（仅供引用）：",
    contextBlocks
  ]
    .filter(Boolean)
    .join("\n");

  return [
    { role: "system", content: system },
    { role: "user", content: user }
  ];
}

function sanitizeLibraryRagQueryResult(raw, refMap) {
  const citationsIn = Array.isArray(raw?.citations) ? raw.citations : [];
  const citations = citationsIn
    .map((c) => {
      const refId = String(c?.ref_id || "").trim();
      const ref = refMap.get(refId);
      if (!ref) {
        return null;
      }
      return {
        document_id: ref.doc.id,
        document_name: ref.doc.fileName,
        chunk_id: ref.chunk.chunk_id,
        excerpt: String(c?.excerpt || "").trim() || ref.chunk.text.slice(0, 180),
        page_start: c?.page_start == null || c.page_start === "" ? ref.chunk.page_start : Number(c.page_start),
        page_end: c?.page_end == null || c.page_end === "" ? ref.chunk.page_end : Number(c.page_end),
        paragraph_index: Number.isFinite(Number(c?.paragraph_index))
          ? Number(c.paragraph_index)
          : ref.chunk.paragraph_index
      };
    })
    .filter(Boolean);

  return {
    answer: String(raw?.answer || "无法基于当前文献库生成回答。").trim(),
    citations,
    confidence: String(raw?.confidence || "N/A").trim(),
    out_of_scope: Boolean(raw?.out_of_scope)
  };
}

async function ingestPdfToDocument(buffer, originalFileName) {
  const fileHash = crypto.createHash("sha256").update(buffer).digest("hex");
  const existedId = documentHashIndex.get(fileHash);
  if (existedId && documents.has(existedId)) {
    const existedDoc = documents.get(existedId);
    existedDoc.fileName = decodeFileName(originalFileName) || existedDoc.fileName;
    try {
      const data = await pdfParse(buffer);
      const text = normalizeExtractedText(data.text || "");
      if (text) {
        const freshAbstract = await translateTextToChinese(extractAbstractSection(text), "论文摘要");
        existedDoc.abstract = chooseBetterAbstract(existedDoc.abstract, freshAbstract, text);
      }
    } catch (_error) {
      // 缓存文献刷新失败时，保留原缓存结果，不阻断用户流程。
    }
    schedulePersistLibraryState();
    return existedDoc;
  }

  const documentId = crypto.randomUUID();
  const doc = {
    id: documentId,
    fileHash,
    fileName: decodeFileName(originalFileName),
    pages: 0,
    fullText: "",
    abstract: "",
    chunks: [],
    embeddings: null,
    status: "indexing",
    statusDetail: null,
    error: null,
    summaryPrd: null,
    deepProfile: null,
    deepProfileUpdatedAt: null,
    chatHistory: [],
    createdAt: Date.now(),
    locationNote:
      "页码通常按字符在全文中的位置占页数比例估算；若与 PDF 阅读器页码不一致，请以 PDF 为准并对照 excerpt。"
  };
  documents.set(documentId, doc);
  documentHashIndex.set(fileHash, documentId);

  try {
    const data = await pdfParse(buffer);
    const text = normalizeExtractedText(data.text || "");
    if (!text) {
      doc.status = "failed";
      doc.statusDetail = "EMPTY_TEXT";
      doc.error =
        "未从 PDF 中提取到可用文本，可能是扫描版 PDF、图片型 PDF，或当前解析器不支持该文件格式。";
      schedulePersistLibraryState();
      return doc;
    }
    doc.pages = data.numpages || 0;
    doc.abstract = await translateTextToChinese(extractAbstractSection(text), "论文摘要");
    const preferred = getPreferredPaperText(text, doc.abstract);
    doc.fullText = preferred;

    const rawChunks = rag.chunkDocumentText(preferred, doc.pages, {
      targetChars: CHUNK_TARGET_CHARS,
      minChars: CHUNK_MIN_CHARS,
      overlapRatio: CHUNK_OVERLAP_RATIO
    });
    const limited = rawChunks.slice(0, MAX_CHUNKS);
    doc.chunks = limited.map((c, idx) => ({
      chunk_id: `${documentId}_c_${idx + 1}`,
      text: c.text,
      paragraph_index: c.paragraph_index,
      page_start: c.page_start,
      page_end: c.page_end,
      char_start: c.char_start,
      char_end: c.char_end
    }));

    if (!OPENAI_API_KEY) {
      doc.embeddings = null;
      doc.status = "ready_no_embed";
      doc.statusDetail = "MISSING_API_KEY";
      schedulePersistLibraryState();
      return doc;
    }

    if (doc.chunks.length === 0) {
      doc.embeddings = [];
      doc.status = "ready";
      schedulePersistLibraryState();
      return doc;
    }

    const vectors = [];
    for (let i = 0; i < doc.chunks.length; i += EMBED_BATCH_SIZE) {
      const batch = doc.chunks.slice(i, i + EMBED_BATCH_SIZE).map((ch) => ch.text);
      const part = await embedTextsBatch(batch);
      if (part.length !== batch.length) {
        throw new Error("Embedding 返回数量与分块数量不一致。");
      }
      vectors.push(...part);
    }
    doc.embeddings = vectors;
    doc.status = "ready";
    schedulePersistLibraryState();
    return doc;
  } catch (error) {
    doc.status = "failed";
    doc.statusDetail = "INGEST_FAILED";
    doc.error = error.message || "文献入库失败";
    schedulePersistLibraryState();
    return doc;
  }
}

function getDocumentOrThrow(documentId) {
  const doc = documents.get(documentId);
  if (!doc) {
    const err = new Error("文献不存在或已过期。");
    err.status = 404;
    throw err;
  }
  return doc;
}

function sanitizeRagQueryResult(raw, allowedChunkIds) {
  const allowed = new Set(allowedChunkIds);
  const citationsIn = Array.isArray(raw?.citations) ? raw.citations : [];
  const citations = citationsIn
    .map((c) => ({
      chunk_id: String(c?.chunk_id || "").trim(),
      excerpt: String(c?.excerpt || "").trim(),
      page_start: c?.page_start == null || c.page_start === "" ? null : Number(c.page_start),
      page_end: c?.page_end == null || c.page_end === "" ? null : Number(c.page_end),
      paragraph_index: Number.isFinite(Number(c?.paragraph_index)) ? Number(c.paragraph_index) : null
    }))
    .filter((c) => c.chunk_id && allowed.has(c.chunk_id));

  return {
    answer: String(raw?.answer || "无法基于当前文献生成回答。").trim(),
    citations,
    confidence: String(raw?.confidence || "N/A").trim(),
    out_of_scope: Boolean(raw?.out_of_scope)
  };
}

function enrichCitationsFromDoc(doc, citations) {
  return citations.map((c) => {
    const ch = doc.chunks.find((x) => x.chunk_id === c.chunk_id);
    if (!ch) {
      return c;
    }
    return {
      ...c,
      page_start: c.page_start == null || Number.isNaN(c.page_start) ? ch.page_start : c.page_start,
      page_end: c.page_end == null || Number.isNaN(c.page_end) ? ch.page_end : c.page_end,
      paragraph_index:
        c.paragraph_index == null || Number.isNaN(c.paragraph_index) ? ch.paragraph_index : c.paragraph_index
    };
  });
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
      modelOptions: MODEL_OPTIONS,
      analysisTextMaxChars: ANALYSIS_TEXT_MAX_CHARS,
      baseUrl: OPENAI_BASE_URL,
      maxPdfMb: PDF_MAX_MB,
      embeddingModel: EMBEDDING_MODEL,
      ragTopK: RAG_TOP_K,
      ragMinSimilarity: RAG_MIN_SIMILARITY,
      chunkTargetChars: CHUNK_TARGET_CHARS,
      maxChunks: MAX_CHUNKS
    });
  }
  return res.json({
    ...base,
    ready: Boolean(OPENAI_API_KEY),
    maxPdfMb: PDF_MAX_MB,
    defaultModel: DEFAULT_MODEL,
    modelOptions: MODEL_OPTIONS,
    analysisTextMaxChars: ANALYSIS_TEXT_MAX_CHARS,
    embeddingModel: EMBEDDING_MODEL,
    ragTopK: RAG_TOP_K,
    ragMinSimilarity: RAG_MIN_SIMILARITY,
    chunkTargetChars: CHUNK_TARGET_CHARS,
    maxChunks: MAX_CHUNKS
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
    const abstract = await translateTextToChinese(extractAbstractSection(text), "论文摘要");
    const preferredText = getPreferredPaperText(text, abstract);
    if (!preferredText) {
      return res.status(422).json({
        error: "未从 PDF 中提取到可用于分析的文本。"
      });
    }

    return res.json({
      fileName: decodedFileName,
      pages: data.numpages || 0,
      characters: text.length,
      abstract,
      text: preferredText,
      fullText: preferredText
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
    const cacheKey = makeCacheKey("summarize", payload);
    const cached = getCachedValue(cacheKey);
    if (cached) {
      return res.json({ result: cached, cached: true });
    }
    const model = payload.model || DEFAULT_MODEL;
    const temperature = Number(payload.temperature);
    const messages = buildSummaryMessages(payload);
    const raw = await callChatCompletions(model, temperature, messages);
    const result = sanitizeSummary(raw);
    setCachedValue(cacheKey, result);
    return res.json({ result, cached: false });
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
    const cacheKey = makeCacheKey("compare", payload);
    const cached = getCachedValue(cacheKey);
    if (cached) {
      return res.json({ result: cached, cached: true });
    }
    const model = payload.model || DEFAULT_MODEL;
    const temperature = Number(payload.temperature);
    const messages = buildCompareMessages(payload);
    const raw = await callChatCompletions(model, temperature, messages);
    const result = sanitizeComparison(raw);
    setCachedValue(cacheKey, result);
    return res.json({ result, cached: false });
  } catch (error) {
    return res.status(500).json({ error: error.message });
  }
});

app.post("/api/documents", pdfLimiter, upload.single("pdf"), async (req, res) => {
  try {
    if (!req.file) {
      return res.status(400).json({ error: "缺少 pdf 文件。", error_code: "MISSING_FILE" });
    }
    const doc = await ingestPdfToDocument(req.file.buffer, req.file.originalname);
    const payload = {
      document_id: doc.id,
      status: doc.status,
      fileName: doc.fileName,
      pages: doc.pages,
      characters: (doc.fullText || "").length,
      abstract: doc.abstract,
      text: doc.fullText,
      fullText: doc.fullText,
      chunk_count: doc.chunks.length,
      has_embeddings: Boolean(doc.embeddings && doc.embeddings.length > 0),
      location_note: doc.locationNote,
      error: doc.error,
      error_code: doc.statusDetail
    };
    if (doc.status === "failed") {
      const code = doc.statusDetail === "EMPTY_TEXT" ? 422 : 500;
      return res.status(code).json(payload);
    }
    return res.status(201).json(payload);
  } catch (error) {
    return res.status(500).json({ error: error.message || "文献入库失败", error_code: "INGEST_EXCEPTION" });
  }
});

app.get("/api/documents/:id/status", (req, res) => {
  try {
    const doc = getDocumentOrThrow(req.params.id);
    return res.json({
      document_id: doc.id,
      status: doc.status,
      chunk_count: doc.chunks.length,
      has_embeddings: Boolean(doc.embeddings && doc.embeddings.length > 0),
      summary_cached: Boolean(doc.summaryPrd),
      has_deep_profile: Boolean(doc.deepProfile),
      chat_history_count: Array.isArray(doc.chatHistory) ? doc.chatHistory.length : 0,
      error: doc.error,
      error_code: doc.statusDetail,
      location_note: doc.locationNote
    });
  } catch (error) {
    const status = Number.isInteger(error.status) ? error.status : 500;
    return res.status(status).json({ error: error.message, error_code: "NOT_FOUND" });
  }
});

app.get("/api/library/status", (req, res) => {
  const all = Array.from(documents.values());
  const readyDocs = all.filter((d) => d.status === "ready");
  return res.json({
    total_documents: all.length,
    ready_documents: readyDocs.length,
    total_chunks: readyDocs.reduce((sum, d) => sum + d.chunks.length, 0),
    has_queryable_docs: readyDocs.some((d) => Array.isArray(d.embeddings) && d.embeddings.length > 0),
    deep_profile_documents: readyDocs.filter((d) => Boolean(d.deepProfile)).length,
    library_chat_history_count: libraryChatHistory.length
  });
});

app.post("/api/library/reset", async (req, res) => {
  try {
    const confirm = String(req.body?.confirm || "").trim().toLowerCase();
    if (confirm !== "yes") {
      return res.status(400).json({
        error: '危险操作：请传入 {"confirm":"yes"} 后重试。',
        error_code: "RESET_CONFIRM_REQUIRED"
      });
    }
    await resetLibraryState();
    return res.json({ ok: true, message: "文献库与持久化缓存已清空。" });
  } catch (error) {
    return res.status(500).json({ error: error.message || "重置文献库失败", error_code: "RESET_LIBRARY_FAILED" });
  }
});

app.post("/api/documents/:id/summarize", summarizeLimiter, async (req, res) => {
  try {
    const doc = getDocumentOrThrow(req.params.id);
    if (doc.status === "failed") {
      return res.status(422).json({
        error: doc.error || "该文献不可用，请重新上传。",
        error_code: doc.statusDetail || "DOCUMENT_FAILED"
      });
    }
    if (!(doc.fullText || "").trim()) {
      return res.status(422).json({ error: "文献文本为空，无法总结。", error_code: "EMPTY_TEXT" });
    }
    await ensureDocDeepProfile(doc);
    const model = String(req.body?.model || DEFAULT_MODEL).trim();
    const temperature = Number(req.body?.temperature);
    if (req.body?.force) {
      doc.summaryPrd = null;
    }
    if (doc.summaryPrd && doc.summaryPrd._model === model) {
      const { _model, _updatedAt, ...rest } = doc.summaryPrd;
      return res.json({ result: rest, cached: true });
    }
    try {
      const messages = buildPrdSummaryMessages(doc);
      const raw = await callChatCompletions(model, temperature, messages);
      const result = sanitizePrdSummary(raw);
      doc.summaryPrd = { ...result, _model: model, _updatedAt: Date.now() };
      schedulePersistLibraryState();
      const { _model, _updatedAt, ...rest } = doc.summaryPrd;
      return res.json({ result: rest, cached: false });
    } catch (error) {
      if (doc.summaryPrd) {
        const { _model, _updatedAt, ...rest } = doc.summaryPrd;
        return res.status(200).json({
          result: rest,
          cached: true,
          degraded: true,
          warning: error.message
        });
      }
      throw error;
    }
  } catch (error) {
    const status = Number.isInteger(error.status) ? error.status : 500;
    return res.status(status).json({ error: error.message, error_code: "SUMMARY_FAILED" });
  }
});

let _compiledRagGraph = null;
function getCompiledRagGraph() {
  if (_compiledRagGraph) return _compiledRagGraph;

  const env = {
    apiKey: OPENAI_API_KEY,
    baseURL: OPENAI_BASE_URL,
    defaultModel: DEFAULT_MODEL,
    embeddingModel: EMBEDDING_MODEL,
    embedBatchSize: EMBED_BATCH_SIZE,
    ragTopK: RAG_TOP_K,
    checkpointFilePath:
      LANGGRAPH_CHECKPOINT_FILE || path.resolve(__dirname, "data", "langgraph-checkpoints.json")
  };

  const helpers = {
    inferQuestionIntent,
    buildExpandedQuestions,
    noHitsResult: (state) => {
      if (state.scope === "library") {
        return {
          answer: "文献库中未检索到足够相关片段，暂无法可靠回答。建议问题更具体（方法名、实验设置、数据集等）。",
          citations: [],
          confidence: "N/A",
          out_of_scope: false
        };
      }
      return {
        answer:
          "未在文献中检索到与该问题足够相关的片段，暂无法基于原文可靠作答。建议把问题写得更具体（例如指定方法名、数据集或实验设置），或围绕论文的研究问题 / 方法 / 结论改写提问。",
        citations: [],
        confidence: "N/A",
        out_of_scope: false
      };
    },
    retrieve: async (state, { topK }) => {
      const intent = state.intent || inferQuestionIntent(state.question);
      const question = String(state.question || "").trim();
      const effectiveTop = Number.isFinite(Number(topK)) ? Math.min(24, Math.max(1, Math.floor(topK))) : RAG_TOP_K;
      const qVectors = Array.isArray(state.qVectors) ? state.qVectors : [];
      if (qVectors.length === 0) {
        throw new Error("问题向量化失败。");
      }

      if (state.scope === "document") {
        const doc = state.allowed?.doc;
        const questionKeywords = Array.from(new Set([...buildQuestionKeywords(question), ...intentKeywords(intent)]));

        const unionHits = [];
        const seenHit = new Set();
        for (const vec of qVectors) {
          const hits = rag.topKIndices(vec, doc.embeddings, effectiveTop, RAG_MIN_SIMILARITY);
          for (const h of hits) {
            if (!seenHit.has(h.index)) {
              unionHits.push(h);
              seenHit.add(h.index);
            }
          }
        }
        unionHits.sort((a, b) => b.score - a.score);
        let selectedHits = unionHits.slice(0, effectiveTop);

        let usedRelaxedRetrieval = false;
        if (selectedHits.length === 0) {
          const relaxedThreshold = looksLikeBroadSummaryQuestion(question)
            ? Math.min(RAG_RELAXED_MIN_SIMILARITY, RAG_MIN_SIMILARITY)
            : Math.min(0.12, RAG_MIN_SIMILARITY);
          const relaxedHitsRaw = [];
          const seenRelaxed = new Set();
          for (const vec of qVectors) {
            const rows = rag.topKIndices(vec, doc.embeddings, effectiveTop, relaxedThreshold);
            for (const r of rows) {
              if (!seenRelaxed.has(r.index)) {
                relaxedHitsRaw.push(r);
                seenRelaxed.add(r.index);
              }
            }
          }
          relaxedHitsRaw.sort((a, b) => b.score - a.score);
          const relaxedHits = relaxedHitsRaw.slice(0, effectiveTop);
          if (relaxedHits.length > 0) {
            selectedHits = relaxedHits;
            usedRelaxedRetrieval = true;
          }
        }

        const keywordHits = keywordSearchDocChunks(doc, questionKeywords, effectiveTop);
        selectedHits = mergeDocHits(selectedHits, keywordHits, effectiveTop);
        if (isDataSourceIntent(intent, question)) {
          const ruleHits = dataSourceRuleHitsForDoc(doc, effectiveTop);
          selectedHits = mergeDocHits(selectedHits, ruleHits, effectiveTop);
        }
        if (intent === "findings") {
          const ruleHits = findingsRuleHitsForDoc(doc, effectiveTop);
          selectedHits = mergeDocHits(selectedHits, ruleHits, effectiveTop);
        }

        const allowedChunkIds = selectedHits.map((h) => doc.chunks[h.index]?.chunk_id).filter(Boolean);

        return {
          retrieved: selectedHits,
          degraded: usedRelaxedRetrieval,
          retrievalMeta: {
            hit_count: selectedHits.length,
            min_score: RAG_MIN_SIMILARITY,
            used_relaxed_threshold: usedRelaxedRetrieval,
            relaxed_min_score: usedRelaxedRetrieval ? RAG_RELAXED_MIN_SIMILARITY : null,
            scores: selectedHits.map((h) => ({
              chunk_id: doc.chunks[h.index]?.chunk_id,
              score: Number(h.score.toFixed(4))
            })),
            intent
          },
          allowed: { ...state.allowed, allowedChunkIds }
        };
      }

      const queryableDocs = getQueryableDocs();
      const questionKeywords = Array.from(new Set([...buildQuestionKeywords(question), ...intentKeywords(intent)]));
      const collectHits = (threshold) => {
        const merged = [];
        for (const doc of queryableDocs) {
          const seen = new Set();
          for (const vec of qVectors) {
            const docHits = rag.topKIndices(vec, doc.embeddings, effectiveTop, threshold);
            for (const h of docHits) {
              if (seen.has(h.index)) continue;
              seen.add(h.index);
              const chunk = doc.chunks[h.index];
              if (!chunk) continue;
              merged.push({ doc, chunk, score: h.score, ref_id: `${doc.id}::${chunk.chunk_id}` });
            }
          }
        }
        merged.sort((a, b) => b.score - a.score);
        return merged.slice(0, effectiveTop);
      };
      const collectKeywordHits = () => {
        const merged = [];
        for (const doc of queryableDocs) {
          const docHits = keywordSearchDocChunks(doc, questionKeywords, effectiveTop);
          for (const h of docHits) {
            const chunk = doc.chunks[h.index];
            if (!chunk) continue;
            merged.push({
              doc,
              chunk,
              score: Math.min(0.25, 0.05 + h.score * 0.03),
              ref_id: `${doc.id}::${chunk.chunk_id}`
            });
          }
        }
        merged.sort((a, b) => b.score - a.score);
        return merged.slice(0, effectiveTop);
      };
      const collectRuleHits = () => {
        const merged = [];
        for (const doc of queryableDocs) {
          const docHits = dataSourceRuleHitsForDoc(doc, effectiveTop);
          for (const h of docHits) {
            const chunk = doc.chunks[h.index];
            if (!chunk) continue;
            merged.push({
              doc,
              chunk,
              score: Math.min(0.28, 0.08 + h.score * 0.025),
              ref_id: `${doc.id}::${chunk.chunk_id}`
            });
          }
        }
        merged.sort((a, b) => b.score - a.score);
        return merged.slice(0, effectiveTop);
      };
      const collectFindingsRuleHits = () => {
        const merged = [];
        for (const doc of queryableDocs) {
          const docHits = findingsRuleHitsForDoc(doc, effectiveTop);
          for (const h of docHits) {
            const chunk = doc.chunks[h.index];
            if (!chunk) continue;
            merged.push({
              doc,
              chunk,
              score: Math.min(0.28, 0.08 + h.score * 0.025),
              ref_id: `${doc.id}::${chunk.chunk_id}`
            });
          }
        }
        merged.sort((a, b) => b.score - a.score);
        return merged.slice(0, effectiveTop);
      };

      let selected = collectHits(RAG_MIN_SIMILARITY);
      let usedRelaxedRetrieval = false;
      if (selected.length === 0) {
        const relaxedThreshold = looksLikeBroadSummaryQuestion(question)
          ? Math.min(RAG_RELAXED_MIN_SIMILARITY, RAG_MIN_SIMILARITY)
          : Math.min(0.12, RAG_MIN_SIMILARITY);
        const relaxed = collectHits(relaxedThreshold);
        if (relaxed.length > 0) {
          selected = relaxed;
          usedRelaxedRetrieval = true;
        }
      }

      const keywordHits = collectKeywordHits();
      if (keywordHits.length > 0) {
        const seen = new Set(selected.map((r) => r.ref_id));
        for (const row of keywordHits) {
          if (!seen.has(row.ref_id)) {
            selected.push(row);
            seen.add(row.ref_id);
          }
        }
        selected.sort((a, b) => b.score - a.score);
        selected = selected.slice(0, effectiveTop);
      }
      if (isDataSourceIntent(intent, question)) {
        const ruleHits = collectRuleHits();
        if (ruleHits.length > 0) {
          const seen = new Set(selected.map((r) => r.ref_id));
          for (const row of ruleHits) {
            if (!seen.has(row.ref_id)) {
              selected.push(row);
              seen.add(row.ref_id);
            }
          }
          selected.sort((a, b) => b.score - a.score);
          selected = selected.slice(0, effectiveTop);
        }
      }
      if (intent === "findings") {
        const ruleHits = collectFindingsRuleHits();
        if (ruleHits.length > 0) {
          const seen = new Set(selected.map((r) => r.ref_id));
          for (const row of ruleHits) {
            if (!seen.has(row.ref_id)) {
              selected.push(row);
              seen.add(row.ref_id);
            }
          }
          selected.sort((a, b) => b.score - a.score);
          selected = selected.slice(0, effectiveTop);
        }
      }

      return {
        retrieved: selected,
        degraded: usedRelaxedRetrieval,
        retrievalMeta: {
          hit_count: selected.length,
          min_score: RAG_MIN_SIMILARITY,
          used_relaxed_threshold: usedRelaxedRetrieval,
          relaxed_min_score: usedRelaxedRetrieval ? RAG_RELAXED_MIN_SIMILARITY : null,
          scores: selected.map((h) => ({
            ref_id: h.ref_id,
            document_name: h.doc.fileName,
            score: Number(h.score.toFixed(4))
          })),
          intent
        }
      };
    },
    ensureProfiles: async (retrieved) => {
      for (const row of retrieved || []) {
        await ensureDocDeepProfile(row.doc);
      }
    },
    buildMessages: async (state) => {
      const question = String(state.question || "").trim();
      if (state.scope === "document") {
        const doc = state.allowed?.doc;
        return buildRagQueryMessages(doc, question, state.retrieved, state.history);
      }
      return buildLibraryRagQueryMessages(question, state.retrieved, state.history);
    },
    validateAndSanitize: async (state) => {
      const raw = state.rawModelJson ?? parseModelJson(state.rawModelText);
      if (state.scope === "document") {
        const doc = state.allowed?.doc;
        const allowedChunkIds = state.allowed?.allowedChunkIds || [];
        let parsed = sanitizeRagQueryResult(raw, allowedChunkIds);
        parsed = { ...parsed, citations: enrichCitationsFromDoc(doc, parsed.citations) };
        parsed = {
          ...parsed,
          citations: parsed.citations.map((c) => {
            const ch = doc.chunks.find((x) => x.chunk_id === c.chunk_id);
            if (!ch) return c;
            const ex = String(c.excerpt || "").trim();
            if (ex && ch.text.includes(ex)) return c;
            return { ...c, excerpt: ch.text.slice(0, 180) };
          })
        };
        return { result: parsed };
      }

      const refMap = new Map((state.retrieved || []).map((r) => [r.ref_id, r]));
      const parsed = sanitizeLibraryRagQueryResult(raw, refMap);
      const fixed = {
        ...parsed,
        citations: parsed.citations.map((c) => {
          const refId = `${c.document_id}::${c.chunk_id}`;
          const ref = refMap.get(refId);
          if (!ref) return c;
          const ex = String(c.excerpt || "").trim();
          if (ex && ref.chunk.text.includes(ex)) return c;
          return { ...c, excerpt: ref.chunk.text.slice(0, 180) };
        })
      };
      return { result: fixed };
    },
    persistHistory: async (state) => {
      const q = String(state.question || "").trim();
      const a = String(state.result?.answer || "").trim();
      if (!q || !a) return;
      if (state.scope === "document") {
        appendDocChatHistory(state.allowed?.doc, q, a);
        return;
      }
      appendLibraryChatHistory(q, a);
      for (const c of state.result?.citations || []) {
        const doc = documents.get(c.document_id);
        if (doc) appendDocChatHistory(doc, q, a);
      }
    }
  };

  _compiledRagGraph = compileRagGraph({ env, helpers, useCheckpoint: ENABLE_LANGGRAPH_CHECKPOINTS });
  return _compiledRagGraph;
}

app.post("/api/documents/:id/query", documentQueryLimiter, async (req, res) => {
  try {
    const doc = getDocumentOrThrow(req.params.id);
    if (doc.status === "failed") {
      return res.status(422).json({
        error: doc.error || "该文献不可用，请重新上传。",
        error_code: doc.statusDetail || "DOCUMENT_FAILED"
      });
    }
    if (doc.status === "ready_no_embed") {
      return res.status(409).json({
        error:
          "当前未配置可用的向量索引（缺少 API Key 或未完成向量化）。请配置 OPENAI_API_KEY 后重新上传 PDF，再使用文献问答。",
        error_code: "EMBEDDINGS_DISABLED"
      });
    }
    const question = String(req.body?.question || "").trim();
    if (!question) {
      return res.status(400).json({ error: "缺少 question 字段。", error_code: "MISSING_QUESTION" });
    }
    if (!doc.embeddings || doc.embeddings.length === 0 || doc.chunks.length === 0) {
      return res.status(422).json({
        error: "该文献没有可分块文本或向量索引为空，无法执行检索问答。",
        error_code: "NO_INDEX"
      });
    }

    const model = String(req.body?.model || DEFAULT_MODEL).trim();
    const temperature = Number(req.body?.temperature);
    const clientHistory = sanitizeRagHistory(req.body?.history);
    const docHistory = sanitizeRagHistory(doc.chatHistory);
    const history = clientHistory.length > 0 ? clientHistory : docHistory;
    const requestedTop = Number(req.body?.top_k);
    const effectiveTop = Number.isFinite(requestedTop)
      ? Math.min(24, Math.max(1, Math.floor(requestedTop)))
      : RAG_TOP_K;

    const compiled = getCompiledRagGraph();
    const out = await runRagGraph(
      compiled,
      {
        scope: "document",
        documentId: doc.id,
        question,
        history,
        model,
        temperature,
        topK: effectiveTop,
        allowed: { doc }
      },
      { checkpointNs: "doc" }
    );

    if (out && out.result) {
      return res.json({
        result: out.result,
        cached: false,
        degraded: Boolean(out.degraded),
        retrieval: out.retrievalMeta || { hit_count: 0, min_score: RAG_MIN_SIMILARITY, scores: [] }
      });
    }
    throw new Error("问答流程异常：缺少 result。");
  } catch (error) {
    const status = Number.isInteger(error.status) ? error.status : 500;
    return res.status(status).json({ error: error.message, error_code: "QUERY_FAILED" });
  }
});

app.post("/api/library/query", documentQueryLimiter, async (req, res) => {
  try {
    const question = String(req.body?.question || "").trim();
    if (!question) {
      return res.status(400).json({ error: "缺少 question 字段。", error_code: "MISSING_QUESTION" });
    }
    const queryableDocs = getQueryableDocs();
    if (queryableDocs.length === 0) {
      return res.status(422).json({
        error: "当前文献库中没有可检索的文献，请先上传并完成索引。",
        error_code: "EMPTY_LIBRARY"
      });
    }

    const model = String(req.body?.model || DEFAULT_MODEL).trim();
    const temperature = Number(req.body?.temperature);
    const clientHistory = sanitizeRagHistory(req.body?.history);
    const history = clientHistory.length > 0 ? clientHistory : sanitizeRagHistory(libraryChatHistory);

    const requestedTop = Number(req.body?.top_k);
    const effectiveTop = Number.isFinite(requestedTop)
      ? Math.min(24, Math.max(1, Math.floor(requestedTop)))
      : RAG_TOP_K;
    const compiled = getCompiledRagGraph();
    const out = await runRagGraph(
      compiled,
      {
        scope: "library",
        question,
        history,
        model,
        temperature,
        topK: effectiveTop
      },
      { checkpointNs: "library" }
    );

    if (out && out.result) {
      return res.json({
        result: out.result,
        cached: false,
        degraded: Boolean(out.degraded),
        retrieval: out.retrievalMeta || { hit_count: 0, min_score: RAG_MIN_SIMILARITY, scores: [] }
      });
    }
    throw new Error("问答流程异常：缺少 result。");
  } catch (error) {
    const status = Number.isInteger(error.status) ? error.status : 500;
    return res.status(status).json({ error: error.message, error_code: "LIBRARY_QUERY_FAILED" });
  }
});

app.post("/api/feedback", async (req, res) => {
  try {
    const body = req.body || {};
    const answerId = String(body.answer_id || "").trim();
    const vote = String(body.vote || "").trim();
    if (!answerId) {
      return res.status(400).json({ error: "缺少 answer_id。", error_code: "MISSING_ANSWER_ID" });
    }
    if (vote !== "up" && vote !== "down") {
      return res.status(400).json({ error: "vote 仅支持 up/down。", error_code: "INVALID_VOTE" });
    }
    const allowedErrorTags = new Set([
      "intent_mismatch",
      "retrieval_miss",
      "retrieval_noise",
      "citation_invalid",
      "answer_incomplete",
      "answer_incorrect"
    ]);
    const errorTags = Array.isArray(body.error_tags)
      ? body.error_tags
          .map((x) => String(x || "").trim())
          .filter((x) => allowedErrorTags.has(x))
          .slice(0, 5)
      : [];
    const topScores = Array.isArray(body.retrieval_top_scores)
      ? body.retrieval_top_scores.map((x) => Number(x)).filter((x) => Number.isFinite(x)).slice(0, 3)
      : [];
    const record = {
      schema_version: "2.0",
      answer_id: answerId,
      vote,
      wrong_question_type: Boolean(body.wrong_question_type),
      error_tags: errorTags,
      question: String(body.question || "").trim().slice(0, 4000),
      answer: String(body.answer || "").trim().slice(0, 12000),
      intent: String(body.intent || "unknown").trim(),
      document_scope: String(body.document_scope || "library").trim(),
      document_ids: Array.isArray(body.document_ids)
        ? body.document_ids.map((x) => String(x).trim()).filter(Boolean).slice(0, 30)
        : [],
      retrieval_hit_count: Number.isFinite(Number(body.retrieval_hit_count))
        ? Number(body.retrieval_hit_count)
        : null,
      retrieval_top_scores: topScores,
      retrieval_score_gap: Number.isFinite(Number(body.retrieval_score_gap)) ? Number(body.retrieval_score_gap) : null,
      used_relaxed_threshold: Boolean(body.used_relaxed_threshold),
      citation_count: Number.isFinite(Number(body.citation_count)) ? Number(body.citation_count) : null,
      degraded: Boolean(body.degraded),
      feedback_source: String(body.feedback_source || "explicit").trim().slice(0, 40),
      client_time: String(body.client_time || "").trim(),
      client_day: toIsoDate(body.client_time || Date.now()),
      session_id: String(body.session_id || "").trim().slice(0, 120)
    };
    await appendFeedbackRecord(record);
    return res.json({ ok: true });
  } catch (error) {
    return res.status(500).json({ error: error.message || "写入反馈失败", error_code: "FEEDBACK_WRITE_FAILED" });
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

async function bootstrap() {
  await loadLibraryStateFromDisk();
  app.listen(PORT, HOST, () => {
    const hint = HOST === "0.0.0.0" ? `http://localhost:${PORT}` : `http://${HOST}:${PORT}`;
    console.log(`PaperPilot server listening on ${hint} (bind ${HOST}:${PORT})`);
  });
}

process.on("SIGINT", () => {
  void persistLibraryStateNow().finally(() => process.exit(0));
});

process.on("SIGTERM", () => {
  void persistLibraryStateNow().finally(() => process.exit(0));
});

void bootstrap();
