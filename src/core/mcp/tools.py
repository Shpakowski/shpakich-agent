"""Прикладной сервис 8 инструментов протокола Model Context Protocol (`CoopMCPToolService`, Layer 2).

Связывает транспортный уровень `FastMCP` (`Streamable HTTP /mcp`) с доменными сервисами ядра:
- Долгосрочной гибридной памятью опыта (`coop_search_memory`, `coop_learn_memory`).
- Замкнутым циклом эволюции правил и навыков `Harness²` (`coop_propose_harness_edit`, `coop_publish_skill`).
- Локальным Контекстным Графом `.context/` (`coop_query_context_graph`, `coop_upsert_context_node`).
- Диагностикой состояния фабрики и горячим управлением пулом провайдеров Tier 2 (`coop_get_factory_status`, `coop_manage_decision_providers`).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.config import CoreSettings, get_settings
from src.core.decision.engine import DecisionEngine
from src.core.decision.models import DecisionRequest
from src.core.decision.providers import RuntimeProviderPool
from src.core.enums import (
    ComponentStatus,
    ComponentType,
    ContextEntityClass,
    ContextEntityStatus,
    DecisionDomain,
    RedisFlag,
    ScopeType,
    SessionMetaField,
    VerdictAction,
)
from src.core.graph.context_graph import (
    CONTEXT_DIR_NAME,
    DEFAULT_SUBGRAPH_DEPTH,
    ENTITIES_DIR_NAME,
    ContextGraphManager,
)
from src.core.harness.compiler import HarnessCompiler, normalize_stack_tags
from src.core.services.embeddings import EmbeddingClient

if TYPE_CHECKING:
    from fastapi import FastAPI

    from src.core.db.postgres import PostgresDatabase
    from src.core.db.redis_client import RedisSessionStore

logger = logging.getLogger("mcp_coop.core.mcp.tools")

PROVIDER_ACTION_LIST: str = "list"
PROVIDER_ACTION_UPDATE: str = "update"

UPSERT_HARNESS_COMPONENT_SQL: str = """
INSERT INTO harness_components (
    slug,
    component_type,
    scope,
    stack_tags,
    title,
    description,
    content,
    metadata,
    status,
    score,
    source_project_path,
    version,
    updated_at
)
VALUES ($1, $2, $3, $4::text[], $5, $6, $7, $8::jsonb, $9, $10, $11, 1, NOW())
ON CONFLICT (slug) DO UPDATE SET
    component_type = EXCLUDED.component_type,
    scope = EXCLUDED.scope,
    stack_tags = EXCLUDED.stack_tags,
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    content = EXCLUDED.content,
    metadata = EXCLUDED.metadata,
    status = EXCLUDED.status,
    score = EXCLUDED.score,
    source_project_path = EXCLUDED.source_project_path,
    version = harness_components.version + 1,
    updated_at = NOW()
