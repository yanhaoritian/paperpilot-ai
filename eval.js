const fs = require("fs/promises");
const path = require("path");

const FEEDBACK_FILE = path.resolve(__dirname, "data", "feedback.jsonl");

function dayOffsetIso(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function parseJsonl(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function scoreRecords(records) {
  const total = records.length;
  if (total === 0) {
    return { total: 0, up: 0, down: 0, wrongType: 0, score: 0 };
  }
  let up = 0;
  let down = 0;
  let wrongType = 0;
  for (const r of records) {
    if (r.vote === "up") up += 1;
    if (r.vote === "down") down += 1;
    if (r.wrong_question_type) wrongType += 1;
  }
  const score = (up - down - wrongType * 0.5) / total;
  return { total, up, down, wrongType, score };
}

function scoreByIntent(records) {
  const map = new Map();
  for (const r of records) {
    const intent = String(r.intent || "unknown").trim() || "unknown";
    if (!map.has(intent)) map.set(intent, []);
    map.get(intent).push(r);
  }
  const rows = [];
  for (const [intent, rs] of map.entries()) {
    const s = scoreRecords(rs);
    rows.push({
      intent,
      total: s.total,
      upRate: s.total > 0 ? s.up / s.total : 0,
      wrongTypeRate: s.total > 0 ? s.wrongType / s.total : 0,
      score: s.score
    });
  }
  rows.sort((a, b) => b.total - a.total || b.score - a.score);
  return rows;
}

function summarizeErrorTags(records) {
  const tagCount = new Map();
  let totalTagged = 0;
  for (const r of records) {
    const tags = Array.isArray(r.error_tags) ? r.error_tags : [];
    for (const tag of tags) {
      const t = String(tag || "").trim();
      if (!t) continue;
      tagCount.set(t, (tagCount.get(t) || 0) + 1);
      totalTagged += 1;
    }
  }
  return Array.from(tagCount.entries())
    .map(([tag, count]) => ({ tag, count, ratio: totalTagged > 0 ? count / totalTagged : 0 }))
    .sort((a, b) => b.count - a.count);
}

function summarizeRetrieval(records) {
  if (!records.length) {
    return {
      avgHitCount: 0,
      avgTop1Score: 0,
      relaxedRatio: 0,
      degradedRatio: 0,
      avgCitationCount: 0
    };
  }
  let hitTotal = 0;
  let top1Total = 0;
  let top1Count = 0;
  let relaxedCount = 0;
  let degradedCount = 0;
  let citationTotal = 0;
  let citationCount = 0;
  for (const r of records) {
    const hit = Number(r.retrieval_hit_count);
    if (Number.isFinite(hit)) hitTotal += hit;
    const top1 = Array.isArray(r.retrieval_top_scores) ? Number(r.retrieval_top_scores[0]) : NaN;
    if (Number.isFinite(top1)) {
      top1Total += top1;
      top1Count += 1;
    }
    if (r.used_relaxed_threshold) relaxedCount += 1;
    if (r.degraded) degradedCount += 1;
    const cite = Number(r.citation_count);
    if (Number.isFinite(cite)) {
      citationTotal += cite;
      citationCount += 1;
    }
  }
  return {
    avgHitCount: hitTotal / records.length,
    avgTop1Score: top1Count > 0 ? top1Total / top1Count : 0,
    relaxedRatio: relaxedCount / records.length,
    degradedRatio: degradedCount / records.length,
    avgCitationCount: citationCount > 0 ? citationTotal / citationCount : 0
  };
}

async function main() {
  let raw = "";
  try {
    raw = await fs.readFile(FEEDBACK_FILE, "utf8");
  } catch (error) {
    if (error && error.code === "ENOENT") {
      console.log("暂无 feedback.jsonl，无法评估。");
      process.exit(0);
    }
    throw error;
  }
  const rows = parseJsonl(raw);
  const today = dayOffsetIso(0);
  const yesterday = dayOffsetIso(-1);
  const byDay = (day) => rows.filter((r) => String(r.client_day || "").trim() === day);

  const sToday = scoreRecords(byDay(today));
  const sYesterday = scoreRecords(byDay(yesterday));
  const intentToday = scoreByIntent(byDay(today));
  const tagsToday = summarizeErrorTags(byDay(today));
  const retrievalToday = summarizeRetrieval(byDay(today));
  const delta = sToday.score - sYesterday.score;
  const trend =
    sYesterday.total === 0
      ? "昨天无数据，无法比较"
      : delta > 0.03
        ? "今天比昨天好"
        : delta < -0.03
          ? "今天比昨天差"
          : "今天和昨天基本持平";

  console.log(`评估日期：today=${today}, yesterday=${yesterday}`);
  console.log(
    `today: total=${sToday.total}, up=${sToday.up}, down=${sToday.down}, wrongType=${sToday.wrongType}, score=${sToday.score.toFixed(3)}`
  );
  console.log(
    `yesterday: total=${sYesterday.total}, up=${sYesterday.up}, down=${sYesterday.down}, wrongType=${sYesterday.wrongType}, score=${sYesterday.score.toFixed(3)}`
  );
  console.log(
    `today retrieval: avg_hit_count=${retrievalToday.avgHitCount.toFixed(2)}, avg_top1_score=${retrievalToday.avgTop1Score.toFixed(
      3
    )}, relaxed_ratio=${retrievalToday.relaxedRatio.toFixed(3)}, degraded_ratio=${retrievalToday.degradedRatio.toFixed(
      3
    )}, avg_citation_count=${retrievalToday.avgCitationCount.toFixed(2)}`
  );
  if (intentToday.length > 0) {
    console.log("today by intent:");
    for (const row of intentToday) {
      console.log(
        `  - ${row.intent}: total=${row.total}, up_rate=${row.upRate.toFixed(3)}, wrong_type_rate=${row.wrongTypeRate.toFixed(
          3
        )}, score=${row.score.toFixed(3)}`
      );
    }
  }
  if (tagsToday.length > 0) {
    console.log("today error tags:");
    for (const row of tagsToday) {
      console.log(`  - ${row.tag}: count=${row.count}, ratio=${row.ratio.toFixed(3)}`);
    }
  }
  console.log(`结论：${trend}`);
}

main().catch((error) => {
  console.error("评估失败：", error.message || error);
  process.exit(1);
});

