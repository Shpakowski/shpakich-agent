"""Маршрутизатор управляющих REST API эндпоинтов ядра (Layer 2: FastAPI Management Routes).

Предоставляет диагностический эндпоинт `GET /health`, фоновое самоисцеление графа
`POST /api/context/heal`, регистрацию и компиляцию проектов `POST /api/projects/register`,
управление каталогом компонентов `Harness` (`/api/harness/components`), горячее управление
пулом провайдеров Tier 2 (`/api/providers`) и прямую точку оценки решений `POST /api/decisions/evaluate`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.core.api.deps import (
    _build_local_mcp_url,
    _get_engine,
    _resolve_project_context,
)
from src.core.api.models import (
    UPDATE_COMPONENT_STATUS_SQL,
    UPSERT_PROJECT_SQL,
    ComponentPatchRequest,
    ContextHealRequest,
    ProjectRegistrationRequest,
    ProviderPatchRequest,
)
from src.core.config import CoreSettings, get_settings
from src.core.db import PostgresDatabase, RedisSessionStore
from src.core.decision import (
    DecisionProviderConfig,
    DecisionRequest,
    DecisionResolution,
)
from src.core.enums import EMBEDDING_PROVIDER_DIALECTS, ComponentStatus
from src.core.harness import (
    MCP_HTTP_PATH,
    HarnessCompiler,
    normalize_stack_tags,
)
from src.core.harness.profiles import list_agent_catalog_metadata
from src.core.services import AntigravitySDKService, EmbeddingClient

logger = logging.getLogger("mcp_coop.core")

management_router = APIRouter()


@management_router.get("/health", tags=["diagnostics"])
async def health_check(request: Request) -> dict[str, Any]:
    """Диагностический эндпоинт для Docker healthcheck и хостового CLI-инжектора на Bun."""
    active_settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    emb_client: EmbeddingClient = getattr(
        request.app.state,
        "embedding_client",
        None,
    ) or EmbeddingClient(active_settings)
    agy_sdk: AntigravitySDKService = getattr(
        request.app.state,
        "antigravity_sdk",
        None,
    ) or AntigravitySDKService(active_settings)
    postgres_connected = await db.check_health() if db is not None else False
    redis_connected = await redis_store.check_health() if redis_store is not None else False
    return {
        "status": "ok",
        "service": active_settings.app_name,
        "version": active_settings.version,
        "postgres_connected": postgres_connected,
        "redis_connected": redis_connected,
        "decisions_api_configured": active_settings.decisions_api_configured,
        "embedding_api_configured": emb_client.is_configured,
        "antigravity_sdk_configured": active_settings.antigravity_sdk_configured,
        "embedding_client": emb_client.get_status(),
        "antigravity_sdk": agy_sdk.get_status(),
        "mcp_endpoint": MCP_HTTP_PATH,
    }


@management_router.post("/api/context/heal", tags=["context"])
async def heal_project_context(
    payload: ContextHealRequest,
    request: Request,
) -> dict[str, Any]:
    """Запускает двухфазное самоисцеление графа `.context/` (`Background Context Healer`)."""
    active_settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
    agy_sdk: AntigravitySDKService = getattr(
        request.app.state,
        "antigravity_sdk",
        None,
    ) or AntigravitySDKService(active_settings)
    report = await agy_sdk.heal_context_graph(
        payload.workspace_path,
        changed_files=payload.changed_files,
        diff_summary=payload.diff_summary,
        target_agent=payload.agent_type,
    )
    return report.model_dump(mode="json")


@management_router.post("/api/projects/register", tags=["projects"])
async def register_project(
    payload: ProjectRegistrationRequest,
    request: Request,
) -> dict[str, Any]:
    """Регистрирует локальный проект в базе знаний и возвращает скомпилированный артефакт харнеса."""
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
                    json.dumps(payload.profile_overrides or {}, ensure_ascii=False),
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


@management_router.get("/api/harness/components", tags=["harness"])
async def list_harness_components(
    request: Request,
    workspace_path: str = "",
    stack_tags: str = "",
) -> dict[str, Any]:
    """Возвращает каталог всех компонентов `Harness` с вычисленным статусом для стека/проекта."""
    active_settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    compiler: HarnessCompiler = getattr(request.app.state, "compiler", None) or HarnessCompiler(
        db=db,
        redis=redis_store,
        mcp_url=_build_local_mcp_url(active_settings),
        plugin_version=active_settings.version,
    )

    explicit_tags = [tag.strip() for tag in stack_tags.split(",") if tag.strip()]
    raw_lookup: dict[str, Any] = {"stack_tags": explicit_tags} if explicit_tags else {}
    resolved_tags, profile_overrides = await _resolve_project_context(
        db,
        workspace_path.strip(),
        raw_lookup,
    )
    items = await compiler.list_catalog_components(
        resolved_tags,
        profile_overrides=profile_overrides,
    )
    return {
        "status": "ok",
        "count": len(items),
        "stack_tags": list(resolved_tags),
        "profile_overrides": profile_overrides,
        "components": items,
        "agents": list_agent_catalog_metadata(),
    }


@management_router.patch("/api/harness/components/{slug}", tags=["harness"])
async def patch_harness_component(
    slug: str,
    payload: ComponentPatchRequest,
    request: Request,
) -> dict[str, Any]:
    """Обновляет глобальный статус компонента `Harness` в PostgreSQL и сбрасывает кэш правил в Redis."""
    if payload.status is not None:
        target_status = payload.status
    elif payload.enabled is True:
        target_status = ComponentStatus.COMPOSED
    elif payload.enabled is False:
        target_status = ComponentStatus.DISABLED
    else:
        raise HTTPException(
            status_code=400,
            detail="Either 'status' or 'enabled' must be provided",
        )

    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    if db is None or not db.is_connected:
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL is not connected; global catalog cannot be modified offline",
        )

    clean_slug = slug.strip()
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            UPDATE_COMPONENT_STATUS_SQL,
            clean_slug,
            target_status.value,
        )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Harness component '{clean_slug}' not found",
        )

    if redis_store is not None and redis_store.is_connected:
        try:
            await redis_store.invalidate_rules_cache()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to invalidate Redis rules cache: %s", exc)

    return {
        "status": "ok",
        "component": {
            "slug": str(row["slug"]),
            "component_type": str(row["component_type"]),
            "scope": str(row["scope"]),
            "stack_tags": list(row["stack_tags"] or []),
            "title": str(row["title"]),
            "status": str(row["status"]),
            "score": float(row["score"]),
            "version": int(row["version"]),
        },
    }


@management_router.get("/api/providers", tags=["providers"])
async def list_providers(request: Request) -> dict[str, Any]:
    """Возвращает отсортированный список провайдеров Tier 2 с актуальным статусом здоровья."""
    engine = _get_engine(request)
    views = await engine.provider_pool.list_providers()
    return {
        "status": "ok",
        "providers": [v.model_dump(mode="json") for v in views],
    }


@management_router.post("/api/providers", tags=["providers"])
async def upsert_provider(
    payload: DecisionProviderConfig,
    request: Request,
) -> dict[str, Any]:
    """Добавляет или полностью обновляет провайдера в рантайм-пуле (Tier 2 или Embeddings) на лету."""
    engine = _get_engine(request)
    prev_emb = engine.provider_pool.get_enabled_embedding_providers()
    prev_model = prev_emb[0].model if prev_emb else None

    view = await engine.provider_pool.upsert_provider(payload)
    if payload.enabled and payload.dialect in EMBEDDING_PROVIDER_DIALECTS:
        new_emb = engine.provider_pool.get_enabled_embedding_providers()
        new_model = new_emb[0].model if new_emb else None
        model_switched = bool(prev_model and new_model and prev_model != new_model)
        db: PostgresDatabase | None = getattr(request.app.state, "db", None)
        emb_client: EmbeddingClient | None = getattr(
            request.app.state,
            "embedding_client",
            None,
        )
        if db is not None and emb_client is not None:
            asyncio.create_task(
                emb_client.backfill_missing_embeddings(db, force_reindex=model_switched)
            )
    return {
        "status": "ok",
        "provider": view.model_dump(mode="json"),
    }


@management_router.patch("/api/providers/{name}", tags=["providers"])
async def patch_provider(
    name: str,
    payload: ProviderPatchRequest,
    request: Request,
) -> dict[str, Any]:
    """Частично обновляет приоритет, ключ, статус включения или сбрасывает Circuit Breaker провайдера."""
    engine = _get_engine(request)
    prev_emb = engine.provider_pool.get_enabled_embedding_providers()
    prev_model = prev_emb[0].model if prev_emb else None

    view = await engine.provider_pool.update_provider(
        name,
        priority=payload.priority,
        enabled=payload.enabled,
        api_key=payload.api_key,
        model=payload.model,
        base_url=payload.base_url,
        timeout_ms=payload.timeout_ms,
        reset_circuit=payload.reset_circuit,
    )
    if view is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    if view.enabled and view.dialect in EMBEDDING_PROVIDER_DIALECTS:
        new_emb = engine.provider_pool.get_enabled_embedding_providers()
        new_model = new_emb[0].model if new_emb else None
        model_switched = bool(prev_model and new_model and prev_model != new_model)
        db: PostgresDatabase | None = getattr(request.app.state, "db", None)
        emb_client: EmbeddingClient | None = getattr(
            request.app.state,
            "embedding_client",
            None,
        )
        if db is not None and emb_client is not None:
            asyncio.create_task(
                emb_client.backfill_missing_embeddings(db, force_reindex=model_switched)
            )
    return {
        "status": "ok",
        "provider": view.model_dump(mode="json"),
    }


@management_router.delete("/api/providers/{name}", tags=["providers"])
async def delete_provider(name: str, request: Request) -> dict[str, Any]:
    """Удаляет провайдера Tier 2 из рантайм-пула и таблицы `decision_providers`."""
    engine = _get_engine(request)
    removed = await engine.provider_pool.remove_provider(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return {"status": "ok", "removed": name}


@management_router.post("/api/decisions/evaluate", tags=["decisions"])
async def evaluate_decision(
    payload: DecisionRequest,
    request: Request,
) -> DecisionResolution:
    """Прямой эндпоинт вычисления решения в любом из 5 доменов `DecisionEngine`."""
    engine = _get_engine(request)
    return await engine.evaluate(payload)


__all__ = [
    "delete_provider",
    "evaluate_decision",
    "heal_project_context",
    "health_check",
    "list_harness_components",
    "list_providers",
    "management_router",
    "patch_harness_component",
    "patch_provider",
    "register_project",
    "upsert_provider",
]
