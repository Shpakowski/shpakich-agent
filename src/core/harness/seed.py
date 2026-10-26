"""Базовый идемпотентный Seed эволюционирующих компонентов Harness² (Layer 2: Knowledge Seed).

Определяет эталонный набор стартовых правил (`system_prompt`, `guardrail`, `tool_guidance`, `skill`)
и обеспечивает их идемпотентную загрузку в таблицу `harness_components` PostgreSQL при старте ядра,
а также чистую фильтрацию в памяти при работе в автономном режиме без БД (`Graceful Degradation`).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import (
    ComponentStatus,
    ComponentType,
    GuardrailAction,
    HookEventType,
    ScopeType,
    ScriptExecutionMode,
)

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase

logger = logging.getLogger("mcp_coop.core.harness.seed")

DEFAULT_COMPONENT_SCORE: float = 1.0
DEFAULT_COMPONENT_VERSION: int = 1
DEFAULT_SCRIPT_PRIORITY: int = 10
DEFAULT_SCRIPT_TIMEOUT_MS: int = 500
BUN_STACK_TAG: str = "bun"
HARNESS_ORCHESTRATOR_SLUG: str = "coop-orchestrator"
LEGACY_SYSTEM_SLUGS: tuple[str, ...] = (
    "harness-orchestrator",
    "context-graph-steward",
    "context-graph-discipline",
)


def compute_component_content_hash(content: str) -> str:
    """Вычисляет детерминированный SHA-256 дайджест контента с нормализацией LF и краевых пробелов."""
    normalized = content.replace("\r\n", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

INJECT_CONTEXT_MAP_SCRIPT_CODE: str = '''import json
import sys

def main() -> None:
    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}
    index_md = str(payload.get("context_index_md") or "").strip()
    if not index_md:
        print(json.dumps({}))
        return
    msg = (
        "[MCP-COOP Context Graph]: Active project context graph map (`.context/index.md`).\\n"
        "Use links from this map for targeted node reading and always update the graph "
        "after modifying project files:\\n\\n"
        f"{index_md}"
    )
    print(json.dumps({"system_message": msg}, ensure_ascii=False))

if __name__ == "__main__":
    main()
'''

TRACK_FILE_CHANGES_SCRIPT_CODE: str = '''import json
import os
import sys

FILE_MUTATION_TOOLS = {
    "write_file",
    "edit_file",
    "write",
    "edit",
    "multiedit",
    "replace_file_content",
    "multi_replace_file_content",
}
TRACKED_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".sol", ".sql", ".sh", ".yml", ".yaml", ".toml"
}

def main() -> None:
    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}
    if payload.get("error"):
        print(json.dumps({}))
        return

    tool_name = str(payload.get("tool_name") or "").strip().lower()
    if tool_name not in FILE_MUTATION_TOOLS:
        print(json.dumps({}))
        return

    tool_args = payload.get("tool_args") or {}
    raw_path = str(
        tool_args.get("Path")
        or tool_args.get("path")
        or tool_args.get("file_path")
        or tool_args.get("TargetFile")
        or ""
    ).strip()
    workspace_path = str(payload.get("workspace_path") or "").strip()
    if not raw_path or not workspace_path:
        print(json.dumps({}))
        return

    abs_ws = os.path.abspath(workspace_path)
    abs_target = os.path.abspath(os.path.join(abs_ws, raw_path))
    rel_path = os.path.relpath(abs_target, abs_ws).replace("\\\\", "/")
    if rel_path.startswith(".."):
        print(json.dumps({}))
        return

    if rel_path == ".context" or rel_path.startswith(".context/"):
        print(json.dumps({
            "state_updates": {
                "context_updated": "1",
                "reminder_sent": "0",
            }
        }))
        return

    _, ext = os.path.splitext(rel_path.lower())
    if ext in TRACKED_EXTENSIONS:
        session_state = payload.get("session_state") or {}
        existing_raw = str(session_state.get("modified_files") or "").strip()
        files = [f for f in existing_raw.split(",") if f]
        if rel_path not in files:
            files.append(rel_path)
        print(json.dumps({
            "state_updates": {
                "code_modified": "1",
                "context_updated": "0",
                "modified_files": ",".join(files[:10]),
            }
        }))
        return

    print(json.dumps({}))

if __name__ == "__main__":
    main()
'''

ENFORCE_CONTEXT_SYNC_SCRIPT_CODE: str = '''import json
import sys

BYPASS_REASONS = {
    "USER_CANCELED",
    "ERROR",
    "MAX_INVOCATIONS",
    "MAX_FORCED_INVOCATIONS",
    "MAX_TOKEN_BUDGET_EXCEEDED",
}

def main() -> None:
    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}
    raw_payload = payload.get("raw_payload") or {}
    termination_reason = str(raw_payload.get("terminationReason") or "")
    if (
        payload.get("stop_hook_active")
        or payload.get("error")
        or termination_reason in BYPASS_REASONS
    ):
        print(json.dumps({"action": "allow"}))
        return

    state = payload.get("session_state") or {}
    code_modified = str(state.get("code_modified") or "0") == "1"
    context_updated = str(state.get("context_updated") or "0") == "1"
    reminder_sent = str(state.get("reminder_sent") or "0") == "1"
    modified_raw = str(state.get("modified_files") or "").strip()
    files_list = ", ".join(f"`{f}`" for f in modified_raw.split(",") if f) or "project files"

    if code_modified and not context_updated and not reminder_sent:
        reason = (
            f"[MCP-COOP Context Gate]: Project files were modified in this turn ({files_list}), "
            "but the context graph in `.context/` (`index.md` or `.context/entities/`) "
            "was not synchronized. Please update `.context/` and append an entry to `AI Traces` "
            "before finishing. Always reply to the user in the language they use in chat."
        )
        print(json.dumps({
            "action": "continue",
            "reason": reason,
            "state_updates": {"reminder_sent": "1"},
        }, ensure_ascii=False))
        return

    if context_updated or reminder_sent:
        print(json.dumps({
            "action": "allow",
            "state_updates": {
                "code_modified": "0",
                "context_updated": "0",
                "reminder_sent": "0",
                "modified_files": "",
            },
        }))
        return

    print(json.dumps({"action": "allow"}))

if __name__ == "__main__":
    main()
'''

# Регулярные выражения для перехвата вызовов терминальных инструментов во всех 5 агентах
SHELL_TOOL_MATCHER: str = "run_command|Bash|shell|exec_command"
DESTRUCTIVE_COMMANDS_PATTERN: str = (
    r"(?:rm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+/(?:\s|$)"
    r"|git\s+push\s+.*--force(?:-with-lease)?\s+(?:origin\s+)?(?:main|master)\b"
    r"|git\s+reset\s+--hard\b)"
)
NPM_TO_BUN_PATTERN: str = r"^\s*npm\s+(install|i|add|run|test)\b"
NPM_TO_BUN_REPLACEMENT: str = r"bun \1"

DELETE_LEGACY_SYSTEM_SLUGS_SQL: str = """
DELETE FROM harness_components
WHERE slug = ANY($1::text[])
"""

INSERT_SEED_COMPONENT_SQL: str = """
INSERT INTO harness_components (
    slug,
    component_type,
    scope,
    stack_tags,
    title,
    description,
    content,
    content_hash,
    metadata,
    status,
    score,
    version
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12)
ON CONFLICT (slug) DO UPDATE SET
    component_type = EXCLUDED.component_type,
    scope = EXCLUDED.scope,
    stack_tags = EXCLUDED.stack_tags,
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    content = EXCLUDED.content,
    content_hash = EXCLUDED.content_hash,
    metadata = EXCLUDED.metadata
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
        title="Universal Engineering Rules & Thinking Discipline",
        description=(
            "Universal, project-agnostic engineering mindset rules: incremental execution, "
            "anti-sycophancy code review, KISS/DRY/SOLID/YAGNI with Zero-Hardcode, and operational safety."
        ),
        content=(
            "## 1. Deliberate & Incremental Execution\n"
            "- Work in small, verifiable iterations (`Plan -> Step -> Review`) rather than "
            "dumping large, multi-module changes at once.\n"
            "- Before writing code, briefly state **WHAT** is changing and **WHY**; after "
            "modifying code, summarize the resulting data flow.\n\n"
            "## 2. Engineering Rigor & Anti-Sycophancy\n"
            "- Treat user questions (`Why is it done this way?`, `What if we use X?`) as an "
            "engineering discussion, **never** as an automatic order to delete and rewrite code.\n"
            "- Explain trade-offs first, propose alternatives with a `(Recommended)` option, "
            "and modify code only after explicit user confirmation.\n"
            "- Verify unfamiliar APIs, schemas, and file paths via context or documentation "
            "instead of guessing.\n\n"
            "## 3. Code Quality & Language Standard\n"
            "- Maintain a Single Source of Truth (`DRY`), clear single responsibility (`SRP`), "
            "and `KISS / SOLID / YAGNI`.\n"
            "- **Zero-Hardcode**: never scatter magic numbers or raw string literals across "
            "modules; extract constants, enums, and typed settings.\n"
            "- **Language**: write all code, identifiers, and system literals in **English**; "
            "write architectural comments/docstrings (`Why & How`) and chat responses in the "
            "**user's language**.\n\n"
            "## 4. Operational Safety & Orchestration\n"
            "- Never run destructive commands (`rm -rf /`, force-push to main branches, or "
            "unconfirmed `git reset --hard`).\n"
            "- Apply the `coop-orchestrator` skill for task decomposition, context hygiene, "
            "subagent delegation, and verification loops."
        ),
        metadata={
            "execution_mode": ScriptExecutionMode.ALWAYS.value,
        },
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
            "execution_mode": ScriptExecutionMode.SEMANTIC.value,
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
            "execution_mode": ScriptExecutionMode.SEMANTIC.value,
        },
    ),
    HarnessComponentSeed(
        slug=HARNESS_ORCHESTRATOR_SLUG,
        component_type=ComponentType.SKILL,
        scope=ScopeType.GLOBAL,
        stack_tags=(ScopeType.GLOBAL.value,),
        title="Cooperative Task Orchestrator",
        description=(
            "Universal task orchestration, context window hygiene, subagent delegation, "
            "and verification loop workflow. Apply when decomposing multi-step tasks, "
            "coordinating research or subagents, and verifying code changes."
        ),
        content=(
            "# Cooperative Task Orchestrator (`coop-orchestrator`)\n\n"
            "## 1. Task Decomposition & Single-Loop First\n"
            "- Break ambiguous or multi-step goals into small, atomic, independently "
            "verifiable steps before editing code.\n"
            "- Default to a direct single-agent loop for localized tasks; avoid over-engineering "
            "workflows when a direct edit and check is sufficient.\n\n"
            "## 2. Context Hygiene & Subagent Delegation\n"
            "- Protect the primary context window from noise and large raw outputs.\n"
            "- Spawn subagents only for broad codebase exploration, heavy documentation "
            "research, or independent parallel workstreams.\n"
            "- Because a subagent has an isolated context, always provide a self-contained brief:\n"
            "  1. **Objective**: exact question to answer or artifact to produce.\n"
            "  2. **Scope & Constraints**: target directories, files, or boundaries.\n"
            "  3. **Expected Output**: concise structured summary with exact file/line "
            "references, never raw file dumps.\n\n"
            "## 3. Execution-Verification Loop (Evaluator-Optimizer)\n"
            "- Never ship code blind: validate every atomic change with targeted typechecks, "
            "linters, or unit tests.\n"
            "- **Root-Cause First**: when a test or command fails, read the exact error and "
            "identify the root cause before making another edit.\n"
            "- **Bounded Retries**: never repeat a failing command blindly. If an issue is not "
            "resolved after 2–3 reasoned attempts, stop, summarize the findings, and align "
            "with the user."
        ),
        metadata={
            "execution_mode": ScriptExecutionMode.ALWAYS.value,
        },
    ),
    HarnessComponentSeed(
        slug="inject-context-map",
        component_type=ComponentType.SCRIPT,
        scope=ScopeType.GLOBAL,
        stack_tags=(ScopeType.GLOBAL.value,),
        title="Context Graph Map Injector (SessionStart Hook Script)",
        description=(
            "Injects the project's active .context/index.md knowledge graph map into the "
            "agent's system context at SessionStart."
        ),
        content=INJECT_CONTEXT_MAP_SCRIPT_CODE,
        metadata={
            "hook_events": [HookEventType.SESSION_START.value],
            "execution_mode": ScriptExecutionMode.ALWAYS.value,
            "priority": DEFAULT_SCRIPT_PRIORITY,
            "timeout_ms": DEFAULT_SCRIPT_TIMEOUT_MS,
        },
    ),
    HarnessComponentSeed(
        slug="track-file-changes",
        component_type=ComponentType.SCRIPT,
        scope=ScopeType.GLOBAL,
        stack_tags=(ScopeType.GLOBAL.value,),
        title="Workspace Code & Context Graph Mutation Tracker (PostToolUse Hook Script)",
        description=(
            "Tracks file edits on PostToolUse and records code_modified and context_updated "
            "flags in Redis session state for Context Graph enforcement."
        ),
        content=TRACK_FILE_CHANGES_SCRIPT_CODE,
        metadata={
            "hook_events": [HookEventType.POST_TOOL_USE.value],
            "execution_mode": ScriptExecutionMode.ALWAYS.value,
            "priority": DEFAULT_SCRIPT_PRIORITY,
            "timeout_ms": DEFAULT_SCRIPT_TIMEOUT_MS,
        },
    ),
    HarnessComponentSeed(
        slug="enforce-context-sync",
        component_type=ComponentType.SCRIPT,
        scope=ScopeType.GLOBAL,
        stack_tags=(ScopeType.GLOBAL.value,),
        title="Context Graph Synchronization Gate (Stop Hook Script)",
        description=(
            "Enforces atomic .context/ graph synchronization on Stop when project source "
            "files were modified during the turn."
        ),
        content=ENFORCE_CONTEXT_SYNC_SCRIPT_CODE,
        metadata={
            "hook_events": [HookEventType.STOP.value],
            "execution_mode": ScriptExecutionMode.ALWAYS.value,
            "priority": DEFAULT_SCRIPT_PRIORITY,
            "timeout_ms": DEFAULT_SCRIPT_TIMEOUT_MS,
        },
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
    return resolve_seed_components_with_overrides(stack_tags or ())


def resolve_seed_components_with_overrides(
    stack_tags: Sequence[str],
    enabled_components: Sequence[str] = (),
    disabled_components: Sequence[str] = (),
) -> list[HarnessComponentSeed]:
    """Выбирает стартовые компоненты с учётом стековых тегов и пер-проектных списков включения/исключения.

    Правила фильтрации:
    1. Любой компонент из `disabled_components` безусловно исключается.
    2. Компонент из `enabled_components` принудительно включается даже без совпадения `stack_tags`.
    3. Остальные компоненты со статусом `COMPOSED` включаются, если их `scope == GLOBAL`
       или пересечение `comp.stack_tags` и `stack_tags` непусто.
    """
    normalized_tags: set[str] = {
        tag.strip().lower()
        for tag in stack_tags
        if tag and tag.strip()
    }
    normalized_tags.add(ScopeType.GLOBAL.value)
    enabled_set: set[str] = {
        slug.strip().lower()
        for slug in enabled_components
        if slug and slug.strip()
    }
    disabled_set: set[str] = {
        slug.strip().lower()
        for slug in disabled_components
        if slug and slug.strip()
    }

    matched: list[HarnessComponentSeed] = []
    for comp in DEFAULT_HARNESS_SEEDS:
        comp_slug = comp.slug.strip().lower()
        if comp_slug in disabled_set:
            continue
        comp_tags = {tag.strip().lower() for tag in comp.stack_tags if tag}
        is_composed_match = comp.status == ComponentStatus.COMPOSED and (
            comp.scope == ScopeType.GLOBAL or bool(comp_tags & normalized_tags)
        )
        if comp_slug in enabled_set or is_composed_match:
            matched.append(comp)
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
        await conn.execute(DELETE_LEGACY_SYSTEM_SLUGS_SQL, list(LEGACY_SYSTEM_SLUGS))
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
                compute_component_content_hash(seed.content),
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
    "HARNESS_ORCHESTRATOR_SLUG",
    "NPM_TO_BUN_PATTERN",
    "NPM_TO_BUN_REPLACEMENT",
    "SHELL_TOOL_MATCHER",
    "HarnessComponentSeed",
    "compute_component_content_hash",
    "filter_seed_components",
    "resolve_seed_components_with_overrides",
    "seed_default_components",
]
