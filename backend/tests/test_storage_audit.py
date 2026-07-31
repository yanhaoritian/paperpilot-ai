from __future__ import annotations

import hashlib
from types import SimpleNamespace

from scripts import audit_storage


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def scalars(self, _query):
        return _ScalarResult(self._rows)

    def close(self):
        self.closed = True


def test_storage_audit_reports_unreferenced_pdfs(tmp_path, monkeypatch):
    root = tmp_path / "pdfs"
    referenced = root / "owner-1" / "library-1" / "doc-1.pdf"
    orphan = root / "owner-old" / "library-old" / "orphan.pdf"
    referenced.parent.mkdir(parents=True)
    orphan.parent.mkdir(parents=True)
    payload = b"%PDF-1.4\n%%EOF"
    referenced.write_bytes(payload)
    orphan.write_bytes(payload)
    document = SimpleNamespace(
        id="doc-1",
        owner_id="owner-1",
        library_id="library-1",
        file_path="owner-1/library-1/doc-1.pdf",
        file_hash=hashlib.sha256(payload).hexdigest(),
        created_at=None,
    )
    session = _FakeSession([document])

    monkeypatch.setattr(audit_storage, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        audit_storage,
        "get_settings",
        lambda: SimpleNamespace(pdf_storage_dir=str(root)),
    )
    monkeypatch.setattr(
        audit_storage,
        "resolve_document_path",
        lambda _document: referenced.resolve(),
    )

    without_orphans = audit_storage.audit_storage(
        verify_hash=True,
        check_orphans=False,
    )
    assert without_orphans["ok"] is True
    assert without_orphans["orphan_files"] == []

    with_orphans = audit_storage.audit_storage(
        verify_hash=True,
        check_orphans=True,
    )
    assert with_orphans["ok"] is False
    assert with_orphans["orphan_files"] == [
        "owner-old/library-old/orphan.pdf"
    ]
    assert session.closed is True
