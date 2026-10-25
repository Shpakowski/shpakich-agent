"""Репозиторий долгосрочной архивации и гибридного поиска сводок сжатия контекста (Layer 2: SPEC-003).

Отвечает за идемпотентное сохранение чекпоинтов `<summary>` после выполнения `/compact`
в таблицу `session_compactions` PostgreSQL 18 с генерацией 768-мерных векторов `EmbeddingGemma 2`
и трёхуровневым гибридным поиском (`pgvector` HNSW + `tsvector` + `pg_trgm`) с автономным
лексическим фолбэком при работе в оффлайне.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import AgentType, EmbeddingTaskType, ScopeType
from src.core.services.embeddings import EmbeddingClient, format_pgvector

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase

logger = logging.getLogger("mcp_coop.core.db.compactions")

COMPACTION_DOC_TITLE_PREFIX: str = "compaction:"
DEFAULT_COMPACTION_SEARCH_LIMIT: int = 3
MAX_SEARCH_QUERY_CHARS: int = 500
MAX_EMBED_QUERY_CHARS: int = 1_000
RELEVANCE_DECIMAL_PLACES: int = 3

INSERT_COMPACTION_SQL: str = """
INSERT INTO session_compactions (
    conversation_id,
    workspace_path,
    agent_type,
    step_idx,
    checkpoint_hash,
    summary_text,
    stack_tags,
    metadata,
    embedding
)
VALUES ($1, $2, $3, $4, $5, $6, $7::text[], $8::jsonb, $9::vector)
ON CONFLICT (conversation_id, checkpoint_hash) DO NOTHING
RETURNING id
"""

SELECT_EXISTING_COMPACTION_ID_SQL: str = """
SELECT id
FROM session_compactions
WHERE conversation_id = $1
  AND checkpoint_hash = $2
LIMIT 1
"""

SEARCH_COMPACTIONS_HYBRID_SQL: str = """
WITH scored AS (
    SELECT
        id,
        conversation_id,
        workspace_path,
        agent_type,
        step_idx,
        checkpoint_hash,
        summary_text,
        stack_tags,
        created_at,
        (embedding IS NOT NULL) AS has_vec,
        CASE
            WHEN embedding IS NOT NULL THEN GREATEST(0.0, 1.0 - (embedding <=> $5::vector))
            ELSE 0.0
        END AS vec_sim,
        ts_rank_cd(search_tsv, plainto_tsquery('simple', $1)) AS ts_r,
        similarity(summary_text, $1) AS trgm_sim
    FROM session_compactions
    WHERE ($3::text IS NULL OR workspace_path = $3)
      AND (
          $2::text[] = ARRAY[$4]
          OR stack_tags && $2::text[]
          OR stack_tags = ARRAY[$4]
      )
)
SELECT
    id,
    conversation_id,
    workspace_path,
    agent_type,
    step_idx,
    checkpoint_hash,
    summary_text,
    stack_tags,
    CASE
        WHEN has_vec THEN (
            vec_sim * 0.55
            + (ts_r / (ts_r + 1.0)) * 0.25
            + trgm_sim * 0.20
        )
        ELSE (
            (ts_r / (ts_r + 1.0)) * 0.60
            + trgm_sim * 0.40
        )
    END AS relevance
FROM scored
WHERE vec_sim > 0.30 OR ts_r > 0.0 OR trgm_sim > 0.10
ORDER BY relevance DESC, created_at DESC
LIMIT $6
"""

SEARCH_COMPACTIONS_LEXICAL_SQL: str = """
WITH scored AS (
    SELECT
        id,
        conversation_id,
        workspace_path,
        agent_type,
        step_idx,
        checkpoint_hash,
        summary_text,
        stack_tags,
        created_at,
        ts_rank_cd(search_tsv, plainto_tsquery('simple', $1)) AS ts_r,
        similarity(summary_text, $1) AS trgm_sim
    FROM session_compactions
    WHERE ($3::text IS NULL OR workspace_path = $3)
      AND (
          $2::text[] = ARRAY[$4]
          OR stack_tags && $2::text[]
          OR stack_tags = ARRAY[$4]
      )
      AND (
          search_tsv @@ plainto_tsquery('simple', $1)
          OR similarity(summary_text, $1) > 0.10
      )
)
SELECT
    id,
    conversation_id,
    workspace_path,
    agent_type,
    step_idx,
    checkpoint_hash,
    summary_text,
    stack_tags,
    (
        (ts_r / (ts_r + 1.0)) * 0.60
        + trgm_sim * 0.40
    ) AS relevance
