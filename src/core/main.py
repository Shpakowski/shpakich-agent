"""Точка входа HTTP-шлюза и оркестратора жизненного цикла (Layer 2: FastAPI Core Engine).

Управляет инициализацией и корректным освобождением ресурсов через современный паттерн
lifespan (asynccontextmanager), подключает модульные маршрутизаторы управления (`management_router`),
жизненных хуков (`hooks_router`) и локальный MCP-шлюз (`CoopMCPGateway`), сохраняя 100% обратную
совместимость всех публичных и внутренних символов пакета `src.core.main`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.core.api import (
    HARNESS_CONFIG_REL_PATH,
    MAX_PLAN_COMPACTIONS_INJECTED,
    MAX_PLAN_MEMORIES_INJECTED,
    MAX_PLAN_PATTERNS_INJECTED,
    PLAN_KEYWORDS,
    UPDATE_COMPONENT_STATUS_SQL,
    UPSERT_PROJECT_SQL,
    ComponentPatchRequest,
    ContextHealRequest,
    HarnessConsolidateRequest,
    ProjectRegistrationRequest,
    ProviderPatchRequest,
    SyncContextRequest,
    SyncIngestRequest,
    _build_local_mcp_url,
    _build_plan_best_practices_hint,
    _get_compaction_store,
    _get_consolidator,
    _get_engine,
    _get_script_dispatcher,
    _get_sync_engine,
    _get_visualizer_service,
    _log_hook_event,
    _resolve_project_context,
    _resolve_project_stack_tags,
    hooks_router,
    management_router,
    sync_router,
    visualizer_router,
)
from src.core.config import CoreSettings, get_settings
from src.core.db import CompactionArchiveStore, PostgresDatabase, RedisSessionStore
from src.core.decision import DecisionEngine, RuntimeProviderPool
from src.core.graph import WorkspaceSyncEngine
from src.core.harness import (
    HarnessCompiler,
    HarnessConsolidator,
    HookScriptDispatcher,
    seed_default_components,
)
from src.core.mcp import CoopMCPGateway, CoopMCPToolService
from src.core.services import AntigravitySDKService, EmbeddingClient
from src.core.visualizer import VisualizerGraphService

logger = logging.getLogger("mcp_coop.core")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Управляет жизненным циклом приложения (Startup / Shutdown) без глобальных переменных."""
    settings: CoreSettings = get_settings()
    app.state.settings = settings

    db = PostgresDatabase(settings.postgres_dsn)
    app.state.db = db

    redis_store = RedisSessionStore(
        settings.redis_url,
        session_ttl_seconds=settings.redis_session_ttl_seconds,
        cache_ttl_seconds=settings.redis_cache_ttl_seconds,
    )
    app.state.redis = redis_store

    compiler = HarnessCompiler(
        db=db,
        redis=redis_store,
        mcp_url=_build_local_mcp_url(settings),
        plugin_version=settings.version,
    )
    app.state.compiler = compiler

    provider_pool: RuntimeProviderPool = getattr(
        app.state,
        "provider_pool",
        None,
    ) or RuntimeProviderPool(db=db, redis=redis_store)
    provider_pool._db = db
    provider_pool._redis = redis_store
    app.state.provider_pool = provider_pool

    embedding_client: EmbeddingClient = getattr(
        app.state,
        "embedding_client",
        None,
    ) or EmbeddingClient(settings)
    app.state.embedding_client = embedding_client

    antigravity_sdk: AntigravitySDKService = getattr(
        app.state,
        "antigravity_sdk",
        None,
    ) or AntigravitySDKService(settings)
    app.state.antigravity_sdk = antigravity_sdk

    app.state.compaction_store = CompactionArchiveStore(
        db=db,
        embedding_client=embedding_client,
    )
    app.state.decision_engine = DecisionEngine(
        db=db,
        redis=redis_store,
        provider_pool=provider_pool,
        compiler=compiler,
        embedding_client=embedding_client,
        antigravity_sdk=antigravity_sdk,
        max_consecutive_failures=settings.max_consecutive_cmd_failures,
    )
    app.state.script_dispatcher = HookScriptDispatcher(
        db=db,
        redis=redis_store,
        embedding_client=embedding_client,
    )
    app.state.sync_engine = WorkspaceSyncEngine(
        db=db,
        redis=redis_store,
        engine=app.state.decision_engine,
        embedding_client=embedding_client,
    )
    app.state.consolidator = HarnessConsolidator(
        db=db,
        redis=redis_store,
        engine=app.state.decision_engine,
        embedding_client=embedding_client,
    )
    app.state.visualizer_service = VisualizerGraphService(
        db=db,
        embedding_client=embedding_client,
    )

    mcp_gateway: CoopMCPGateway | None = getattr(app.state, "mcp_gateway", None)

    try:
        try:
            await db.connect()
            await seed_default_components(db)
            await provider_pool.sync_from_db()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PostgreSQL is unavailable at startup (%s); running in degraded mode", exc)
        try:
            await redis_store.connect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis is unavailable at startup (%s); running in degraded mode", exc)

        await provider_pool.seed_from_env(settings.openai_api_key)
        if mcp_gateway is not None:
            async with mcp_gateway.run_lifespan():
                yield
        else:
            yield
    finally:
        await redis_store.close()
        await db.close()
        logger.info("Shutting down %s and releasing external resources", settings.app_name)


def create_app() -> FastAPI:
    """Фабрика экземпляра FastAPI для запуска через Uvicorn и изоляции в интеграционных тестах."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="MCP-COOP Agentic Factory Core Engine (FastAPI + FastMCP)",
        lifespan=lifespan,
    )
    embedding_client = EmbeddingClient(settings)
    application.state.settings = settings
    application.state.provider_pool = RuntimeProviderPool()
    application.state.embedding_client = embedding_client
    application.state.antigravity_sdk = AntigravitySDKService(settings)
    application.state.compaction_store = CompactionArchiveStore(
        db=None,
        embedding_client=embedding_client,
    )
    application.state.visualizer_service = VisualizerGraphService(
        db=None,
        embedding_client=embedding_client,
    )
    mcp_service = CoopMCPToolService(application)
    mcp_gateway = CoopMCPGateway(application, mcp_service)
    application.state.mcp_service = mcp_service
    application.state.mcp_gateway = mcp_gateway

    application.include_router(management_router)
    application.include_router(sync_router)
    application.include_router(hooks_router)
    application.include_router(visualizer_router)
    mcp_gateway.mount(application)
    return application


app = create_app()

__all__ = [
    "HARNESS_CONFIG_REL_PATH",
    "MAX_PLAN_COMPACTIONS_INJECTED",
    "MAX_PLAN_MEMORIES_INJECTED",
    "MAX_PLAN_PATTERNS_INJECTED",
    "PLAN_KEYWORDS",
    "UPDATE_COMPONENT_STATUS_SQL",
    "UPSERT_PROJECT_SQL",
    "ComponentPatchRequest",
    "ContextHealRequest",
    "HarnessConsolidateRequest",
    "ProjectRegistrationRequest",
    "ProviderPatchRequest",
    "SyncContextRequest",
    "SyncIngestRequest",
    "_build_local_mcp_url",
    "_build_plan_best_practices_hint",
    "_get_compaction_store",
    "_get_consolidator",
    "_get_engine",
    "_get_script_dispatcher",
    "_get_sync_engine",
    "_get_visualizer_service",
    "_log_hook_event",
    "_resolve_project_context",
    "_resolve_project_stack_tags",
    "app",
    "create_app",
    "lifespan",
    "visualizer_router",
]
