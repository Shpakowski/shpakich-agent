"""Единый реестр доменных перечислений ядра фабрики (Layer 2: Domain Enums).

Определяет типы агентов, эволюционирующих компонентов Harness², областей видимости (scope),
событий жизненных хуков, пространств ключей Redis и сущностей Контекстного Графа (`.context/`).
Валидация выполняется на уровне Python/Pydantic, что позволяет сохранять в PostgreSQL гибкий тип TEXT
без миграций ALTER TYPE при добавлении новых агентов или типов компонентов.
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


class RedisNamespace(StrEnum):
    """Корневые пространства имён ключей Redis (Short-Term Working Memory vs LangCache Lite)."""

    SESSION = "session"
    CACHE = "cache"


class RedisKeySegment(StrEnum):
    """Функциональные сегменты ключей Redis для разделения метаданных сессии, ошибок и кэша."""

    META = "meta"
    LAST_ERROR = "last_error"
    PROMOTION = "promotion"
    CMD_FAILS = "cmd_fails"
    DECISION = "decision"
    RULES = "rules"


class SessionMetaField(StrEnum):
    """Имена полей в Redis Hash (`session:{id}:meta`), хранящем оперативный контекст диалога."""

    WORKSPACE_PATH = "workspace_path"
    RESOLVED_ERROR = "resolved_error"
    REFLECTION_PROMPTED = "reflection_prompted"


class RedisFlag(StrEnum):
    """Строковые булевы флаги для атомарного хранения состояний внутри Redis Hash."""

    FALSE = "0"
    TRUE = "1"


class ContextEntityClass(StrEnum):
    """Канонические классы архитектурных вершин в локальном графе знаний `.context/`."""

    MODULE = "Module"
    CONTRACT = "Contract"
    DECISION = "Decision"


class ContextEntityStatus(StrEnum):
    """Статусы жизненного цикла узла в графе `.context/entities/`."""

    ACTIVE = "Active"
    DRAFT = "Draft"
    DEPRECATED = "Deprecated"


class ContextRelationType(StrEnum):
    """Типы направленных связей (рёбер `nx.DiGraph`) между узлами Контекстного Графа."""

    DEPENDS_ON = "depends_on"
    IMPLEMENTS = "implements"
    GOVERNED_BY = "governed_by"
    REFERENCES = "references"


class ContextEntityFolder(StrEnum):
    """Подкаталоги внутри `.context/entities/`, соответствующие классам узлов."""

    MODULES = "modules"
    CONTRACTS = "contracts"
    DECISIONS = "decisions"


__all__ = [
    "AgentType",
    "ComponentStatus",
    "ComponentType",
    "ContextEntityClass",
    "ContextEntityFolder",
    "ContextEntityStatus",
    "ContextRelationType",
    "HookEventType",
    "RedisFlag",
    "RedisKeySegment",
    "RedisNamespace",
    "ScopeType",
    "SessionMetaField",
]