FROM scored
ORDER BY relevance DESC, created_at DESC
LIMIT $5
"""


def _normalize_compaction_tags(stack_tags: Sequence[str] | None) -> list[str]:
    """Нормализует список стековых тегов к нижнему регистру без дубликатов с фолбэком на `global`."""
    if not stack_tags:
        return [ScopeType.GLOBAL.value]
    seen: set[str] = set()
    normalized: list[str] = []
    for raw_tag in stack_tags:
        cleaned = str(raw_tag).strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized or [ScopeType.GLOBAL.value]


def _normalize_search_tags(stack_tags: Sequence[str] | None) -> list[str]:
    """Формирует список поисковых тегов: выделяет специфичные теги стека или возвращает `['global']`."""
    normalized = _normalize_compaction_tags(stack_tags)
    specific = [tag for tag in normalized if tag != ScopeType.GLOBAL.value]
    return specific or [ScopeType.GLOBAL.value]


class CompactionArchiveRecord(BaseModel):
    """Неизменяемая карточка сархивированного чекпоинта сжатия контекста (`session_compactions`)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Primary UUID identifier or fallback hash of the archived compaction.")
    conversation_id: str = Field(description="Session conversation identifier.")
    workspace_path: str = Field(default="", description="Normalized project workspace path.")
    agent_type: AgentType = Field(description="Agent runtime that produced the compaction.")
    step_idx: int = Field(default=0, ge=0, description="Conversation step index at compaction time.")
    checkpoint_hash: str = Field(description="Deterministic SHA-256 prefix of the summary checkpoint.")
    summary_text: str = Field(description="Extracted post-compaction <summary> body.")
    stack_tags: list[str] = Field(
        default_factory=lambda: [ScopeType.GLOBAL.value],
        description="Project stack tags associated with the session.",
    )
    inserted: bool = Field(
        default=True,
        description="True when newly inserted; False when deduplicated by ON CONFLICT DO NOTHING.",
    )


