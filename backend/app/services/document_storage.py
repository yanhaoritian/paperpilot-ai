from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings


def storage_root() -> Path:
    return Path(get_settings().pdf_storage_dir).resolve()


def portable_document_path(
    *,
    owner_id: str,
    library_id: str,
    document_id: str,
) -> Path:
    return Path(owner_id) / library_id / f"{document_id}.pdf"


def _under_root(root: Path, relative: str) -> Path | None:
    normalized = relative.replace("\\", "/").lstrip("/")
    if not normalized:
        return None
    candidate = (root / Path(normalized)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def resolve_document_path(document: Any) -> Path:
    """Resolve portable and legacy absolute paths inside the configured PDF root."""
    root = storage_root()
    raw_text = str(getattr(document, "file_path", "") or "").strip()
    candidates: list[Path] = []

    if raw_text:
        raw_native = Path(raw_text)
        if raw_native.is_absolute():
            try:
                raw_native.resolve().relative_to(root)
                candidates.append(raw_native.resolve())
            except ValueError:
                pass

        normalized = raw_text.replace("\\", "/")
        marker = "/data/pdfs/"
        marker_index = normalized.lower().rfind(marker)
        if marker_index >= 0:
            legacy_relative = normalized[marker_index + len(marker) :]
            legacy_candidate = _under_root(root, legacy_relative)
            if legacy_candidate is not None:
                candidates.append(legacy_candidate)
        elif not raw_native.is_absolute() and not (
            len(normalized) >= 2 and normalized[1] == ":"
        ):
            portable_candidate = _under_root(root, normalized)
            if portable_candidate is not None:
                candidates.append(portable_candidate)

    canonical = _under_root(
        root,
        portable_document_path(
            owner_id=str(document.owner_id),
            library_id=str(document.library_id),
            document_id=str(document.id),
        ).as_posix(),
    )
    if canonical is None:
        raise ValueError("invalid document storage identifiers")
    candidates.append(canonical)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return canonical


def remove_storage_file(path: Path) -> bool:
    """Delete one PDF inside the configured root and prune empty tenant folders."""
    root = storage_root()
    candidate = path.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("refusing to delete a file outside PDF storage") from exc

    if not candidate.is_file():
        return False
    candidate.unlink()

    parent = candidate.parent
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return True
