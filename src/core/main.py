"""Точка входа HTTP-шлюза и оркестратора жизненного цикла (Layer 2: FastAPI Core Engine).

Управляет инициализацией и корректным освобождением ресурсов через современный паттерн
lifespan (asynccontextmanager), предоставляет диагностический эндпоинт `/health`, точку
регистрации/компиляции проектов `POST /api/projects/register`, REST API горячего управления
пулом провайдеров Tier 2 (`/api/providers`) и 5 универсальных эндпоинтов жизненных хуков (`/api/hooks/*`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from src.core.config import CoreSettings, get_settings
from src.core.db import PostgresDatabase, RedisSessionStore, SessionLastError
from src.core.decision import (
    COMMAND_ARG_KEYS,
    CanonicalHookInput,
    DecisionEngine,
    DecisionProviderConfig,
    DecisionRequest,
    DecisionResolution,
    RuntimeProviderPool,
    format_hook_response,
    inspect_transcript_tail,
    normalize_hook_payload,
)
from src.core.enums import (
    AgentType,
    DecisionDomain,
    HookEventType,
    RedisFlag,
    ScopeType,
    SessionMetaField,
    VerdictAction,
)
from src.core.harness import (
    DEFAULT_LOCALHOST_HOST,
    MCP_HTTP_PATH,
    HarnessCompiler,
    normalize_stack_tags,
    seed_default_components,
)
from src.core.mcp import CoopMCPGateway, CoopMCPToolService
from src.core.services import EmbeddingClient

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


class ProviderPatchRequest(BaseModel):
    """Запрос на частичное обновление провайдера Tier 2 в рантайм-пуле без перезапуска ядра."""

    priority: int | None = None
    enabled: bool | None = None
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    timeout_ms: int | None = Field(default=None, ge=50, le=10000)
    reset_circuit: bool = False


def _build_local_mcp_url(settings: CoreSettings) -> str:
    """Формирует локальный HTTP URL FastMCP-шлюза по порту из настроек ядра."""
    return f"http://{DEFAULT_LOCALHOST_HOST}:{settings.port}{MCP_HTTP_PATH}"


def _get_engine(request: Request) -> DecisionEngine:
    """Возвращает или лениво создаёт экземпляр `DecisionEngine` в `request.app.state`."""
    engine: DecisionEngine | None = getattr(request.app.state, "decision_engine", None)
    if engine is not None:
        return engine

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
    pool: RuntimeProviderPool = getattr(request.app.state, "provider_pool", None) or RuntimeProviderPool(
        db=db,
        redis=redis_store,
    )
    request.app.state.provider_pool = pool
    engine = DecisionEngine(
        db=db,
        redis=redis_store,
        provider_pool=pool,
        compiler=compiler,
        embedding_client=embedding_client,
        max_consecutive_failures=settings.max_consecutive_cmd_failures,
    )
    request.app.state.decision_engine = engine
    return engine


async def _resolve_project_stack_tags(
    db: PostgresDatabase | None,
    workspace_path: str,
    raw_payload: dict[str, Any],
) -> tuple[str, ...]:
    """Определяет стековые теги проекта из пэйлоада хука или таблицы `projects` PostgreSQL."""
    explicit_tags = raw_payload.get("stack_tags") or raw_payload.get("stackTags")
    if isinstance(explicit_tags, list) and explicit_tags:
        return normalize_stack_tags([str(t) for t in explicit_tags])

    if workspace_path and db is not None and db.is_connected:
        try:
            row = await db.pool.fetchrow(
                "SELECT stack_tags FROM projects WHERE workspace_path = $1",
                workspace_path,
            )
            if row and row["stack_tags"]:
                return normalize_stack_tags(list(row["stack_tags"]))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Project stack lookup failed for %s: %s", workspace_path, exc)

    return (ScopeType.GLOBAL.value,)


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

    app.state.decision_engine = DecisionEngine(
        db=db,
        redis=redis_store,
        provider_pool=provider_pool,
        compiler=compiler,
        embedding_client=embedding_client,
        max_consecutive_failures=settings.max_consecutive_cmd_failures,
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
    application.state.settings = settings
    application.state.provider_pool = RuntimeProviderPool()
    application.state.embedding_client = EmbeddingClient(settings)
    mcp_service = CoopMCPToolService(application)
    mcp_gateway = CoopMCPGateway(application, mcp_service)
    application.state.mcp_service = mcp_service
    application.state.mcp_gateway = mcp_gateway

    @application.get("/health", tags=["diagnostics"])
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
            "embedding_client": emb_client.get_status(),
            "mcp_endpoint": MCP_HTTP_PATH,
        }

    @application.post("/api/projects/register", tags=["projects"])
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

    @application.get("/api/providers", tags=["providers"])
    async def list_providers(request: Request) -> dict[str, Any]:
        """Возвращает отсортированный список провайдеров Tier 2 с актуальным статусом здоровья."""
        engine = _get_engine(request)
        views = await engine.provider_pool.list_providers()
        return {
            "status": "ok",
            "providers": [v.model_dump(mode="json") for v in views],
        }

    @application.post("/api/providers", tags=["providers"])
    async def upsert_provider(
        payload: DecisionProviderConfig,
        request: Request,
    ) -> dict[str, Any]:
        """Добавляет или полностью обновляет провайдера второго контура на лету."""
        engine = _get_engine(request)
        view = await engine.provider_pool.upsert_provider(payload)
        return {
            "status": "ok",
            "provider": view.model_dump(mode="json"),
        }

    @application.patch("/api/providers/{name}", tags=["providers"])
    async def patch_provider(
        name: str,
        payload: ProviderPatchRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Частично обновляет приоритет, ключ, статус включения или сбрасывает Circuit Breaker провайдера."""
        engine = _get_engine(request)
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
        return {
            "status": "ok",
            "provider": view.model_dump(mode="json"),
        }

    @application.delete("/api/providers/{name}", tags=["providers"])
    async def delete_provider(name: str, request: Request) -> dict[str, Any]:
        """Удаляет провайдера Tier 2 из рантайм-пула и таблицы `decision_providers`."""
        engine = _get_engine(request)
        removed = await engine.provider_pool.remove_provider(name)
        if not removed:
            raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
        return {"status": "ok", "removed": name}

    @application.post("/api/decisions/evaluate", tags=["decisions"])
    async def evaluate_decision(
        payload: DecisionRequest,
        request: Request,
    ) -> DecisionResolution:
        """Прямой эндпоинт вычисления решения в любом из 5 доменов `DecisionEngine`."""
        engine = _get_engine(request)
        return await engine.evaluate(payload)


    @application.post("/api/hooks/session-start", tags=["hooks"])
    async def hook_session_start(
        raw_payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        """Обрабатывает старт сессии: инициализирует метаданные в Redis и инжектирует карту `.context/index.md`."""
        hook_in = normalize_hook_payload(HookEventType.SESSION_START, raw_payload)
        db: PostgresDatabase | None = getattr(request.app.state, "db", None)
        redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
        stack_tags = await _resolve_project_stack_tags(db, hook_in.workspace_path, raw_payload)

        if redis_store is not None and redis_store.is_connected:
            try:
                await redis_store.init_session(
                    hook_in.conversation_id,
                    hook_in.workspace_path,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Redis session init failed: %s", exc)

        sys_msg = (
            f"[MCP-COOP Factory]: Active stack tags={list(stack_tags)}. "
            "Follow Learning-First iterative workflow and keep .context/ graph synchronized."
        )
        if hook_in.workspace_path:
            idx_path = Path(hook_in.workspace_path) / ".context" / "index.md"
            if idx_path.is_file():
                try:
                    idx_text = idx_path.read_text(encoding="utf-8").strip()
                    sys_msg = f"{sys_msg}\n\n[Active Context Graph (.context/index.md)]:\n{idx_text}"
                except OSError:
                    pass

        active_settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
        compiler: HarnessCompiler = getattr(request.app.state, "compiler", None) or HarnessCompiler(
            db=db,
            redis=redis_store,
            mcp_url=_build_local_mcp_url(active_settings),
            plugin_version=active_settings.version,
        )
        artifact = await compiler.compile_for_project(
            agent_type=hook_in.agent_type,
            stack_tags=stack_tags,
        )
        sync_files = [f.model_dump(mode="json") for f in artifact.files]

        response_dict = format_hook_response(
            HookEventType.SESSION_START,
            hook_in.dialect,
            system_message=sys_msg,
            sync_files=sync_files,
        )
        await _log_hook_event(db, hook_in, response_dict)
        return response_dict

    @application.post("/api/hooks/pre-tool-use", tags=["hooks"])
    async def hook_pre_tool_use(
        raw_payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        """Перехватывает вызов инструмента: проверяет `PLAN_VALIDATION` для планов или `TOOL_SAFETY` для команд."""
        hook_in = normalize_hook_payload(HookEventType.PRE_TOOL_USE, raw_payload)
        db: PostgresDatabase | None = getattr(request.app.state, "db", None)
        engine = _get_engine(request)
        stack_tags = await _resolve_project_stack_tags(db, hook_in.workspace_path, raw_payload)

        target_path = str(
            hook_in.tool_args.get("Path")
            or hook_in.tool_args.get("path")
            or hook_in.tool_args.get("file_path")
            or ""
        )
        is_plan_write = hook_in.tool_name in ("write_file", "edit_file") and (
            target_path.endswith("implementation_plan.md")
        )

        if is_plan_write:
            plan_content = str(
                hook_in.tool_args.get("Content")
                or hook_in.tool_args.get("Replacement")
                or hook_in.tool_args.get("content")
                or ""
            )
            dec_req = DecisionRequest(
                domain=DecisionDomain.PLAN_VALIDATION,
                conversation_id=hook_in.conversation_id,
                workspace_path=hook_in.workspace_path,
                agent_type=hook_in.agent_type,
                stack_tags=list(stack_tags),
                payload={"content": plan_content, "path": target_path},
            )
        else:
            dec_req = DecisionRequest(
                domain=DecisionDomain.TOOL_SAFETY,
                conversation_id=hook_in.conversation_id,
                workspace_path=hook_in.workspace_path,
                agent_type=hook_in.agent_type,
                stack_tags=list(stack_tags),
                payload={
                    "tool_name": hook_in.tool_name,
                    "tool_args": hook_in.tool_args,
                },
            )

        resolution = await engine.evaluate(dec_req)
        response_dict = format_hook_response(
            HookEventType.PRE_TOOL_USE,
            hook_in.dialect,
            resolution,
        )
        await _log_hook_event(db, hook_in, response_dict)
        return response_dict

    @application.post("/api/hooks/post-tool-use", tags=["hooks"])
    async def hook_post_tool_use(
        raw_payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        """Фиксирует ошибку инструмента в Redis или отмечает устранение сбоя (`MemoryPromotionCandidate`)."""
        hook_in = normalize_hook_payload(HookEventType.POST_TOOL_USE, raw_payload)
        db: PostgresDatabase | None = getattr(request.app.state, "db", None)
        redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)

        cmd = ""
        for key in COMMAND_ARG_KEYS:
            val = hook_in.tool_args.get(key)
            if isinstance(val, str) and val.strip():
                cmd = val.strip()
                break

        eph_msg: str | None = None
        if redis_store is not None and redis_store.is_connected:
            try:
                if hook_in.error:
                    fail_count = await redis_store.record_tool_failure(
                        hook_in.conversation_id,
                        SessionLastError(
                            tool_name=hook_in.tool_name,
                            command=cmd or hook_in.tool_name,
                            error_text=hook_in.error,
                            step_idx=hook_in.step_idx,
                        ),
                    )
                    eph_msg = (
                        f"[MCP-COOP Working Memory]: Recorded tool failure #{fail_count} "
                        f"for '{hook_in.tool_name}'. Inspect the root cause before retrying."
                    )
                else:
                    candidate = await redis_store.mark_error_resolved(
                        hook_in.conversation_id,
                        recovery_command=cmd or hook_in.tool_name,
                    )
                    if candidate is not None:
                        eph_msg = (
                            "[MCP-COOP Memory Promotion]: Previous tool failure was resolved! "
                            "Before finishing, record the root cause and fix into long-term memory."
                        )
            except Exception as exc:  # noqa: BLE001
                logger.debug("PostToolUse Redis tracking failed: %s", exc)

        response_dict = format_hook_response(
            HookEventType.POST_TOOL_USE,
            hook_in.dialect,
            ephemeral_message=eph_msg,
        )
        await _log_hook_event(db, hook_in, response_dict)
        return response_dict

    @application.post("/api/hooks/pre-invocation", tags=["hooks"])
    async def hook_pre_invocation(
        raw_payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        """Анализирует контекст перед вызовом LLM: ре-гидратация после `/compact`, `/plan` и RAG по ошибкам."""
        hook_in = normalize_hook_payload(HookEventType.PRE_INVOCATION, raw_payload)
        db: PostgresDatabase | None = getattr(request.app.state, "db", None)
        redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
        engine = _get_engine(request)
        stack_tags = await _resolve_project_stack_tags(db, hook_in.workspace_path, raw_payload)

        is_post_compact, tail_prompt = inspect_transcript_tail(hook_in.transcript_path)
        active_prompt = hook_in.prompt or tail_prompt
        hints: list[str] = []
        sys_msg: str | None = None

        if is_post_compact:
            rehydrated_msg = (
                "[Post-Compaction Re-Hydration]: Context was compacted. Re-verify `.context/index.md` "
                "and active session state before editing project files."
            )
            if hook_in.workspace_path:
                idx_path = Path(hook_in.workspace_path) / ".context" / "index.md"
                if idx_path.is_file():
                    try:
                        idx_text = idx_path.read_text(encoding="utf-8").strip()
                        rehydrated_msg = f"{rehydrated_msg}\n\n[Active Context Graph (.context/index.md)]:\n{idx_text}"
                    except OSError:
                        pass
            sys_msg = rehydrated_msg

        if "/plan" in active_prompt.lower():
            hints.append(
                "[Plan Guardrail]: Break the implementation plan into small Learning-First iterations "
                "and include a `.context/` graph synchronization step in every iteration."
            )

        if redis_store is not None and redis_store.is_connected:
            try:
                last_err = await redis_store.get_last_error(hook_in.conversation_id)
                if last_err is not None:
                    matches = await engine.search_memories(last_err.error_text, stack_tags)
                    if matches:
                        top = matches[0]
                        hints.append(
                            f"[Error Recovery RAG]: Similar error '{top['title']}' solved previously: {top['solution']}"
                        )
                    else:
                        hints.append(
                            f"[Active Session Error]: Unresolved error in '{last_err.tool_name}': {last_err.error_text[:200]}"
                        )
            except Exception as exc:  # noqa: BLE001
                logger.debug("PreInvocation error lookup failed: %s", exc)

        eph_msg = "\n\n".join(hints) if hints else None
        response_dict = format_hook_response(
            HookEventType.PRE_INVOCATION,
            hook_in.dialect,
            system_message=sys_msg,
            ephemeral_message=eph_msg,
        )
        await _log_hook_event(db, hook_in, response_dict)
        return response_dict

    @application.post("/api/hooks/stop", tags=["hooks"])
    async def hook_stop(
        raw_payload: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        """Перехватывает завершение хода: предлагает сохранить решённую ошибку в `Harness²` (не более 1 раза)."""
        hook_in = normalize_hook_payload(HookEventType.STOP, raw_payload)
        db: PostgresDatabase | None = getattr(request.app.state, "db", None)
        redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)

        resolution = DecisionResolution(
            domain=DecisionDomain.HARNESS_PROBE,
            action=VerdictAction.ALLOW,
            decided_by_tier="stop_gate",
        )

        if not hook_in.stop_hook_active and redis_store is not None and redis_store.is_connected:
            try:
                meta = await redis_store.get_session_meta(hook_in.conversation_id)
                resolved_flag = meta.get(SessionMetaField.RESOLVED_ERROR.value) == RedisFlag.TRUE.value
                prompted_flag = meta.get(SessionMetaField.REFLECTION_PROMPTED.value) == RedisFlag.TRUE.value
                if resolved_flag and not prompted_flag:
                    await redis_store.mark_reflection_prompted(hook_in.conversation_id)
                    resolution = DecisionResolution(
                        domain=DecisionDomain.HARNESS_PROBE,
                        action=VerdictAction.CONTINUE,
                        decided_by_tier="stop_reflection_gate",
                        reason=(
                            "[Harness² Reflection]: You resolved a non-trivial tool error in this session. "
                            "Please propose a memory update or summarize the root cause before finishing."
                        ),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Stop hook Redis check failed: %s", exc)

        response_dict = format_hook_response(
            HookEventType.STOP,
            hook_in.dialect,
            resolution,
        )
        await _log_hook_event(db, hook_in, response_dict)
        return response_dict

    mcp_gateway.mount(application)
    return application


app = create_app()