class CompactionArchiveStore:
    """Асинхронное хранилище долгосрочных сводок сжатия сессий (`session_compactions`, SPEC-003).

    Инкапсулирует:
    - Идемпотентную запись `<summary>` с автоматической векторизацией через `EmbeddingClient`.
    - Гибридный поиск (`pgvector` + `tsvector` + `pg_trgm`) для кросс-сессионного восстановления контекста.
    """

    def __init__(
        self,
        db: PostgresDatabase | None = None,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self._db: PostgresDatabase | None = db
        self._embedding_client: EmbeddingClient | None = embedding_client

    async def archive_compaction(
        self,
        *,
        conversation_id: str,
        workspace_path: str,
        agent_type: AgentType,
        step_idx: int,
        checkpoint_hash: str,
        summary_text: str,
        stack_tags: Sequence[str] = (ScopeType.GLOBAL.value,),
        metadata: Mapping[str, Any] | None = None,
    ) -> CompactionArchiveRecord | None:
        """Идемпотентно архивирует `<summary>` сжатия контекста в таблицу `session_compactions`.

        Если запись с парой `(conversation_id, checkpoint_hash)` уже существует в БД,
        `ON CONFLICT DO NOTHING` предотвращает дублирование и возвращает карточку с `inserted=False`.
        """
        clean_conv_id = conversation_id.strip()
        clean_workspace = workspace_path.strip()
        clean_hash = checkpoint_hash.strip()
        clean_summary = summary_text.strip()
        if not clean_conv_id or not clean_hash or not clean_summary:
            return None
        if self._db is None or not self._db.is_connected:
            return None

        resolved_agent = AgentType(agent_type)
        safe_step = max(0, int(step_idx))
        tags = _normalize_compaction_tags(stack_tags)
        metadata_json = json.dumps(dict(metadata or {}), ensure_ascii=False)

        vec_literal: str | None = None
        if self._embedding_client is not None:
            doc_title = f"{COMPACTION_DOC_TITLE_PREFIX}{clean_workspace or clean_conv_id}"
            try:
                raw_vec = await self._embedding_client.embed_text(
                    clean_summary,
                    task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT,
                    title=doc_title,
                )
                vec_literal = format_pgvector(raw_vec)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Compaction document embedding failed, archiving without vector: %s",
                    exc,
                )
                vec_literal = None

        try:
            row = await self._db.pool.fetchrow(
                INSERT_COMPACTION_SQL,
                clean_conv_id,
                clean_workspace,
                resolved_agent.value,
                safe_step,
                clean_hash,
                clean_summary,
                tags,
                metadata_json,
                vec_literal,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to archive compaction into PostgreSQL: %s", exc)
            return None

        if row is not None and "id" in row and row["id"] is not None:
            return CompactionArchiveRecord(
                id=str(row["id"]),
                conversation_id=clean_conv_id,
                workspace_path=clean_workspace,
                agent_type=resolved_agent,
                step_idx=safe_step,
                checkpoint_hash=clean_hash,
                summary_text=clean_summary,
                stack_tags=tags,
                inserted=True,
            )

        existing_id: str = clean_hash
        try:
            fetched_id = await self._db.pool.fetchval(
                SELECT_EXISTING_COMPACTION_ID_SQL,
                clean_conv_id,
                clean_hash,
            )
            if isinstance(fetched_id, (str, uuid.UUID)) and str(fetched_id).strip():
                existing_id = str(fetched_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to look up existing compaction id on conflict: %s", exc)

        return CompactionArchiveRecord(
            id=existing_id,
            conversation_id=clean_conv_id,
            workspace_path=clean_workspace,
            agent_type=resolved_agent,
            step_idx=safe_step,
            checkpoint_hash=clean_hash,
            summary_text=clean_summary,
            stack_tags=tags,
            inserted=False,
        )

    async def search_compactions(
        self,
        query: str,
        stack_tags: Sequence[str] = (ScopeType.GLOBAL.value,),
        *,
        workspace_path: str | None = None,
        limit: int = DEFAULT_COMPACTION_SEARCH_LIMIT,
    ) -> list[dict[str, Any]]:
        """Ищет релевантные сводки `<summary>` через 3-way гибридный поиск или лексический фолбэк."""
        clean_query = query.strip()
        if not clean_query or self._db is None or not self._db.is_connected:
            return []

        tags = _normalize_search_tags(stack_tags)
        clean_workspace = (
            workspace_path.strip()
            if workspace_path is not None and workspace_path.strip()
            else None
        )
        safe_limit = max(1, int(limit))
        truncated_query = clean_query[:MAX_SEARCH_QUERY_CHARS]

        vec_literal: str | None = None
        if self._embedding_client is not None:
            try:
                query_vec = await self._embedding_client.embed_text(
                    clean_query[:MAX_EMBED_QUERY_CHARS],
                    task_type=EmbeddingTaskType.RETRIEVAL_QUERY,
                )
                vec_literal = format_pgvector(query_vec)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Compaction query embedding failed, falling back to lexical search: %s",
                    exc,
                )
                vec_literal = None

        if vec_literal is not None:
            try:
                rows = await self._db.pool.fetch(
                    SEARCH_COMPACTIONS_HYBRID_SQL,
                    truncated_query,
                    tags,
                    clean_workspace,
                    ScopeType.GLOBAL.value,
                    vec_literal,
                    safe_limit,
                )
                return self._format_rows(rows)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "3-way vector compaction search failed, falling back to lexical: %s",
                    exc,
                )

        try:
            rows = await self._db.pool.fetch(
                SEARCH_COMPACTIONS_LEXICAL_SQL,
                truncated_query,
                tags,
                clean_workspace,
                ScopeType.GLOBAL.value,
                safe_limit,
            )
            return self._format_rows(rows)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Lexical compaction search failed: %s", exc)
            return []

    @staticmethod
    def _format_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Форматирует строки выборки `session_compactions` в унифицированный список словарей."""
        results: list[dict[str, Any]] = []
        for r in rows:
            raw_tags = r.get("stack_tags")
            tags_list = (
                _normalize_compaction_tags(raw_tags)
                if isinstance(raw_tags, Sequence) and not isinstance(raw_tags, (str, bytes))
                else [ScopeType.GLOBAL.value]
            )
            results.append(
                {
                    "id": str(r["id"]) if "id" in r and r["id"] is not None else "",
                    "conversation_id": str(r.get("conversation_id") or ""),
                    "workspace_path": str(r.get("workspace_path") or ""),
                    "agent_type": str(r.get("agent_type") or AgentType.ANTIGRAVITY.value),
                    "step_idx": int(r.get("step_idx") or 0),
                    "checkpoint_hash": str(r.get("checkpoint_hash") or ""),
                    "summary_text": str(r.get("summary_text") or ""),
                    "stack_tags": tags_list,
                    "relevance": round(
                        float(r.get("relevance") or 0.0),
                        RELEVANCE_DECIMAL_PLACES,
                    ),
                }
            )
        return results


__all__ = [
    "COMPACTION_DOC_TITLE_PREFIX",
    "DEFAULT_COMPACTION_SEARCH_LIMIT",
    "CompactionArchiveRecord",
    "CompactionArchiveStore",
]
