"""Провайдеры зависимостей и резолверы проектного контекста из `request.app.state` (Layer 2: FastAPI Core API).

Обеспечивает динамическое разрешение сервисов (`DecisionEngine`, `HookScriptDispatcher`,
`CompactionArchiveStore`) из состояния приложения на каждый HTTP-запрос, гидратацию тегов стека
и пер-проектных оверрайдов (`.context/harness.json` / таблица `projects`), а также асинхронный
аудит вызовов жизненных хуков в таблицу `hook_events`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import Request

from src.core.api.models import HARNESS_CONFIG_REL_PATH
from src.core.config import CoreSettings, get_settings
from src.core.db import CompactionArchiveStore, PostgresDatabase, RedisSessionStore
from src.core.decision import CanonicalHookInput, DecisionEngine, RuntimeProviderPool
from src.core.enums import ScopeType
from src.core.graph import WorkspaceSyncEngine
from src.core.harness import (
    DEFAULT_LOCALHOST_HOST,
    MCP_HTTP_PATH,
    HarnessCompiler,
    HarnessConsolidator,
    HookScriptDispatcher,
    ProjectHarnessOverrides,
    normalize_stack_tags,
    resolve_effective_stack_tags,
)
from src.core.services import AntigravitySDKService, EmbeddingClient

logger = logging.getLogger("mcp_coop.core")


def _build_local_mcp_url(settings: CoreSettings) -> str:
    """Формирует локальный HTTP URL FastMCP-шлюза по порту из настроек ядра."""
    return f"http://{DEFAULT_LOCALHOST_HOST}:{settings.port}{MCP_HTTP_PATH}"


def _get_engine(request: Request) -> DecisionEngine:
    """Возвращает или лениво создаёт экземпляр `DecisionEngine` в `request.app.state`."""
    settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    compiler: HarnessCompiler | None = getattr(request.app.state, "compiler", None)
    embedding_client: EmbeddingClient = getattr(
        request.app.state,
        "embedding_client",
        None,
    ) or EmbeddingClient(settings)
    request.app.state.embedding_client = embedding_client

    engine: DecisionEngine | None = getattr(request.app.state, "decision_engine", None)
    if engine is not None:
        if db is not None and engine._db is not db:
            engine._db = db
        if redis_store is not None and engine._redis is not redis_store:
            engine._redis = redis_store
        if compiler is not None and engine._compiler is not compiler:
            engine._compiler = compiler
        if engine._embedding_client is None:
            engine._embedding_client = embedding_client
        return engine

    antigravity_sdk: AntigravitySDKService = getattr(
        request.app.state,
        "antigravity_sdk",
        None,
    ) or AntigravitySDKService(settings)
    request.app.state.antigravity_sdk = antigravity_sdk
    pool: RuntimeProviderPool = getattr(request.app.state, "provider_pool", None) or RuntimeProviderPool(
        db=db,
        redis=redis_store,
    )
    if db is not None and pool._db is not db:
        pool._db = db
    if redis_store is not None and pool._redis is not redis_store:
        pool._redis = redis_store
    request.app.state.provider_pool = pool
    engine = DecisionEngine(
        db=db,
        redis=redis_store,
        provider_pool=pool,
        compiler=compiler,
        embedding_client=embedding_client,
        antigravity_sdk=antigravity_sdk,
        max_consecutive_failures=settings.max_consecutive_cmd_failures,
    )
    request.app.state.decision_engine = engine
    return engine


def _get_script_dispatcher(request: Request) -> HookScriptDispatcher:
    """Возвращает или лениво создаёт экземпляр `HookScriptDispatcher` в `request.app.state`."""
    settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    embedding_client: EmbeddingClient = getattr(
        request.app.state,
        "embedding_client",
        None,
    ) or EmbeddingClient(settings)
    request.app.state.embedding_client = embedding_client

    dispatcher: HookScriptDispatcher | None = getattr(
        request.app.state,
        "script_dispatcher",
        None,
    )
    if dispatcher is not None:
        if db is not None and dispatcher._db is not db:
            dispatcher._db = db
        if redis_store is not None and dispatcher._redis is not redis_store:
            dispatcher._redis = redis_store
        if dispatcher._embedding_client is None:
            dispatcher._embedding_client = embedding_client
        return dispatcher

    dispatcher = HookScriptDispatcher(
        db=db,
        redis=redis_store,
        embedding_client=embedding_client,
    )
    request.app.state.script_dispatcher = dispatcher
    return dispatcher


def _get_compaction_store(request: Request) -> CompactionArchiveStore:
    """Возвращает или лениво создаёт экземпляр `CompactionArchiveStore` в `request.app.state`."""
    settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    embedding_client: EmbeddingClient = getattr(
        request.app.state,
        "embedding_client",
        None,
    ) or EmbeddingClient(settings)
    request.app.state.embedding_client = embedding_client

    store: CompactionArchiveStore | None = getattr(
        request.app.state,
        "compaction_store",
        None,
    )
    if store is not None:
        if db is not None and store._db is not db:
            store._db = db
        if store._embedding_client is None:
            store._embedding_client = embedding_client
        return store

    store = CompactionArchiveStore(
        db=db,
        embedding_client=embedding_client,
    )
    request.app.state.compaction_store = store
    return store


def _get_sync_engine(request: Request) -> WorkspaceSyncEngine:
    """Возвращает или лениво создаёт экземпляр `WorkspaceSyncEngine` в `request.app.state`."""
    settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    engine = _get_engine(request)
    embedding_client: EmbeddingClient = getattr(
        request.app.state,
        "embedding_client",
        None,
    ) or EmbeddingClient(settings)
    request.app.state.embedding_client = embedding_client

    sync_engine: WorkspaceSyncEngine | None = getattr(
        request.app.state,
        "sync_engine",
        None,
    )
    if sync_engine is not None:
        if db is not None and sync_engine._db is not db:
            sync_engine._db = db
        if redis_store is not None and sync_engine._redis is not redis_store:
            sync_engine._redis = redis_store
        if sync_engine._engine is not engine:
            sync_engine._engine = engine
        if sync_engine._embedding_client is None:
            sync_engine._embedding_client = embedding_client
        return sync_engine

    sync_engine = WorkspaceSyncEngine(
        db=db,
        redis=redis_store,
        engine=engine,
        embedding_client=embedding_client,
    )
    request.app.state.sync_engine = sync_engine
    return sync_engine


def _get_consolidator(request: Request) -> HarnessConsolidator:
    """Возвращает или лениво создаёт экземпляр `HarnessConsolidator` в `request.app.state`."""
    settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    engine = _get_engine(request)
    embedding_client: EmbeddingClient = getattr(
        request.app.state,
        "embedding_client",
        None,
    ) or EmbeddingClient(settings)
    request.app.state.embedding_client = embedding_client

    consolidator: HarnessConsolidator | None = getattr(
        request.app.state,
        "consolidator",
        None,
    )
    if consolidator is not None:
        if db is not None and consolidator._db is not db:
            consolidator._db = db
        if redis_store is not None and consolidator._redis is not redis_store:
            consolidator._redis = redis_store
        if consolidator._engine is not engine:
            consolidator._engine = engine
        if consolidator._embedding_client is None:
            consolidator._embedding_client = embedding_client
        return consolidator

    consolidator = HarnessConsolidator(
        db=db,
        redis=redis_store,
        engine=engine,
        embedding_client=embedding_client,
    )
    request.app.state.consolidator = consolidator
    return consolidator


async def _resolve_project_context(
    db: PostgresDatabase | None,
    workspace_path: str,
    raw_payload: dict[str, Any],
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Разрешает эффективные теги стека и пер-проектные оверрайды из пэйлоада, БД или `.context/harness.json`."""
    explicit_tags = raw_payload.get("stack_tags") or raw_payload.get("stackTags")
    explicit_overrides = (
        raw_payload.get("profile_overrides")
        if "profile_overrides" in raw_payload
        else raw_payload.get("profileOverrides")
    )

    resolved_tags: tuple[str, ...] | None = (
        normalize_stack_tags([str(t) for t in explicit_tags])
        if isinstance(explicit_tags, list) and explicit_tags
        else None
    )
    resolved_overrides: dict[str, Any] | None = (
        dict(explicit_overrides)
        if isinstance(explicit_overrides, dict)
        else None
    )

    if (
        (resolved_tags is None or resolved_overrides is None)
        and workspace_path
        and db is not None
        and db.is_connected
    ):
        try:
            row = await db.pool.fetchrow(
                "SELECT stack_tags, profile_overrides FROM projects WHERE workspace_path = $1",
                workspace_path,
            )
            if row is not None:
                if resolved_tags is None and row["stack_tags"]:
                    resolved_tags = normalize_stack_tags(list(row["stack_tags"]))
                if resolved_overrides is None and row["profile_overrides"] is not None:
                    raw_ov = row["profile_overrides"]
                    parsed_ov = (
                        json.loads(raw_ov)
                        if isinstance(raw_ov, str)
                        else dict(raw_ov)
                    )
                    if isinstance(parsed_ov, dict) and parsed_ov:
                        resolved_overrides = parsed_ov
        except Exception as exc:  # noqa: BLE001
            logger.debug("Project context lookup failed for %s: %s", workspace_path, exc)

    if not resolved_overrides and workspace_path:
        harness_cfg_path = Path(workspace_path) / HARNESS_CONFIG_REL_PATH
        if harness_cfg_path.is_file():
            try:
                disk_data = json.loads(harness_cfg_path.read_text(encoding="utf-8"))
                if isinstance(disk_data, dict):
                    resolved_overrides = disk_data
            except (OSError, json.JSONDecodeError):
                pass

    final_overrides: dict[str, Any] = resolved_overrides or {}
    base_tags: tuple[str, ...] = resolved_tags or (ScopeType.GLOBAL.value,)
    if final_overrides:
        effective_tags = resolve_effective_stack_tags(
            base_tags,
            ProjectHarnessOverrides.from_mapping(final_overrides),
        )
        return effective_tags, final_overrides
    return base_tags, final_overrides


