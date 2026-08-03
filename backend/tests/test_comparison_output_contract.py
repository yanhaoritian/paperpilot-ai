from __future__ import annotations

from app.services.generate import generate_answer
from app.services.prompts import RAG_AGENT_FINAL_SYSTEM, RAG_EVIDENCE_POLICY, RAG_STREAM_SYSTEM
from app.services.research_skills import (
    comparison_answer_violations,
    get_research_skill,
    sanitize_comparison_answer,
    validate_skill_answer,
)
from app.services.retrieve import RetrievedChunk


def _validate(answer: str) -> dict:
    return validate_skill_answer(
        get_research_skill("multi_paper_synthesis"),
        answer=answer,
        citations=[{"document_id": "d1", "chunk_id": "c1"}],
        degraded=False,
    )


def test_compare_contract_prefers_evidence_dimensions_and_one_gap_section():
    skill = get_research_skill("multi_paper_synthesis")
    contract = skill.system_prompt()

    for marker in ("研究问题", "方法", "数据/样本", "评价指标", "主要结果", "局限"):
        assert marker in contract
    assert "主表只保留当前证据充分" in contract
    assert "证据缺口" in contract
    assert "本轮未检索到不等于论文未报告" in contract

    for prompt in (RAG_EVIDENCE_POLICY, RAG_STREAM_SYSTEM, RAG_AGENT_FINAL_SYSTEM):
        assert "证据缺口" in prompt
        assert "本轮未检索到" in prompt
        assert "论文未报告" in prompt


def test_compare_validator_accepts_supported_table_and_consolidated_gap():
    answer = """
共同点是三篇工作都采用了受控实验；差异主要体现在方法和主要结果。

| 文献 | 方法 | 主要结果 |
| --- | --- | --- |
| a.pdf | 使用方法 A（第 2 页） | 指标提升 12%（第 5 页） |
| b.pdf | 使用方法 B（第 3 页） | 指标提升 9%（第 7 页） |
| c.pdf | 使用方法 C（第 4 页） | 指标提升 15%（第 8 页） |

证据缺口：本轮未检索到三篇文献可直接横向比较的样本规模信息；这不等于论文未报告，建议补查各篇方法与附录。现有证据仍显示实验边界与局限不同。
"""
    result = _validate(answer)

    assert result["passed"] is True
    assert result["comparison_placeholder_cells"] == 0
    assert "comparison_placeholder_cells" not in result["warnings"]
    assert "unsupported_not_reported_claim" not in result["warnings"]


def test_compare_validator_rejects_placeholder_cells_in_main_table():
    answer = """
三篇工作有共同目标，但方法存在差异。

| 文献 | 方法 | 数据 | 结果 |
| --- | --- | --- | --- |
| a.pdf | 方法 A | 未在检索片段中提及 | 结果 A |
| b.pdf | 方法 B | 无法判断 | 结果 B |
| c.pdf | 方法 C | — | 结果 C |

证据缺口与局限需要后续补查。
"""
    result = _validate(answer)

    assert result["passed"] is False
    assert result["comparison_placeholder_cells"] == 3
    assert "comparison_placeholder_cells" in result["warnings"]


def test_compare_validator_rejects_unqualified_not_reported_claim():
    answer = """
共同点与差异已有直接证据。

| 文献 | 方法 | 结果 |
| --- | --- | --- |
| a.pdf | 方法 A | 结果 A |
| b.pdf | 方法 B | 结果 B |

证据缺口：论文未报告样本规模，因此存在局限。
"""
    result = _validate(answer)

    assert result["passed"] is False
    assert "unsupported_not_reported_claim" in result["warnings"]


def test_compare_validator_recognizes_user_reported_placeholder_wording():
    answer = """
共同点和差异如下。

| 文献 | 方法 | 结果 |
| --- | --- | --- |
| a.pdf | 方法 A | 结果 A |
| b.pdf | 方法 B | 未在检索片段中体现 |

局限与证据空白需要补查。
"""
    assert comparison_answer_violations(answer) == [
        "comparison_placeholder_cells"
    ]


