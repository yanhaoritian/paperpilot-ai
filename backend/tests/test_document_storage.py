from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import document_storage


def _document(*, file_path: str):
    return SimpleNamespace(
        id="doc-1",
        owner_id="owner-1",
        library_id="library-1",
        file_path=file_path,
    )


def test_resolves_portable_document_path(tmp_path, monkeypatch):
    root = tmp_path / "pdfs"
    target = root / "owner-1" / "library-1" / "doc-1.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        document_storage,
        "get_settings",
        lambda: SimpleNamespace(pdf_storage_dir=str(root)),
    )

    resolved = document_storage.resolve_document_path(
        _document(file_path="owner-1/library-1/doc-1.pdf")
    )
    assert resolved == target.resolve()


def test_resolves_legacy_windows_path_inside_container_root(tmp_path, monkeypatch):
    root = tmp_path / "pdfs"
    target = root / "owner-1" / "library-1" / "doc-1.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        document_storage,
        "get_settings",
        lambda: SimpleNamespace(pdf_storage_dir=str(root)),
    )

    resolved = document_storage.resolve_document_path(
        _document(
            file_path=(
                r"C:\old-host\PaperPilot\data\pdfs"
                r"\owner-1\library-1\doc-1.pdf"
            )
        )
    )
    assert resolved == target.resolve()


def test_path_traversal_falls_back_to_canonical_location(tmp_path, monkeypatch):
    root = tmp_path / "pdfs"
    monkeypatch.setattr(
        document_storage,
        "get_settings",
        lambda: SimpleNamespace(pdf_storage_dir=str(root)),
    )

    resolved = document_storage.resolve_document_path(
        _document(file_path="../../outside.pdf")
    )
    assert resolved == (root / "owner-1" / "library-1" / "doc-1.pdf").resolve()
    assert resolved.is_relative_to(root.resolve())


def test_remove_storage_file_prunes_empty_tenant_directories(tmp_path, monkeypatch):
    root = tmp_path / "pdfs"
    target = root / "owner-1" / "library-1" / "doc-1.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        document_storage,
        "get_settings",
        lambda: SimpleNamespace(pdf_storage_dir=str(root)),
    )

    assert document_storage.remove_storage_file(target) is True
    assert not target.exists()
    assert not (root / "owner-1").exists()
    assert root.is_dir()


def test_remove_storage_file_rejects_outside_path(tmp_path, monkeypatch):
    root = tmp_path / "pdfs"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        document_storage,
        "get_settings",
        lambda: SimpleNamespace(pdf_storage_dir=str(root)),
    )

    with pytest.raises(ValueError, match="outside PDF storage"):
        document_storage.remove_storage_file(outside)
    assert outside.is_file()
