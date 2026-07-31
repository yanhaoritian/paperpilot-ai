from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Document
from app.services.document_storage import resolve_document_path


def _sha256(path) -> str:  # noqa: ANN001
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit_storage(*, verify_hash: bool = False, check_orphans: bool = False) -> dict:
    db = SessionLocal()
    try:
        documents = list(
            db.scalars(select(Document).order_by(Document.created_at.asc())).all()
        )
        missing: list[str] = []
        invalid_pdf: list[str] = []
        hash_mismatch: list[str] = []
        referenced_paths: set[Path] = set()
        for document in documents:
            path = resolve_document_path(document)
            referenced_paths.add(path.resolve())
            if not path.is_file():
                missing.append(str(document.id))
                continue
            with path.open("rb") as handle:
                if not handle.read(1024).lstrip().startswith(b"%PDF-"):
                    invalid_pdf.append(str(document.id))
                    continue
            if verify_hash and _sha256(path) != str(document.file_hash):
                hash_mismatch.append(str(document.id))
        orphan_files: list[str] = []
        if check_orphans:
            root = Path(get_settings().pdf_storage_dir).resolve()
            for path in root.rglob("*.pdf") if root.is_dir() else []:
                resolved = path.resolve()
                try:
                    relative = resolved.relative_to(root)
                except ValueError:
                    continue
                if resolved.is_file() and resolved not in referenced_paths:
                    orphan_files.append(relative.as_posix())
            orphan_files.sort()
        return {
            "documents": len(documents),
            "files_present": len(documents) - len(missing),
            "missing_document_ids": missing,
            "invalid_pdf_document_ids": invalid_pdf,
            "hash_mismatch_document_ids": hash_mismatch,
            "orphan_files": orphan_files,
            "orphan_check_enabled": check_orphans,
            "ok": not (
                missing
                or invalid_pdf
                or hash_mismatch
                or (check_orphans and orphan_files)
            ),
            "hash_verified": verify_hash,
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only consistency audit for database document rows and stored PDFs."
    )
    parser.add_argument(
        "--verify-hash",
        action="store_true",
        help="also compare each file with the SHA-256 stored in the database",
    )
    parser.add_argument(
        "--check-orphans",
        action="store_true",
        help="also fail when PDF files exist without a matching database document",
    )
    args = parser.parse_args()
    result = audit_storage(
        verify_hash=args.verify_hash,
        check_orphans=args.check_orphans,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
