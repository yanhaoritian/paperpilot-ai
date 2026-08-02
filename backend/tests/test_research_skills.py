from __future__ import annotations

import pytest

from app.services.intent import QueryIntent
from app.services.research_skills import (
    get_research_skill,
    list_research_skills,
    resolve_research_skill,
    validate_skill_answer,
)


def test_research_skill_registry_has_versioned_core_contracts():
    skills = list_research_skills()
    ids = {skill.id for skill in skills}
    assert {
        "paper_qa",
        "paper_deep_read",
        "multi_paper_synthesis",
        "claim_evidence_audit",
        "academic_writing",
    } <= ids
    assert all(skill.version and skill.output_contract for skill in skills)
    assert "不能放宽" in get_research_skill("paper_qa").system_prompt()


@pytest.mark.parametrize(
    ("question", "intent", "expected"),
    [
        ("比较这些论文的方法差异", QueryIntent.COMPARE, "multi_paper_synthesis"),
        ("精读这篇论文并给出复现要点", QueryIntent.QA, "paper_deep_read"),
        ("核查这个观点是否被论文支持", QueryIntent.QA, "claim_evidence_audit"),
        ("基于这些文献写一段相关工作", QueryIntent.QA, "academic_writing"),
        ("作者用了什么数据集？", QueryIntent.QA, "paper_qa"),
    ],
)
def test_auto_skill_routing(question, intent, expected):
    skill = resolve_research_skill("auto", question=question, intent=intent)
    assert skill.id == expected


def test_explicit_skill_wins_and_invalid_skill_is_rejected():
    selected = resolve_research_skill(
        "paper_deep_read",
        question="比较几篇论文",
        intent=QueryIntent.COMPARE,
    )
    assert selected.id == "paper_deep_read"
    with pytest.raises(ValueError):
        get_research_skill("made-up-skill")


def test_skill_validation_flags_missing_evidence_and_sections():
    skill = get_research_skill("paper_deep_read")
    result = validate_skill_answer(
        skill,
        answer="这里只给出一个笼统结论。",
        citations=[],
        degraded=False,
    )
    assert result["passed"] is False
    assert "missing_citations" in result["warnings"]
    assert "missing_required_sections" in result["warnings"]
