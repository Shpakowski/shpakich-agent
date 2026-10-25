"""Маршрутизатор 5 жизненных хуков ИИ-агентов (`/api/hooks/*`, Layer 2: FastAPI Hooks Gateway).

Обрабатывает фазы `SessionStart`, `PreToolUse`, `PostToolUse`, `PreInvocation` и `Stop`:
интегрирует `DecisionEngine`, `HookScriptDispatcher`, архивацию сводок `/compact` (`SPEC-003`)
с дедупликацией через Redis (`last_compact_hash`) и ре-гидратацией `.context/index.md`,
а также динамическую инъекцию паттернов и прошлых сводок при планировании (`/plan`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from src.core.api.deps import (
    _build_local_mcp_url,
    _get_compaction_store,
    _get_engine,
    _get_script_dispatcher,
    _log_hook_event,
    _resolve_project_context,
)
from src.core.api.models import (
    MAX_PLAN_COMPACTIONS_INJECTED,
    MAX_PLAN_MEMORIES_INJECTED,
    MAX_PLAN_PATTERNS_INJECTED,
    PLAN_KEYWORDS,
)
from src.core.config import CoreSettings, get_settings
from src.core.db import (
    CompactionArchiveStore,
    PostgresDatabase,
    RedisSessionStore,
    SessionLastError,
)
from src.core.decision import (
    COMMAND_ARG_KEYS,
    DecisionEngine,
    DecisionRequest,
    DecisionResolution,
    format_hook_response,
    inspect_transcript_tail_detailed,
    normalize_hook_payload,
)
from src.core.enums import (
    ComponentEffectiveState,
    ComponentType,
    DecisionDomain,
    HookEventType,
    RedisFlag,
    SessionMetaField,
    VerdictAction,
)
from src.core.harness import HarnessCompiler

logger = logging.getLogger("mcp_coop.core")

hooks_router = APIRouter()


async def _build_plan_best_practices_hint(
    compiler: HarnessCompiler,
    engine: DecisionEngine,
    active_prompt: str,
    stack_tags: tuple[str, ...],
    profile_overrides: dict[str, Any],
    compaction_store: CompactionArchiveStore | None = None,
    workspace_path: str = "",
) -> str:
    """Формирует транзиентную подсказку для `/plan` из `harness_components`, `memories` и `session_compactions`."""
    lines: list[str] = [
        (
            "[Plan Guardrail]: Break the implementation plan into small Learning-First iterations "
            "and include a `.context/` graph synchronization step in every iteration."
        )
    ]
    try:
        catalog = await compiler.list_catalog_components(
            stack_tags,
            profile_overrides=profile_overrides,
        )
        active_states = {
            ComponentEffectiveState.ACTIVE_AUTO.value,
            ComponentEffectiveState.ACTIVE_FORCED.value,
        }
        plan_types = {
            ComponentType.SYSTEM_PROMPT.value,
            ComponentType.TOOL_GUIDANCE.value,
            ComponentType.SKILL.value,
        }
        active_patterns = [
            item
            for item in catalog
            if item.get("effective_state") in active_states
            and item.get("component_type") in plan_types
        ][:MAX_PLAN_PATTERNS_INJECTED]
        if active_patterns:
            pattern_items = "; ".join(
                f"{item.get('slug')} ({item.get('title')})" for item in active_patterns
            )
            lines.append(f"[Stack Harness² Patterns ({', '.join(stack_tags)})]: {pattern_items}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Plan catalog pattern lookup failed: %s", exc)

    query_text = active_prompt.strip() or "architecture design plan best practices"
    try:
        memories = await engine.search_memories(query_text, stack_tags)
        if memories:
            mem_items = "; ".join(
                f"{m.get('title')}: {str(m.get('solution') or '')[:140]}"
                for m in memories[:MAX_PLAN_MEMORIES_INJECTED]
            )
            lines.append(f"[Prior Architectural Memories]: {mem_items}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Plan memory lookup failed: %s", exc)

    if compaction_store is not None:
        try:
            compactions = await compaction_store.search_compactions(
                query_text,
                stack_tags,
                workspace_path=workspace_path or None,
                limit=MAX_PLAN_COMPACTIONS_INJECTED,
            )
            if compactions:
                comp_items = "; ".join(
                    f"step {c.get('step_idx', 0)} ({c.get('checkpoint_hash', '')}): "
                    f"{str(c.get('summary_text') or '')[:160]}"
                    for c in compactions[:MAX_PLAN_COMPACTIONS_INJECTED]
                )
                lines.append(f"[Prior Session Compactions]: {comp_items}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Plan compaction lookup failed: %s", exc)

    return "\n".join(lines)


@hooks_router.post("/api/hooks/session-start", tags=["hooks"])
async def hook_session_start(
    raw_payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Обрабатывает старт сессии: инициализирует метаданные в Redis и запускает скрипты `session-start`."""
    if not raw_payload.get("context_index_md"):
        ws_candidate = str(
            raw_payload.get("workspace_path")
            or (raw_payload.get("workspacePaths") or [""])[0]
            if isinstance(raw_payload.get("workspacePaths"), list)
            else raw_payload.get("workspace_path") or ""
        ).strip()
        if ws_candidate:
            idx_path = Path(ws_candidate) / ".context" / "index.md"
            if idx_path.is_file():
                try:
                    raw_payload = {
                        **raw_payload,
                        "context_index_md": idx_path.read_text(encoding="utf-8").strip(),
                    }
                except OSError:
                    pass

    hook_in = normalize_hook_payload(HookEventType.SESSION_START, raw_payload)
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    dispatcher = _get_script_dispatcher(request)
    stack_tags, profile_overrides = await _resolve_project_context(
        db,
        hook_in.workspace_path,
        raw_payload,
    )

    if redis_store is not None and redis_store.is_connected:
        try:
            await redis_store.init_session(
                hook_in.conversation_id,
                hook_in.workspace_path,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis session init failed: %s", exc)

    sys_parts: list[str] = [
        (
            f"[MCP-COOP Factory]: Active stack tags={list(stack_tags)}. "
            "Follow Learning-First iterative workflow and keep .context/ graph synchronized."
        )
    ]
    script_res = await dispatcher.execute_hook_scripts(
        hook_in,
        stack_tags,
        profile_overrides,
    )
    sys_parts.extend(script_res.system_messages)
    sys_msg = "\n\n".join(sys_parts)

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
        profile_overrides=profile_overrides,
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


@hooks_router.post("/api/hooks/pre-tool-use", tags=["hooks"])
async def hook_pre_tool_use(
    raw_payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Перехватывает вызов инструмента: проверяет `DecisionEngine` и скрипты `pre-tool-use` из БД."""
    hook_in = normalize_hook_payload(HookEventType.PRE_TOOL_USE, raw_payload)
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    engine = _get_engine(request)
    dispatcher = _get_script_dispatcher(request)
    stack_tags, profile_overrides = await _resolve_project_context(
        db,
        hook_in.workspace_path,
        raw_payload,
    )

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
                "profile_overrides": profile_overrides,
            },
        )

    resolution = await engine.evaluate(dec_req)
    script_res = await dispatcher.execute_hook_scripts(
        hook_in,
        stack_tags,
        profile_overrides,
    )
    if script_res.action != VerdictAction.ALLOW and resolution.action == VerdictAction.ALLOW:
        resolution = resolution.model_copy(
            update={
                "action": script_res.action,
                "reason": script_res.reason or resolution.reason,
                "overwrite_args": script_res.overwrite_args or resolution.overwrite_args,
                "decided_by_tier": "hook_script_dispatcher",
            }
        )
    response_dict = format_hook_response(
        HookEventType.PRE_TOOL_USE,
        hook_in.dialect,
        resolution,
    )
    await _log_hook_event(db, hook_in, response_dict)
    return response_dict


@hooks_router.post("/api/hooks/post-tool-use", tags=["hooks"])
async def hook_post_tool_use(
    raw_payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Фиксирует ошибку инструмента в Redis и исполняет скрипты `post-tool-use` (трекинг файлов и др.)."""
    hook_in = normalize_hook_payload(HookEventType.POST_TOOL_USE, raw_payload)
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    dispatcher = _get_script_dispatcher(request)
    stack_tags, profile_overrides = await _resolve_project_context(
        db,
        hook_in.workspace_path,
        raw_payload,
    )

    cmd = ""
    for key in COMMAND_ARG_KEYS:
        val = hook_in.tool_args.get(key)
        if isinstance(val, str) and val.strip():
            cmd = val.strip()
            break

    eph_parts: list[str] = []
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
                eph_parts.append(
                    f"[MCP-COOP Working Memory]: Recorded tool failure #{fail_count} "
                    f"for '{hook_in.tool_name}'. Inspect the root cause before retrying."
                )
            else:
                candidate = await redis_store.mark_error_resolved(
                    hook_in.conversation_id,
                    recovery_command=cmd or hook_in.tool_name,
                )
                if candidate is not None:
                    eph_parts.append(
                        "[MCP-COOP Memory Promotion]: Previous tool failure was resolved! "
                        "Before finishing, record the root cause and fix into long-term memory."
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug("PostToolUse Redis tracking failed: %s", exc)

    script_res = await dispatcher.execute_hook_scripts(
        hook_in,
        stack_tags,
        profile_overrides,
    )
    eph_parts.extend(script_res.ephemeral_messages)
    eph_msg = "\n\n".join(eph_parts) if eph_parts else None

    response_dict = format_hook_response(
        HookEventType.POST_TOOL_USE,
        hook_in.dialect,
        ephemeral_message=eph_msg,
    )
    await _log_hook_event(db, hook_in, response_dict)
    return response_dict


@hooks_router.post("/api/hooks/pre-invocation", tags=["hooks"])
async def hook_pre_invocation(
    raw_payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Анализирует контекст перед вызовом LLM: ре-гидратация и архивация `/compact` (`SPEC-003`), `/plan` и RAG."""
    hook_in = normalize_hook_payload(HookEventType.PRE_INVOCATION, raw_payload)
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    engine = _get_engine(request)
    dispatcher = _get_script_dispatcher(request)
    compaction_store = _get_compaction_store(request)
    stack_tags, profile_overrides = await _resolve_project_context(
        db,
        hook_in.workspace_path,
        raw_payload,
    )

    inspection = inspect_transcript_tail_detailed(
        hook_in.transcript_path,
        fallback_step_idx=hook_in.step_idx,
        raw_payload=raw_payload,
    )
    active_prompt = hook_in.prompt or inspection.last_user_prompt
    hints: list[str] = []
    sys_parts: list[str] = []

    if inspection.is_post_compact:
        checkpoint = inspection.checkpoint
        already_processed = False
        if (
            checkpoint is not None
            and redis_store is not None
            and redis_store.is_connected
        ):
            try:
                last_hash, _ = await redis_store.get_last_compaction_checkpoint(
                    hook_in.conversation_id
                )
                if last_hash and last_hash == checkpoint.checkpoint_hash:
                    already_processed = True
            except Exception as exc:  # noqa: BLE001
                logger.debug("Compaction checkpoint Redis lookup failed: %s", exc)

        if not already_processed:
            rehydrated_msg = (
                "[Post-Compaction Re-Hydration]: Context was compacted. Re-verify `.context/index.md` "
                "and active session state before editing project files."
            )
            idx_text = str(raw_payload.get("context_index_md") or "").strip()
            if not idx_text and hook_in.workspace_path:
                idx_path = Path(hook_in.workspace_path) / ".context" / "index.md"
                if idx_path.is_file():
                    try:
                        idx_text = idx_path.read_text(encoding="utf-8").strip()
                    except OSError:
                        pass
            if idx_text:
                rehydrated_msg = (
                    f"{rehydrated_msg}\n\n[Active Context Graph (.context/index.md)]:\n{idx_text}"
                )
            sys_parts.append(rehydrated_msg)

            if checkpoint is not None and checkpoint.summary_text.strip():
                try:
                    await compaction_store.archive_compaction(
                        conversation_id=hook_in.conversation_id,
                        workspace_path=hook_in.workspace_path,
                        agent_type=hook_in.agent_type,
                        step_idx=checkpoint.step_idx,
                        checkpoint_hash=checkpoint.checkpoint_hash,
                        summary_text=checkpoint.summary_text,
                        stack_tags=stack_tags,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Post-compaction archival failed: %s", exc)

                if redis_store is not None and redis_store.is_connected:
                    try:
                        await redis_store.record_compaction_checkpoint(
                            hook_in.conversation_id,
                            checkpoint.checkpoint_hash,
                            checkpoint.step_idx,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Compaction checkpoint Redis record failed: %s", exc)

    prompt_lower = active_prompt.lower()
    if any(kw in prompt_lower for kw in PLAN_KEYWORDS):
        active_settings: CoreSettings = getattr(request.app.state, "settings", get_settings())
        compiler: HarnessCompiler = getattr(request.app.state, "compiler", None) or HarnessCompiler(
            db=db,
            redis=redis_store,
            mcp_url=_build_local_mcp_url(active_settings),
            plugin_version=active_settings.version,
        )
        hints.append(
            await _build_plan_best_practices_hint(
                compiler=compiler,
                engine=engine,
                active_prompt=active_prompt,
                stack_tags=stack_tags,
                profile_overrides=profile_overrides,
                compaction_store=compaction_store,
                workspace_path=hook_in.workspace_path,
            )
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

    script_res = await dispatcher.execute_hook_scripts(
        hook_in,
        stack_tags,
        profile_overrides,
    )
    sys_parts.extend(script_res.system_messages)
    hints.extend(script_res.ephemeral_messages)

    sys_msg = "\n\n".join(sys_parts) if sys_parts else None
    eph_msg = "\n\n".join(hints) if hints else None
    response_dict = format_hook_response(
        HookEventType.PRE_INVOCATION,
        hook_in.dialect,
        system_message=sys_msg,
        ephemeral_message=eph_msg,
    )
    await _log_hook_event(db, hook_in, response_dict)
    return response_dict


@hooks_router.post("/api/hooks/stop", tags=["hooks"])
async def hook_stop(
    raw_payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Перехватывает завершение хода: исполняет скрипты `stop` (`enforce-context-sync` и др.) и рефлексию `Harness²`."""
    hook_in = normalize_hook_payload(HookEventType.STOP, raw_payload)
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    redis_store: RedisSessionStore | None = getattr(request.app.state, "redis", None)
    dispatcher = _get_script_dispatcher(request)
    stack_tags, profile_overrides = await _resolve_project_context(
        db,
        hook_in.workspace_path,
        raw_payload,
    )

    resolution = DecisionResolution(
        domain=DecisionDomain.HARNESS_PROBE,
        action=VerdictAction.ALLOW,
        decided_by_tier="stop_gate",
    )

    script_res = await dispatcher.execute_hook_scripts(
        hook_in,
        stack_tags,
        profile_overrides,
    )
    if script_res.action != VerdictAction.ALLOW:
        resolution = DecisionResolution(
            domain=DecisionDomain.HARNESS_PROBE,
            action=script_res.action,
            decided_by_tier="hook_script_dispatcher",
            reason=script_res.reason,
        )
    elif not hook_in.stop_hook_active and redis_store is not None and redis_store.is_connected:
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


__all__ = [
    "_build_plan_best_practices_hint",
    "hook_post_tool_use",
    "hook_pre_invocation",
    "hook_pre_tool_use",
    "hook_session_start",
    "hook_stop",
    "hooks_router",
]
