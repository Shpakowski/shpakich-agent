"""Декларативные целевые профили поддерживаемых ИИ-агентов (Layer 2 & Layer 4: Agent Target Profiles).

Загружает файловую топологию, форматы конфигураций MCP, диалекты жизненных хуков, стратегии
безопасной записи (`overwrite`, `merge_json`, `merge_toml`) и маркеры автодетекции для 5 целевых
агентов из единого манифеста `seeds/harness-seed.json`.
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
from src.core.harness.seed import load_harness_seed_payload

_SEED_PAYLOAD: dict[str, Any] = load_harness_seed_payload()
_PLUGIN_SECTION: dict[str, Any] = (
    dict(_SEED_PAYLOAD.get("plugin") or {})
    if isinstance(_SEED_PAYLOAD.get("plugin"), Mapping)
    else {}
)

FACTORY_PLUGIN_ID: str = str(_PLUGIN_SECTION.get("id") or "mcp-coop")
DEFAULT_PLUGIN_DISPLAY_NAME: str = str(
    _PLUGIN_SECTION.get("display_name") or "MCP-COOP Agentic Factory"
)
DEFAULT_PLUGIN_DESCRIPTION: str = str(
    _PLUGIN_SECTION.get("description")
    or (
        "Adaptive engineering workspace plugin providing project context "
        "management, cross-session memory, and automated workflow guardrails."
    )
)
DEFAULT_MCP_SERVER_KEY: str = str(_PLUGIN_SECTION.get("mcp_server_key") or "harness-factory")
CODEX_MCP_SERVER_KEY: str = str(_PLUGIN_SECTION.get("codex_mcp_server_key") or "harness_factory")
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

    Хранит как правила генерации файлов адаптера (`rules`, `skills`, `mcp`, `hooks`, `plugin_manifest`),
    так и метаданные каталога для CLI (`cli_display_name`, `config_hint`, `workspace_markers`).
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
    cli_display_name: str = ""
    config_hint: str = ""
    workspace_markers: tuple[str, ...] = ()


def _build_agent_profiles() -> dict[AgentType, AgentTargetProfile]:
    """Загружает реестр `AGENT_PROFILES` из секции `agents` манифеста `seeds/harness-seed.json`."""
    payload = load_harness_seed_payload()
    raw_agents = payload.get("agents")
    profiles: dict[AgentType, AgentTargetProfile] = {}
    if isinstance(raw_agents, list):
        for item in raw_agents:
            if not isinstance(item, Mapping):
                continue
            profile = AgentTargetProfile.model_validate(item)
            profiles[profile.agent_type] = profile
    return profiles


AGENT_PROFILES: dict[AgentType, AgentTargetProfile] = _build_agent_profiles()


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
    """Возвращает декларативный профиль целевого агента с поддержкой точечного переопределения полей."""
    resolved_agent = AgentType(agent_type)
    base_profile = AGENT_PROFILES[resolved_agent]
    if not overrides:
        return base_profile

    merged_data = _deep_merge_dict(base_profile.model_dump(mode="python"), overrides)
    return AgentTargetProfile.model_validate(merged_data)


def list_agent_profiles() -> tuple[AgentTargetProfile, ...]:
    """Возвращает упорядоченный кортеж всех поддерживаемых профилей агентов."""
    return tuple(AGENT_PROFILES[agent] for agent in AgentType if agent in AGENT_PROFILES)


def list_agent_catalog_metadata() -> list[dict[str, Any]]:
    """Возвращает сериализуемый каталог метаданных поддерживаемых ИИ-агентов для REST API и CLI."""
    return [
        {
            "id": profile.agent_type.value,
            "display_name": profile.cli_display_name or profile.display_name,
            "profile_display_name": profile.display_name,
            "config_hint": profile.config_hint,
            "workspace_markers": list(profile.workspace_markers),
        }
        for profile in list_agent_profiles()
    ]


__all__ = [
    "AGENT_PROFILES",
    "AGY_PLUGIN_ROOT",
    "CODEX_MCP_SERVER_KEY",
    "DEFAULT_HOOK_TIMEOUT_SECONDS",
    "DEFAULT_MCP_SERVER_KEY",
    "DEFAULT_PLUGIN_DESCRIPTION",
    "DEFAULT_PLUGIN_DISPLAY_NAME",
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
    "list_agent_catalog_metadata",
    "list_agent_profiles",
]
