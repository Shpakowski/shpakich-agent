"""Единый реестр доменных перечислений ядра фабрики (Layer 2: Domain Enums).

Определяет типы агентов, эволюционирующих компонентов Harness², областей видимости (scope),
событий жизненных хуков, пространств ключей Redis, сущностей Контекстного Графа (`.context/`),
целевых форматов компилятора адаптеров (`HarnessCompiler`) и мульти-тирового конвейера `DecisionEngine`.
Валидация выполняется на уровне Python/Pydantic, что позволяет сохранять в PostgreSQL гибкий тип TEXT
без миграций ALTER TYPE при добавлении новых агентов, доменов оценки или типов компонентов.
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
    DISABLED = "disabled"


class ComponentEffectiveState(StrEnum):
    """Результирующее состояние компонента каталога `Harness²` с учётом стека и локальных оверрайдов проекта."""

    ACTIVE_FORCED = "active_forced"
    DISABLED_PROJECT = "disabled_project"
    DISABLED_GLOBAL = "disabled_global"
    ACTIVE_AUTO = "active_auto"
    UNMATCHED_STACK = "unmatched_stack"


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
    """Функциональные сегменты ключей Redis для разделения метаданных сессии, ошибок, кэша и Circuit Breaker."""

    META = "meta"
    LAST_ERROR = "last_error"
    PROMOTION = "promotion"
    CMD_FAILS = "cmd_fails"
    DECISION = "decision"
    RULES = "rules"
    PROVIDER_CB = "provider_cb"


class SessionMetaField(StrEnum):
    """Имена полей в Redis Hash (`session:{id}:meta`), хранящем оперативный контекст диалога."""

    WORKSPACE_PATH = "workspace_path"
    RESOLVED_ERROR = "resolved_error"
    REFLECTION_PROMPTED = "reflection_prompted"
    CONTEXT_UPDATED = "context_updated"
    CODE_MODIFIED = "code_modified"
    REMINDER_SENT = "reminder_sent"
    MODIFIED_FILES = "modified_files"
    LAST_COMPACT_HASH = "last_compact_hash"
    LAST_COMPACT_STEP = "last_compact_step"


class ScriptExecutionMode(StrEnum):
    """Режимы запуска скриптов `ComponentType.SCRIPT` в шине жизненных хуков."""

    ALWAYS = "always"
    SEMANTIC = "semantic"


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


class GuardrailAction(StrEnum):
    """Действия детерминированного защитного барьера (Guardrail) при перехвате вызова инструмента."""

    DENY = "deny"
    OVERWRITE = "overwrite"


class FileWriteStrategy(StrEnum):
    """Стратегии безопасной записи скомпилированных артефактов харнеса в рабочую директорию проекта."""

    OVERWRITE = "overwrite"
    MERGE_JSON = "merge_json"
    MERGE_TOML = "merge_toml"


class HookDialect(StrEnum):
    """Целевые диалекты конфигураций жизненных хуков для поддерживаемых ИИ-агентов."""

    ANTIGRAVITY_PROTOJSON = "agy_protojson"
    CLAUDE_CODEX_V1 = "claude_codex_v1"
    CURSOR_V1 = "cursor_v1"
    OPENCODE_TS_PLUGIN = "opencode_ts_plugin"


class McpConfigFormat(StrEnum):
    """Целевые форматы декларации MCP-серверов в конфигурационных файлах агентов."""

    MCP_SERVERS_URL = "mcp_servers_url"
    MCP_SERVERS_HTTP_TYPED = "mcp_servers_http_typed"
    CODEX_TOML = "codex_toml"
    OPENCODE_REMOTE = "opencode_remote"


class RulesFormat(StrEnum):
    """Целевые форматы рендеринга правил системного промпта и гайдлайнов инструментов."""

    PLAIN_MD = "plain_md"
    CURSOR_MDC = "cursor_mdc"
    CLAUDE_SCOPED_MD = "claude_scoped_md"


class DecisionDomain(StrEnum):
    """Функциональные домены оценки в двухкоординатной архитектуре `DecisionEngine`."""

    TOOL_SAFETY = "tool_safety"
    PLAN_VALIDATION = "plan_validation"
    SUBAGENT_ROUTING = "subagent_routing"
    CONTEXT_PRUNING = "context_pruning"
    HARNESS_PROBE = "harness_probe"


class DecisionQuestionType(StrEnum):
    """Канонические типы структурированных микро-вопросов для оценки во втором контуре (System-1 / Tier 2)."""

    PREDICATE = "predicate"
    CHOICE = "choice"
    SCORE = "score"


class DecisionProviderDialect(StrEnum):
    """Протокольные диалекты HTTP-провайдеров второго контура принятия решений (Tier 2 Pool)."""

    OPENAI_DECISIONS = "openai_decisions"
    JEV_CLEF = "jev_clef"
    OPENAI_CHAT_SCHEMA = "openai_chat_schema"


class ProviderHealthStatus(StrEnum):
    """Оперативные статусы доступности провайдера Tier 2 в рантайм-пуле и Redis Circuit Breaker."""

    HEALTHY = "healthy"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    AUTH_ERROR = "auth_error"
    UPSTREAM_ERROR = "upstream_error"
    DISABLED = "disabled"


class TierStepStatus(StrEnum):
    """Статусы завершения шага отдельного тира в цепочке ответственности `DecisionEngine`."""

    TERMINAL = "terminal"
    ESCALATE = "escalate"
    ABSTAIN = "abstain"


class VerdictAction(StrEnum):
    """Канонические действия итогового вердикта `DecisionEngine`, маппируемые в диалекты хуков."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    FORCE_ASK = "force_ask"
    OVERWRITE = "overwrite"
    CONTINUE = "continue"


