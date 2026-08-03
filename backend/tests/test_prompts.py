from __future__ import annotations

from app.services.generate import (
    _format_context_grouped,
    build_rag_messages,
    build_rag_stream_messages,
)
from app.services.conversation_memory import (
    ConversationContext,
    RecalledMemory,
)
from app.services.prompt_budget import budget_history
from app.services.prompts import RAG_AGENT_FINAL_SYSTEM, RAG_EVIDENCE_POLICY, RAG_JSON_SYSTEM, RAG_STREAM_SYSTEM
from app.services.retrieve import RetrievedChunk


def _chunk(
    *,
    chunk_id: str = "c1",
    text: str = "α激酶在 37°C 下活性提高 2 倍。",
    file_name: str = "paper_a.pdf",
    page_start: int = 3,
    page_end: int = 3,
    library_id: str = "lib1",
    document_id: str = "d1",
    chunk_index: int = 0,
    score: float = 0.9,
    section_path: str | None = "Methods",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        library_id=library_id,
        file_name=file_name,
        chunk_index=chunk_index,
        text=text,
        score=score,
        page_start=page_start,
        page_end=page_end,
        section_path=section_path,
    )


def test_evidence_policy_covers_four_pillars():
    for key in ("证据边界", "无法回答", "多文献综合", "引用规则"):
        assert key in RAG_EVIDENCE_POLICY


def test_format_policy_discourages_pipe_tables_for_normal_qa():
    assert "自然段落" in RAG_EVIDENCE_POLICY
    assert "禁止输出 Markdown 表格" in RAG_EVIDENCE_POLICY
    assert "对比" in RAG_STREAM_SYSTEM


def test_json_system_requires_schema_and_policy():
    assert "JSON schema" in RAG_JSON_SYSTEM
    assert "证据边界" in RAG_JSON_SYSTEM
    assert "chunk_id" in RAG_JSON_SYSTEM


def test_stream_and_agent_share_policy():
    assert "证据边界" in RAG_STREAM_SYSTEM
    assert "证据边界" in RAG_AGENT_FINAL_SYSTEM
    assert "不要输出 JSON" in RAG_STREAM_SYSTEM


def test_build_rag_messages_embeds_policy_and_chunk_ids():
    rows = [
        _chunk(chunk_id="c1", file_name="a.pdf"),
        _chunk(chunk_id="c2", file_name="b.pdf", text="另一篇提到抑制剂 X。"),
    ]
    msgs = build_rag_messages(
        "两篇有何共同点？",
        rows,
        inventory_text="所选知识库文献清单（共 2 篇）：\n1. 《a.pdf》\n2. 《b.pdf》",
        intent_hint="这是跨文献对比/共同点问题",
    )
    assert msgs[0]["role"] == "system"
    assert "证据边界" in msgs[0]["content"]
    assert "禁止声称" in msgs[0]["content"] or "只检索到一篇" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "chunk_id=c1" in user
    assert "chunk_id=c2" in user
    assert "a.pdf" in user and "b.pdf" in user
    assert "文献清单" in user
    assert "覆盖的文献文件" in user
    assert "意图提示" in user
    assert "文献 1:" in user or "文献 1：" in user or "#### 文献" in user


def test_build_rag_stream_messages_includes_history_and_policy():
    rows = [_chunk()]
    msgs = build_rag_stream_messages(
        "它提高了多少？",
        rows,
        history=[{"role": "user", "content": "α激酶怎样？"}, {"role": "assistant", "content": "先看方法段。"}],
    )
    assert msgs[0]["content"] == RAG_STREAM_SYSTEM
    assert msgs[1]["role"] == "user"
    assert msgs[2]["role"] == "assistant"
    assert "当前问题：它提高了多少？" in msgs[3]["content"]
    assert "paper_a.pdf" in msgs[3]["content"]
    assert "章节=Methods" in msgs[3]["content"]
    assert "覆盖的文献文件" in msgs[3]["content"]


def test_stream_messages_label_memory_as_non_evidence():
    rows = [_chunk()]
    context = ConversationContext(
        retrieval_query="AlphaNet 使用什么数据集？",
        enabled=True,
        query_rewritten=True,
        summary="此前讨论 AlphaNet。",
        recalled=(
            RecalledMemory(
                id="m1",
                content="用户曾追问 AlphaNet 的训练设置。",
                score=0.9,
                source_start_index=1,
                source_end_index=4,
            ),
        ),
    )
    msgs = build_rag_stream_messages(
        "它用了什么数据集？",
        rows,
        history=[{"role": "user", "content": "继续前面的问题"}],
        conversation_context=context,
    )
    memory_message = msgs[1]
    assert memory_message["role"] == "system"
    assert "不属于论文证据" in memory_message["content"]
    assert "AlphaNet" in memory_message["content"]


def test_context_budget_preserves_each_document_entry():
    rows = [
        _chunk(
            chunk_id=f"c{i}",
            document_id=f"d{i}",
            file_name=f"paper_{i}.pdf",
            text=("证据" * 1000),
            score=1.0 / i,
        )
        for i in range(1, 4)
    ]
    context = _format_context_grouped(
        rows,
        include_ids=True,
        max_chars=1800,
    )
    assert len(context) <= 1800
    for i in range(1, 4):
        assert f"paper_{i}.pdf" in context


def test_balanced_comparison_context_preserves_facets_for_every_document():
    rows: list[RetrievedChunk] = []
    for doc_index, score in ((1, 0.99), (2, 0.5), (3, 0.01)):
        for facet_index, facet in enumerate(("METHOD", "DATA", "RESULT")):
            rows.append(
                _chunk(
                    chunk_id=f"d{doc_index}-{facet.lower()}",
                    document_id=f"d{doc_index}",
                    file_name=f"paper_{doc_index}.pdf",
                    text=f"DOC{doc_index}_{facet} " + ("长片段" * 350),
                    chunk_index=facet_index,
                    score=score,
                    section_path=facet.title(),
                )
            )

    context = _format_context_grouped(
        rows,
        include_ids=False,
        max_chars=3600,
        balance_documents=True,
    )

    assert len(context) <= 3600
    for doc_index in range(1, 4):
        assert f"paper_{doc_index}.pdf" in context
        for facet in ("METHOD", "DATA", "RESULT"):
            assert f"DOC{doc_index}_{facet}" in context


def test_history_budget_keeps_newest_turns():
    history = [
        {"role": "user", "content": "旧问题" * 100},
        {"role": "assistant", "content": "旧回答" * 100},
        {"role": "user", "content": "最新问题"},
    ]
    selected = budget_history(history, 30)
    assert selected[-1]["content"] == "最新问题"
    assert sum(len(turn["content"]) for turn in selected) <= 30


def test_history_budget_does_not_orphan_assistant_from_user():
    history = [
        {"role": "user", "content": "旧问题" * 100},
        {"role": "assistant", "content": "旧回答" * 100},
    ]
    selected = budget_history(history, 80)
    assert [turn["role"] for turn in selected] == ["user", "assistant"]
    assert sum(len(turn["content"]) for turn in selected) <= 80
