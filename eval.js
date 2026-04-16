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
  console.log(`结论：${trend}`);
}

main().catch((error) => {
  console.error("评估失败：", error.message || error);
  process.exit(1);
});

