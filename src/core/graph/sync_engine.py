"""Движок двусторонней синхронизации компонентов `Harness²` и узлов `.context/` (`WorkspaceSyncEngine`, Layer 2).

Реализует Контур 2 (инжест пользовательских скиллов и правил с `Content-Hash Gate` и проверкой
`DecisionDomain.HARNESS_PROBE`) и Контур 3 (алгоритм 3-Way Hash Sync `(local_hash, db_hash, base_hash)`
и Write-Through синхронизацию графа `.context/` с таблицей `project_context_nodes` по `SPEC-002`).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import (
    ComponentStatus,
    ComponentType,
    ContextEntityClass,
    ContextSyncAction,
    DecisionDomain,
    EmbeddingTaskType,
    ScopeType,
    ScriptExecutionMode,
    VerdictAction,
)
from src.core.graph.context_graph import CONTEXT_DIR_NAME
from src.core.graph.markdown_merge import (
    compute_normalized_content_hash,
    merge_context_node_markdown,
)
from src.core.harness.compiler import normalize_stack_tags
from src.core.harness.seed import DEFAULT_COMPONENT_SCORE

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase
    from src.core.db.redis_client import RedisSessionStore
    from src.core.decision.engine import DecisionEngine
    from src.core.services.embeddings import EmbeddingClient

logger = logging.getLogger("mcp_coop.core.graph.sync_engine")

INGEST_ACTION_CREATED: str = "created"
INGEST_ACTION_UPDATED: str = "updated"
INGEST_ACTION_UNCHANGED: str = "unchanged"
INGEST_ACTION_SKIPPED_SYSTEM: str = "skipped_system"
INGEST_ACTION_REJECTED_PROBE: str = "rejected_probe"
MIN_CANDIDATE_CONTENT_LENGTH: int = 20
MIN_SLUG_TAG_LENGTH: int = 2
MAX_COMPONENT_DOC_CHARS: int = 500
MAX_CONTEXT_NODE_DOC_CHARS: int = 1500
SLUG_TOKEN_SPLIT_REGEX: re.Pattern[str] = re.compile(r"[-_]+")
SYSTEM_BOOTSTRAP_SLUGS: frozenset[str] = frozenset({
    "coop-orchestrator", "harness-orchestrator", "core-engineering-mentor", "block-destructive-commands",
    "context-graph-discipline", "context-graph-steward", "inject-context-map", "track-file-changes", "enforce-context-sync",
})
SELECT_COMPONENT_HASH_SQL: str = "SELECT id, content_hash, version FROM harness_components WHERE slug = $1"
UPSERT_INGESTED_COMPONENT_SQL: str = (
    "INSERT INTO harness_components (slug, component_type, scope, stack_tags, title, description, content, "
    "content_hash, metadata, status, score, source_project_path, embedding, version, updated_at) "
    "VALUES ($1, $2, $3, $4::text[], $5, $6, $7, $8, $9::jsonb, $10, $11, $12, $13::vector, 1, NOW()) "
    "ON CONFLICT (slug) DO UPDATE SET component_type = EXCLUDED.component_type, scope = EXCLUDED.scope, "
    "stack_tags = EXCLUDED.stack_tags, title = EXCLUDED.title, description = EXCLUDED.description, "
    "content = EXCLUDED.content, content_hash = EXCLUDED.content_hash, metadata = EXCLUDED.metadata, "
    "status = EXCLUDED.status, score = EXCLUDED.score, source_project_path = EXCLUDED.source_project_path, "
    "embedding = COALESCE(EXCLUDED.embedding, harness_components.embedding), "
    "version = harness_components.version + 1, updated_at = NOW() RETURNING version"
)
SELECT_PROJECT_ID_SQL: str = "SELECT id FROM projects WHERE workspace_path = $1 LIMIT 1"
SELECT_WORKSPACE_CONTEXT_NODES_SQL: str = (
    "SELECT relative_path, node_id, entity_class, content, content_hash, base_hash "
    "FROM project_context_nodes WHERE workspace_path = $1 ORDER BY relative_path ASC"
)
UPSERT_CONTEXT_NODE_SQL: str = (
    "INSERT INTO project_context_nodes (project_id, workspace_path, relative_path, node_id, entity_class, "
    "content, content_hash, base_hash, embedding, updated_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::vector, NOW()) "
    "ON CONFLICT (workspace_path, relative_path) DO UPDATE SET "
    "project_id = COALESCE(EXCLUDED.project_id, project_context_nodes.project_id), "
    "node_id = EXCLUDED.node_id, entity_class = EXCLUDED.entity_class, content = EXCLUDED.content, "
    "content_hash = EXCLUDED.content_hash, base_hash = EXCLUDED.base_hash, "
    "embedding = COALESCE(EXCLUDED.embedding, project_context_nodes.embedding), updated_at = NOW()"
)
UPDATE_CONTEXT_NODE_BASE_HASH_SQL: str = (
    "UPDATE project_context_nodes SET base_hash = $1, updated_at = NOW() "
    "WHERE workspace_path = $2 AND relative_path = $3"
)


class WorkspaceComponentCandidate(BaseModel):
    """Кандидат кастомного скилла или правила из рабочей директории для инжеста в `harness_components`."""

    model_config = ConfigDict(frozen=True)
    slug: str = Field(min_length=1)
    component_type: ComponentType
    title: str = Field(min_length=1)
    description: str = ""
    content: str = Field(min_length=1)
    content_hash: str = ""
    stack_tags: list[str] = Field(default_factory=list)
    file_updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ComponentIngestItemResult(BaseModel):
    """Результат обработки одного компонента при двустороннем `sync` (`POST /api/sync/ingest`)."""

    model_config = ConfigDict(frozen=True)
    slug: str
    component_type: str
    action: str
    version: int = 1
    reason: str | None = None


class ComponentIngestReport(BaseModel):
    """Сводный отчёт инжеста пользовательских скиллов и правил проекта в глобальную БД."""

    model_config = ConfigDict(frozen=True)
    workspace_path: str
    processed: int
    created: int
    updated: int
    unchanged: int
    rejected: int
    items: list[ComponentIngestItemResult] = Field(default_factory=list)


class ContextNodeSyncInput(BaseModel):
    """Локальное состояние файла `.context/` для сверки по алгоритму 3-Way Hash Sync."""

    model_config = ConfigDict(frozen=True)
    relative_path: str = Field(min_length=1)
    node_id: str = ""
    entity_class: str = ContextEntityClass.MODULE.value
    content: str
    content_hash: str = ""
    base_hash: str = ""
    file_updated_at: str | None = None


class ContextNodeSyncItemResult(BaseModel):
    """Итог синхронизации отдельного узла `.context/` (включая `content` при `pulled_from_db`/`merged`)."""

    model_config = ConfigDict(frozen=True)
    relative_path: str
    node_id: str
    action: ContextSyncAction
    content_hash: str
    base_hash: str
    content: str | None = None


class ContextSyncReport(BaseModel):
    """Сводный отчёт 3-Way Hash синхронизации графа `.context/` (`POST /api/sync/context`)."""

    model_config = ConfigDict(frozen=True)
    workspace_path: str
    unchanged: int
    pushed_to_db: int
    pulled_from_db: int
    merged: int
    items: list[ContextNodeSyncItemResult] = Field(default_factory=list)


def normalize_relative_context_path(relative_path: str) -> str:
    """Нормализует путь узла относительно корня `.context/` (`index.md`, `entities/modules/mod-x.md`)."""
    cleaned = relative_path.strip().replace("\\", "/").lstrip("/")
    while cleaned.startswith("./"):
        cleaned = cleaned.removeprefix("./").lstrip("/")
    prefix = f"{CONTEXT_DIR_NAME}/"
    return cleaned.removeprefix(prefix).lstrip("/") if cleaned.startswith(prefix) else cleaned


def extract_tags_from_slug(slug: str) -> list[str]:
    """Разбивает `slug` компонента по разделителям `[-_]+` в упорядоченный список уникальных тегов."""
    seen: dict[str, None] = {}
    for raw_tok in SLUG_TOKEN_SPLIT_REGEX.split(slug.strip().lower()):
        tok = raw_tok.strip()
        if len(tok) >= MIN_SLUG_TAG_LENGTH and tok not in seen:
            seen[tok] = None
    return list(seen.keys())


def resolve_component_tags_and_scope(
    slug: str,
    raw_tags: Sequence[str],
    fallback_tags: Sequence[str] = (),
) -> tuple[list[str], str]:
    """Определяет чистые `stack_tags` компонента (из `raw_tags` или `slug`) и глобальный `scope = 'global'`."""
    cleaned_explicit: dict[str, None] = {}
    for item in raw_tags:
        tag = str(item or "").strip().lower()
        if len(tag) >= MIN_SLUG_TAG_LENGTH and tag != ScopeType.GLOBAL.value:
            cleaned_explicit[tag] = None

    resolved_tags = list(cleaned_explicit.keys()) or extract_tags_from_slug(slug)
    if not resolved_tags:
        resolved_tags = list(normalize_stack_tags(fallback_tags))

    return resolved_tags, ScopeType.GLOBAL.value


class WorkspaceSyncEngine:
    """Оркестратор двусторонней синхронизации пользовательских компонентов и графа `.context/` с PostgreSQL."""

    def __init__(
        self, db: PostgresDatabase | None = None, redis: RedisSessionStore | None = None,
        engine: DecisionEngine | None = None, embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self._db, self._redis, self._engine, self._embedding_client = db, redis, engine, embedding_client

    async def _embed_document(self, text: str, title: str) -> str | None:
        """Вычисляет строковый литерал `pgvector(768)` для индексируемого документа или возвращает `None`."""
        if self._embedding_client is None:
            return None
        from src.core.services.embeddings import format_pgvector

        return format_pgvector(await self._embedding_client.embed_text(text, task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT, title=title))

    async def _persist_context_node(self, project_id: Any, ws: str, rel_path: str, node_id: str, entity_class: str, content: str, content_hash: str) -> None:
        """Вычисляет эмбеддинг и выполняет UPSERT узла в `project_context_nodes` с `base_hash = content_hash`."""
        if self._db is None or not self._db.is_connected:
            return
        vec_lit = await self._embed_document(content[:MAX_CONTEXT_NODE_DOC_CHARS], node_id)
        await self._db.pool.execute(UPSERT_CONTEXT_NODE_SQL, project_id, ws, rel_path, node_id, entity_class, content, content_hash, content_hash, vec_lit)

    async def ingest_components(self, workspace_path: str, stack_tags: Sequence[str], candidates: Sequence[WorkspaceComponentCandidate]) -> ComponentIngestReport:
        """Инжестирует кастомные компоненты проекта в `harness_components` через `Content-Hash Gate` и `HARNESS_PROBE`."""
        clean_ws = workspace_path.strip()
        base_tags = list(normalize_stack_tags(stack_tags))
        created = updated = unchanged = rejected = 0
        items: list[ComponentIngestItemResult] = []
        invalidated_tags: set[str] = set()

        for cand in candidates:
            clean_slug = cand.slug.strip().lower()
            comp_type_str = cand.component_type.value
            if clean_slug in SYSTEM_BOOTSTRAP_SLUGS:
                items.append(ComponentIngestItemResult(slug=clean_slug, component_type=comp_type_str, action=INGEST_ACTION_SKIPPED_SYSTEM, reason="System bootstrap component is protected from self-ingestion"))
                continue

            content_hash = cand.content_hash.strip() or compute_normalized_content_hash(cand.content)
            existing_row: Any = None
            if self._db is not None and self._db.is_connected:
                existing_row = await self._db.pool.fetchrow(SELECT_COMPONENT_HASH_SQL, clean_slug)
                if existing_row is not None and str(existing_row["content_hash"] or "") == content_hash:
                    unchanged += 1
                    items.append(ComponentIngestItemResult(slug=clean_slug, component_type=comp_type_str, action=INGEST_ACTION_UNCHANGED, version=int(existing_row["version"] or 1)))
                    continue

            cand_tags, scope_str = resolve_component_tags_and_scope(
                clean_slug,
                cand.stack_tags,
                base_tags,
            )
            clean_title, clean_desc, clean_content = cand.title.strip() or clean_slug, cand.description.strip(), cand.content.strip()
            if self._engine is not None:
                from src.core.decision.models import DecisionRequest

                eval_req = DecisionRequest(
                    domain=DecisionDomain.HARNESS_PROBE, workspace_path=clean_ws, stack_tags=cand_tags,
                    payload={"slug": clean_slug, "component_type": comp_type_str, "title": clean_title, "description": clean_desc, "content": clean_content, "scope": scope_str, "metadata": dict(cand.metadata)},
                )
                res = await self._engine.evaluate(eval_req)
                if res.action != VerdictAction.ALLOW:
                    rejected += 1
                    items.append(ComponentIngestItemResult(slug=clean_slug, component_type=comp_type_str, action=INGEST_ACTION_REJECTED_PROBE, reason=res.reason))
                    continue
                score = float(res.score if res.score is not None else res.confidence)
            elif len(clean_content) < MIN_CANDIDATE_CONTENT_LENGTH:
                rejected += 1
                items.append(ComponentIngestItemResult(slug=clean_slug, component_type=comp_type_str, action=INGEST_ACTION_REJECTED_PROBE, reason="Candidate content is shorter than minimum required length"))
                continue
            else:
                score = DEFAULT_COMPONENT_SCORE

            meta_payload, version = {**cand.metadata, "execution_mode": ScriptExecutionMode.SEMANTIC.value}, 1
            if self._db is not None and self._db.is_connected:
                vec_literal = await self._embed_document(f"{clean_title}\n{clean_desc}".strip() or clean_content[:MAX_COMPONENT_DOC_CHARS], clean_title)
                row = await self._db.pool.fetchrow(
                    UPSERT_INGESTED_COMPONENT_SQL, clean_slug, comp_type_str, scope_str, cand_tags, clean_title, clean_desc,
                    clean_content, content_hash, json.dumps(meta_payload, ensure_ascii=False), ComponentStatus.COMPOSED.value, score, clean_ws, vec_literal,
                )
                version = int(row["version"]) if row is not None and row.get("version") is not None else 1

            action = INGEST_ACTION_UPDATED if existing_row is not None else INGEST_ACTION_CREATED
            updated, created = updated + int(action == INGEST_ACTION_UPDATED), created + int(action == INGEST_ACTION_CREATED)
            invalidated_tags.update(cand_tags)
            items.append(ComponentIngestItemResult(slug=clean_slug, component_type=comp_type_str, action=action, version=version))

        if invalidated_tags and self._redis is not None and self._redis.is_connected:
            await self._redis.invalidate_rules_cache(sorted(invalidated_tags))

        return ComponentIngestReport(workspace_path=clean_ws, processed=len(candidates), created=created, updated=updated, unchanged=unchanged, rejected=rejected, items=items)

    async def sync_context_nodes(self, workspace_path: str, local_nodes: Sequence[ContextNodeSyncInput]) -> ContextSyncReport:
        """Выполняет 3-Way Hash синхронизацию `(local_hash, db_hash, base_hash)` узлов `.context/` с PostgreSQL."""
        clean_ws = workspace_path.strip()
        unchanged = pushed = pulled = merged = 0
        items: list[ContextNodeSyncItemResult] = []

        if self._db is None or not self._db.is_connected:
            for node in local_nodes:
                rel_path = normalize_relative_context_path(node.relative_path)
                l_hash = node.content_hash.strip() or compute_normalized_content_hash(node.content)
                unchanged += 1
                items.append(ContextNodeSyncItemResult(relative_path=rel_path, node_id=node.node_id.strip() or Path(rel_path).stem, action=ContextSyncAction.UNCHANGED, content_hash=l_hash, base_hash=l_hash))
            return ContextSyncReport(workspace_path=clean_ws, unchanged=unchanged, pushed_to_db=0, pulled_from_db=0, merged=0, items=items)

        project_id = await self._db.pool.fetchval(SELECT_PROJECT_ID_SQL, clean_ws)
        db_rows = await self._db.pool.fetch(SELECT_WORKSPACE_CONTEXT_NODES_SQL, clean_ws)
        db_by_path: dict[str, Any] = {normalize_relative_context_path(str(r["relative_path"])): r for r in db_rows}
        seen_paths: set[str] = set()

        for local_node in local_nodes:
            rel_path = normalize_relative_context_path(local_node.relative_path)
            seen_paths.add(rel_path)
            node_id = local_node.node_id.strip() or Path(rel_path).stem
            entity_class = local_node.entity_class.strip() or ContextEntityClass.MODULE.value
            local_hash = local_node.content_hash.strip() or compute_normalized_content_hash(local_node.content)
            db_row = db_by_path.get(rel_path)
            db_hash = str(db_row["content_hash"] or "") if db_row is not None else ""
            effective_base = local_node.base_hash.strip() or (str(db_row["base_hash"] or "") if db_row is not None else "")

            if db_row is not None and local_hash == db_hash:
                if str(db_row["base_hash"] or "") != local_hash:
                    await self._db.pool.execute(UPDATE_CONTEXT_NODE_BASE_HASH_SQL, local_hash, clean_ws, rel_path)
                unchanged += 1
                items.append(ContextNodeSyncItemResult(relative_path=rel_path, node_id=node_id, action=ContextSyncAction.UNCHANGED, content_hash=local_hash, base_hash=local_hash))
            elif db_row is None or db_hash == effective_base:
                await self._persist_context_node(project_id, clean_ws, rel_path, node_id, entity_class, local_node.content, local_hash)
                pushed += 1
                items.append(ContextNodeSyncItemResult(relative_path=rel_path, node_id=node_id, action=ContextSyncAction.PUSHED_TO_DB, content_hash=local_hash, base_hash=local_hash))
            elif local_hash == effective_base and db_hash != effective_base:
                if str(db_row["base_hash"] or "") != db_hash:
                    await self._db.pool.execute(UPDATE_CONTEXT_NODE_BASE_HASH_SQL, db_hash, clean_ws, rel_path)
                pulled += 1
                items.append(ContextNodeSyncItemResult(relative_path=rel_path, node_id=str(db_row["node_id"] or node_id), action=ContextSyncAction.PULLED_FROM_DB, content_hash=db_hash, base_hash=db_hash, content=str(db_row["content"])))
            else:
                merged_md = merge_context_node_markdown(local_node.content, str(db_row["content"]), prefer_local_body=True)
                merged_hash = compute_normalized_content_hash(merged_md)
                await self._persist_context_node(project_id, clean_ws, rel_path, node_id, entity_class, merged_md, merged_hash)
                merged += 1
                items.append(ContextNodeSyncItemResult(relative_path=rel_path, node_id=node_id, action=ContextSyncAction.MERGED, content_hash=merged_hash, base_hash=merged_hash, content=merged_md))

        for rel_path, db_row in db_by_path.items():
            if rel_path in seen_paths:
                continue
            db_hash = str(db_row["content_hash"] or "")
            pulled += 1
            items.append(ContextNodeSyncItemResult(relative_path=rel_path, node_id=str(db_row["node_id"] or Path(rel_path).stem), action=ContextSyncAction.PULLED_FROM_DB, content_hash=db_hash, base_hash=db_hash, content=str(db_row["content"])))

        return ContextSyncReport(workspace_path=clean_ws, unchanged=unchanged, pushed_to_db=pushed, pulled_from_db=pulled, merged=merged, items=items)

    async def upsert_node_write_through(
        self, workspace_path: str, relative_path: str, node_id: str, entity_class: str, content: str
    ) -> bool:
        """Атомарно зеркалирует записанный на диск узел `.context/` в `project_context_nodes` (`Write-Through`)."""
        if self._db is None or not self._db.is_connected:
            return False
        clean_ws = workspace_path.strip()
        rel_path = normalize_relative_context_path(relative_path)
        clean_id = node_id.strip() or Path(rel_path).stem
        clean_class = entity_class.strip() or ContextEntityClass.MODULE.value
        content_hash = compute_normalized_content_hash(content)
        project_id = await self._db.pool.fetchval(SELECT_PROJECT_ID_SQL, clean_ws)
        await self._persist_context_node(project_id, clean_ws, rel_path, clean_id, clean_class, content, content_hash)
        return True