def test_compare_validator_catches_gap_phrase_inside_partially_filled_cell():
    answer = """
共同点和差异如下。

| 文献 | 方法 | 数据 | 结果 |
| --- | --- | --- | --- |
| 中文文献 | 多道面波成像 | 81 km 深地震反射剖面 | 沉积厚度约 1000 m |
| 英文文献 | 区域构造演化分析（具体方法未在检索片段中体现） | 区域地质资料（具体数据未在检索片段中明确提及） | 未在检索片段中体现 |

局限与证据空白需要后续核对。
"""
    result = _validate(answer)

    assert result["passed"] is False
    assert result["comparison_placeholder_cells"] == 3
    assert "comparison_placeholder_cells" in result["warnings"]

    cleaned = sanitize_comparison_answer(answer)
    assert "具体方法未在检索片段中体现" not in cleaned
    assert "具体数据未在检索片段中明确提及" not in cleaned
    assert comparison_answer_violations(cleaned) == []


def test_comparison_sanitizer_drops_placeholder_columns_and_consolidates_gaps():
    answer = """
共同点是目标一致，差异体现在方法。

| 文献 | 方法 | 数据 | 结果 |
| --- | --- | --- | --- |
| a.pdf | 方法 A | 数据 A | 结果 A |
| b.pdf | 方法 B | 未在检索片段中体现 | 结果 B |

作者未报告样本规模，因此存在局限。
"""
    cleaned = sanitize_comparison_answer(answer)

    assert "| 文献 | 方法 | 结果 |" in cleaned
    assert "| 数据 |" not in cleaned
    assert "未在检索片段中体现" not in cleaned
    assert cleaned.count("证据缺口") == 1
    assert "b.pdf的数据" in cleaned
    assert "不等于论文未报告" in cleaned
    assert comparison_answer_violations(cleaned) == []


def test_sync_comparison_answer_is_repaired_before_return(monkeypatch):
    invalid = {
        "answer": (
            "共同点与差异如下。\n\n"
            "| 文献 | 方法 | 数据 |\n"
            "| --- | --- | --- |\n"
            "| a.pdf | 方法 A | 数据 A |\n"
            "| b.pdf | 方法 B | 未在检索片段中体现 |\n\n"
            "局限需要补查。"
        ),
        "citations": [{"chunk_id": "c1", "excerpt": "方法 A"}],
        "confidence": "medium",
        "out_of_scope": False,
    }
    repaired = {
        "answer": (
            "共同点是研究目标一致，差异在方法。\n\n"
            "| 文献 | 方法 |\n"
            "| --- | --- |\n"
            "| a.pdf | 方法 A |\n"
            "| b.pdf | 方法 B |\n\n"
            "证据缺口：本轮未检索到可横向比较的数据证据，这不等于论文未报告。"
        ),
        "citations": [{"chunk_id": "c1", "excerpt": "方法 A"}],
        "confidence": "medium",
        "out_of_scope": False,
    }
    responses = iter((invalid, repaired))
    calls: list[float] = []

    def fake_chat_json(_messages, **kwargs):
        calls.append(float(kwargs.get("temperature") or 0.0))
        return next(responses)

    monkeypatch.setattr("app.services.generate.chat_json", fake_chat_json)
    row = RetrievedChunk(
        chunk_id="c1",
        document_id="d1",
        library_id="l1",
        file_name="a.pdf",
        text="方法 A 使用反演算法处理完整数据。",
        page_start=2,
        page_end=2,
        chunk_index=1,
        score=0.9,
        section_path="Methods",
        role="paragraph",
    )

    result = generate_answer(
        "比较两篇论文",
        [row],
        balance_documents=True,
    )

    assert len(calls) == 2
    assert calls[-1] == 0.0
    assert "未在检索片段中体现" not in result["answer"]
    assert "证据缺口" in result["answer"]
    assert result["citations"][0]["chunk_id"] == "c1"
