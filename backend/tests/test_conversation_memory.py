from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Conversation, ConversationMemory, Message, User
from app.services import conversation_memory


def _settings(**overrides):
    values = {
        "conversation_memory_enabled": True,
        "memory_query_rewrite_enabled": True,
        "memory_semantic_recall_enabled": True,
        "memory_background_compaction_enabled": True,
        "memory_model": "",
        "memory_summary_trigger_messages": 6,
        "memory_summary_batch_messages": 2,
        "memory_summary_max_chars": 2_000,
        "memory_episode_max_chars": 1_000,
        "memory_rewrite_history_max_chars": 1_000,
        "memory_recall_top_k": 3,
        "memory_recall_candidate_cap": 20,
        "memory_recall_max_chars": 1_000,
        "memory_recall_min_score": 0.1,
        "chat_history_turns": 2,
        "openai_api_key": "",
        "embedding_model": "test-embedding",
        "embedding_version": "test-v1",
        "is_sqlite": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'memory.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_conversation(local_session, *, message_count: int = 8):
    db = local_session()
    user = User(
        id="owner-1",
        username="memory-user",
        email="memory@example.com",
        password_hash="hash",
    )
    conversation = Conversation(
        id="conversation-1",
        owner_id=user.id,
        title="Memory",
        library_ids=["library-1"],
    )
    db.add_all([user, conversation])
    for sequence in range(1, message_count + 1):
        role = "user" if sequence % 2 else "assistant"
        db.add(
            Message(
                id=f"message-{sequence}",
                conversation_id=conversation.id,
                sequence=sequence,
                role=role,
                content=f"{role} content {sequence}",
            )
        )
    db.commit()
    db.close()


def test_rewrite_followup_into_standalone_query(monkeypatch):
    settings = _settings(openai_api_key="configured")
    monkeypatch.setattr(conversation_memory, "get_settings", lambda: settings)
    captured = {}

    def fake_chat_json(messages, **_kwargs):
        captured["messages"] = messages
        return {
            "resolved_subject": "AlphaNet",
            "search_query": "AlphaNet 方法使用了什么数据集？",
        }

    monkeypatch.setattr(conversation_memory, "chat_json", fake_chat_json)
    rewritten, changed = conversation_memory.rewrite_retrieval_query(
        "它用了什么数据集？",
        history=[
            {"role": "user", "content": "介绍 AlphaNet 方法"},
            {"role": "assistant", "content": "AlphaNet 是论文中的主要方法。"},
        ],
        summary="此前讨论 AlphaNet。",
    )
    assert changed is True
    assert rewritten == "AlphaNet 方法使用了什么数据集？"
    assert "不可信" in captured["messages"][0]["content"]


def test_rewrite_rejects_model_output_that_changes_question_intent(
    monkeypatch,
):
    settings = _settings(openai_api_key="configured")
    monkeypatch.setattr(conversation_memory, "get_settings", lambda: settings)
    monkeypatch.setattr(
        conversation_memory,
        "chat_json",
        lambda *_args, **_kwargs: {
            "resolved_subject": "AlphaNet",
            "search_query": "AlphaNet 是如何工作的？",
        },
    )
    rewritten, changed = conversation_memory.rewrite_retrieval_query(
        "它用了什么数据集？",
        history=[{"role": "user", "content": "介绍 AlphaNet 方法"}],
        summary="",
    )
    assert changed is True
    assert "AlphaNet" in rewritten
    assert "数据集" in rewritten


def test_rewrite_has_deterministic_fallback_without_chat_key(monkeypatch):
    settings = _settings(openai_api_key="")
    monkeypatch.setattr(conversation_memory, "get_settings", lambda: settings)
    rewritten, changed = conversation_memory.rewrite_retrieval_query(
        "它的局限是什么？",
        history=[{"role": "user", "content": "介绍 AlphaNet 方法"}],
        summary="",
    )
    assert changed is True
    assert "AlphaNet" in rewritten
    assert "它的局限" in rewritten


def test_compaction_creates_versioned_episode_without_losing_recent_turns(
    tmp_path,
    monkeypatch,
):
    local_session = _session_factory(tmp_path)
    _seed_conversation(local_session, message_count=8)
    settings = _settings()
    monkeypatch.setattr(conversation_memory, "SessionLocal", local_session)
    monkeypatch.setattr(conversation_memory, "get_settings", lambda: settings)
    monkeypatch.setattr(
        conversation_memory,
        "_summarize_episode",
        lambda **_kwargs: ("rolling summary", "AlphaNet episode", 0.7),
    )
    monkeypatch.setattr(
        conversation_memory,
        "embed_texts",
        lambda _texts, **_kwargs: [[1.0, 0.0]],
    )

    result = conversation_memory.refresh_conversation_memory(
        "conversation-1",
        "owner-1",
    )
    assert result["updated"] is True
    assert result["source_start_index"] == 1
    assert result["source_end_index"] == 2

    db = local_session()
    conversation = db.get(Conversation, "conversation-1")
    memories = list(
        db.scalars(
            select(ConversationMemory).where(
                ConversationMemory.conversation_id == "conversation-1"
            )
        ).all()
    )
    assert conversation.memory_summary == "rolling summary"
    assert conversation.summarized_message_count == 2
    assert conversation.memory_revision == 1
    assert len(memories) == 1
    assert memories[0].source_start_index == 1
    assert memories[0].source_end_index == 2
    assert memories[0].embedding == [1.0, 0.0]
    db.close()

    second = conversation_memory.refresh_conversation_memory(
        "conversation-1",
        "owner-1",
    )
    assert second["updated"] is True
    assert second["source_start_index"] == 3
    assert second["source_end_index"] == 4
    unchanged = conversation_memory.refresh_conversation_memory(
        "conversation-1",
        "owner-1",
    )
    assert unchanged == {"updated": False, "reason": "below-batch"}


def test_memory_recall_is_owner_and_conversation_scoped(
    tmp_path,
    monkeypatch,
):
    local_session = _session_factory(tmp_path)
    _seed_conversation(local_session, message_count=0)
    settings = _settings()
    monkeypatch.setattr(conversation_memory, "get_settings", lambda: settings)
    monkeypatch.setattr(
        conversation_memory,
        "embed_texts",
        lambda _texts, **_kwargs: [[1.0, 0.0]],
    )
    db = local_session()
    db.add_all(
        [
            ConversationMemory(
                id="memory-relevant",
                owner_id="owner-1",
                conversation_id="conversation-1",
                content="AlphaNet 数据集与训练设置",
                source_start_index=1,
                source_end_index=2,
                importance=0.8,
                embedding_model="test-embedding",
                embedding_version="test-v1",
                embedding=[1.0, 0.0],
                extra={"library_ids": ["library-1"]},
            ),
            ConversationMemory(
                id="memory-irrelevant",
                owner_id="owner-1",
                conversation_id="conversation-1",
                content="完全不同的主题",
                source_start_index=3,
                source_end_index=4,
                importance=0.5,
                embedding_model="test-embedding",
                embedding_version="test-v1",
                embedding=[0.0, 1.0],
                extra={"library_ids": ["library-1"]},
            ),
            ConversationMemory(
                id="memory-other-library",
                owner_id="owner-1",
                conversation_id="conversation-1",
                content="AlphaNet 另一知识库中的数据集",
                source_start_index=5,
                source_end_index=6,
                importance=1.0,
                embedding_model="test-embedding",
                embedding_version="test-v1",
                embedding=[1.0, 0.0],
                extra={"library_ids": ["library-2"]},
            ),
        ]
    )
    db.commit()
    hits, vector = conversation_memory.recall_conversation_memories(
        db,
        owner_id="owner-1",
        conversation_id="conversation-1",
        query="AlphaNet 使用哪个数据集",
        library_ids=["library-1"],
    )
    assert vector == [1.0, 0.0]
    assert [hit.id for hit in hits] == ["memory-relevant"]
    db.close()


def test_clear_memory_disables_rebuild(tmp_path):
    local_session = _session_factory(tmp_path)
    _seed_conversation(local_session, message_count=0)
    db = local_session()
    conversation = db.get(Conversation, "conversation-1")
    conversation.memory_summary = "summary"
    conversation.summarized_message_count = 4
    db.add(
        ConversationMemory(
            owner_id="owner-1",
            conversation_id="conversation-1",
            content="episode",
            source_start_index=1,
            source_end_index=4,
            importance=0.5,
        )
    )
    db.commit()
    conversation_memory.clear_conversation_memory(
        db,
        conversation,
        disable=True,
    )
    db.commit()
    assert conversation.memory_enabled is False
    assert conversation.memory_summary is None
    assert conversation.summarized_message_count == 0
    assert (
        db.scalar(
            select(ConversationMemory).where(
                ConversationMemory.conversation_id == conversation.id
            )
        )
        is None
    )
    db.close()


def test_sixty_message_thread_compacts_to_summary_plus_recent_window(
    tmp_path,
    monkeypatch,
):
    local_session = _session_factory(tmp_path)
    _seed_conversation(local_session, message_count=60)
    settings = _settings()
    monkeypatch.setattr(conversation_memory, "SessionLocal", local_session)
    monkeypatch.setattr(conversation_memory, "get_settings", lambda: settings)

    def summarize(*, existing_summary, transcript, **_kwargs):
        latest = transcript.splitlines()[0]
        return (
            f"{existing_summary} {latest}".strip(),
            transcript,
            0.6,
        )

    monkeypatch.setattr(
        conversation_memory,
        "_summarize_episode",
        summarize,
    )
    monkeypatch.setattr(
        conversation_memory,
        "embed_texts",
        lambda _texts, **_kwargs: [[1.0, 0.0]],
    )

    updates = 0
    for _ in range(40):
        result = conversation_memory.refresh_conversation_memory(
            "conversation-1",
            "owner-1",
        )
        if not result.get("updated"):
            break
        updates += 1

    db = local_session()
    conversation = db.get(Conversation, "conversation-1")
    memories = list(
        db.scalars(
            select(ConversationMemory)
            .where(
                ConversationMemory.conversation_id == "conversation-1"
            )
            .order_by(ConversationMemory.source_start_index)
        ).all()
    )
    assert updates == 28
    assert conversation.summarized_message_count == 56
    assert len(memories) == 28
    assert memories[0].source_start_index == 1
    assert memories[-1].source_end_index == 56
    recent = list(
        db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.sequence
                > conversation.summarized_message_count,
            )
            .order_by(Message.sequence)
        ).all()
    )
    assert [message.sequence for message in recent] == [57, 58, 59, 60]
    db.close()
