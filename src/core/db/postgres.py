"""Асинхронный слой доступа к PostgreSQL 18 + pgvector + pg_trgm (Layer 2: Storage Engine).

Отвечает за управление нативным пулом соединений asyncpg, идемпотентную инициализацию
DDL-схемы (проекты, компоненты Harness, гибридная векторно-полнотекстовая память, аудит хуков,
рантайм-реестр провайдеров Tier 2 и архив сводок сжатия контекста SPEC-003) и диагностику БД.
"""

from __future__ import annotations

import logging

import asyncpg

from src.core.enums import ComponentStatus, ScopeType

logger = logging.getLogger("mcp_coop.core.db.postgres")

# Идемпотентная DDL-схема хранилища по спецификации Раздела 4.1 SPEC.md.
# Выполняется внутри единой транзакции на старте ядра, что гарантирует атомарное
# развёртывание расширений (vector, pg_trgm), таблиц и GIN-индексов без внешних миграторов.
SCHEMA_DDL: str = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1. Зарегистрированные локальные проекты
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_path TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    stack_tags TEXT[] NOT NULL DEFAULT ARRAY['{ScopeType.GLOBAL}'],
    profile_overrides JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    last_synced_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE projects ADD COLUMN IF NOT EXISTS profile_overrides JSONB NOT NULL DEFAULT '{{}}'::jsonb;

-- 2. Компоненты харнеса (Harness: System Prompt, Guardrails, Skills, Tool Guidance, Scripts, Subagents)
CREATE TABLE IF NOT EXISTS harness_components (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT UNIQUE NOT NULL,
    component_type TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '{ScopeType.GLOBAL}',
    stack_tags TEXT[] NOT NULL DEFAULT ARRAY['{ScopeType.GLOBAL}'],
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    embedding vector(768),
    search_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(slug, '') || ' ' || coalesce(title, '') || ' ' || coalesce(description, ''))
    ) STORED,
    status TEXT NOT NULL DEFAULT '{ComponentStatus.COMPOSED}',
    score REAL NOT NULL DEFAULT 1.0,
    usage_count INT NOT NULL DEFAULT 0,
    source_project_path TEXT,
    version INT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE harness_components ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE harness_components ADD COLUMN IF NOT EXISTS embedding vector(768);
ALTER TABLE harness_components ADD COLUMN IF NOT EXISTS search_tsv tsvector GENERATED ALWAYS AS (
    to_tsvector('simple', coalesce(slug, '') || ' ' || coalesce(title, '') || ' ' || coalesce(description, ''))
) STORED;

CREATE INDEX IF NOT EXISTS idx_components_tsv ON harness_components USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_components_desc_trgm ON harness_components USING GIN (description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_components_embedding_hnsw ON harness_components USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;

-- 3. Сквозная база опыта и решений ошибок (Memory компонент из Harness)
CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    error_symptom TEXT NOT NULL,
    solution TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '{ScopeType.GLOBAL}',
    stack_tags TEXT[] NOT NULL DEFAULT ARRAY['{ScopeType.GLOBAL}'],
    embedding vector(768),
    search_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(error_symptom, '') || ' ' || coalesce(solution, ''))
    ) STORED,
    helpful_count INT NOT NULL DEFAULT 0,
    source_project_path TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memories_tsv ON memories USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_memories_symptom_trgm ON memories USING GIN (error_symptom gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw ON memories USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;

-- 4. Лог перехватов и событий жизненных хуков
CREATE TABLE IF NOT EXISTS hook_events (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    workspace_path TEXT,
    event_type TEXT NOT NULL,
    tool_name TEXT,
    payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    decision JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Рантайм-реестр провайдеров второго контура принятия решений (Tier 2 Decision API Pool)
CREATE TABLE IF NOT EXISTS decision_providers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    dialect TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model TEXT NOT NULL,
    api_key TEXT NOT NULL DEFAULT '',
    priority INT NOT NULL DEFAULT 100,
    timeout_ms INT NOT NULL DEFAULT 600,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. Долгосрочный архив сводок сжатия контекста (<summary> после /compact, SPEC-003)
CREATE TABLE IF NOT EXISTS session_compactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id TEXT NOT NULL,
    workspace_path TEXT,
    agent_type TEXT NOT NULL,
    step_idx INT NOT NULL DEFAULT 0,
    checkpoint_hash TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    stack_tags TEXT[] NOT NULL DEFAULT ARRAY['{ScopeType.GLOBAL}'],
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    embedding vector(768),
    search_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(summary_text, '') || ' ' || coalesce(workspace_path, ''))
    ) STORED,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (conversation_id, checkpoint_hash)
);

