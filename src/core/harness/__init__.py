"""Пакет управления знаниями, декларативными профилями агентов и компилятором `Harness²` (Layer 2 & Layer 4).

Объединяет базовый идемпотентный Seed (`seed.py`), декларативные профили 5 целевых агентов (`profiles.py`)
и двухфазный компилятор конфигураций (`compiler.py`).
"""

from __future__ import annotations

from src.core.harness.compiler import (
    BUNDLE_SOURCE_POSTGRES,
    BUNDLE_SOURCE_REDIS_CACHE,
    BUNDLE_SOURCE_SEED_FALLBACK,
    DEFAULT_LOCALHOST_HOST,
    DEFAULT_MCP_URL,
    MAX_RULES_MARKDOWN_BYTES,
    MCP_HTTP_PATH,
    CanonicalHarnessBundle,
    CompiledGuardrail,
    CompiledSkillArtifact,
    HarnessCompiler,
    RenderedFile,
    RenderedHarnessArtifact,
    enforce_markdown_byte_budget,
    normalize_stack_tags,
)
from src.core.harness.profiles import (
    AGENT_PROFILES,
    FACTORY_PLUGIN_ID,
    AgentTargetProfile,
    HooksTargetSpec,
    McpTargetSpec,
    PluginManifestSpec,
    RulesTargetSpec,
    SkillsTargetSpec,
    get_agent_profile,
    list_agent_profiles,
)
from src.core.harness.seed import (
    DEFAULT_HARNESS_SEEDS,
    HarnessComponentSeed,
    filter_seed_components,
    seed_default_components,
)

__all__ = [
    "AGENT_PROFILES",
    "BUNDLE_SOURCE_POSTGRES",
    "BUNDLE_SOURCE_REDIS_CACHE",
    "BUNDLE_SOURCE_SEED_FALLBACK",
    "DEFAULT_HARNESS_SEEDS",
    "DEFAULT_LOCALHOST_HOST",
    "DEFAULT_MCP_URL",
    "FACTORY_PLUGIN_ID",
    "MAX_RULES_MARKDOWN_BYTES",
    "MCP_HTTP_PATH",
    "AgentTargetProfile",
    "CanonicalHarnessBundle",
    "CompiledGuardrail",
    "CompiledSkillArtifact",
    "HarnessCompiler",
    "HarnessComponentSeed",
    "HooksTargetSpec",
    "McpTargetSpec",
    "PluginManifestSpec",
    "RenderedFile",
    "RenderedHarnessArtifact",
    "RulesTargetSpec",
    "SkillsTargetSpec",
    "enforce_markdown_byte_budget",
    "filter_seed_components",
    "get_agent_profile",
    "list_agent_profiles",
    "normalize_stack_tags",
    "seed_default_components",
]