class FailMode(StrEnum):
    """Политика деградации при исчерпании квоты или недоступности всех провайдеров Tier 2."""

    FAIL_OPEN_ALLOW = "fail_open_allow"
    FAIL_CLOSED_ASK = "fail_closed_ask"
    FAIL_CLOSED_DENY = "fail_closed_deny"


class EmbeddingDialect(StrEnum):
    """Протокольные диалекты тонкого HTTP-клиента векторных эмбеддингов (EmbeddingGemma 2 / Gemini / OpenAI)."""

    AUTO = "auto"
    OPENAI_COMPATIBLE = "openai_compatible"
    GEMINI_NATIVE = "gemini_native"


class EmbeddingTaskType(StrEnum):
    """Типы задач для асимметричных эмбеддингов поиска (запрос против индексируемого документа)."""

    RETRIEVAL_QUERY = "RETRIEVAL_QUERY"
    RETRIEVAL_DOCUMENT = "RETRIEVAL_DOCUMENT"


class MCPToolName(StrEnum):
    """Канонические имена инструментов MCP-шлюза (`FastMCP`, префикс `coop_`)."""

    SEARCH_MEMORY = "coop_search_memory"
    LEARN_MEMORY = "coop_learn_memory"
    PROPOSE_HARNESS_EDIT = "coop_propose_harness_edit"
    PUBLISH_SKILL = "coop_publish_skill"
    QUERY_CONTEXT_GRAPH = "coop_query_context_graph"
    UPSERT_CONTEXT_NODE = "coop_upsert_context_node"
    GET_FACTORY_STATUS = "coop_get_factory_status"
    MANAGE_DECISION_PROVIDERS = "coop_manage_decision_providers"


class AntigravityAuthMode(StrEnum):
    """Режимы аутентификации опционального рантайма google-antigravity Python SDK."""

    UNCONFIGURED = "unconfigured"
    GEMINI_API_KEY = "gemini_api_key"
    VERTEX_EXPRESS = "vertex_express"
    VERTEX_ADC = "vertex_adc"


class AntigravityAgentRole(StrEnum):
    """Целевые роли изолированных микро-агентов второго уровня (SPEC.md §6.3)."""

    PROBE_AUDITOR = "probe_auditor"
    CONTEXT_HEALER = "context_healer"
    E2E_RUNNER = "e2e_runner"


__all__ = [
    "AgentType",
    "AntigravityAgentRole",
    "AntigravityAuthMode",
    "ComponentEffectiveState",
    "ComponentStatus",
    "ComponentType",
    "ContextEntityClass",
    "ContextEntityFolder",
    "ContextEntityStatus",
    "ContextRelationType",
    "DecisionDomain",
    "DecisionProviderDialect",
    "DecisionQuestionType",
    "EmbeddingDialect",
    "EmbeddingTaskType",
    "FailMode",
    "FileWriteStrategy",
    "GuardrailAction",
    "HookDialect",
    "HookEventType",
    "MCPToolName",
    "McpConfigFormat",
    "ProviderHealthStatus",
    "RedisFlag",
    "RedisKeySegment",
    "RedisNamespace",
    "RulesFormat",
    "ScopeType",
    "ScriptExecutionMode",
    "SessionMetaField",
    "TierStepStatus",
    "VerdictAction",
]
