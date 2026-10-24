"""Адаптер диалектов жизненных хуков (`HookDialect`) для 5 целевых ИИ-агентов.

Отвечает за:
1. Входную нормализацию разнородных JSON-пэйлоадов (`Antigravity ProtoJSON`, `Claude Code / Codex v1`,
   `Cursor v1`, `OpenCode TS Plugin`) в единую каноническую модель `CanonicalHookInput`.
2. Безопасное чтение хвоста транскрипта (`inspect_transcript_tail`) для детекции момента
   выхода из `/compact` (`CHECKPOINT`) и извлечения последней реплики пользователя.
3. Выходную трансляцию `DecisionResolution` в точный протокольный JSON-ответ целевого агента
   с обязательным пробросом предупреждений о переключении провайдеров или исчерпании квот.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.decision.models import DecisionResolution
from src.core.enums import (
    AgentType,
    HookDialect,
    HookEventType,
    VerdictAction,
)

TRANSCRIPT_TAIL_MAX_BYTES: int = 16_384
COMPACT_RESUMING_MARKER: str = "# Resuming from a compaction"
COMPACT_STEP_TYPE: str = "CHECKPOINT"
USER_INPUT_STEP_TYPE: str = "USER_INPUT"


class CanonicalHookInput(BaseModel):
    """Унифицированное представление входного события жизненного хука независимо от вендора."""

    model_config = ConfigDict(frozen=True)

    event_type: HookEventType
    agent_type: AgentType
    dialect: HookDialect
    conversation_id: str
    workspace_path: str
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    step_idx: int = 0
    error: str | None = None
    transcript_path: str | None = None
    prompt: str = ""
    termination_reason: str = ""
    stop_hook_active: bool = False
    raw_payload: dict[str, Any] = Field(default_factory=dict)


def resolve_hook_dialect(
    agent_type: AgentType | None,
    raw_payload: Mapping[str, Any],
) -> tuple[AgentType, HookDialect]:
    """Определяет `AgentType` и целевой `HookDialect` по явному параметру или сигнатуре полей JSON."""
    resolved_agent = agent_type
    if resolved_agent is None:
        raw_agent = str(raw_payload.get("agent_type") or raw_payload.get("agent") or "").strip().lower()
        for candidate in AgentType:
            if candidate.value == raw_agent:
                resolved_agent = candidate
                break

    if resolved_agent is None:
        if "conversationId" in raw_payload or "toolCall" in raw_payload or "workspacePaths" in raw_payload:
            resolved_agent = AgentType.ANTIGRAVITY
        elif "workspace_roots" in raw_payload:
            resolved_agent = AgentType.CURSOR
        elif "hook_event_name" in raw_payload or "tool_input" in raw_payload:
            resolved_agent = AgentType.CLAUDE_CODE
        elif "sessionID" in raw_payload:
            resolved_agent = AgentType.OPENCODE
        else:
            resolved_agent = AgentType.ANTIGRAVITY

    dialect_map: dict[AgentType, HookDialect] = {
        AgentType.ANTIGRAVITY: HookDialect.ANTIGRAVITY_PROTOJSON,
        AgentType.CLAUDE_CODE: HookDialect.CLAUDE_CODEX_V1,
        AgentType.CODEX: HookDialect.CLAUDE_CODEX_V1,
        AgentType.CURSOR: HookDialect.CURSOR_V1,
        AgentType.OPENCODE: HookDialect.OPENCODE_TS_PLUGIN,
    }
    return resolved_agent, dialect_map[resolved_agent]


def normalize_hook_payload(
    event_type: HookEventType,
    raw_payload: Mapping[str, Any],
    explicit_agent: AgentType | None = None,
) -> CanonicalHookInput:
    """Нормализует сырой JSON-пэйлоад любого из 5 агентов в `CanonicalHookInput`."""
    agent_type, dialect = resolve_hook_dialect(explicit_agent, raw_payload)

    conv_id = str(
        raw_payload.get("conversationId")
        or raw_payload.get("session_id")
        or raw_payload.get("conversation_id")
        or raw_payload.get("sessionID")
        or "default"
    ).strip()

    ws_path = ""
    ws_list = raw_payload.get("workspacePaths") or raw_payload.get("workspace_roots")
    if isinstance(ws_list, list) and ws_list:
        ws_path = str(ws_list[0]).strip()
    if not ws_path:
        ws_path = str(
            raw_payload.get("workspace_path")
            or raw_payload.get("cwd")
            or raw_payload.get("directory")
            or ""
        ).strip()

    tool_call_obj = raw_payload.get("toolCall")
    tool_name = ""
    tool_args: dict[str, Any] = {}
    if isinstance(tool_call_obj, Mapping):
        tool_name = str(tool_call_obj.get("name") or "").strip()
        raw_tc_args = tool_call_obj.get("args")
        if isinstance(raw_tc_args, Mapping):
            tool_args = dict(raw_tc_args)

    if not tool_name:
        tool_name = str(
            raw_payload.get("tool_name")
            or raw_payload.get("tool")
            or ""
        ).strip()

    if not tool_args:
        alt_args = raw_payload.get("tool_input") or raw_payload.get("tool_args") or raw_payload.get("args")
        if isinstance(alt_args, Mapping):
            tool_args = dict(alt_args)

    step_idx_raw = raw_payload.get("stepIdx") or raw_payload.get("step_idx") or 0
    step_idx = int(step_idx_raw) if isinstance(step_idx_raw, (int, float)) else 0

    raw_err = raw_payload.get("error")
    err_str: str | None = str(raw_err).strip() if raw_err is not None and str(raw_err).strip() else None

    transcript_path_raw = raw_payload.get("transcriptPath") or raw_payload.get("transcript_path")
    transcript_path = str(transcript_path_raw).strip() if transcript_path_raw else None

    prompt = str(raw_payload.get("prompt") or raw_payload.get("user_prompt") or "").strip()
    term_reason = str(raw_payload.get("terminationReason") or raw_payload.get("reason") or "").strip()
    stop_active = bool(raw_payload.get("stop_hook_active") or raw_payload.get("stopHookActive") or False)

    return CanonicalHookInput(
        event_type=event_type,
        agent_type=agent_type,
        dialect=dialect,
        conversation_id=conv_id,
        workspace_path=ws_path,
        tool_name=tool_name,
        tool_args=tool_args,
        step_idx=step_idx,
        error=err_str,
        transcript_path=transcript_path,
        prompt=prompt,
        termination_reason=term_reason,
        stop_hook_active=stop_active,
        raw_payload=dict(raw_payload),
    )



def inspect_transcript_tail(transcript_path: str | None) -> tuple[bool, str]:
    """Читает последние 16 КБ JSONL-транскрипта для детекции `/compact` и последнего промпта пользователя."""
    if not transcript_path:
        return False, ""

    path = Path(transcript_path)
    if not path.is_file():
        return False, ""

    try:
        file_size = path.stat().st_size
        offset = max(0, file_size - TRANSCRIPT_TAIL_MAX_BYTES)
        with path.open("rb") as fh:
            if offset > 0:
                fh.seek(offset)
            tail_text = fh.read().decode("utf-8", errors="ignore")
    except OSError:
        return False, ""

    lines = [ln.strip() for ln in tail_text.splitlines() if ln.strip()]
    is_post_compact = False
    last_user_prompt = ""

    for line in lines[-8:]:
        if COMPACT_RESUMING_MARKER in line:
            is_post_compact = True
        try:
            obj = json.loads(line)
            if isinstance(obj, Mapping):
                step_type = str(obj.get("type") or "")
                content_str = str(obj.get("content") or "")
                if step_type == COMPACT_STEP_TYPE or COMPACT_RESUMING_MARKER in content_str:
                    is_post_compact = True
                if step_type == USER_INPUT_STEP_TYPE and content_str.strip():
                    last_user_prompt = content_str.strip()
        except ValueError:
            continue

    return is_post_compact, last_user_prompt


def format_hook_response(
    event_type: HookEventType,
    dialect: HookDialect,
    resolution: DecisionResolution | None = None,
    *,
    system_message: str | None = None,
    ephemeral_message: str | None = None,
    sync_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Форматирует каноническую резолюцию в протокольный JSON-ответ целевого `HookDialect`."""
    action = resolution.action if resolution is not None else VerdictAction.ALLOW
    reason = resolution.reason if resolution is not None else None
    warnings = list(resolution.warnings) if resolution is not None else []
    overwrite_args = resolution.overwrite_args if resolution is not None else None

    sys_msg = system_message or (resolution.inject_system_message if resolution else None)
    eph_msg = ephemeral_message or (resolution.inject_ephemeral_message if resolution else None)

    if warnings:
        warning_block = "[Tier 2 Provider Notice] " + " | ".join(warnings)
        eph_msg = f"{eph_msg}\n{warning_block}".strip() if eph_msg else warning_block
        if reason and action in (VerdictAction.ASK, VerdictAction.FORCE_ASK, VerdictAction.DENY):
            reason = f"{reason} ({warning_block})"

    payload: dict[str, Any] = {}

    if event_type == HookEventType.POST_TOOL_USE:
        return payload

    if event_type in (
        HookEventType.SESSION_START,
        HookEventType.PRE_INVOCATION,
    ):
        if dialect == HookDialect.ANTIGRAVITY_PROTOJSON:
            inject_steps: list[dict[str, Any]] = []
            if sys_msg:
                inject_steps.append({"systemMessage": {"systemMessage": sys_msg}})
            if eph_msg:
                inject_steps.append({"ephemeralMessage": eph_msg})
            if inject_steps:
                payload["injectSteps"] = inject_steps
        elif dialect == HookDialect.CLAUDE_CODEX_V1:
            combined = "\n\n".join(m for m in (sys_msg, eph_msg) if m)
            if combined:
                hook_event_label = (
                    "SessionStart"
                    if event_type == HookEventType.SESSION_START
                    else "UserPromptSubmit"
                )
                payload["hookSpecificOutput"] = {
                    "hookEventName": hook_event_label,
                    "additionalContext": combined,
                }
        elif dialect == HookDialect.CURSOR_V1:
            combined = "\n\n".join(m for m in (sys_msg, eph_msg) if m)
            if combined:
                payload["additional_context"] = combined
        else:
            combined = "\n\n".join(m for m in (sys_msg, eph_msg) if m)
            if combined:
                payload["systemMessage"] = combined

        if sync_files:
            payload["_syncFiles"] = sync_files
        return payload

    if event_type == HookEventType.PRE_TOOL_USE:
        if dialect == HookDialect.ANTIGRAVITY_PROTOJSON:
            payload["decision"] = (
                VerdictAction.ALLOW.value
                if action == VerdictAction.OVERWRITE
                else action.value
            )
            if reason:
                payload["reason"] = reason
            if action == VerdictAction.OVERWRITE and overwrite_args is not None:
                payload["overwrite"] = overwrite_args
            if eph_msg:
                payload["ephemeralMessage"] = eph_msg
            return payload

        if dialect == HookDialect.CLAUDE_CODEX_V1:
            perm_map = {
                VerdictAction.ALLOW: "allow",
                VerdictAction.DENY: "deny",
                VerdictAction.ASK: "ask",
                VerdictAction.FORCE_ASK: "ask",
                VerdictAction.OVERWRITE: "allow",
            }
            hook_out: dict[str, Any] = {
                "hookEventName": "PreToolUse",
                "permissionDecision": perm_map.get(action, "allow"),
            }
            if reason:
                hook_out["permissionDecisionReason"] = reason
            if action == VerdictAction.OVERWRITE and overwrite_args is not None:
                hook_out["updatedInput"] = overwrite_args
            payload["hookSpecificOutput"] = hook_out
            return payload

        if dialect == HookDialect.CURSOR_V1:
            cursor_perm = "deny" if action == VerdictAction.DENY else ("ask" if action in (VerdictAction.ASK, VerdictAction.FORCE_ASK) else "allow")
            payload["permission"] = cursor_perm
            if reason:
                payload["user_message"] = reason
                payload["agent_message"] = reason
            if action == VerdictAction.OVERWRITE and overwrite_args is not None:
                payload["updated_input"] = overwrite_args
            return payload

        payload["decision"] = action.value
        if reason:
            payload["reason"] = reason
        if action == VerdictAction.OVERWRITE and overwrite_args is not None:
            payload["overwriteArgs"] = overwrite_args
        return payload

    if action == VerdictAction.CONTINUE:
        if dialect == HookDialect.ANTIGRAVITY_PROTOJSON:
            return {
                "decision": VerdictAction.CONTINUE.value,
                "reason": reason or "Reflection required before stopping.",
                "ephemeralMessage": eph_msg or reason or "",
            }
        if dialect == HookDialect.CLAUDE_CODEX_V1:
            return {
                "decision": "block",
                "reason": eph_msg or reason or "Reflection required before stopping.",
            }
        if dialect == HookDialect.CURSOR_V1:
            return {"followup_message": eph_msg or reason or "Reflection required before stopping."}
        return {
            "decision": VerdictAction.CONTINUE.value,
            "reason": eph_msg or reason or "",
        }

    return {"decision": VerdictAction.ALLOW.value}


__all__ = [
    "COMPACT_RESUMING_MARKER",
    "COMPACT_STEP_TYPE",
    "TRANSCRIPT_TAIL_MAX_BYTES",
    "CanonicalHookInput",
    "format_hook_response",
    "inspect_transcript_tail",
    "normalize_hook_payload",
    "resolve_hook_dialect",
]
