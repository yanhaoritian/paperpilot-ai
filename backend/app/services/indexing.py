from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Block, Chunk, Document, DocumentStatus
from app.services.acl import collection_key
from app.services.doc_context import build_context_snapshot
from app.services.document_storage import resolve_document_path
from app.services.index_jobs import (
    mark_job_done,
    mark_job_failed,
    mark_job_retrying,
    mark_job_running,
)
from app.services.openai_client import embed_texts
from app.services.pdf_parse import parse_pdf_pages
from app.services.response_cache import query_cache
from app.services.semantic_chunk import build_contextual_prefixes, chunks_from_blocks
from app.services.structure import restore_structure

logger = logging.getLogger(__name__)


def index_document(db: Session, document_id: str) -> None:
    settings = get_settings()
    doc = db.get(Document, document_id)
    if not doc:
        return

    mark_job_running(db, document_id)
    doc.index_attempts = int(doc.index_attempts or 0) + 1
    doc.status = DocumentStatus.processing.value
    doc.status_detail = None
    db.commit()

    try:
        path = resolve_document_path(doc)
        data = path.read_bytes()
        parse = parse_pdf_pages(data)
        if not parse.full_text.strip():
            doc.status = DocumentStatus.failed.value
            doc.status_detail = "EMPTY_TEXT"
            doc.page_count = parse.page_count
            db.commit()
            mark_job_failed(db, document_id, "EMPTY_TEXT")
            query_cache().invalidate_owner(str(doc.owner_id))
            return

        blocks = restore_structure(parse, file_name=doc.file_name)
        chunks = chunks_from_blocks(blocks)
        if not chunks:
            doc.status = DocumentStatus.failed.value
            doc.status_detail = "NO_CHUNKS"
            doc.page_count = parse.page_count
            db.commit()
            mark_job_failed(db, document_id, "NO_CHUNKS")
            query_cache().invalidate_owner(str(doc.owner_id))
            return

        prefixes = build_contextual_prefixes(
            chunks,
            file_name=doc.file_name,
            use_llm=settings.contextual_chunk_enabled,
        )
        embed_inputs = [
            f"{pref}{c['text']}" if pref else c["text"]
            for pref, c in zip(prefixes, chunks, strict=True)
        ]
        embeddings = embed_texts(embed_inputs)
        coll = collection_key(str(doc.owner_id), str(doc.library_id))
        emb_model = settings.embedding_model
        emb_ver = settings.embedding_version

        db.query(Chunk).filter(Chunk.document_id == doc.id).delete()
        db.query(Block).filter(Block.document_id == doc.id).delete()
        db.commit()

        for b in blocks:
            db.add(
                Block(
                    id=b.block_id,
                    document_id=doc.id,
                    library_id=doc.library_id,
                    owner_id=doc.owner_id,
                    role=b.role,
                    text=b.text,
                    page_start=b.page_start,
                    page_end=b.page_end,
                    section_path=b.section_path or None,
                    bbox=b.bbox,
                    extra=b.extra or None,
                )
            )

        for meta, vector, pref in zip(chunks, embeddings, prefixes, strict=True):
            db.add(
                Chunk(
                    document_id=doc.id,
                    library_id=doc.library_id,
                    owner_id=doc.owner_id,
                    chunk_index=int(meta["paragraph_index"]),
                    text=meta["text"],
                    page_start=meta.get("page_start"),
                    page_end=meta.get("page_end"),
                    section_path=meta.get("section_path"),
                    role=meta.get("role"),
                    context_prefix=pref,
                    block_ids=meta.get("block_ids"),
                    extra=meta.get("extra"),
                    collection_key=coll,
                    embedding_model=emb_model,
                    embedding_version=emb_ver,
                    embedding=vector,
                )
            )

        routes = parse.routes_used
        detail_parts = [f"routes={routes}", f"emb={emb_model}@{emb_ver}"]
        if parse.ocr_used:
            detail_parts.append("ocr_used=1")
        if parse.vision_used:
            detail_parts.append("vision_used=1")
        partial = False
        if parse.empty_pages:
            detail_parts.append(f"empty_pages={parse.empty_pages}")
            partial = True
        if parse.empty_pages_after_ocr_cap:
            detail_parts.append(f"empty_after_ocr_cap={parse.empty_pages_after_ocr_cap}")
            partial = True
        if len(chunks) >= settings.max_chunks:
            detail_parts.append(f"chunk_cap_reached={settings.max_chunks}")
            partial = True
        if partial:
            detail_parts.append("partial_index=1")
        doc.context_snapshot = build_context_snapshot(
            file_name=doc.file_name,
            page_count=parse.page_count,
            blocks=blocks,
        )
        doc.embedding_model = emb_model
        doc.embedding_version = emb_ver
        doc.status = DocumentStatus.ready.value
        doc.status_detail = ";".join(detail_parts)
        doc.page_count = parse.page_count
        db.commit()
        mark_job_done(db, document_id)
        query_cache().invalidate_owner(str(doc.owner_id))
        logger.info(
            "indexed document %s chunks=%s blocks=%s attempts=%s routes=%s emb=%s@%s",
            doc.id,
            len(chunks),
            len(blocks),
            doc.index_attempts,
            routes,
            emb_model,
            emb_ver,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("index failed for %s", document_id)
        db.rollback()
        doc = db.get(Document, document_id)
        if doc:
            detail = str(exc)[:500]
            if doc.index_attempts < settings.index_max_attempts:
                doc.status = DocumentStatus.pending.value
                doc.status_detail = f"retryable:{detail}"
            else:
                doc.status = DocumentStatus.failed.value
                doc.status_detail = detail
            db.commit()
            if doc.status == DocumentStatus.pending.value:
                mark_job_retrying(db, document_id, detail)
            else:
                mark_job_failed(db, document_id, detail)
            query_cache().invalidate_owner(str(doc.owner_id))


def index_document_with_retries(db: Session, document_id: str) -> None:
    """Run one durable job until it succeeds or reaches its configured attempt cap."""
    settings = get_settings()
    while True:
        index_document(db, document_id)
        db.expire_all()
        doc = db.get(Document, document_id)
        if not doc:
            return
        attempts = int(doc.index_attempts or 0)
        if doc.status != DocumentStatus.pending.value or attempts >= settings.index_max_attempts:
            return
        delay = min(8.0, float(2 ** max(0, attempts - 1)))
        logger.warning(
            "retrying document %s attempt=%s/%s in %.1fs",
            document_id,
            attempts + 1,
            settings.index_max_attempts,
            delay,
        )
        time.sleep(delay)
