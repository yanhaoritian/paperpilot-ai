"""Versioned research-task contracts layered on top of the shared evidence policy.

These are application skills, not arbitrary user prompts.  Each contract may
change retrieval behaviour and output structure, while the base evidence policy
in ``prompts.py`` always remains authoritative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.intent import QueryIntent


@dataclass(frozen=True)
class ResearchSkill:
    id: str
    version: str
    title: str
    description: str
    output_contract: str
    retrieval_hint: str
    requires_evidence: bool = True
    cover_all_documents: bool = False
    required_marker_groups: tuple[tuple[str, ...], ...] = ()
    example_prompts: tuple[str, ...] = ()

    def system_prompt(self) -> str:
        return (
            "## 当前科研 Skill（任务合同）\n"
            f"名称：{self.title}\n"
            f"版本：{self.version}\n"
            "该合同只能细化任务与格式，不能放宽前述证据边界、引用规则或安全规则。\n"
            f"执行要求：{self.output_contract}"
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "requires_evidence": self.requires_evidence,
            "cover_all_documents": self.cover_all_documents,
            "example_prompts": list(self.example_prompts),
        }


SKILL_VERSION = "2026-08-03-v2"

_SKILLS: tuple[ResearchSkill, ...] = (
    ResearchSkill(
        id="paper_qa",
        version=SKILL_VERSION,
        title="论文证据问答",
        description="从所选论文定位原文证据并直接回答，适合一般科研问题。",
        output_contract=(
            "先直接回答问题，再解释证据。关键事实、数值、实验设置和结论应在相邻文字中"
            "点明文献名与页码；不得把摘要、会话记忆或常识替代原文证据。证据不足时仅回答"
            "有依据的部分，并明确缺失项。"
        ),
        retrieval_hint="优先检索能直接回答问题的定义、方法、实验和结论段落。",
        example_prompts=(
            "这篇论文的核心贡献是什么？",
            "作者使用了什么数据集和评价指标？",
        ),
    ),
    ResearchSkill(
        id="paper_deep_read",
        version=SKILL_VERSION,
        title="单篇论文精读",
        description="按研究问题、方法、数据、结果、局限和复现要点结构化精读。",
        output_contract=(
            "按“研究问题与动机、方法与关键机制、数据与实验设计、主要结果、局限与适用边界、"
            "复现要点”组织回答。每部分都必须由当前证据支撑；文献没有写明的内容标注“原文未"
            "找到”，不得依据领域经验补齐。保留关键数值、单位、比较基线与页码。"
        ),
        retrieval_hint=(
            "兼顾摘要/引言、方法、实验、结果、讨论/局限等不同章节；不要只依据摘要。"
        ),
        required_marker_groups=(
            ("研究问题", "研究动机"),
            ("方法", "机制"),
            ("实验", "数据"),
            ("结果", "发现"),
            ("局限", "边界"),
            ("复现", "实现"),
        ),
        example_prompts=("精读这篇论文并给出可复现要点。",),
    ),
    ResearchSkill(
        id="multi_paper_synthesis",
        version=SKILL_VERSION,
        title="多文献对比综述",
        description="跨多篇论文建立证据矩阵，辨析共同点、差异、冲突和研究空白。",
        output_contract=(
            "先给出综合结论，并优先核对每篇文献的研究问题、方法、数据/样本、评价指标、主要"
            "结果和局限。Markdown 主表只保留当前证据充分且适合横向比较的维度，不得为了凑齐"
            "六个维度而生成空列，也不得在多个单元格中反复填写“未在检索片段中提及、无法判断、"
            "未报告”等占位语。所有缺失项统一放在表后的一次“证据缺口”说明中；明确本轮未检索"
            "到不等于论文未报告，并给出建议补查的章节。若没有足够可比维度，可省略主表。表后"
            "再说明一致结论、冲突结论、研究趋势与空白，禁止臆造引用。"
        ),
        retrieval_hint=(
            "对所选文献执行覆盖优先检索；优先查找研究问题、方法、数据/样本、评价指标、主要"
            "结果和局限，每篇先保留相关证据，再进行跨文献精排。"
        ),
        cover_all_documents=True,
        required_marker_groups=(
            ("共同", "一致"),
            ("差异", "不同"),
            ("局限", "空白", "不足"),
        ),
        example_prompts=("比较这些论文的方法、数据、结果和局限。",),
    ),
    ResearchSkill(
        id="claim_evidence_audit",
        version=SKILL_VERSION,
        title="观点证据核查",
        description="检查一个观点或草稿论断在所选论文中是否获得支持。",
        output_contract=(
            "把用户观点拆成可核查的原子论断。逐条标注“支持 / 部分支持 / 冲突 / 无证据”，"
            "并给出对应文献、页码、短摘录和判断理由。区分文献实际结论、作者推测和用户外推；"
            "不得把相关性当因果、把无显著差异当等效，也不得用未检索到解释为事实错误。"
        ),
        retrieval_hint="围绕待核查论断检索直接证据、反例、限制条件和统计显著性描述。",
        required_marker_groups=(("支持", "部分支持", "冲突", "无证据"),),
        example_prompts=("核查这个观点是否被库内论文支持：……",),
    ),
    ResearchSkill(
        id="academic_writing",
        version=SKILL_VERSION,
        title="证据约束写作",
        description="仅使用库内证据撰写综述、相关工作或学术段落。",
        output_contract=(
            "先提炼写作目标和论证顺序，再输出可直接编辑的学术文本。每个关键论断都要能映射到"
            "当前引用；不得生成库中不存在的作者、年份、题名或参考文献。对冲突证据使用审慎措辞，"
            "对无证据内容保留明确占位提示。除非用户要求，不夸大新颖性、因果性或普适性。"
        ),
        retrieval_hint="按论证主题检索多篇来源，优先保留能支撑不同论证环节的互补证据。",
        required_marker_groups=(),
        example_prompts=("基于这些论文写一段相关工作，并保留来源。",),
    ),
)

_BY_ID = {skill.id: skill for skill in _SKILLS}

_DEEP_READ_RE = re.compile(
    r"(精读|深度解读|深入分析|逐章|研究问题.{0,8}(方法|实验)|复现要点|可复现)",
    re.I,
)
_CLAIM_AUDIT_RE = re.compile(
    r"(核查|查证|验证|证据审计|是否支持|支持.{0,6}(观点|结论|说法)|"
    r"观点.{0,8}(成立|可靠|正确)|论断.{0,8}(证据|依据))",
    re.I,
)
_WRITING_RE = re.compile(
    r"(撰写|写一段|写成|改写|润色|相关工作|related\s*work|"
    r"综述草稿|论文段落|引言草稿|讨论部分|学术表达)",
    re.I,
)


def list_research_skills() -> list[ResearchSkill]:
    return list(_SKILLS)


def get_research_skill(skill_id: str) -> ResearchSkill:
    normalized = (skill_id or "").strip().lower()
    if normalized not in _BY_ID:
        raise ValueError(f"未知科研 Skill：{skill_id}")
    return _BY_ID[normalized]


def resolve_research_skill(
    requested: str | None,
    *,
    question: str,
    intent: QueryIntent | str,
) -> ResearchSkill:
    normalized = (requested or "auto").strip().lower()
    if normalized and normalized != "auto":
        return get_research_skill(normalized)

    intent_value = intent.value if isinstance(intent, QueryIntent) else str(intent)
    q = (question or "").strip()
    if intent_value == QueryIntent.COMPARE.value:
        return _BY_ID["multi_paper_synthesis"]
    if _CLAIM_AUDIT_RE.search(q):
        return _BY_ID["claim_evidence_audit"]
    if _WRITING_RE.search(q):
        return _BY_ID["academic_writing"]
    if _DEEP_READ_RE.search(q):
        return _BY_ID["paper_deep_read"]
    return _BY_ID["paper_qa"]


def validate_skill_answer(
    skill: ResearchSkill,
    *,
    answer: str,
    citations: list[dict[str, Any]] | None,
    degraded: bool,
) -> dict[str, Any]:
    """Return deterministic contract diagnostics without inventing content."""

    text = (answer or "").strip()
    citation_count = len(citations or [])
    warnings: list[str] = []
    if not text:
        warnings.append("empty_answer")
    if skill.requires_evidence and not degraded and citation_count == 0:
        warnings.append("missing_citations")

    missing_groups: list[list[str]] = []
    for group in skill.required_marker_groups:
        if not any(marker.lower() in text.lower() for marker in group):
            missing_groups.append(list(group))
    if missing_groups:
        warnings.append("missing_required_sections")

    if skill.id == "multi_paper_synthesis":
        comparison_warnings = comparison_answer_violations(text)
        table_placeholder_count = _comparison_table_placeholder_count(text)
        warnings.extend(comparison_warnings)

    return {
        "passed": not warnings,
        "warnings": warnings,
        "citation_count": citation_count,
        "missing_marker_groups": missing_groups,
        "comparison_placeholder_cells": (
            table_placeholder_count
            if skill.id == "multi_paper_synthesis"
            else 0
        ),
    }


_TABLE_PLACEHOLDER_RE = re.compile(
    r"^(?:[-—–/]|n\s*/?\s*a|无|未知|不详|无法判断|无法比较|"
    r"未(?:在)?(?:当前|本轮)?(?:检索)?(?:片段|证据|结果)?中?"
    r"(?:提及|找到|检索到|体现|显示|说明|反映)|"
    r"原文未找到|证据不足|论文未报告|文中未报告|作者未报告)$",
    re.I,
)
_TABLE_GAP_PHRASE_RE = re.compile(
    r"未(?:在)?(?:当前|本轮)?(?:检索)?(?:片段|证据|结果)?中?"
    r"(?:明确|直接|充分)?(?:提及|找到|检索到|体现|显示|说明|反映)|"
    r"无法判断|无法比较|证据不足",
    re.I,
)
_NOT_REPORTED_RE = re.compile(r"(?:论文|文中|作者|原文).{0,4}未(?:予以)?报告")
_NOT_REPORTED_NEGATION_RE = re.compile(
    r"(?:不等于|≠|不代表|并不表示|不能据此(?:断定|认定)?|不可据此(?:断定|认定)?)"
    r".{0,12}(?:论文|文中|作者|原文).{0,4}未(?:予以)?报告"
)


def _markdown_table_data_cells(text: str) -> list[str]:
    cells: list[str] = []
    in_table = False
    saw_header = False
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if line.count("|") < 2:
            in_table = False
            saw_header = False
            continue
        normalized = line.strip("|").strip()
        row = [cell.strip() for cell in normalized.split("|")]
        if row and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in row):
            in_table = True
            saw_header = True
            continue
        if not saw_header:
            # First pipe row is the table header. Data rows only follow a
            # Markdown separator, so prose containing pipes is not inspected.
            continue
        if in_table:
            cells.extend(row[1:] if len(row) > 1 else row)
    return cells


def _comparison_table_placeholder_count(text: str) -> int:
    count = 0
    for cell in _markdown_table_data_cells(text):
        if _is_placeholder_cell(cell):
            count += 1
    return count


def _has_unsupported_not_reported_claim(text: str) -> bool:
    scrubbed = _NOT_REPORTED_NEGATION_RE.sub("", text or "")
    return bool(_NOT_REPORTED_RE.search(scrubbed))


def comparison_answer_violations(text: str) -> list[str]:
    """Return repairable compare-format violations in deterministic order."""
    warnings: list[str] = []
    if _comparison_table_placeholder_count(text):
        warnings.append("comparison_placeholder_cells")
    if _has_unsupported_not_reported_claim(text):
        warnings.append("unsupported_not_reported_claim")
    return warnings


def _table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", ""))
        for cell in cells
    )


def _is_placeholder_cell(cell: str) -> bool:
    compact = re.sub(r"[。；;，,]+$", "", cell.strip().strip("*_` "))
    return (
        not compact
        or _TABLE_PLACEHOLDER_RE.fullmatch(compact) is not None
        or _TABLE_GAP_PHRASE_RE.search(compact) is not None
    )


def _replace_unsupported_not_reported(text: str) -> str:
    safe_spans = [match.span() for match in _NOT_REPORTED_NEGATION_RE.finditer(text)]
    matches = list(_NOT_REPORTED_RE.finditer(text))
    for match in reversed(matches):
        if any(start <= match.start() and match.end() <= end for start, end in safe_spans):
            continue
        replacement = "本轮未检索到足以判断该项的原文证据（不等于论文未报告）"
        text = text[: match.start()] + replacement + text[match.end() :]
    return text


def sanitize_comparison_answer(text: str) -> str:
    """Deterministically remove placeholder columns after a failed LLM repair.

    This is a last-resort format guard. It never fills missing facts; it keeps
    supported table columns and moves detected gaps into one consolidated note.
    """
    lines = (text or "").splitlines()
    output: list[str] = []
    gaps: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 >= len(lines) or lines[index].count("|") < 2:
            output.append(lines[index])
            index += 1
            continue
        header = _table_row(lines[index])
        separator = _table_row(lines[index + 1])
        if not _is_table_separator(separator) or len(separator) != len(header):
            output.append(lines[index])
            index += 1
            continue

        end = index + 2
        data_rows: list[list[str]] = []
        while end < len(lines) and lines[end].count("|") >= 2:
            row = _table_row(lines[end])
            if len(row) != len(header):
                break
            data_rows.append(row)
            end += 1

        bad_columns: set[int] = set()
        for column in range(1, len(header)):
            for row in data_rows:
                if _is_placeholder_cell(row[column]):
                    bad_columns.add(column)
                    document = row[0] or "未命名文献"
                    gaps.append(f"{document}的{header[column] or '对应维度'}")

        keep_columns = [
            column for column in range(len(header)) if column not in bad_columns
        ]
        if data_rows and len(keep_columns) >= 2:
            output.append("| " + " | ".join(header[column] for column in keep_columns) + " |")
            output.append(
                "| "
                + " | ".join(separator[column] for column in keep_columns)
                + " |"
            )
            for row in data_rows:
                output.append(
                    "| " + " | ".join(row[column] for column in keep_columns) + " |"
                )
        index = end

    cleaned = _replace_unsupported_not_reported("\n".join(output)).strip()
    unique_gaps = list(dict.fromkeys(gap for gap in gaps if gap))
    if unique_gaps:
        note = (
            "本轮未检索到足以支持以下横向比较项的原文证据："
            + "、".join(unique_gaps)
            + "。这不等于论文未报告；建议补查对应方法、结果、讨论或附录章节。"
        )
        if "证据缺口" in cleaned:
            cleaned += f"\n\n结构校验补充：{note}"
        else:
            cleaned += f"\n\n证据缺口：{note}"
    return cleaned
