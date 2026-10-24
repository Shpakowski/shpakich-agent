"""Единый реестр доменных перечислений ядра фабрики (Layer 2: Domain Enums).

Реализует принципы DRY и KISS: все допустимые типы агентов, компонентов Harness²,
областей видимости (scope) и событий жизненных хуков определяются в одном месте через StrEnum.
Это позволяет строго валидировать входные данные на уровне Python/Pydantic, сохраняя в PostgreSQL
гибкий тип TEXT без жёсткого вендор-лок-ина и сложных миграций ALTER TYPE.
"""

from __future__ import annotations

from enum import StrEnum


class AgentType(StrEnum):
    """Поддерживаемые типы целевых ИИ-агентов (CLI-клиентов и IDE)."""

    ANTIGRAVITY = "antigravity"
    CLAUDE_CODE = "claude-code"
    CURSOR = "cursor"
    CODEX = "codex"
    OPENCODE = "opencode"


class ComponentType(StrEnum):
    """Типы эволюционирующих компонентов в цикле замкнутого обучения Harness²."""

    SYSTEM_PROMPT = "system_prompt"
    GUARDRAIL = "guardrail"
    SKILL = "skill"
    TOOL_GUIDANCE = "tool_guidance"
    SCRIPT = "script"
    SUBAGENT = "subagent"
    MIDDLEWARE = "middleware"


class ScopeType(StrEnum):
    """Уровень применимости правил, навыков и записей памяти (инвариантный или стековый)."""

    GLOBAL = "global"
    DOMAIN = "domain"


class ComponentStatus(StrEnum):
    """Фазы жизненного цикла компонента в стейт-машине Propose -> Probe -> Compose."""

    CANDIDATE = "candidate"
    PROBING = "probing"
    COMPOSED = "composed"
    REJECTED = "rejected"


class HookEventType(StrEnum):
    """Канонические идентификаторы перехватываемых событий жизненного цикла агента."""

    SESSION_START = "session-start"
    PRE_TOOL_USE = "pre-tool-use"
    POST_TOOL_USE = "post-tool-use"
    PRE_INVOCATION = "pre-invocation"
    STOP = "stop"


__all__ = [
    "AgentType",
    "ComponentStatus",
    "ComponentType",
    "HookEventType",
    "ScopeType",
]