CREATE INDEX IF NOT EXISTS idx_compactions_workspace_created
    ON session_compactions (workspace_path, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_compactions_tsv
    ON session_compactions USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_compactions_summary_trgm
    ON session_compactions USING GIN (summary_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_compactions_embedding_hnsw
    ON session_compactions USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

-- 7. Версионированные узлы графа .context/ для 3-Way Hash Sync (SPEC-002)
CREATE TABLE IF NOT EXISTS project_context_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    workspace_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    node_id TEXT NOT NULL,
    entity_class TEXT NOT NULL DEFAULT 'Module',
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    base_hash TEXT NOT NULL DEFAULT '',
    file_updated_at TIMESTAMPTZ DEFAULT NOW(),
    embedding vector(768),
    search_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(node_id, '') || ' ' || coalesce(content, ''))
    ) STORED,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (workspace_path, relative_path)
);
CREATE INDEX IF NOT EXISTS idx_context_nodes_tsv ON project_context_nodes USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_context_nodes_content_trgm ON project_context_nodes USING GIN (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_context_nodes_embedding_hnsw ON project_context_nodes USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
"""


def normalize_asyncpg_dsn(dsn: str) -> str:
    """Нормализует DSN-строку подключения для прямой передачи в asyncpg.create_pool().

    Почему это важно:
    - В окружении Docker часто задаётся переменная DATABASE_URL в формате SQLAlchemy
      (`postgresql+asyncpg://...` или `postgres+asyncpg://...`).
    - Нативный драйвер asyncpg принимает только схемы `postgresql://` или `postgres://`,
      поэтому нормализация защищает от падения на старте при любом варианте конфигурации.
    """
    normalized = dsn.strip()
    if normalized.startswith("postgresql+asyncpg://"):
        return "postgresql://" + normalized.removeprefix("postgresql+asyncpg://")
    if normalized.startswith("postgres+asyncpg://"):
        return "postgresql://" + normalized.removeprefix("postgres+asyncpg://")
    return normalized


class PostgresDatabase:
    """Менеджер асинхронного пула соединений PostgreSQL и инициализатор схемы БД.

    Почему выбран нативный asyncpg.Pool вместо SQLAlchemy ORM:
    - Минимальный оверхед по памяти и латентности при обработке синхронных хуков PreToolUse (<5 мс).
    - Прямая работа с типами PostgreSQL (массивы TEXT[], JSONB, tsvector и vector(768))
      без промежуточной сериализации тяжёлых моделей ORM.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        command_timeout: float = 30.0,
    ) -> None:
        self._dsn: str = normalize_asyncpg_dsn(dsn)
        self._min_size: int = min_size
        self._max_size: int = max_size
        self._command_timeout: float = command_timeout
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        """Возвращает активный пул соединений или выбрасывает RuntimeError, если пул не инициализирован."""
        if self._pool is None:
            raise RuntimeError("PostgreSQL connection pool is not initialized")
        return self._pool

    @property
    def is_connected(self) -> bool:
        """Проверяет, инициализирован ли пул соединений в текущем жизненном цикле."""
        return self._pool is not None

    async def connect(self) -> None:
        """Создаёт пул соединений asyncpg и гарантированно накатывает идемпотентную схему БД."""
        if self._pool is not None:
            return

        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=self._command_timeout,
        )
        await self.init_schema()
        logger.info("PostgreSQL connection pool initialized and schema verified")

    async def init_schema(self) -> None:
        """Атомарно создаёт расширения pgvector/pg_trgm, таблицы и индексы внутри транзакции."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(SCHEMA_DDL)

    async def check_health(self) -> bool:
        """Выполняет быстрый легковесный пинг БД (`SELECT 1`) для эндпоинта /health."""
        if self._pool is None:
            return False
        try:
            val = await self._pool.fetchval("SELECT 1")
            return bool(val == 1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("PostgreSQL health check failed: %s", exc)
            return False

    async def close(self) -> None:
        """Корректно закрывает все соединения пула при остановке ASGI-сервера."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL connection pool closed")
