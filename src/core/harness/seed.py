"""Базовый идемпотентный Seed эволюционирующих компонентов Harness (Layer 2: Knowledge Seed).

Загружает единый источник истины (`Single Source of Truth`) из JSON-манифестов
`seeds/harness-seed.json` и `seeds/prompts-config.json`:
- стартовые правила, барьеры, скрипты хуков, активные навыки и пустые технологические стабы;
- профили целевых ИИ-агентов и метаданные плагина;
- системные промпты компилятора, жизненных хуков, динамического RAG, доменов `DecisionEngine`
  и микро-агентов `AntigravitySDKService`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.enums import (
    ComponentStatus,
    ComponentType,
    ScopeType,
)

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase

logger = logging.getLogger("mcp_coop.core.harness.seed")

SEEDS_DIR_ENV_VAR: str = "MCP_COOP_SEEDS_DIR"
SEEDS_DIR_NAME: str = "seeds"
HARNESS_SEED_FILENAME: str = "harness-seed.json"
PROMPTS_CONFIG_FILENAME: str = "prompts-config.json"

DEFAULT_COMPONENT_SCORE: float = 1.0
DEFAULT_COMPONENT_VERSION: int = 1
DEFAULT_SCRIPT_PRIORITY: int = 10
DEFAULT_SCRIPT_TIMEOUT_MS: int = 500
BUN_STACK_TAG: str = "bun"
HARNESS_ORCHESTRATOR_SLUG: str = "coop-orchestrator"

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
    metadata = EXCLUDED.metadata,
    status = EXCLUDED.status
"""

_CACHED_HARNESS_SEED_PAYLOAD: dict[str, Any] | None = None
_CACHED_PROMPTS_CONFIG: dict[str, Any] | None = None


def _resolve_seeds_directory() -> Path:
    """Определяет директорию `seeds/` через `MCP_COOP_SEEDS_DIR` или корень репозитория/контейнера."""
    env_dir = os.environ.get(SEEDS_DIR_ENV_VAR, "").strip()
    if env_dir:
        candidate = Path(env_dir).expanduser().resolve()
        if candidate.is_dir():
            return candidate

    repo_root_candidate = Path(__file__).resolve().parents[3] / SEEDS_DIR_NAME
    if repo_root_candidate.is_dir():
        return repo_root_candidate

    cwd_candidate = Path.cwd() / SEEDS_DIR_NAME
    if cwd_candidate.is_dir():
        return cwd_candidate

    return repo_root_candidate


