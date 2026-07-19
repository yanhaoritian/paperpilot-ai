from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Document, DocumentStatus, Library, User
from app.security import hash_password
from app.services.indexing import index_document
from app.services.retrieve import retrieve_chunks

EVALS_DIR = Path(__file__).resolve().parent
FIXTURES = EVALS_DIR / "fixtures"
GOLD_PATH = EVALS_DIR / "gold.json"

MIN_RECALL_AT_K = 0.8
MIN_MRR = 0.7
TOP_K = 6


@dataclass
class EvalCaseResult:
    case_id: str
    hit: bool
    rank: int | None
    retrieved_docs: list[str]


@dataclass
class EvalSummary:
    recall_at_k: float
    mrr: float
    results: list[EvalCaseResult]

    @property
    def passed(self) -> bool:
        return self.recall_at_k >= MIN_RECALL_AT_K and self.mrr >= MIN_MRR


def _load_gold() -> list[dict]:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def _setup_eval_corpus(db: Session, pdf_dir: Path) -> tuple[str, str]:
    user = User(
        username="eval_user",
        email="eval@example.com",
        password_hash=hash_password("eval-secret"),
    )
    db.add(user)
    db.flush()
    lib = Library(owner_id=user.id, name="eval_lib", description="retrieval eval")
    db.add(lib)
    db.flush()

    for pdf in sorted(FIXTURES.glob("*.pdf")):
        data = pdf.read_bytes()
        dest = pdf_dir / f"{pdf.stem}.pdf"
        dest.write_bytes(data)
        doc = Document(
            library_id=lib.id,
            owner_id=user.id,
            file_name=pdf.name,
            file_path=str(dest),
            file_hash=hashlib.sha256(data).hexdigest(),
            status=DocumentStatus.pending.value,
        )
        db.add(doc)
        db.commit()
        index_document(db, doc.id)
        refreshed = db.get(Document, doc.id)
        if not refreshed or refreshed.status != DocumentStatus.ready.value:
            detail = refreshed.status_detail if refreshed else "missing"
            raise RuntimeError(f"Failed to index {pdf.name}: {detail}")

    return user.id, lib.id


def run_retrieval_eval(*, top_k: int = TOP_K) -> EvalSummary:
    """Index fixtures into a temp SQLite DB and score gold questions."""
    settings = get_settings()
    settings.embedding_provider = "local"
    settings.ocr_enabled = False
    settings.index_recover_on_startup = False
    settings.jwt_require_strong = False
    settings.rag_min_similarity = 0.05
    settings.contextual_chunk_enabled = False
    settings.hybrid_recall_enabled = True
    settings.rerank_provider = "off"
    settings.agent_enabled = False
    settings.vision_enabled = False

    gold = _load_gold()
    tmp = tempfile.mkdtemp(prefix="pp_eval_")
    tmp_path = Path(tmp)
    db_path = tmp_path / "eval.db"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()

    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_fk(dbapi_conn, _connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    from app.db import Base
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Point indexing/retrieve helpers at this engine via app.db.SessionLocal
    import app.db as db_mod

    prev_engine = db_mod.engine
    prev_session = db_mod.SessionLocal
    db_mod.engine = engine
    db_mod.SessionLocal = SessionFactory

    db = SessionFactory()
    try:
        owner_id, library_id = _setup_eval_corpus(db, pdf_dir)
        results: list[EvalCaseResult] = []
        hits = 0
        rr_sum = 0.0
        for case in gold:
            retrieved = retrieve_chunks(
                db,
                owner_id=owner_id,
                library_ids=[library_id],
                question=case["question"],
                top_k=top_k,
                min_similarity=0.05,
            )
            names = [r.file_name for r in retrieved]
            expect = set(case.get("expect_doc_names") or [])
            rank = None
            for i, name in enumerate(names, start=1):
                if name in expect:
                    rank = i
                    break
            hit = rank is not None
            if hit:
                hits += 1
                rr_sum += 1.0 / float(rank)
            results.append(
                EvalCaseResult(
                    case_id=str(case["id"]),
                    hit=hit,
                    rank=rank,
                    retrieved_docs=names,
                )
            )
        n = max(1, len(gold))
        return EvalSummary(recall_at_k=hits / n, mrr=rr_sum / n, results=results)
    finally:
        db.close()
        engine.dispose()
        db_mod.engine = prev_engine
        db_mod.SessionLocal = prev_session
        try:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    summary = run_retrieval_eval()
    print(f"Recall@{TOP_K}={summary.recall_at_k:.3f}  MRR={summary.mrr:.3f}")
    for r in summary.results:
        print(f"  [{r.case_id}] hit={r.hit} rank={r.rank} docs={r.retrieved_docs}")
    if not summary.passed:
        raise SystemExit(f"FAIL thresholds Recall>={MIN_RECALL_AT_K} MRR>={MIN_MRR}")
    print("PASS")


if __name__ == "__main__":
    main()
