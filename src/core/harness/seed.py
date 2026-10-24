"""Базовый идемпотентный Seed эволюционирующих компонентов Harness² (Layer 2: Knowledge Seed).

Определяет эталонный набор стартовых правил (`system_prompt`, `guardrail`, `tool_guidance`, `skill`)
и обеспечивает их идемпотентную загрузку в таблицу `harness_components` PostgreSQL при старте ядра,
а также чистую фильтрацию в памяти при работе в автономном режиме без БД (`Graceful Degradation`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import (
    ComponentStatus,
    ComponentType,
    GuardrailAction,
    ScopeType,
)

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase

logger = logging.getLogger("mcp_coop.core.harness.seed")

DEFAULT_COMPONENT_SCORE: float = 1.0
DEFAULT_COMPONENT_VERSION: int = 1
BUN_STACK_TAG: str = "bun"

# Регулярные выражения для перехвата вызовов терминальных инструментов во всех 5 агентах
SHELL_TOOL_MATCHER: str = "run_command|Bash|shell|exec_command"
DESTRUCTIVE_COMMANDS_PATTERN: str = (
    r"(?:rm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+/(?:\s|$)"
    r"|git\s+push\s+.*--force(?:-with-lease)?\s+(?:origin\s+)?(?:main|master)\b"
    r"|git\s+reset\s+--hard\b)"
)
NPM_TO_BUN_PATTERN: str = r"^\s*npm\s+(install|i|add|run|test)\b"
NPM_TO_BUN_REPLACEMENT: str = r"bun \1"

INSERT_SEED_COMPONENT_SQL: str = """
INSERT INTO harness_components (
    slug,
    component_type,
    scope,
    stack_tags,
    title,
    description,
    content,
    metadata,
    status,
    score,
    version
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11)
ON CONFLICT (slug) DO NOTHING
"""


class HarnessComponentSeed(BaseModel):
    """Неизменяемая спецификация стартового компонента Harness² для инициализации БД и оффлайн-фолбэка.

    Схема полностью повторяет доменный контракт таблицы `harness_components`, что позволяет
    компилятору `HarnessCompiler` прозрачно собирать `CanonicalHarnessBundle` как из PostgreSQL,
    так и напрямую из `DEFAULT_HARNESS_SEEDS`, если контейнер БД временно недоступен.
    """

    model_config = ConfigDict(frozen=True)

    slug: str = Field(..., min_length=1)
    component_type: ComponentType
    scope: ScopeType = ScopeType.GLOBAL
    stack_tags: tuple[str, ...] = (ScopeType.GLOBAL.value,)
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: ComponentStatus = ComponentStatus.COMPOSED
    score: float = DEFAULT_COMPONENT_SCORE
    version: int = DEFAULT_COMPONENT_VERSION


DEFAULT_HARNESS_SEEDS: tuple[HarnessComponentSeed, ...] = (
    HarnessComponentSeed(
        slug="core-engineering-mentor",
        component_type=ComponentType.SYSTEM_PROMPT,
        scope=ScopeType.GLOBAL,
        stack_tags=(ScopeType.GLOBAL.value,),
        title="Core Engineering Mentor & Production Standards",
        description=(
            "Enforces Learning-First incremental delivery, English code with Russian "
            "architectural comments, thoughtful code review discussion, anti-sycophancy, "
            "and KISS/DRY/SOLID/YAGNI with Zero-Hardcode."
        ),
        content=(
            "## 1. Learning-First Incremental Workflow\n"
            "- Never dump multiple large modules in a single step. Work in small, logically "
            "complete iterations (`SPEC.md -> Step-by-Step Plan -> Iteration -> User Code Review`).\n"
            "- Before writing code, briefly explain **WHAT** component is being built and **WHY** "
            "this pattern was chosen. After writing code, summarize the data flow.\n\n"
            "## 2. Language Standard\n"
            "- **Code (`English Only`)**: all identifiers, string literals, log messages, hook "
            "reasons, and system messages must be written strictly in English.\n"
            "- **Comments & Docstrings (`Russian Only — Why & How`)**: write module headers "
            "(2-4 lines on architectural layer and interactions), docstrings, and inline comments "
            "in Russian, explaining business contracts and algorithmic mechanics rather than "
            "self-evident syntax.\n"
            "- **Chat Language**: always reply in the user's language (Russian), even after "
            "processing English tool outputs or hook payloads.\n\n"
            "## 3. Code Review & Anti-Sycophancy\n"
            "- Treat user questions (`Why is it done this way?`, `What if we use X?`) as an "
            "engineering discussion, **never** as an automatic order to delete and rewrite code.\n"
            "- Explain trade-offs first and modify code only after explicit user confirmation.\n"
            "- Challenge suboptimal assumptions with reasoned counter-arguments, propose "
            "alternatives with a clear `(Recommended)` option, and verify unfamiliar APIs "
            "via research instead of guessing.\n\n"
            "## 4. Production-Grade Standards (KISS, DRY, SOLID, YAGNI, Zero-Hardcode)\n"
            "- Maintain a Single Source of Truth (`DRY`) and clear single responsibility (`SRP`).\n"
            "- **Zero-Hardcode Policy**: never scatter magic numbers or raw string literals across "
            "modules. Extract all statuses, agent identifiers, key prefixes, TTLs, and limits "
            "into strict `StrEnum` / `as const` types, top-level constants, or typed settings."
        ),
    ),
    HarnessComponentSeed(
        slug="block-destructive-commands",
        component_type=ComponentType.GUARDRAIL,
        scope=ScopeType.GLOBAL,
        stack_tags=(ScopeType.GLOBAL.value,),
        title="Block Destructive Shell & Git Commands",
        description=(
            "Deterministic PreToolUse guardrail that blocks root filesystem deletion, "
            "force-pushes to main/master, and destructive hard resets."
        ),
        content=(
            "### Guardrail: Destructive Command Prevention\n"
            "- Never execute recursive root deletion (`rm -rf /`).\n"
            "- Never force-push (`git push --force` or `--force-with-lease`) to `main` or `master`.\n"
            "- Never run `git reset --hard` without explicit prior confirmation from the user."
        ),
        metadata={
            "action": GuardrailAction.DENY.value,
            "matcher": SHELL_TOOL_MATCHER,
            "pattern": DESTRUCTIVE_COMMANDS_PATTERN,
        },
    ),
    HarnessComponentSeed(
        slug="rewrite-npm-to-bun",
        component_type=ComponentType.GUARDRAIL,
        scope=ScopeType.DOMAIN,
        stack_tags=(BUN_STACK_TAG,),
        title="Rewrite npm Package Commands to Bun",
        description=(
            "Domain-scoped PreToolUse guardrail for Bun workspaces that transparently "
            "rewrites npm install/add/run/test commands to bun equivalents."
        ),
        content=(
            "### Guardrail: Bun Package Manager Enforcement\n"
            "- In workspaces tagged with `bun`, use `bun` (`bun install`, `bun add`, `bun run`, "
            "`bun test`, `bunx`) instead of `npm`. Invocations matching `npm install|i|add|run|test` "
            "are automatically rewritten by the PreToolUse hook."
        ),
        metadata={
            "action": GuardrailAction.OVERWRITE.value,
            "matcher": SHELL_TOOL_MATCHER,
            "pattern": NPM_TO_BUN_PATTERN,
            "replacement": NPM_TO_BUN_REPLACEMENT,
        },
    ),
    HarnessComponentSeed(
        slug="context-graph-discipline",
        component_type=ComponentType.TOOL_GUIDANCE,
        scope=ScopeType.GLOBAL,
        stack_tags=(ScopeType.GLOBAL.value,),
        title="Context Graph (.context/) Exploration & Sync Discipline",
        description=(
            "Tool and workflow guidance for navigating .context/index.md and maintaining "
            "Graph-as-Code integrity before finishing a turn."
        ),
        content=(
            "### Tool Guidance: `.context/` Graph-as-Code Discipline\n"
            "1. **Start from the Map**: read `.context/index.md` at the beginning of a session "
            "to locate relevant `Module`, `Contract`, and `Decision` nodes before running broad "
            "filesystem searches.\n"
            "2. **No Phantom Nodes**: never register a file in `.context/entities/` until that "
            "file physically exists on disk.\n"
            "3. **Atomic Turn Synchronization**: whenever you create, rename, or modify project "
            "source files or architectural contracts, update the corresponding node in "
            "`.context/entities/`, append an entry under `## Логи контекста (AI Traces)`, and "
            "refresh `.context/index.md` before ending your response."
        ),
    ),
    HarnessComponentSeed(
        slug="context-graph-steward",
        component_type=ComponentType.SKILL,
        scope=ScopeType.GLOBAL,
        stack_tags=(ScopeType.GLOBAL.value,),
        title="Context Graph Steward",
        description=(
            "Read, validate, and synchronize the project's .context/ Graph-as-Code knowledge "
            "nodes (Module, Contract, Decision) and AI Traces whenever source files or "
            "architectural contracts change."
        ),
        content=(
            "# Context Graph Steward (`context-graph-steward`)\n\n"
            "Use this skill whenever you need to inspect, validate, or update the repository's "
            "local `.context/` knowledge graph.\n\n"
            "## 1. Entity Classes (`.context/schema.md`)\n"
            "- **`Module`** (`.context/entities/modules/mod-*.md`): cohesive code subsystem mapped "
            "to real repository paths in `files`.\n"
            "- **`Contract`** (`.context/entities/contracts/cnt-*.md`): API, MCP, DDL, or JSON-RPC "
            "interface specification.\n"
            "- **`Decision`** (`.context/entities/decisions/dec-*.md`): Architectural Decision "
            "Record (ADR) capturing trade-offs and invariants.\n\n"
            "## 2. Directed Edge Types (`nx.DiGraph`)\n"
            "- `depends_on`: runtime or structural dependency on another node.\n"
            "- `implements`: module realizes a formal `Contract`.\n"
            "- `governed_by`: node obeys an architectural `Decision`.\n"
            "- `references`: inline `[[node-id]]` wiki-link inside the Markdown body.\n\n"
            "## 3. Synchronization Workflow\n"
            "1. Verify that every path listed in frontmatter `files:` exists on disk "
            "(**No Phantom Nodes**) and every `[[wiki-link]]` resolves to an existing node ID "
            "(**No Broken Links**).\n"
            "2. Update the affected `.context/entities/<folder>/<id>.md` file and record a "
            "dated entry in `## Логи контекста (AI Traces)`.\n"
            "3. Update `.context/index.md` (`updated_at` and node summary if files changed) so "
            "the `Stop` hook (`enforce_context_sync.py`) passes cleanly."
        ),
    ),
)


def filter_seed_components(
    stack_tags: Sequence[str] | None = None,
) -> list[HarnessComponentSeed]:
    """Фильтрует стартовые компоненты по стеку проекта для автономного режима без БД.

    Включает все глобальные инварианты (`scope == ScopeType.GLOBAL`), а также доменные
    компоненты (`scope == ScopeType.DOMAIN`), у которых хотя бы один тег из `stack_tags`
    совпадает с нормализованными тегами рабочего пространства.
    """
    normalized_tags: set[str] = {
        tag.strip().lower()
        for tag in (stack_tags or ())
        if tag and tag.strip()
    }
    normalized_tags.add(ScopeType.GLOBAL.value)

    matched: list[HarnessComponentSeed] = []
    for seed in DEFAULT_HARNESS_SEEDS:
        if seed.scope == ScopeType.GLOBAL:
            matched.append(seed)
            continue
        seed_tags = {tag.strip().lower() for tag in seed.stack_tags if tag}
        if seed_tags & normalized_tags:
            matched.append(seed)
    return matched


async def seed_default_components(db: PostgresDatabase) -> int:
    """Идемпотентно загружает базовые компоненты `DEFAULT_HARNESS_SEEDS` в таблицу `harness_components`.

    Выполняется внутри единой транзакции на старте ядра с конструкцией `ON CONFLICT (slug) DO NOTHING`,
    поэтому пользовательские модификации и эволюционировавшие веса (`score`, `version`) в БД
    никогда не затираются при перезапуске контейнера. При отключённом пуле возвращает `0`.
    """
    if not db.is_connected:
        logger.debug("Skipping Harness² baseline seed because PostgreSQL pool is not connected")
        return 0

    inserted_count = 0
    async with db.pool.acquire() as conn, conn.transaction():
        for seed in DEFAULT_HARNESS_SEEDS:
            command_tag: str = await conn.execute(
                INSERT_SEED_COMPONENT_SQL,
                seed.slug,
                seed.component_type.value,
                seed.scope.value,
                list(seed.stack_tags),
                seed.title,
                seed.description,
                seed.content,
                json.dumps(seed.metadata, ensure_ascii=True),
                seed.status.value,
                seed.score,
                seed.version,
            )
            # PostgreSQL возвращает тег команды вида "INSERT 0 1" или "INSERT 0 0"
            rows_affected = int(command_tag.rsplit(" ", maxsplit=1)[-1])
            inserted_count += rows_affected

    logger.info(
        "Harness² baseline seed verified (inserted=%d, total_defaults=%d)",
        inserted_count,
        len(DEFAULT_HARNESS_SEEDS),
    )
    return inserted_count


__all__ = [
    "BUN_STACK_TAG",
    "DEFAULT_COMPONENT_SCORE",
    "DEFAULT_COMPONENT_VERSION",
    "DEFAULT_HARNESS_SEEDS",
    "DESTRUCTIVE_COMMANDS_PATTERN",
    "NPM_TO_BUN_PATTERN",
    "NPM_TO_BUN_REPLACEMENT",
    "SHELL_TOOL_MATCHER",
    "HarnessComponentSeed",
    "filter_seed_components",
    "seed_default_components",
]
