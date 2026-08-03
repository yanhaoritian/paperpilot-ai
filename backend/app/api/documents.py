import hashlib
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    Chunk,
    Document,
    DocumentStatus,
    IndexJob,
    IndexJobStatus,
    Library,
    User,
)
from app.schemas import DocumentOut
from app.services.document_storage import (
    portable_document_path,
    remove_storage_file,
    resolve_document_path,
    storage_root,
)
from app.services.index_jobs import enqueue_index_job
from app.services.index_worker import schedule_index
from app.services.quotas import consume_upload_quota
from app.services.response_cache import query_cache

router = APIRouter(tags=["documents"])
settings = get_settings()
logger = logging.getLogger(__name__)


def _library_or_404(db: Session, library_id: str, owner_id: str) -> Library:
    lib = db.scalar(select(Library).where(Library.id == library_id, Library.owner_id == owner_id))
    if not lib:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    return lib


def _doc_or_404(
    db: Session,
    document_id: str,
    owner_id: str,
    *,
    lock: bool = False,
) -> Document:
    statement = select(Document).where(
        Document.id == document_id,
        Document.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    doc = db.scalar(statement)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return doc


def _active_index_job(db: Session, document_id: str) -> IndexJob | None:
    statement = (
        select(IndexJob)
        .where(
            IndexJob.document_id == document_id,
            IndexJob.status.in_(
                [IndexJobStatus.pending.value, IndexJobStatus.running.value]
            ),
        )
        .order_by(IndexJob.created_at.desc())
    )
    if not settings.is_sqlite:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _indexed_chunk_count(db: Session, document_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.document_id == document_id)
        )
        or 0
    )


def _ensure_existing_document_index(db: Session, doc: Document) -> str:
    """Return ready/active/queued while repairing a duplicate-upload shell."""
    if (
        doc.status == DocumentStatus.ready.value
        and int(doc.page_count or 0) > 0
        and _indexed_chunk_count(db, str(doc.id)) > 0
    ):
        return "ready"

    if _active_index_job(db, str(doc.id)) is not None:
        return "active"

    doc.status = DocumentStatus.pending.value
    doc.status_detail = "queued_upload_repair"
    doc.index_attempts = 0
    enqueue_index_job(
        db,
        document_id=doc.id,
        owner_id=doc.owner_id,
        commit=False,
    )
    return "queued"


