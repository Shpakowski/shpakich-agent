"""Прикладной сервис 8 инструментов протокола Model Context Protocol (`CoopMCPToolService`, Layer 2).

Связывает транспортный уровень `FastMCP` (`Streamable HTTP /mcp`) с доменными сервисами ядра:
- Долгосрочной гибридной памятью опыта (`coop_search_memory`, `coop_learn_memory`).
- Замкнутым циклом эволюции правил и навыков `Harness` (`coop_propose_harness_edit`, `coop_publish_skill`).
- Локальным Контекстным Графом `.context/` (`coop_query_context_graph`, `coop_upsert_context_node`).
- Диагностикой состояния фабрики и горячим управлением пулом провайдеров Tier 2 (`coop_get_factory_status`, `coop_manage_decision_providers`).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.api.deps import _log_mcp_tool_event
from src.core.config import CoreSettings, get_settings
from src.core.db.compactions import CompactionArchiveStore
from src.core.decision.engine import DecisionEngine
from src.core.decision.models import DecisionRequest
from src.core.decision.providers import RuntimeProviderPool
from src.core.enums import (
    ComponentStatus,
    ComponentType,
    ContextEntityClass,
    ContextEntityStatus,
    DecisionDomain,
    EmbeddingTaskType,
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
from src.core.graph.sync_engine import WorkspaceSyncEngine
from src.core.harness.compiler import HarnessCompiler, normalize_stack_tags
from src.core.services.antigravity import AntigravitySDKService
from src.core.services.embeddings import EmbeddingClient, format_pgvector
from src.core.telemetry import build_and_log_mcp_telemetry

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
    embedding,
    version,
    updated_at
)
VALUES ($1, $2, $3, $4::text[], $5, $6, $7, $8::jsonb, $9, $10, $11, $12::vector, 1, NOW())
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
    embedding = COALESCE(EXCLUDED.embedding, harness_components.embedding),
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
    def _antigravity_sdk(self) -> AntigravitySDKService:
        """Возвращает или лениво создаёт `AntigravitySDKService` в `app.state.antigravity_sdk`."""
        sdk_svc: AntigravitySDKService | None = getattr(
            self._app.state,
            "antigravity_sdk",
            None,
        )
        if sdk_svc is None:
            sdk_svc = AntigravitySDKService(self._settings)
            self._app.state.antigravity_sdk = sdk_svc
        return sdk_svc

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
                antigravity_sdk=self._antigravity_sdk,
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

    @property
    def _compaction_store(self) -> CompactionArchiveStore:
        """Возвращает или лениво создаёт `CompactionArchiveStore` в `app.state.compaction_store`."""
        store: CompactionArchiveStore | None = getattr(
            self._app.state,
            "compaction_store",
            None,
        )
        if store is None:
            store = CompactionArchiveStore(
                db=self._db,
                embedding_client=self._embedding_client,
            )
            self._app.state.compaction_store = store
        else:
            if self._db is not None and store._db is not self._db:
                store._db = self._db
            if store._embedding_client is None:
                store._embedding_client = self._embedding_client
        return store

    @property
    def _sync_engine(self) -> WorkspaceSyncEngine:
        """Возвращает оркестратор двусторонней синхронизации `WorkspaceSyncEngine` с актуальными зависимостями."""
        return WorkspaceSyncEngine(
            db=self._db,
            redis=self._redis,
            engine=self._engine,
            embedding_client=self._embedding_client,
        )

    async def _record_mcp_telemetry(
        self,
        *,
        tool_name: str,
        workspace_path: str,
        args_payload: dict[str, Any],
        result_payload: dict[str, Any],
        start_t: float,
        summary_note: str,
    ) -> None:
        """Формирует телеметрию вызова MCP-инструмента, выводит в Docker-лог и пишет в `hook_events`."""
        telemetry = build_and_log_mcp_telemetry(
            tool_name=tool_name,
            workspace_path=workspace_path,
            args_payload=args_payload,
            result_payload=result_payload,
            duration_ms=(time.perf_counter() - start_t) * 1000.0,
            summary_note=summary_note,
        )
        await _log_mcp_tool_event(
            self._db,
            tool_name=tool_name,
            workspace_path=workspace_path,
            args_payload=args_payload,
            result_payload=result_payload,
            telemetry=telemetry,
        )

    async def search_memory(
        self,
        query: str,
        stack_tags: list[str] | None = None,
        limit: int = 5,
        include_compactions: bool = False,
        workspace_path: str = "",
    ) -> dict[str, Any]:
        """Ищет решения ошибок в долгосрочной базе опыта (`memories`) и опционально сводки `/compact` (`session_compactions`)."""
        start_t = time.perf_counter()
        clean_query = query.strip()
        normalized_tags = stack_tags or [ScopeType.GLOBAL.value]
        results = await self._engine.search_memories(
            clean_query,
            normalized_tags,
            limit=limit,
        )
        response: dict[str, Any] = {
            "query": query,
            "count": len(results),
            "results": results,
        }
        if include_compactions:
            compactions = await self._compaction_store.search_compactions(
                clean_query,
                normalized_tags,
                workspace_path=workspace_path.strip() or None,
                limit=limit,
            )
            response["compactions"] = compactions
            response["compactions_count"] = len(compactions)

        await self._record_mcp_telemetry(
            tool_name="coop_search_memory",
            workspace_path=workspace_path,
            args_payload={
                "query": query,
                "stack_tags": normalized_tags,
                "limit": limit,
                "include_compactions": include_compactions,
            },
            result_payload=response,
            start_t=start_t,
            summary_note=f"found={len(results)} memories",
        )
        return response

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
        start_t = time.perf_counter()
        validated_scope = ScopeType(scope).value
        normalized_tags = stack_tags or [ScopeType.GLOBAL.value]
        result = await self._engine.store_memory(
            title=title,
            error_symptom=error_symptom,
            solution=solution,
            stack_tags=normalized_tags,
            scope=validated_scope,
            source_project_path=source_project_path,
        )
        await self._record_mcp_telemetry(
            tool_name="coop_learn_memory",
            workspace_path=source_project_path or "",
            args_payload={
                "title": title,
                "error_symptom": error_symptom,
                "scope": validated_scope,
                "stack_tags": normalized_tags,
            },
            result_payload=result,
            start_t=start_t,
            summary_note=f"stored title=\"{title[:40]}\"",
        )
        return result

    async def _propose_harness_edit_impl(
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
        """Внутренняя реализация цикла `Propose -> Probe -> Compose` (`HARNESS_PROBE`)."""
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
            doc_text = f"{clean_title}\n{clean_description}"
            vec = await self._embedding_client.embed_text(
                doc_text,
                task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT,
                title=clean_title,
            )
            vec_literal = format_pgvector(vec)

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
                vec_literal,
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
        start_t = time.perf_counter()
        result = await self._propose_harness_edit_impl(
            component_type=component_type,
            slug=slug,
            title=title,
            description=description,
            content=content,
            scope=scope,
            stack_tags=stack_tags,
            metadata=metadata,
            source_project_path=source_project_path,
        )
        await self._record_mcp_telemetry(
            tool_name="coop_propose_harness_edit",
            workspace_path=source_project_path or "",
            args_payload={
                "slug": slug,
                "component_type": component_type,
                "title": title,
                "scope": scope,
                "stack_tags": stack_tags or [],
            },
            result_payload=result,
            start_t=start_t,
            summary_note=f"status={result.get('status')} score={result.get('score')}",
        )
        return result

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
        start_t = time.perf_counter()
        inferred_scope = (
            ScopeType.DOMAIN.value
            if (
                stack_tags
                and any(
                    t.strip().lower() == ScopeType.DOMAIN.value
                    for t in stack_tags
                    if t.strip()
                )
            )
            else ScopeType.GLOBAL.value
        )
        result = await self._propose_harness_edit_impl(
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
        await self._record_mcp_telemetry(
            tool_name="coop_publish_skill",
            workspace_path=source_project_path or "",
            args_payload={
                "slug": slug,
                "description": description,
                "stack_tags": stack_tags or [],
            },
            result_payload=result,
            start_t=start_t,
            summary_note=f"status={result.get('status')} score={result.get('score')}",
        )
        return result

    async def query_context_graph(
        self,
        workspace_path: str,
        node_id: str = "",
        depth: int = DEFAULT_SUBGRAPH_DEPTH,
    ) -> dict[str, Any]:
        """Извлекает локальный подграф окрестности узла `node_id` радиуса `depth` из `.context/` (`nx.DiGraph`)."""
        start_t = time.perf_counter()
        try:
            result = self._graph_manager.query_subgraph(
                workspace_path=workspace_path,
                node_id=node_id,
                depth=depth,
            )
            res_dict = result.model_dump(mode="json")
        except (KeyError, ValueError, OSError) as exc:
            res_dict = {
                "error": str(exc),
                "is_valid": False,
                "root_node_id": node_id.strip() or None,
                "depth": max(0, depth),
            }

        nodes_count = len(res_dict.get("nodes") or [])
        await self._record_mcp_telemetry(
            tool_name="coop_query_context_graph",
            workspace_path=workspace_path,
            args_payload={
                "workspace_path": workspace_path,
                "node_id": node_id,
                "depth": depth,
            },
            result_payload=res_dict,
            start_t=start_t,
            summary_note=f"nodes={nodes_count} valid={res_dict.get('is_valid', False)}",
        )
        return res_dict

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
        start_t = time.perf_counter()
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

        try:
            node_file_path = Path(workspace_path).expanduser().resolve() / node.relative_path
            written_content = node_file_path.read_text(encoding="utf-8")
            await self._sync_engine.upsert_node_write_through(
                workspace_path=workspace_path,
                relative_path=node.relative_path,
                node_id=node.frontmatter.id,
                entity_class=node.frontmatter.entity_class.value,
                content=written_content,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Write-through context node sync skipped: %s", exc)

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
        res_dict = {
            "node": node.model_dump(mode="json"),
            "validation": validation.model_dump(mode="json"),
        }
        await self._record_mcp_telemetry(
            tool_name="coop_upsert_context_node",
            workspace_path=workspace_path,
            args_payload={
                "node_id": node_id,
                "entity_class": entity_class,
                "name": name,
                "files": files,
            },
            result_payload=res_dict,
            start_t=start_t,
            summary_note=f"upserted node={node_id} valid={validation.is_valid}",
        )
        return res_dict

    async def get_factory_status(
        self,
        workspace_path: str = "",
    ) -> dict[str, Any]:
        """Возвращает агрегированную диагностику БД, Redis, `EmbeddingClient`, провайдеров Tier 2 и `.context/`."""
        start_t = time.perf_counter()
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
            "session_compactions": 0,
            "project_context_nodes": 0,
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

        res_dict = {
            "service": self._settings.app_name,
            "version": self._settings.version,
            "postgres_connected": postgres_connected,
            "redis_connected": redis_connected,
            "embedding_client": self._embedding_client.get_status(),
            "antigravity_sdk": self._antigravity_sdk.get_status(),
            "decision_providers": [v.model_dump(mode="json") for v in provider_views],
            "database_counts": counts,
            "context_graph": context_summary,
        }
        await self._record_mcp_telemetry(
            tool_name="coop_get_factory_status",
            workspace_path=workspace_path,
            args_payload={"workspace_path": workspace_path},
            result_payload=res_dict,
            start_t=start_t,
            summary_note=f"pg={postgres_connected} redis={redis_connected}",
        )
        return res_dict

    async def manage_decision_providers(
        self,
        action: str = PROVIDER_ACTION_LIST,
        name: str = "",
        priority: int | None = None,
        enabled: bool | None = None,
        reset_circuit: bool = False,
    ) -> dict[str, Any]:
        """Управляет провайдерами Tier 2 в рантайме (`list` или `update`) без передачи секретных API-ключей в чат."""
        start_t = time.perf_counter()
        normalized_action = action.strip().lower()
        if normalized_action == PROVIDER_ACTION_LIST:
            views = await self._provider_pool.list_providers()
            res_dict: dict[str, Any] = {
                "action": PROVIDER_ACTION_LIST,
                "count": len(views),
                "providers": [v.model_dump(mode="json") for v in views],
            }
        elif normalized_action == PROVIDER_ACTION_UPDATE:
            clean_name = name.strip()
            if not clean_name:
                res_dict = {
                    "action": PROVIDER_ACTION_UPDATE,
                    "updated": False,
                    "error": "Provider 'name' is required for update action.",
                }
            else:
                updated = await self._provider_pool.update_provider(
                    clean_name,
                    priority=priority,
                    enabled=enabled,
                    reset_circuit=reset_circuit,
                )
                if updated is None:
                    res_dict = {
                        "action": PROVIDER_ACTION_UPDATE,
                        "updated": False,
                        "error": f"Provider '{clean_name}' not found.",
                    }
                else:
                    res_dict = {
                        "action": PROVIDER_ACTION_UPDATE,
                        "updated": True,
                        "provider": updated.model_dump(mode="json"),
                    }
        else:
            res_dict = {
                "action": normalized_action,
                "updated": False,
                "error": (
                    f"Unsupported action '{action}'. Use '{PROVIDER_ACTION_LIST}' or '{PROVIDER_ACTION_UPDATE}'."
                ),
            }

        await self._record_mcp_telemetry(
            tool_name="coop_manage_decision_providers",
            workspace_path="",
            args_payload={
                "action": action,
                "name": name,
                "priority": priority,
                "enabled": enabled,
                "reset_circuit": reset_circuit,
            },
            result_payload=res_dict,
            start_t=start_t,
            summary_note=f"action={normalized_action}",
        )
        return res_dict


__all__ = [
    "PROVIDER_ACTION_LIST",
    "PROVIDER_ACTION_UPDATE",
    "UPSERT_HARNESS_COMPONENT_SQL",
    "CoopMCPToolService",
]