def _load_json_seed_file(filename: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Безопасно читает и парсит JSON-файл из директории `seeds/` с возвратом фолбэка при ошибке."""
    seed_path = _resolve_seeds_directory() / filename
    try:
        raw_text = seed_path.read_text(encoding="utf-8")
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            return parsed
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Failed to load seed file %s (%s); using in-memory fallback", seed_path, exc)
    return dict(fallback)


def load_harness_seed_payload() -> dict[str, Any]:
    """Загружает и кэширует манифест `seeds/harness-seed.json` (плагин, профили агентов, компоненты)."""
    global _CACHED_HARNESS_SEED_PAYLOAD
    if _CACHED_HARNESS_SEED_PAYLOAD is None:
        _CACHED_HARNESS_SEED_PAYLOAD = _load_json_seed_file(
            HARNESS_SEED_FILENAME,
            fallback={
                "version": "2026.2",
                "plugin": {},
                "legacy_slugs_to_purge": [],
                "agents": [],
                "components": [],
            },
        )
    return _CACHED_HARNESS_SEED_PAYLOAD


def load_prompts_config() -> dict[str, Any]:
    """Загружает и кэширует манифест `seeds/prompts-config.json` (шаблоны промптов и вопросов Tier 2)."""
    global _CACHED_PROMPTS_CONFIG
    if _CACHED_PROMPTS_CONFIG is None:
        _CACHED_PROMPTS_CONFIG = _load_json_seed_file(
            PROMPTS_CONFIG_FILENAME,
            fallback={
                "version": "2026.2",
                "compiler": {},
                "hooks": {},
                "dynamic_rag": {},
                "decision_domains": {},
                "antigravity_micro_agents": {},
            },
        )
    return _CACHED_PROMPTS_CONFIG


def compute_component_content_hash(content: str) -> str:
    """Вычисляет детерминированный SHA-256 дайджест контента с нормализацией LF и краевых пробелов."""
    normalized = content.replace("\r\n", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class HarnessComponentSeed(BaseModel):
    """Неизменяемая спецификация стартового компонента Harness для инициализации БД и оффлайн-фолбэка.

    Поддерживает как полноценные правила/скиллы/скрипты (`has_content == True`, `status = COMPOSED`),
    так и пустые технологические стабы (`content == ""`, `has_content == False`, `status = DISABLED`),
    которые хранят правила детекции стека в `metadata.detectors` и активируются автоматически,
    когда в `seeds/harness-seed.json` или БД появляется непустой контент.
    """

    model_config = ConfigDict(frozen=True)

    slug: str = Field(..., min_length=1)
    component_type: ComponentType
    scope: ScopeType = ScopeType.GLOBAL
    stack_tags: tuple[str, ...] = (ScopeType.GLOBAL.value,)
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    content: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: ComponentStatus = ComponentStatus.COMPOSED
    score: float = DEFAULT_COMPONENT_SCORE
    version: int = DEFAULT_COMPONENT_VERSION

    @model_validator(mode="before")
    @classmethod
    def _normalize_seed_fields(cls, data: Any) -> Any:
        """Нормализует теги стека, область видимости и статус пустых технологических стабов."""
        if not isinstance(data, Mapping):
            return data

        patched: dict[str, Any] = dict(data)
        raw_content = str(patched.get("content") or "")
        raw_meta = patched.get("metadata")
        meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, Mapping) else {}

        raw_tags = patched.get("stack_tags")
        if isinstance(raw_tags, (list, tuple, set)):
            tags_tuple = tuple(
                str(tag).strip().lower()
                for tag in raw_tags
                if tag is not None and str(tag).strip()
            )
        else:
            tags_tuple = (ScopeType.GLOBAL.value,)
        if not tags_tuple:
            tags_tuple = (ScopeType.GLOBAL.value,)
        patched["stack_tags"] = tags_tuple

        if not patched.get("scope"):
            patched["scope"] = (
                ScopeType.DOMAIN
                if ScopeType.DOMAIN.value in tags_tuple
                else ScopeType.GLOBAL
            )

        if not raw_content.strip():
            patched["content"] = ""
            patched["status"] = ComponentStatus.DISABLED
            meta["is_empty_stub"] = True
        else:
            was_empty_stub = bool(meta.pop("is_empty_stub", False))
            if not patched.get("status") or (
                was_empty_stub
                and str(patched.get("status")) in (ComponentStatus.DISABLED.value, str(ComponentStatus.DISABLED))
            ):
                patched["status"] = ComponentStatus.COMPOSED

        patched["metadata"] = meta
        return patched

    @property
    def has_content(self) -> bool:
        """Возвращает `True`, если компонент содержит непустое тело правила, навыка или скрипта."""
        return bool(self.content.strip())


def _build_legacy_slugs() -> tuple[str, ...]:
    """Извлекает список устаревших слагов для очистки БД из `seeds/harness-seed.json`."""
    payload = load_harness_seed_payload()
    raw_slugs = payload.get("legacy_slugs_to_purge")
    if not isinstance(raw_slugs, list):
        return (
            "harness-orchestrator",
            "context-graph-steward",
            "context-graph-discipline",
        )
    return tuple(str(s).strip() for s in raw_slugs if s and str(s).strip())


def _build_default_harness_seeds() -> tuple[HarnessComponentSeed, ...]:
    """Создаёт кортеж `DEFAULT_HARNESS_SEEDS` из секции `components` файла `seeds/harness-seed.json`."""
    payload = load_harness_seed_payload()
    raw_components = payload.get("components")
    if not isinstance(raw_components, list):
        return ()

    seeds: list[HarnessComponentSeed] = []
    for item in raw_components:
        if not isinstance(item, Mapping):
            continue
        seeds.append(HarnessComponentSeed.model_validate(item))
    return tuple(seeds)


LEGACY_SYSTEM_SLUGS: tuple[str, ...] = _build_legacy_slugs()
DEFAULT_HARNESS_SEEDS: tuple[HarnessComponentSeed, ...] = _build_default_harness_seeds()


def filter_seed_components(
    stack_tags: Sequence[str] | None = None,
) -> list[HarnessComponentSeed]:
    """Фильтрует стартовые компоненты по стеку проекта для автономного режима без БД."""
    return resolve_seed_components_with_overrides(stack_tags or ())


def resolve_seed_components_with_overrides(
    stack_tags: Sequence[str],
    enabled_components: Sequence[str] = (),
    disabled_components: Sequence[str] = (),
) -> list[HarnessComponentSeed]:
    """Выбирает стартовые компоненты с учётом стековых тегов и пер-проектных списков включения/исключения.

    Правила фильтрации:
    1. Любой компонент из `disabled_components` безусловно исключается.
    2. Компонент из `enabled_components` принудительно включается (если у него есть контент).
    3. Остальные непустые компоненты со статусом `COMPOSED` включаются тогда и только тогда,
       когда пересечение `comp.stack_tags` и `normalized_tags` (включающего `"global"`) непусто.
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
        if comp_slug in disabled_set or not comp.has_content:
            continue
        comp_tags = {tag.strip().lower() for tag in comp.stack_tags if tag}
        is_composed_match = (
            comp.status == ComponentStatus.COMPOSED
            and bool(comp_tags & normalized_tags)
        )
        if comp_slug in enabled_set or is_composed_match:
            matched.append(comp)
    return matched


async def seed_default_components(db: PostgresDatabase) -> int:
    """Идемпотентно синхронизирует базовые компоненты `DEFAULT_HARNESS_SEEDS` в таблицу `harness_components`.

    Выполняется внутри единой транзакции на старте ядра с конструкцией `ON CONFLICT (slug) DO UPDATE`,
    включая обновление `status = EXCLUDED.status`, чтобы пустые технологические стабы (`disabled`),
    получившие контент в `seeds/harness-seed.json` (`composed`), автоматически активировались в БД.
    """
    if not db.is_connected:
        logger.debug("Skipping Harness baseline seed because PostgreSQL pool is not connected")
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
            rows_affected = int(command_tag.rsplit(" ", maxsplit=1)[-1])
            inserted_count += rows_affected

    logger.info(
        "Harness baseline seed verified (upserted=%d, total_defaults=%d)",
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
    "HARNESS_SEED_FILENAME",
    "LEGACY_SYSTEM_SLUGS",
    "NPM_TO_BUN_PATTERN",
    "NPM_TO_BUN_REPLACEMENT",
    "PROMPTS_CONFIG_FILENAME",
    "SEEDS_DIR_ENV_VAR",
    "SHELL_TOOL_MATCHER",
    "HarnessComponentSeed",
    "compute_component_content_hash",
    "filter_seed_components",
    "load_harness_seed_payload",
    "load_prompts_config",
    "resolve_seed_components_with_overrides",
    "seed_default_components",
]
