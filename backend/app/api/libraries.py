import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import Chunk, Document, Library, User
from app.schemas import LibraryCreate, LibraryOut, LibraryUpdate
from app.services.document_storage import remove_storage_file, resolve_document_path
from app.services.response_cache import query_cache

router = APIRouter(prefix="/api/libraries", tags=["libraries"])
logger = logging.getLogger(__name__)


def _library_or_404(db: Session, library_id: str, owner_id: str) -> Library:
    lib = db.scalar(select(Library).where(Library.id == library_id, Library.owner_id == owner_id))
    if not lib:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    return lib


def _to_out(db: Session, lib: Library) -> LibraryOut:
    count = db.scalar(select(func.count()).select_from(Document).where(Document.library_id == lib.id)) or 0
    return LibraryOut(
        id=lib.id,
        name=lib.name,
        description=lib.description,
        created_at=lib.created_at,
        document_count=int(count),
    )


@router.get("", response_model=list[LibraryOut])
def list_libraries(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[LibraryOut]:
    libs = db.scalars(select(Library).where(Library.owner_id == user.id).order_by(Library.created_at.desc())).all()
    return [_to_out(db, lib) for lib in libs]


@router.post("", response_model=LibraryOut, status_code=status.HTTP_201_CREATED)
def create_library(
    body: LibraryCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LibraryOut:
    lib = Library(owner_id=user.id, name=body.name.strip(), description=body.description)
    db.add(lib)
    db.commit()
    db.refresh(lib)
    query_cache().invalidate_owner(user.id)
    return _to_out(db, lib)


@router.patch("/{library_id}", response_model=LibraryOut)
def update_library(
    library_id: str,
    body: LibraryUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LibraryOut:
    lib = _library_or_404(db, library_id, user.id)
    if body.name is not None:
        lib.name = body.name.strip()
    if body.description is not None:
        lib.description = body.description
    db.commit()
    db.refresh(lib)
    query_cache().invalidate_owner(user.id)
    return _to_out(db, lib)


@router.delete("/{library_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_library(
    library_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    lib = _library_or_404(db, library_id, user.id)
    docs = db.scalars(select(Document).where(Document.library_id == lib.id)).all()
    document_paths = [resolve_document_path(doc) for doc in docs]
    db.query(Chunk).filter(Chunk.library_id == lib.id).delete()
    db.query(Document).filter(Document.library_id == lib.id).delete()
    db.delete(lib)
    db.commit()
    query_cache().invalidate_owner(user.id)
    for path in document_paths:
        try:
            remove_storage_file(path)
        except OSError:
            logger.exception(
                "failed to remove PDF after library deletion library=%s path=%s",
                library_id,
                path,
            )
