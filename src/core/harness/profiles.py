"""Декларативные целевые профили поддерживаемых ИИ-агентов (Layer 2 & Layer 4: Agent Target Profiles).

Описывает файловую топологию, форматы конфигураций MCP, диалекты жизненных хуков и стратегии
безопасной записи (`overwrite`, `merge_json`, `merge_toml`) для 5 целевых агентов без жёсткой привязки
алгоритма компиляции к вендорным путям.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import (
    AgentType,
    FileWriteStrategy,
    HookDialect,
    HookEventType,
    McpConfigFormat,
    RulesFormat,
)

FACTORY_PLUGIN_ID: str = "mcp-coop-agentic-factory"
DEFAULT_MCP_SERVER_KEY: str = "mcp-coop-factory"
CODEX_MCP_SERVER_KEY: str = "mcp_coop_factory"
DEFAULT_RULES_GLOB: str = "*"
DEFAULT_SKILL_FILENAME: str = "SKILL.md"
DEFAULT_SCRIPTS_SUBDIR: str = "scripts"
DEFAULT_HOOK_TIMEOUT_SECONDS: int = 15

AGY_PLUGIN_ROOT: str = f".agents/plugins/{FACTORY_PLUGIN_ID}"
UNIVERSAL_SKILLS_TEMPLATE: str = ".agents/skills/{slug}"


class PluginManifestSpec(BaseModel):
    """Спецификация манифеста плагина для агентов с пакетной изоляцией кастомизаций (Antigravity 2.0)."""

    model_config = ConfigDict(frozen=True)

    relative_path: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    suggested_prompts: tuple[str, ...] = ()
    write_strategy: FileWriteStrategy = FileWriteStrategy.OVERWRITE


class RulesTargetSpec(BaseModel):
    """Декларативная схема целевого файла системных правил и гайдлайнов инструментов."""

    model_config = ConfigDict(frozen=True)

    relative_path: str = Field(..., min_length=1)
    rules_format: RulesFormat
    write_strategy: FileWriteStrategy = FileWriteStrategy.OVERWRITE
    description: str = ""
    globs: str = DEFAULT_RULES_GLOB
    always_apply: bool = True
    scoped_paths: tuple[str, ...] = ()


class SkillsTargetSpec(BaseModel):
    """Декларативная схема размещения модульных навыков (`SKILL.md` и вспомогательных скриптов)."""

    model_config = ConfigDict(frozen=True)

    directory_template: str = Field(..., min_length=1)
    skill_filename: str = DEFAULT_SKILL_FILENAME
    scripts_subdir: str = DEFAULT_SCRIPTS_SUBDIR
    write_strategy: FileWriteStrategy = FileWriteStrategy.OVERWRITE

    def resolve_skill_path(self, slug: str) -> str:
        """Формирует относительный путь к `SKILL.md` по слагу навыка."""
        base_dir = self.directory_template.format(slug=slug).rstrip("/")
        return f"{base_dir}/{self.skill_filename}"


class McpTargetSpec(BaseModel):
    """Декларативная схема подключения глобального FastMCP-сервера в конфигурацию агента."""

    model_config = ConfigDict(frozen=True)

    relative_path: str = Field(..., min_length=1)
    config_format: McpConfigFormat
    server_key: str = DEFAULT_MCP_SERVER_KEY
    write_strategy: FileWriteStrategy


class HooksTargetSpec(BaseModel):
    """Декларативная схема трансляции канонических событий `HookEventType` в диалект целевого агента."""

    model_config = ConfigDict(frozen=True)

    relative_path: str = Field(..., min_length=1)
    dialect: HookDialect
    event_mapping: dict[HookEventType, str]
    write_strategy: FileWriteStrategy
    hook_group_name: str = FACTORY_PLUGIN_ID
    default_timeout_seconds: int = DEFAULT_HOOK_TIMEOUT_SECONDS


class AgentTargetProfile(BaseModel):
    """Полный декларативный профиль целевого ИИ-агента для компилятора `HarnessCompiler`.

    Позволяет адаптировать рендеринг правил, навыков, MCP и жизненных хуков при изменении
    спецификаций вендоров (например, при переходе на единый стандарт `.agents/`) путём
    обновления данных профиля или передачи словаря `overrides` без переписывания кода компилятора.
    """

    model_config = ConfigDict(frozen=True)

    agent_type: AgentType
    display_name: str = Field(..., min_length=1)
    rules: RulesTargetSpec
    skills: SkillsTargetSpec
    mcp: McpTargetSpec
    hooks: HooksTargetSpec
    plugin_manifest: PluginManifestSpec | None = None
    extra_instruction_paths: tuple[str, ...] = ()


AGENT_PROFILES: dict[AgentType, AgentTargetProfile] = {
    AgentType.ANTIGRAVITY: AgentTargetProfile(
        agent_type=AgentType.ANTIGRAVITY,
        display_name="Google Antigravity (AGY 2.0 Plugin Bundle)",
        plugin_manifest=PluginManifestSpec(
            relative_path=f"{AGY_PLUGIN_ROOT}/plugin.json",
            name=FACTORY_PLUGIN_ID,
            display_name="MCP-COOP Agentic Factory",
            description=(
                "Self-evolving Harness² engineering rules, PreToolUse guardrails, "
                "cross-project memory, and .context/ Graph-as-Code synchronization."
            ),
            suggested_prompts=(
                "Inspect the .context/ knowledge graph and validate all nodes",
                "Search cross-project memory for similar error symptoms",
            ),
            write_strategy=FileWriteStrategy.OVERWRITE,
        ),
        rules=RulesTargetSpec(
            relative_path=f"{AGY_PLUGIN_ROOT}/rules/AGENTS.md",
            rules_format=RulesFormat.PLAIN_MD,
            write_strategy=FileWriteStrategy.OVERWRITE,
            description="Antigravity 2.0 plugin-scoped engineering rules and tool guidance",
        ),
        skills=SkillsTargetSpec(
            directory_template=f"{AGY_PLUGIN_ROOT}/skills/{{slug}}",
        ),
        mcp=McpTargetSpec(
            relative_path=f"{AGY_PLUGIN_ROOT}/mcp_config.json",
            config_format=McpConfigFormat.MCP_SERVERS_URL,
            server_key=DEFAULT_MCP_SERVER_KEY,
            write_strategy=FileWriteStrategy.OVERWRITE,
        ),
        hooks=HooksTargetSpec(
            relative_path=f"{AGY_PLUGIN_ROOT}/hooks.json",
            dialect=HookDialect.ANTIGRAVITY_PROTOJSON,
            event_mapping={
                HookEventType.SESSION_START: "SessionStart",
                HookEventType.PRE_TOOL_USE: "PreToolUse",
                HookEventType.POST_TOOL_USE: "PostToolUse",
                HookEventType.PRE_INVOCATION: "PreInvocation",
                HookEventType.STOP: "Stop",
            },
            write_strategy=FileWriteStrategy.OVERWRITE,
        ),
    ),
    AgentType.CLAUDE_CODE: AgentTargetProfile(
        agent_type=AgentType.CLAUDE_CODE,
        display_name="Anthropic Claude Code",
        rules=RulesTargetSpec(
            relative_path=".claude/rules/mcp-coop-factory.md",
            rules_format=RulesFormat.PLAIN_MD,
            write_strategy=FileWriteStrategy.OVERWRITE,
            description="Claude Code modular project rules managed by MCP-COOP Factory",
        ),
        skills=SkillsTargetSpec(
            directory_template=".claude/skills/{slug}",
        ),
        mcp=McpTargetSpec(
            relative_path=".mcp.json",
            config_format=McpConfigFormat.MCP_SERVERS_HTTP_TYPED,
            server_key=DEFAULT_MCP_SERVER_KEY,
            write_strategy=FileWriteStrategy.MERGE_JSON,
        ),
        hooks=HooksTargetSpec(
            relative_path=".claude/settings.json",
            dialect=HookDialect.CLAUDE_CODEX_V1,
            event_mapping={
                HookEventType.SESSION_START: "SessionStart",
                HookEventType.PRE_TOOL_USE: "PreToolUse",
                HookEventType.POST_TOOL_USE: "PostToolUse",
                HookEventType.PRE_INVOCATION: "UserPromptSubmit",
                HookEventType.STOP: "Stop",
            },
            write_strategy=FileWriteStrategy.MERGE_JSON,
        ),
    ),
    AgentType.CURSOR: AgentTargetProfile(
        agent_type=AgentType.CURSOR,
        display_name="Cursor IDE",
        rules=RulesTargetSpec(
            relative_path=".cursor/rules/mcp-coop-factory.mdc",
            rules_format=RulesFormat.CURSOR_MDC,
            write_strategy=FileWriteStrategy.OVERWRITE,
            description="MCP-COOP Agentic Factory always-on engineering rules and guardrails",
            globs=DEFAULT_RULES_GLOB,
            always_apply=True,
        ),
        skills=SkillsTargetSpec(
            directory_template=UNIVERSAL_SKILLS_TEMPLATE,
        ),
        mcp=McpTargetSpec(
            relative_path=".cursor/mcp.json",
            config_format=McpConfigFormat.MCP_SERVERS_URL,
            server_key=DEFAULT_MCP_SERVER_KEY,
            write_strategy=FileWriteStrategy.MERGE_JSON,
        ),
        hooks=HooksTargetSpec(
            relative_path=".cursor/hooks.json",
            dialect=HookDialect.CURSOR_V1,
            event_mapping={
                HookEventType.SESSION_START: "sessionStart",
                HookEventType.PRE_TOOL_USE: "preToolUse",
                HookEventType.POST_TOOL_USE: "postToolUse",
                HookEventType.PRE_INVOCATION: "beforeSubmitPrompt",
                HookEventType.STOP: "stop",
            },
            write_strategy=FileWriteStrategy.MERGE_JSON,
        ),
    ),
    AgentType.CODEX: AgentTargetProfile(
        agent_type=AgentType.CODEX,
        display_name="OpenAI Codex CLI",
        rules=RulesTargetSpec(
            relative_path="AGENTS.md",
            rules_format=RulesFormat.PLAIN_MD,
            write_strategy=FileWriteStrategy.OVERWRITE,
            description="OpenAI Codex CLI root instructions compiled by MCP-COOP Factory",
        ),
        skills=SkillsTargetSpec(
            directory_template=UNIVERSAL_SKILLS_TEMPLATE,
        ),
        mcp=McpTargetSpec(
            relative_path=".codex/config.toml",
            config_format=McpConfigFormat.CODEX_TOML,
            server_key=CODEX_MCP_SERVER_KEY,
            write_strategy=FileWriteStrategy.MERGE_TOML,
        ),
        hooks=HooksTargetSpec(
            relative_path=".codex/hooks.json",
            dialect=HookDialect.CLAUDE_CODEX_V1,
            event_mapping={
                HookEventType.SESSION_START: "SessionStart",
                HookEventType.PRE_TOOL_USE: "PreToolUse",
                HookEventType.POST_TOOL_USE: "PostToolUse",
                HookEventType.PRE_INVOCATION: "UserPromptSubmit",
                HookEventType.STOP: "Stop",
            },
            write_strategy=FileWriteStrategy.MERGE_JSON,
        ),
    ),
    AgentType.OPENCODE: AgentTargetProfile(
        agent_type=AgentType.OPENCODE,
        display_name="OpenCode CLI",
        rules=RulesTargetSpec(
            relative_path=".opencode/rules/mcp-coop-factory.md",
            rules_format=RulesFormat.PLAIN_MD,
            write_strategy=FileWriteStrategy.OVERWRITE,
            description="OpenCode modular project instructions managed by MCP-COOP Factory",
        ),
        skills=SkillsTargetSpec(
            directory_template=UNIVERSAL_SKILLS_TEMPLATE,
        ),
        mcp=McpTargetSpec(
            relative_path="opencode.json",
            config_format=McpConfigFormat.OPENCODE_REMOTE,
            server_key=DEFAULT_MCP_SERVER_KEY,
            write_strategy=FileWriteStrategy.MERGE_JSON,
        ),
        hooks=HooksTargetSpec(
            relative_path=".opencode/plugins/mcp-coop-factory.ts",
            dialect=HookDialect.OPENCODE_TS_PLUGIN,
            event_mapping={
                HookEventType.SESSION_START: "session.created",
                HookEventType.PRE_TOOL_USE: "tool.execute.before",
                HookEventType.POST_TOOL_USE: "tool.execute.after",
                HookEventType.PRE_INVOCATION: "chat.message",
                HookEventType.STOP: "session.idle",
            },
            write_strategy=FileWriteStrategy.OVERWRITE,
        ),
        extra_instruction_paths=(
            "AGENTS.md",
            ".context/index.md",
            ".opencode/rules/*.md",
        ),
    ),
}


def _deep_merge_dict(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Рекурсивно объединяет словарь базового профиля с пользовательскими переопределениями."""
    merged: dict[str, Any] = dict(base)
    for key, value in patch.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge_dict(existing, value)
        else:
            merged[key] = value
    return merged


