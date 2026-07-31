from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db import Base
from app import models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# These mutually exclusive ANN indexes are selected at runtime according to
# the installed pgvector capabilities. They are operational indexes rather
# than model drift and must not make ``alembic check`` propose their removal.
RUNTIME_MANAGED_INDEXES = {
    "ix_chunks_embedding_hnsw",
    "ix_chunks_embedding_ivfflat",
    "ix_chunks_text_trgm",
    "ix_conversation_memories_embedding_hnsw",
    "ix_conversation_memories_content_trgm",
}


def include_object(object_, name, type_, reflected, compare_to):  # noqa: ANN001
    if type_ == "index" and name in RUNTIME_MANAGED_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
