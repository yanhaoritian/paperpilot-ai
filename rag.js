"use strict";

/**
 * RAG helpers: paragraph-aware chunking + cosine retrieval.
 * Embeddings are produced by the caller (OpenAI API).
 */

function clamp(n, lo, hi) {
  return Math.min(hi, Math.max(lo, n));
}

function pageForCharOffset(charIndex, textLength, numPages) {
  const pages = Number(numPages) || 0;
  if (pages <= 0 || textLength <= 0) {
    return null;
  }
  const ratio = clamp(charIndex / textLength, 0, 1);
  const page = Math.floor(ratio * pages) + 1;
  return clamp(page, 1, pages);
}

function normalizeWhitespace(text) {
  return String(text || "")
    .replace(/\r/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/**
 * @returns {Array<{ text: string, start: number, end: number }>}
 */
function extractParagraphs(normalized) {
  if (!normalized) {
    return [];
  }
  /** @type {Array<{ text: string, start: number, end: number }>} */
  const out = [];
  let i = 0;
  while (i < normalized.length) {
    const sep = normalized.indexOf("\n\n", i);
    const end = sep === -1 ? normalized.length : sep;
    const raw = normalized.slice(i, end);
    const text = raw.replace(/\n+/g, " ").replace(/\s+/g, " ").trim();
    if (text) {
      const lead = raw.match(/^\s*/)?.[0]?.length || 0;
      const start = i + lead;
      const trailing = raw.match(/\s*$/)?.[0]?.length || 0;
      const endTrim = end - trailing;
      out.push({ text, start, end: Math.max(endTrim, start) });
    }
    i = sep === -1 ? normalized.length : sep + 2;
  }
  return out;
}

function sliceWithOverlap(text, start, targetLen, overlapChars) {
  const end = Math.min(text.length, start + targetLen);
  const piece = text.slice(start, end).trim();
  const nextStart = end - overlapChars;
  return { piece, nextStart: Math.max(start + 1, nextStart) };
}

function splitLongParagraph(para, paraStart, paraEnd, targetLen, overlapChars, fullLen, numPages, chunkIndexRef) {
  const chunks = [];
  let start = 0;
  const body = para;
  while (start < body.length) {
    const { piece, nextStart } = sliceWithOverlap(body, start, targetLen, overlapChars);
    if (!piece) {
      break;
    }
    const charStart = paraStart + start;
    const charEnd = paraStart + start + piece.length;
    const pageStart = pageForCharOffset(charStart, fullLen, numPages);
    const pageEnd = pageForCharOffset(Math.min(charEnd - 1, fullLen - 1), fullLen, numPages);
    chunkIndexRef.value += 1;
    chunks.push({
      paragraph_index: chunkIndexRef.value,
      char_start: charStart,
      char_end: charEnd,
      text: piece,
      page_start: pageStart,
      page_end: pageEnd ?? pageStart
    });
    if (nextStart <= start) {
      break;
    }
    start = nextStart;
  }
  return chunks;
}

/**
 * @param {string} fullText
 * @param {number} numPages
 * @param {{ targetChars?: number, minChars?: number, overlapRatio?: number }} options
 */
function chunkDocumentText(fullText, numPages, options = {}) {
  const normalized = normalizeWhitespace(fullText);
  if (!normalized) {
    return [];
  }
  const targetChars = clamp(Number(options.targetChars) || 2000, 500, 8000);
  const minChars = clamp(Number(options.minChars) || 450, 200, targetChars - 1);
  const overlapRatio = clamp(Number(options.overlapRatio) || 0.15, 0, 0.45);
  const overlapChars = Math.floor(targetChars * overlapRatio);

  const paragraphs = extractParagraphs(normalized);
  const fullLen = normalized.length;
  const chunkIndexRef = { value: 0 };
  /** @type {Array<{ paragraph_index: number, char_start: number, char_end: number, text: string, page_start: number|null, page_end: number|null }>} */
  const out = [];

  if (paragraphs.length === 0) {
    return splitLongParagraph(
      normalized,
      0,
      normalized.length,
      targetChars,
      overlapChars,
      fullLen,
      numPages,
      chunkIndexRef
    );
  }

  let group = [];
  let groupStart = -1;
  let groupEnd = -1;

  const flushGroup = () => {
    if (group.length === 0) {
      return;
    }
    const merged = group.map((p) => p.text).join("\n\n");
    const start = groupStart;
    const end = groupEnd;
    if (merged.length <= targetChars) {
      chunkIndexRef.value += 1;
      const pageStart = pageForCharOffset(start, fullLen, numPages);
      const pageEnd = pageForCharOffset(Math.min(end - 1, fullLen - 1), fullLen, numPages);
      out.push({
        paragraph_index: chunkIndexRef.value,
        char_start: start,
        char_end: end,
        text: merged,
        page_start: pageStart,
        page_end: pageEnd ?? pageStart
      });
    } else {
      out.push(
        ...splitLongParagraph(merged, start, end, targetChars, overlapChars, fullLen, numPages, chunkIndexRef)
      );
    }
    group = [];
    groupStart = -1;
    groupEnd = -1;
  };

  for (const p of paragraphs) {
    const projected = group.length ? `${group.map((x) => x.text).join("\n\n")}\n\n${p.text}` : p.text;
    if (projected.length > targetChars && group.length > 0) {
      flushGroup();
    }
    if (p.text.length > targetChars) {
      flushGroup();
      out.push(
        ...splitLongParagraph(p.text, p.start, p.end, targetChars, overlapChars, fullLen, numPages, chunkIndexRef)
      );
      continue;
    }
    if (group.length === 0) {
      groupStart = p.start;
    }
    group.push(p);
    groupEnd = p.end;
    if (projected.length >= minChars) {
      flushGroup();
    }
  }
  flushGroup();

  return out;
}

function dot(a, b) {
  let s = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    s += a[i] * b[i];
  }
  return s;
}

function norm(a) {
  return Math.sqrt(dot(a, a)) || 1;
}

function cosineSimilarity(a, b) {
  return dot(a, b) / (norm(a) * norm(b));
}

/**
 * @param {number[]} query
 * @param {number[][]} corpus
 * @param {number} topK
 * @param {number} minScore
 * @returns {Array<{ index: number, score: number }>}
 */
function topKIndices(query, corpus, topK, minScore) {
  const k = clamp(Number(topK) || 6, 1, 50);
  const min = Number.isFinite(minScore) ? minScore : 0.25;
  const scored = corpus.map((vec, index) => ({
    index,
    score: Array.isArray(vec) && vec.length === query.length ? cosineSimilarity(query, vec) : -1
  }));
  scored.sort((x, y) => y.score - x.score);
  return scored.filter((row) => row.score >= min).slice(0, k);
}

module.exports = {
  normalizeWhitespace,
  chunkDocumentText,
  cosineSimilarity,
  topKIndices,
  pageForCharOffset
};
