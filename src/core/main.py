"""Точка входа HTTP-шлюза и оркестратора жизненного цикла (Layer 2: FastAPI Core Engine).

Управляет инициализацией и корректным освобождением ресурсов через современный паттерн
lifespan (asynccontextmanager) и предоставляет диагностический эндпоинт /health для CLI и Docker.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request

from src.core.config import CoreSettings, get_settings

logger = logging.getLogger("mcp_coop.core")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Управляет жизненным циклом приложения (Startup / Shutdown) без глобальных переменных.

    Почему выбран паттерн asynccontextmanager lifespan:
    - Заменяет устаревшие хуки @app.on_event("startup" / "shutdown") в современном FastAPI.
    - Сохраняет конфигурацию и будущие пулы соединений (asyncpg, Redis, httpx.AsyncClient)
      в изолированном контейнере app.state, что гарантирует чистую очистку ресурсов при остановке
      и упрощает тестирование через httpx.ASGITransport.
    """
    settings: CoreSettings = get_settings()
    app.state.settings = settings

    logger.info(
        "Starting %s v%s (decisions_api=%s, embedding_api=%s)",
        settings.app_name,
        settings.version,
        settings.decisions_api_configured,
        settings.embedding_api_configured,
    )

    try:
        yield
    finally:
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

        Возвращает статус готовности ядра и флаги доступности опциональных внешних интеграций,
        позволяя клиенту понять, работает ли система в режиме полного гибридного RAG
        или в автономном режиме фолбэка (tsvector + pg_trgm + детерминированные эвристики).
        """
        active_settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
        return {
            "status": "ok",
            "service": active_settings.app_name,
            "version": active_settings.version,
            "decisions_api_configured": active_settings.decisions_api_configured,
            "embedding_api_configured": active_settings.embedding_api_configured,
        }

    return application


app = create_app()