def get_agent_profile(
    agent_type: AgentType | str,
    overrides: Mapping[str, Any] | None = None,
) -> AgentTargetProfile:
    """Возвращает декларативный профиль целевого агента с поддержкой точечного переопределения полей.

    Если передан словарь `overrides` (например, из кастомного конфига проекта при изменении
    вендорных путей), функция выполняет глубокое слияние поверх эталонного профиля и повторно
    валидирует результат через Pydantic.
    """
    resolved_agent = AgentType(agent_type)
    base_profile = AGENT_PROFILES[resolved_agent]
    if not overrides:
        return base_profile

    merged_data = _deep_merge_dict(base_profile.model_dump(mode="python"), overrides)
    return AgentTargetProfile.model_validate(merged_data)


def list_agent_profiles() -> tuple[AgentTargetProfile, ...]:
    """Возвращает упорядоченный кортеж всех поддерживаемых профилей агентов."""
    return tuple(AGENT_PROFILES[agent] for agent in AgentType)


__all__ = [
    "AGENT_PROFILES",
    "AGY_PLUGIN_ROOT",
    "CODEX_MCP_SERVER_KEY",
    "DEFAULT_HOOK_TIMEOUT_SECONDS",
    "DEFAULT_MCP_SERVER_KEY",
    "DEFAULT_RULES_GLOB",
    "DEFAULT_SCRIPTS_SUBDIR",
    "DEFAULT_SKILL_FILENAME",
    "FACTORY_PLUGIN_ID",
    "UNIVERSAL_SKILLS_TEMPLATE",
    "AgentTargetProfile",
    "HooksTargetSpec",
    "McpTargetSpec",
    "PluginManifestSpec",
    "RulesTargetSpec",
    "SkillsTargetSpec",
    "get_agent_profile",
    "list_agent_profiles",
]