@router.post(
    "/api/libraries/{library_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    library_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    _library_or_404(db, library_id, user.id)
    if settings.index_external_worker and not settings.index_job_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="索引队列未启用，暂时无法上传文档",
        )
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    max_bytes = settings.pdf_max_upload_mb * 1024 * 1024
    upload_dir = storage_root() / ".uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_dir / f"{uuid4().hex}.part"
    digest = hashlib.sha256()
    header = bytearray()
    total_bytes = 0
    try:
        with temp_path.open("xb") as handle:
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"文件超过上限 {settings.pdf_max_upload_mb} MB",
                    )
                if len(header) < 1024:
                    header.extend(chunk[: 1024 - len(header)])
                digest.update(chunk)
                handle.write(chunk)

        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="空文件")
        if not bytes(header).lstrip().startswith(b"%PDF-"):
            raise HTTPException(status_code=400, detail="文件内容不是有效的 PDF")

        file_hash = digest.hexdigest()
        existing_statement = select(Document).where(
            Document.library_id == library_id,
            Document.file_hash == file_hash,
        )
        if not settings.is_sqlite:
            existing_statement = existing_statement.with_for_update()
        existing = db.scalar(existing_statement)
        if existing:
            restored_file = False
            existing_path = resolve_document_path(existing)
            if not existing_path.is_file():
                relative_path = portable_document_path(
                    owner_id=user.id,
                    library_id=library_id,
                    document_id=str(existing.id),
                )
                existing_path = storage_root() / relative_path
                existing_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path.replace(existing_path)
                existing.file_path = relative_path.as_posix()
                restored_file = True

            index_state = _ensure_existing_document_index(db, existing)
            if restored_file or index_state == "queued":
                db.commit()
                db.refresh(existing)
                query_cache().invalidate_owner(user.id)
            if index_state != "ready" and not settings.index_external_worker:
                schedule_index(existing.id, background_tasks)
            return existing

        consume_upload_quota(db, user.id)

        doc = Document(
            library_id=library_id,
            owner_id=user.id,
            file_name=Path(file.filename).name[:512],
            file_path="",  # filled below
            file_hash=file_hash,
            status=DocumentStatus.pending.value,
            status_detail="queued_upload",
        )
        db.add(doc)
        dest: Path | None = None
        try:
            db.flush()
            relative_path = portable_document_path(
                owner_id=user.id,
                library_id=library_id,
                document_id=str(doc.id),
            )
            dest = storage_root() / relative_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            temp_path.replace(dest)
            doc.file_path = relative_path.as_posix()
            enqueue_index_job(
                db,
                document_id=doc.id,
                owner_id=user.id,
                commit=False,
            )
            db.commit()
        except Exception:
            db.rollback()
            if dest is not None:
                try:
                    remove_storage_file(dest)
                except OSError:
                    logger.exception(
                        "failed to roll back PDF after upload failure path=%s",
                        dest,
                    )
            raise
        db.refresh(doc)

        if not settings.index_external_worker:
            schedule_index(doc.id, background_tasks)
        query_cache().invalidate_owner(user.id)
        return doc
    finally:
        if temp_path.is_file():
            try:
                remove_storage_file(temp_path)
            except OSError:
                logger.exception(
                    "failed to remove temporary PDF upload path=%s",
                    temp_path,
                )


@router.get("/api/libraries/{library_id}/documents", response_model=list[DocumentOut])
def list_documents(
    library_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Document]:
    _library_or_404(db, library_id, user.id)
    return list(
        db.scalars(
            select(Document)
            .where(Document.library_id == library_id, Document.owner_id == user.id)
            .order_by(Document.created_at.desc())
        ).all()
    )


@router.get("/api/documents/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    return _doc_or_404(db, document_id, user.id)


@router.get("/api/documents/{document_id}/file", response_class=FileResponse)
def get_document_file(
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Return the original PDF after the same ownership check as document metadata."""
    doc = _doc_or_404(db, document_id, user.id)
    path = resolve_document_path(doc)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="原始 PDF 文件不存在")
    safe_name = (
        Path(doc.file_name.replace("\\", "/"))
        .name.replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
    )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=safe_name,
        content_disposition_type="inline",
    )


@router.post("/api/documents/{document_id}/reindex", response_model=DocumentOut)
def reindex_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    doc = _doc_or_404(db, document_id, user.id, lock=True)
    active_job = _active_index_job(db, document_id)
    if active_job is not None:
        if not settings.index_external_worker:
            schedule_index(doc.id, background_tasks)
        return doc

    doc.status = DocumentStatus.pending.value
    doc.status_detail = "queued_reindex"
    doc.index_attempts = 0
    enqueue_index_job(
        db,
        document_id=doc.id,
        owner_id=user.id,
        commit=False,
    )
    db.commit()
    db.refresh(doc)
    if not settings.index_external_worker:
        schedule_index(doc.id, background_tasks)
    query_cache().invalidate_owner(user.id)
    return doc


@router.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    doc = _doc_or_404(db, document_id, user.id)
    path = resolve_document_path(doc)
    db.query(Chunk).filter(Chunk.document_id == doc.id).delete()
    db.delete(doc)
    db.commit()
    query_cache().invalidate_owner(user.id)
    if path.is_file():
        try:
            remove_storage_file(path)
        except OSError:
            logger.exception(
                "failed to remove PDF after document deletion document=%s path=%s",
                document_id,
                path,
            )
