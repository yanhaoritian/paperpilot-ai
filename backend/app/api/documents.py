import hashlib
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Chunk, Document, DocumentStatus, Library, User
from app.schemas import DocumentOut
from app.services.index_jobs import enqueue_index_job
from app.services.index_worker import schedule_index
from app.services.quotas import consume_upload_quota

router = APIRouter(tags=["documents"])
settings = get_settings()


def _library_or_404(db: Session, library_id: str, owner_id: str) -> Library:
    lib = db.scalar(select(Library).where(Library.id == library_id, Library.owner_id == owner_id))
    if not lib:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    return lib


def _doc_or_404(db: Session, document_id: str, owner_id: str) -> Document:
    doc = db.scalar(select(Document).where(Document.id == document_id, Document.owner_id == owner_id))
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return doc


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
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    data = await file.read()
    max_bytes = settings.pdf_max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=400, detail=f"文件超过上限 {settings.pdf_max_upload_mb} MB")
    if not data:
        raise HTTPException(status_code=400, detail="空文件")

    file_hash = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(Document).where(Document.library_id == library_id, Document.file_hash == file_hash)
    )
    if existing:
        return existing

    consume_upload_quota(db, user.id)

    doc = Document(
        library_id=library_id,
        owner_id=user.id,
        file_name=Path(file.filename).name[:512],
        file_path="",  # filled below
        file_hash=file_hash,
        status=DocumentStatus.pending.value,
    )
    db.add(doc)
    db.flush()

    dest_dir = Path(settings.pdf_storage_dir) / user.id / library_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{doc.id}.pdf"
    dest.write_bytes(data)
    doc.file_path = str(dest)
    db.commit()
    db.refresh(doc)

    enqueue_index_job(db, document_id=doc.id, owner_id=user.id)
    schedule_index(doc.id, background_tasks)
    return doc


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


@router.post("/api/documents/{document_id}/reindex", response_model=DocumentOut)
def reindex_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    doc = _doc_or_404(db, document_id, user.id)
    doc.status = DocumentStatus.pending.value
    doc.status_detail = "queued_reindex"
    doc.index_attempts = 0
    db.commit()
    db.refresh(doc)
    enqueue_index_job(db, document_id=doc.id, owner_id=user.id)
    schedule_index(doc.id, background_tasks)
    return doc


@router.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    doc = _doc_or_404(db, document_id, user.id)
    path = Path(doc.file_path)
    db.query(Chunk).filter(Chunk.document_id == doc.id).delete()
    db.delete(doc)
    db.commit()
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
