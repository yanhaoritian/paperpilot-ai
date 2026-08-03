from __future__ import annotations

from types import SimpleNamespace

from app.models import Document
from app.services.doc_context import (
    SNAPSHOT_SCHEMA_VERSION,
    build_context_snapshot,
    ensure_document_snapshots,
    format_document_context_cards,
)


def _block(
    role: str,
    text: str,
    page: int,
    section: str | None = None,
):
    return SimpleNamespace(
        role=role,
        text=text,
        page_start=page,
        page_end=page,
        section_path=section,
    )


def _document(doc_id: str, name: str) -> Document:
    return Document(
        id=doc_id,
        library_id="library-1",
        owner_id="owner-1",
        file_name=name,
        file_path=f"/pdfs/{name}",
        file_hash=f"hash-{doc_id}",
        status="ready",
        page_count=12,
    )


def test_snapshot_selects_substantive_bilingual_facets_with_provenance():
    blocks = [
        _block("title", "A Facet-Aware Research Paper", 1),
        *[
            _block("section_heading", f"{index}. Decorative Section Heading", index)
            for index in range(1, 15)
        ],
        _block(
            "paragraph",
            "We propose a graph-based methodology and optimization algorithm for robust prediction.",
            3,
            "Methods / Model Architecture",
        ),
        _block(
            "paragraph",
            "实验使用三个公开数据集，共包含 12,480 个样本，并划分训练集和测试集。",
            5,
            "数据与样本",
        ),
        _block(
            "paragraph",
            "The results show an accuracy improvement of 8.4%, with higher F1 and recall than baselines.",
            8,
            "Experiments and Results",
        ),
        _block(
            "paragraph",
            "本研究的局限在于样本来自单一中心，未来工作需要开展跨区域外部验证。",
            11,
            "讨论与局限",
        ),
    ]

    snapshot = build_context_snapshot(
        file_name="facets.pdf",
        page_count=12,
        blocks=blocks,
    )

    assert snapshot["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert {"method", "data", "metrics", "results", "limitations"} <= set(
        snapshot["facet_coverage"]
    )
    assert snapshot["evidence_snippets"]
    assert all(item["page_start"] for item in snapshot["evidence_snippets"])
    assert any(item["section_path"] == "讨论与局限" for item in snapshot["evidence_snippets"])
    assert "12,480" in snapshot["preview"]
    assert "Decorative Section Heading" not in snapshot["preview"]


def test_legacy_snapshot_is_rebuilt_and_persisted():
    document = _document("doc-legacy", "legacy.pdf")
    document.context_snapshot = {
        "file_name": "legacy.pdf",
        "preview": "old title-heavy preview",
        "sections": ["Introduction"],
    }
    blocks = [
        _block("title", "Legacy Paper", 1),
        _block(
            "paragraph",
            "The method uses a calibrated model and the results improve accuracy on the benchmark dataset.",
            4,
            "Method and Results",
        ),
    ]

    class _Rows:
        def __init__(self, values):
            self._values = values

        def all(self):
            return list(self._values)

    class _Session:
        def __init__(self):
            self.calls = 0
            self.commits = 0

        def scalars(self, _statement):
            self.calls += 1
            return _Rows([document] if self.calls == 1 else blocks)

        def commit(self):
            self.commits += 1

    db = _Session()
    pairs = ensure_document_snapshots(
        db,
        owner_id="owner-1",
        library_ids=["library-1"],
    )

    rebuilt = pairs[0][1]
    assert rebuilt["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert rebuilt["preview"] != "old title-heavy preview"
    assert document.context_snapshot == rebuilt
    assert db.commits == 1


def test_legacy_snapshot_falls_back_to_chunks_when_blocks_are_absent():
    document = _document("doc-chunks", "chunks-only.pdf")
    document.context_snapshot = {
        "preview": "legacy preview",
        "sections": ["Introduction"],
    }
    chunks = [
        _block(
            "paragraph",
            "Methods use a calibrated inversion algorithm on a dataset of 4,200 "
            "samples; results improve accuracy by 9%.",
            4,
            "Methods and Results",
        )
    ]

    class _Rows:
        def __init__(self, values):
            self._values = values

        def all(self):
            return list(self._values)

    class _Session:
        def __init__(self):
            self.values = [[document], [], chunks]
            self.commits = 0

        def scalars(self, _statement):
            return _Rows(self.values.pop(0))

        def commit(self):
            self.commits += 1

    db = _Session()
    rebuilt = ensure_document_snapshots(
        db,
        owner_id="owner-1",
        library_ids=["library-1"],
    )[0][1]

    assert rebuilt["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert rebuilt["evidence_snippets"]
    assert "4,200" in rebuilt["preview"]
    assert rebuilt["preview"] != "legacy preview"
    assert db.commits == 1


def test_sparse_legacy_snapshot_keeps_useful_preview():
    document = _document("doc-sparse", "sparse.pdf")
    document.context_snapshot = {
        "preview": "可用的旧版摘要内容",
        "sections": ["研究背景"],
    }

    class _Rows:
        def __init__(self, values):
            self._values = values

        def all(self):
            return list(self._values)

    class _Session:
        def __init__(self):
            self.values = [[document], [], []]

        def scalars(self, _statement):
            return _Rows(self.values.pop(0))

        def commit(self):
            pass

    rebuilt = ensure_document_snapshots(
        _Session(),
        owner_id="owner-1",
        library_ids=["library-1"],
    )[0][1]

    assert rebuilt["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert rebuilt["preview"] == "可用的旧版摘要内容"
    assert rebuilt["sections"] == ["研究背景"]
    assert rebuilt["legacy_preview_preserved"] is True


def test_card_formatting_keeps_late_documents_with_equal_compact_budget():
    pairs = []
    for index in range(1, 7):
        document = _document(f"doc-{index}", f"paper-{index}.pdf")
        marker = f"EVIDENCE_MARKER_{index}"
        snapshot = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "page_count": 12,
            "sections": ["Methods", "Results", "Limitations"],
            "preview": marker,
            "evidence_snippets": [
                {
                    "facets": ["method"],
                    "text": marker + " " + ("substantive method evidence " * 30),
                    "page_start": index,
                    "page_end": index,
                    "section_path": "Methods",
                    "role": "paragraph",
                }
            ],
        }
        pairs.append((document, snapshot))

    text = format_document_context_cards(pairs, max_chars=2_400)

    assert len(text) <= 2_400
    for index in range(1, 7):
        assert f"paper-{index}.pdf" in text
        assert f"EVIDENCE_MARKER_{index}" in text
    assert text.count("### 卡片") == 6