async def _resolve_project_stack_tags(
    db: PostgresDatabase | None,
    workspace_path: str,
    raw_payload: dict[str, Any],
) -> tuple[str, ...]:
    """Определяет эффективные стековые теги проекта из пэйлоада хука или таблицы `projects` PostgreSQL."""
    stack_tags, _ = await _resolve_project_context(db, workspace_path, raw_payload)
    return stack_tags


async def _log_hook_event(
    db: PostgresDatabase | None,
    hook_input: CanonicalHookInput,
    decision_payload: dict[str, Any],
) -> None:
    """Асинхронно фиксирует событие жизненного хука и принятый вердикт в таблице `hook_events`."""
    if db is None or not db.is_connected:
        return
    try:
        await db.pool.execute(
            """
            INSERT INTO hook_events (conversation_id, workspace_path, event_type, tool_name, payload, decision)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
            """,
            hook_input.conversation_id,
            hook_input.workspace_path or None,
            hook_input.event_type.value,
            hook_input.tool_name or None,
            json.dumps(hook_input.raw_payload, ensure_ascii=False),
            json.dumps(decision_payload, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Hook event audit log failed: %s", exc)


__all__ = [
    "_build_local_mcp_url",
    "_get_compaction_store",
    "_get_consolidator",
    "_get_engine",
    "_get_script_dispatcher",
    "_get_sync_engine",
    "_log_hook_event",
    "_resolve_project_context",
    "_resolve_project_stack_tags",
]