RETURNING id, version
"""


class CoopMCPToolService:
    """Фасад бизнес-логики 8 MCP-инструментов с динамическим разрешением зависимостей из `app.state`."""

    def __init__(self, app: FastAPI) -> None:
        self._app: FastAPI = app
        self._graph_manager: ContextGraphManager = ContextGraphManager()

    @property
    def _settings(self) -> CoreSettings:
        """Возвращает активные настройки из `app.state.settings` или глобальный синглтон."""
        return getattr(self._app.state, "settings", None) or get_settings()

    @property
    def _db(self) -> PostgresDatabase | None:
        """Возвращает активный пул PostgreSQL из `app.state.db`."""
        return getattr(self._app.state, "db", None)

    @property
    def _redis(self) -> RedisSessionStore | None:
        """Возвращает активное хранилище Redis из `app.state.redis`."""
        return getattr(self._app.state, "redis", None)

    @property
    def _embedding_client(self) -> EmbeddingClient:
        """Возвращает или лениво создаёт клиент векторных эмбеддингов в `app.state.embedding_client`."""
        client: EmbeddingClient | None = getattr(
            self._app.state,
            "embedding_client",
            None,
        )
        if client is None:
            client = EmbeddingClient(self._settings)
            self._app.state.embedding_client = client
        return client

    @property
    def _provider_pool(self) -> RuntimeProviderPool:
        """Возвращает или лениво создаёт пул провайдеров Tier 2 в `app.state.provider_pool`."""
        pool: RuntimeProviderPool | None = getattr(
            self._app.state,
            "provider_pool",
            None,
        )
        if pool is None:
            pool = RuntimeProviderPool(db=self._db, redis=self._redis)
            self._app.state.provider_pool = pool
        else:
            if self._db is not None and pool._db is not self._db:
                pool._db = self._db
            if self._redis is not None and pool._redis is not self._redis:
                pool._redis = self._redis
        return pool

    @property
    def _compiler(self) -> HarnessCompiler:
        """Возвращает или лениво создаёт компилятор харнеса в `app.state.compiler`."""
        compiler: HarnessCompiler | None = getattr(self._app.state, "compiler", None)
        if compiler is None:
            compiler = HarnessCompiler(db=self._db, redis=self._redis)
            self._app.state.compiler = compiler
        return compiler

    @property
    def _engine(self) -> DecisionEngine:
        """Возвращает или лениво создаёт `DecisionEngine` с актуальными ссылками на `db`, `redis` и `embedding_client`."""
        engine: DecisionEngine | None = getattr(
            self._app.state,
            "decision_engine",
            None,
        )
        if engine is None:
            engine = DecisionEngine(
                db=self._db,
                redis=self._redis,
                provider_pool=self._provider_pool,
                compiler=self._compiler,
                embedding_client=self._embedding_client,
                max_consecutive_failures=self._settings.max_consecutive_cmd_failures,
            )
            self._app.state.decision_engine = engine
        else:
            if self._db is not None and engine._db is not self._db:
                engine._db = self._db
            if self._redis is not None and engine._redis is not self._redis:
                engine._redis = self._redis
            if engine._embedding_client is None:
                engine._embedding_client = self._embedding_client
        return engine

    async def search_memory(
        self,
        query: str,
        stack_tags: list[str] | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Ищет решения ошибок в долгосрочной базе опыта (`pgvector + tsvector + pg_trgm`)."""
        results = await self._engine.search_memories(
            query,
            stack_tags or [ScopeType.GLOBAL.value],
            limit=limit,
        )
        return {
            "query": query,
            "count": len(results),
            "results": results,
        }

    async def learn_memory(
        self,
        title: str,
        error_symptom: str,
        solution: str,
        stack_tags: list[str] | None = None,
        scope: str = ScopeType.GLOBAL.value,
        source_project_path: str | None = None,
    ) -> dict[str, Any]:
        """Индексирует новое решение ошибки в таблицу `memories` с автоматической векторизацией `EmbeddingGemma 2`."""
        validated_scope = ScopeType(scope).value
        return await self._engine.store_memory(
            title=title,
            error_symptom=error_symptom,
            solution=solution,
            stack_tags=stack_tags or [ScopeType.GLOBAL.value],
            scope=validated_scope,
            source_project_path=source_project_path,
        )

    async def propose_harness_edit(
        self,
        component_type: str,
        slug: str,
        title: str,
        description: str,
        content: str,
        scope: str = ScopeType.GLOBAL.value,
        stack_tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        source_project_path: str | None = None,
    ) -> dict[str, Any]:
        """Проводит кандидата через цикл `Propose -> Probe -> Compose` (`HARNESS_PROBE`) и сохраняет в БД при одобрении."""
        comp_type = ComponentType(component_type)
        comp_scope = ScopeType(scope)
        clean_slug = slug.strip()
        clean_title = title.strip()
        clean_description = description.strip()
        clean_content = content.strip()
        tags = list(normalize_stack_tags(stack_tags))
        meta_payload = dict(metadata or {})

        eval_request = DecisionRequest(
            domain=DecisionDomain.HARNESS_PROBE,
            workspace_path=source_project_path or "",
            stack_tags=tags,
            payload={
                "slug": clean_slug,
                "component_type": comp_type.value,
                "title": clean_title,
                "description": clean_description,
                "content": clean_content,
                "scope": comp_scope.value,
                "metadata": meta_payload,
            },
        )
        resolution = await self._engine.evaluate(eval_request)

        approved = resolution.action == VerdictAction.ALLOW
        status = (
            ComponentStatus.COMPOSED.value
            if approved
            else ComponentStatus.REJECTED.value
        )
        score = float(
            resolution.score
            if resolution.score is not None
            else resolution.confidence
        )

        component_id: str | None = None
        version = 1
        persisted = False

        if approved and self._db is not None and self._db.is_connected:
            row = await self._db.pool.fetchrow(
                UPSERT_HARNESS_COMPONENT_SQL,
                clean_slug,
                comp_type.value,
                comp_scope.value,
                tags,
                clean_title,
                clean_description,
                clean_content,
                json.dumps(meta_payload, ensure_ascii=False),
                status,
                score,
                source_project_path,
            )
            if row is not None:
                component_id = str(row["id"]) if row.get("id") is not None else None
                version = int(row["version"]) if row.get("version") is not None else 1
                persisted = True

            if self._redis is not None and self._redis.is_connected:
                await self._redis.invalidate_rules_cache(tags)

        return {
            "id": component_id,
            "slug": clean_slug,
            "component_type": comp_type.value,
            "scope": comp_scope.value,
            "stack_tags": tags,
            "status": status,
            "score": round(score, 3),
            "action": resolution.action.value,
            "decided_by_tier": resolution.decided_by_tier,
            "reason": resolution.reason,
            "version": version,
            "persisted": persisted,
        }

    async def publish_skill(
        self,
        slug: str,
        description: str,
        instructions_md: str,
        stack_tags: list[str] | None = None,
        helper_scripts: dict[str, str] | None = None,
        source_project_path: str | None = None,
    ) -> dict[str, Any]:
        """Публикует новый переиспользуемый навык (`SKILL.md` + скрипты) через конвейер `propose_harness_edit`."""
        inferred_scope = (
            ScopeType.DOMAIN.value
            if (
                stack_tags
                and any(
                    t.strip().lower() != ScopeType.GLOBAL.value
                    for t in stack_tags
                    if t.strip()
                )
            )
            else ScopeType.GLOBAL.value
        )
        return await self.propose_harness_edit(
            component_type=ComponentType.SKILL.value,
            slug=slug,
            title=slug,
            description=description,
            content=instructions_md,
            scope=inferred_scope,
            stack_tags=stack_tags,
            metadata={"helper_scripts": dict(helper_scripts or {})},
            source_project_path=source_project_path,
        )

    async def query_context_graph(
        self,
        workspace_path: str,
        node_id: str = "",
        depth: int = DEFAULT_SUBGRAPH_DEPTH,
    ) -> dict[str, Any]:
        """Извлекает локальный подграф окрестности узла `node_id` радиуса `depth` из `.context/` (`nx.DiGraph`)."""
        try:
            result = self._graph_manager.query_subgraph(
                workspace_path=workspace_path,
                node_id=node_id,
                depth=depth,
            )
            return result.model_dump(mode="json")
        except (KeyError, ValueError, OSError) as exc:
            return {
                "error": str(exc),
                "is_valid": False,
                "root_node_id": node_id.strip() or None,
                "depth": max(0, depth),
            }

    async def upsert_context_node(
        self,
        workspace_path: str,
        entity_class: str,
        node_id: str,
        name: str,
        files: list[str],
        body_md: str,
        trace_note: str,
        stack: list[str] | None = None,
        depends_on: list[str] | None = None,
        implements: list[str] | None = None,
        governed_by: list[str] | None = None,
        status: str = ContextEntityStatus.ACTIVE.value,
        conversation_id: str = "",
    ) -> dict[str, Any]:
        """Атомарно создаёт или обновляет `.md`-узел графа `.context/entities/` с записью в `AI Traces`."""
        node = self._graph_manager.upsert_node(
            workspace_path=workspace_path,
            entity_class=ContextEntityClass(entity_class),
            node_id=node_id,
            name=name,
            files=files,
            body_md=body_md,
            trace_note=trace_note,
            stack=stack or [],
            depends_on=depends_on or [],
            implements=implements or [],
            governed_by=governed_by or [],
            status=ContextEntityStatus(status),
        )

        clean_conv = conversation_id.strip()
        if clean_conv and self._redis is not None and self._redis.is_connected:
            try:
                await self._redis.set_session_meta_field(
                    clean_conv,
                    SessionMetaField.CONTEXT_UPDATED,
                    RedisFlag.TRUE.value,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to update session context flag in Redis: %s", exc)

        _, _, validation = self._graph_manager.build_and_validate(workspace_path)
        return {
            "node": node.model_dump(mode="json"),
            "validation": validation.model_dump(mode="json"),
        }

    async def get_factory_status(
        self,
        workspace_path: str = "",
    ) -> dict[str, Any]:
        """Возвращает агрегированную диагностику БД, Redis, `EmbeddingClient`, провайдеров Tier 2 и `.context/`."""
        db = self._db
        redis_store = self._redis
        postgres_connected = await db.check_health() if db is not None else False
        redis_connected = (
            await redis_store.check_health() if redis_store is not None else False
        )

        counts: dict[str, int] = {
            "projects": 0,
            "harness_components": 0,
            "memories": 0,
            "hook_events": 0,
        }
        if db is not None and db.is_connected:
            for table_name in tuple(counts):
                try:
                    val = await db.pool.fetchval(f"SELECT COUNT(*) FROM {table_name}")
                    counts[table_name] = int(val or 0)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Count query failed for %s: %s", table_name, exc)

        provider_views = await self._provider_pool.list_providers()
        context_summary: dict[str, Any] | None = None
        if workspace_path.strip():
            entities_dir = (
                Path(workspace_path).expanduser().resolve()
                / CONTEXT_DIR_NAME
                / ENTITIES_DIR_NAME
            )
            if entities_dir.is_dir():
                _, _, report = self._graph_manager.build_and_validate(workspace_path)
                context_summary = report.model_dump(mode="json")

        return {
            "service": self._settings.app_name,
            "version": self._settings.version,
            "postgres_connected": postgres_connected,
            "redis_connected": redis_connected,
            "embedding_client": self._embedding_client.get_status(),
            "decision_providers": [v.model_dump(mode="json") for v in provider_views],
            "database_counts": counts,
            "context_graph": context_summary,
        }

    async def manage_decision_providers(
        self,
        action: str = PROVIDER_ACTION_LIST,
        name: str = "",
        priority: int | None = None,
        enabled: bool | None = None,
        reset_circuit: bool = False,
    ) -> dict[str, Any]:
        """Управляет провайдерами Tier 2 в рантайме (`list` или `update`) без передачи секретных API-ключей в чат."""
        normalized_action = action.strip().lower()
        if normalized_action == PROVIDER_ACTION_LIST:
            views = await self._provider_pool.list_providers()
            return {
                "action": PROVIDER_ACTION_LIST,
                "count": len(views),
                "providers": [v.model_dump(mode="json") for v in views],
            }

        if normalized_action == PROVIDER_ACTION_UPDATE:
            clean_name = name.strip()
            if not clean_name:
                return {
                    "action": PROVIDER_ACTION_UPDATE,
                    "updated": False,
                    "error": "Provider 'name' is required for update action.",
                }
            updated = await self._provider_pool.update_provider(
                clean_name,
                priority=priority,
                enabled=enabled,
                reset_circuit=reset_circuit,
            )
            if updated is None:
                return {
                    "action": PROVIDER_ACTION_UPDATE,
                    "updated": False,
                    "error": f"Provider '{clean_name}' not found.",
                }
            return {
                "action": PROVIDER_ACTION_UPDATE,
                "updated": True,
                "provider": updated.model_dump(mode="json"),
            }

        return {
            "action": normalized_action,
            "updated": False,
            "error": (
                f"Unsupported action '{action}'. Use '{PROVIDER_ACTION_LIST}' or '{PROVIDER_ACTION_UPDATE}'."
            ),
        }


__all__ = [
    "PROVIDER_ACTION_LIST",
    "PROVIDER_ACTION_UPDATE",
    "UPSERT_HARNESS_COMPONENT_SQL",
    "CoopMCPToolService",
]
