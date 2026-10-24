"""Точка входа HTTP-шлюза и оркестратора жизненного цикла (Layer 2: FastAPI Core Engine).

Управляет инициализацией и корректным освобождением ресурсов через современный паттерн
lifespan (asynccontextmanager), предоставляет диагностический эндпоинт `/health` и точку
регистрации/компиляции проектов `POST /api/projects/register` для хостового CLI на Bun.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from src.core.config import CoreSettings, get_settings
from src.core.db import PostgresDatabase, RedisSessionStore
from src.core.enums import AgentType, ScopeType
from src.core.harness import (
     DEFAULT_LOCALHOST_HOST,
    MCP_HTTP_PATH,
    HarnessCompiler,
    normalize_stack_tags,
    seed_default_components,
)

logger = logging.getLogger("mcp_coop.core")

UPSERT_PROJECT_SQL: str = """
INSERT INTO projects (workspace_path, name, agent_type, stack_tags, last_synced_at)
VALUES ($1, $2, $3, $4, NOW())
ON CONFLICT (workspace_path) DO UPDATE SET
    name = EXCLUDED.name,
    agent_type = EXCLUDED.agent_type,
    stack_tags = EXCLUDED.stack_tags,
    last_synced_at = NOW()
RETURNING id
"""


class ProjectRegistrationRequest(BaseModel):
    """Запрос от хостового CLI-инжектора на регистрацию рабочего пространства и компиляцию харнеса."""

    workspace_path: str = Field(..., min_length=1)
    project_name: str = Field(..., min_length=1)
    agent_type: AgentType = AgentType.ANTIGRAVITY
    stack_tags: list[str] = Field(default_factory=lambda: [ScopeType.GLOBAL.value])
    profile_overrides: dict[str, Any] | None = None


def _build_local_mcp_url(settings: CoreSettings) -> str:
    """Формирует локальный HTTP URL FastMCP-шлюза по порту из настроек ядра."""
    return f"http://{DEFAULT_LOCALHOST_HOST}:{settings.port}{MCP_HTTP_PATH}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Управляет жизненным циклом приложения (Startup / Shutdown) без глобальных переменных.

    Почему выбран паттерн asynccontextmanager lifespan:
    - Заменяет устаревшие хуки @app.on_event("startup" / "shutdown") в современном FastAPI.
    - Сохраняет конфигурацию, пулы соединений (asyncpg, Redis) и компилятор `HarnessCompiler`
      в изолированном контейнере app.state, что гарантирует чистую очистку ресурсов при остановке
      и упрощает тестирование через httpx.ASGITransport / FastAPI TestClient.
    """
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

    logger.info(
        "Starting %s v%s (decisions_api=%s, embedding_api=%s)",
        settings.app_name,
        settings.version,
        settings.decisions_api_configured,
        settings.embedding_api_configured,
    )

    try:
        try:
            await db.connect()
            await seed_default_components(db)
        except Exception as exc:  # noqa: BLE001
            # Мягкая деградация (Graceful Degradation): если ядро запущено локально вне Docker
            # или в быстрых unit-тестах без поднятых контейнеров PostgreSQL и Redis, сервер
            # не падает на старте и использует встроенный Seed харнеса.
            logger.warning("PostgreSQL is unavailable at startup (%s); running in degraded mode", exc)
        try:
            await redis_store.connect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis is unavailable at startup (%s); running in degraded mode", exc)
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

    @application.get("/health", tags=["diagnostics"])
    async def health_check(request: Request) -> dict[str, Any]:
        """Диагностический эндпоинт для Docker healthcheck и хостового CLI-инжектора на Bun.

        Возвращает статус готовности ядра, живой пинг пула PostgreSQL, доступность Redis
        и флаги опциональных внешних интеграций, позволяя клиенту понять, работает ли система
        в режиме полного гибридного RAG или в автономном режиме фолбэка.
        """
        active_settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
        db: PostgresDatabase | None = getattr(request.app.state, "db", None)
        redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
        postgres_connected = await db.check_health() if db is not None else False
        redis_connected = await redis_store.check_health() if redis_store is not None else False
        return {
            "status": "ok",
            "service": active_settings.app_name,
            "version": active_settings.version,
            "postgres_connected": postgres_connected,
            "redis_connected": redis_connected,
            "decisions_api_configured": active_settings.decisions_api_configured,
            "embedding_api_configured": active_settings.embedding_api_configured,
        }

    @application.post("/api/projects/register", tags=["projects"])
    async def register_project(
        payload: ProjectRegistrationRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Регистрирует локальный проект в базе знаний и возвращает скомпилированный артефакт харнеса.

        Если PostgreSQL подключён, атомарно создаёт или обновляет запись в таблице `projects`.
        В автономном режиме (когда БД временно недоступна) продолжает работу за счёт фолбэка
        на `DEFAULT_HARNESS_SEEDS`, возвращая полный комплект файлов под выбранного ИИ-агента.
        """
        active_settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
        db: PostgresDatabase | None = getattr(request.app.state, "db", None)
        redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
        compiler: HarnessCompiler = getattr(request.app.state, "compiler", None) or HarnessCompiler(
            db=db,
            redis=redis_store,
            mcp_url=_build_local_mcp_url(active_settings),
            plugin_version=active_settings.version,
        )

        normalized_tags = normalize_stack_tags(payload.stack_tags)
        project_id: Any = None

        if db is not None and db.is_connected:
            try:
                async with db.pool.acquire() as conn:
                    project_id = await conn.fetchval(
                        UPSERT_PROJECT_SQL,
                        payload.workspace_path,
                        payload.project_name,
                        payload.agent_type.value,
                        list(normalized_tags),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to upsert project %s into PostgreSQL: %s", payload.workspace_path, exc)

        artifact = await compiler.compile_for_project(
            agent_type=payload.agent_type,
            stack_tags=normalized_tags,
            profile_overrides=payload.profile_overrides,
        )

        return {
            "status": "ok",
            "project_id": str(project_id) if project_id is not None else None,
            "persisted_in_db": project_id is not None,
            "artifact": artifact.model_dump(mode="json"),
        }

    return application


app = create_app()
