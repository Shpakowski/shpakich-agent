"""Компилятор эволюционирующего харнеса в целевые форматы ИИ-агентов (Layer 2 & Layer 4: Harness² Compiler).

Собирает канонический бандл знаний (`CanonicalHarnessBundle`) из PostgreSQL, кэша Redis LangCache
или базового Seed (`DEFAULT_HARNESS_SEEDS`) по тегам стека проекта и транслирует его через
декларативный профиль `AgentTargetProfile` в готовый список файлов `RenderedFile` со стратегиями записи.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core import __version__
from src.core.db.redis_client import compute_stack_hash
from src.core.enums import (
    AgentType,
    ComponentEffectiveState,
    ComponentStatus,
    ComponentType,
    FileWriteStrategy,
    GuardrailAction,
    HookDialect,
    HookEventType,
    McpConfigFormat,
    RulesFormat,
    ScopeType,
)
from src.core.harness.profiles import (
    AgentTargetProfile,
    HooksTargetSpec,
    McpTargetSpec,
    PluginManifestSpec,
    RulesTargetSpec,
    get_agent_profile,
)
from src.core.harness.seed import (
    DEFAULT_HARNESS_SEEDS,
    SHELL_TOOL_MATCHER,
    HarnessComponentSeed,
    resolve_seed_components_with_overrides,
)

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase
    from src.core.db.redis_client import RedisSessionStore

logger = logging.getLogger("mcp_coop.core.harness.compiler")

# Бюджет размера одного Markdown-файла правил (24 КБ) для гарантированного прохождения
# лимитов контекстного инжектора Antigravity 2.0 и OpenAI Codex CLI (project_doc_max_bytes = 32 KiB).
MAX_RULES_MARKDOWN_BYTES: int = 24_000
TRUNCATION_NOTICE: str = "\n\n<!-- Rules truncated to fit MAX_RULES_MARKDOWN_BYTES budget -->\n"

DEFAULT_LOCALHOST_HOST: str = "127.0.0.1"
MCP_HTTP_PATH: str = "/mcp"
DEFAULT_MCP_URL: str = f"http://{DEFAULT_LOCALHOST_HOST}:8787{MCP_HTTP_PATH}"
DEFAULT_HOOK_CLI_COMMAND_TEMPLATE: str = "bunx mcp-coop-agentic-factory hook {event} --agent {agent}"

CURSOR_HOOKS_SCHEMA_VERSION: int = 1
OPENCODE_SCHEMA_URL: str = "https://opencode.ai/config.json"
PROTOJSON_WILDCARD_MATCHER: str = ".*"
GLOB_WILDCARD_MATCHER: str = "*"

BUNDLE_SOURCE_POSTGRES: str = "postgres"
BUNDLE_SOURCE_REDIS_CACHE: str = "redis_cache"
BUNDLE_SOURCE_SEED_FALLBACK: str = "seed_fallback"

CACHE_TOKEN_ENABLED_COMPONENT_PREFIX: str = "+comp:"
CACHE_TOKEN_DISABLED_COMPONENT_PREFIX: str = "-comp:"
CACHE_TOKEN_DISABLED_HOOK_PREFIX: str = "-hook:"

SELECT_COMPOSED_COMPONENTS_SQL: str = """
SELECT
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
FROM harness_components
WHERE (status = $1 AND (scope = $2 OR stack_tags && $3::text[]))
   OR slug = ANY($4::text[])
ORDER BY score DESC, created_at ASC
"""

SELECT_ALL_COMPONENTS_SQL: str = """
SELECT
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
FROM harness_components
ORDER BY score DESC, created_at ASC
"""


def _normalize_string_tuple(values: Sequence[Any] | None) -> tuple[str, ...]:
    """Приводит произвольную последовательность значений к отсортированному кортежу строк в нижнем регистре."""
    if not values:
        return ()
    cleaned: set[str] = {
        str(item).strip().lower()
        for item in values
        if item is not None and str(item).strip()
    }
    return tuple(sorted(cleaned))


def _extract_sequence_from_mappings(
    mappings: Sequence[Mapping[str, Any]],
    keys: Sequence[str],
) -> list[Any]:
    """Собирает элементы списков/кортежей по заданным ключам из нескольких словарей конфигурации."""
    collected: list[Any] = []
    for mapping in mappings:
        for key in keys:
            raw_val = mapping.get(key)
            if isinstance(raw_val, (list, tuple, set)):
                collected.extend(raw_val)
    return collected


class ProjectHarnessOverrides(BaseModel):
    """Пер-проектные переопределения стека, компонентов и хуков харнеса (`.context/harness.json`).

    Позволяет разработчику точечно адаптировать поведение глобальной фабрики под конкретный
    репозиторий (добавлять или скрывать стековые теги, принудительно включать/выключать отдельные
    правила, барьеры `PreToolUse`, навыки и жизненные хуки) без модификации глобальной БД.
    """

    model_config = ConfigDict(frozen=True)

    extra_tags: tuple[str, ...] = ()
    excluded_tags: tuple[str, ...] = ()
    enabled_components: tuple[str, ...] = ()
    disabled_components: tuple[str, ...] = ()
    disabled_hooks: tuple[HookEventType, ...] = ()

    @field_validator(
        "extra_tags",
        "excluded_tags",
        "enabled_components",
        "disabled_components",
        mode="before",
    )
    @classmethod
    def _validate_normalized_tuples(cls, value: Any) -> tuple[str, ...]:
        """Гарантирует нормализацию строковых кортежей даже при прямой инициализации модели."""
        if isinstance(value, (list, tuple, set)):
            return _normalize_string_tuple(tuple(value))
        return ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> ProjectHarnessOverrides:
        """Безопасно парсит оверрайды из плоского словаря или вложенной структуры `.context/harness.json`."""
        if not raw or not isinstance(raw, Mapping):
            return cls()

        top_sources: list[Mapping[str, Any]] = [raw]
        nested_overrides = raw.get("overrides")
        if isinstance(nested_overrides, Mapping):
            top_sources.append(nested_overrides)

        stack_sources: list[Mapping[str, Any]] = list(top_sources)
        for src in tuple(top_sources):
            nested_stack = src.get("stack")
            if isinstance(nested_stack, Mapping):
                stack_sources.append(nested_stack)

        extra_tags = _normalize_string_tuple(
            _extract_sequence_from_mappings(
                stack_sources,
                ("extra_tags", "extraTags", "extra"),
            )
        )
        excluded_tags = _normalize_string_tuple(
            _extract_sequence_from_mappings(
                stack_sources,
                ("excluded_tags", "excludedTags", "excluded"),
            )
        )
        enabled_components = _normalize_string_tuple(
            _extract_sequence_from_mappings(
                top_sources,
                ("enabled_components", "enabledComponents"),
            )
        )
        disabled_components = _normalize_string_tuple(
            _extract_sequence_from_mappings(
                top_sources,
                ("disabled_components", "disabledComponents"),
            )
        )

        raw_hooks = _extract_sequence_from_mappings(
            top_sources,
            ("disabled_hooks", "disabledHooks"),
        )
        parsed_hooks: set[HookEventType] = set()
        for item in raw_hooks:
            if isinstance(item, HookEventType):
                parsed_hooks.add(item)
                continue
            candidate = str(item).strip().lower()
            if not candidate:
                continue
            try:
                parsed_hooks.add(HookEventType(candidate))
            except ValueError:
                continue

        ordered_hooks = tuple(event for event in HookEventType if event in parsed_hooks)
        return cls(
            extra_tags=extra_tags,
            excluded_tags=excluded_tags,
            enabled_components=enabled_components,
            disabled_components=disabled_components,
            disabled_hooks=ordered_hooks,
        )


class CompiledGuardrail(BaseModel):
    """Скомпилированное детерминированное правило защитного барьера (`PreToolUse`)."""

    model_config = ConfigDict(frozen=True)

    slug: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    action: GuardrailAction
    matcher: str = SHELL_TOOL_MATCHER
    pattern: str = Field(..., min_length=1)
    replacement: str | None = None
    description: str = ""


class CompiledSkillArtifact(BaseModel):
    """Каноническое представление модульного навыка (`SKILL.md` + связанные скрипты)."""

    model_config = ConfigDict(frozen=True)

    slug: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    body_markdown: str = Field(..., min_length=1)
    scripts: dict[str, str] = Field(default_factory=dict)

    def render_skill_md(self) -> str:
        """Генерирует содержимое `SKILL.md` с YAML-фронтматтером по стандарту `.agents/skills/`."""
        clean_desc = " ".join(self.description.strip().splitlines())
        body = self.body_markdown.strip()
        return (
            "---\n"
            f"name: {self.slug}\n"
            "description: >-\n"
            f"  {clean_desc}\n"
            "---\n\n"
            f"{body}\n"
        )


class CanonicalHarnessBundle(BaseModel):
    """Независимое от вендора промежуточное представление (Canonical IR) скомпилированного харнеса.

    Именно этот объект сохраняется в кэше Redis (`cache:rules:{stack_hash}`), что позволяет
    переиспользовать один скомпилированный срез знаний для любых целевых ИИ-агентов с одинаковым стеком.
    """

    model_config = ConfigDict(frozen=True)

    stack_tags: tuple[str, ...]
    stack_hash: str
    rules_markdown: str
    guardrails: tuple[CompiledGuardrail, ...]
    skills: tuple[CompiledSkillArtifact, ...]
    mcp_url: str = DEFAULT_MCP_URL
    hook_events: tuple[HookEventType, ...] = tuple(HookEventType)
    source: str = BUNDLE_SOURCE_SEED_FALLBACK


class RenderedFile(BaseModel):
    """Готовый к записи на диск файл адаптера для конкретного ИИ-агента."""

    model_config = ConfigDict(frozen=True)

    relative_path: str = Field(..., min_length=1)
    content: str
    write_strategy: FileWriteStrategy
    description: str


class RenderedHarnessArtifact(BaseModel):
    """Итоговый артефакт компиляции харнеса, возвращаемый CLI-инжектору при регистрации проекта."""

    model_config = ConfigDict(frozen=True)

    agent_type: AgentType
    display_name: str
    stack_tags: tuple[str, ...]
    stack_hash: str
    source: str
    guardrails: tuple[CompiledGuardrail, ...]
    files: tuple[RenderedFile, ...]


def normalize_stack_tags(stack_tags: Sequence[str] | None = None) -> tuple[str, ...]:
    """Нормализует список тегов стека проекта и гарантированно включает глобальный контур `global`."""
    cleaned: set[str] = {
        tag.strip().lower()
        for tag in (stack_tags or ())
        if tag and tag.strip()
    }
    cleaned.add(ScopeType.GLOBAL.value)
    return tuple(sorted(cleaned))


def resolve_effective_stack_tags(
    base_tags: Sequence[str] | None,
    overrides: ProjectHarnessOverrides | None = None,
) -> tuple[str, ...]:
    """Вычисляет результирующий набор тегов стека с учётом `extra_tags` и `excluded_tags` проекта.

    Глобальный тег `ScopeType.GLOBAL` (`"global"`) защищён от исключения, чтобы базовые
    архитектурные инварианты ядра оставались доступными во всех проектах.
    """
    active_overrides = overrides or ProjectHarnessOverrides()
    effective: set[str] = (
        set(normalize_stack_tags(base_tags)) | set(active_overrides.extra_tags)
    ) - (set(active_overrides.excluded_tags) - {ScopeType.GLOBAL.value})
    effective.add(ScopeType.GLOBAL.value)
    return tuple(sorted(effective))


def compute_bundle_cache_tags(
    effective_tags: Sequence[str],
    overrides: ProjectHarnessOverrides | None = None,
) -> list[str]:
    """Формирует список токенов для `compute_stack_hash`, учитывающий активные пер-проектные оверрайды.

    Почему оверрайды кодируются в токены кэша (`+comp:...`, `-comp:...`, `-hook:...`):
    - Два проекта с одинаковым стеком (`["bun", "global"]`), но разными отключёнными барьерами
      получают изолированные ключи `cache:rules:{stack_hash}` в Redis LangCache без коллизий.
    """
    tokens: list[str] = list(normalize_stack_tags(effective_tags))
    if overrides is None:
        return tokens

    disabled_set = set(overrides.disabled_components)
    for slug in overrides.enabled_components:
        if slug not in disabled_set:
            tokens.append(f"{CACHE_TOKEN_ENABLED_COMPONENT_PREFIX}{slug}")
    for slug in overrides.disabled_components:
        tokens.append(f"{CACHE_TOKEN_DISABLED_COMPONENT_PREFIX}{slug}")
    for event in overrides.disabled_hooks:
        tokens.append(f"{CACHE_TOKEN_DISABLED_HOOK_PREFIX}{event.value}")
    return tokens


def enforce_markdown_byte_budget(
    markdown_text: str,
    max_bytes: int = MAX_RULES_MARKDOWN_BYTES,
) -> str:
    """Гарантирует, что размер Markdown-файла правил не превышает байтовый лимит целевого агента.

    Если текст превышает `max_bytes`, обрезает его по границе целых строк и добавляет комментарий,
    предотвращая отказ загрузки правил в Antigravity 2.0 и Codex CLI.
    """
    encoded = markdown_text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return markdown_text

    notice_bytes = len(TRUNCATION_NOTICE.encode("utf-8"))
    target_budget = max(0, max_bytes - notice_bytes)
    kept_lines: list[str] = []
    used_bytes = 0

    for line in markdown_text.splitlines(keepends=True):
        line_len = len(line.encode("utf-8"))
        if used_bytes + line_len > target_budget:
            break
        kept_lines.append(line)
        used_bytes += line_len

    return "".join(kept_lines).rstrip() + TRUNCATION_NOTICE


class HarnessCompiler:
    """Двухфазный компилятор знаний `Harness²` (`Storage/Seed -> Canonical IR -> AgentTargetProfile`)."""

    def __init__(
        self,
        db: PostgresDatabase | None = None,
        redis: RedisSessionStore | None = None,
        *,
        mcp_url: str = DEFAULT_MCP_URL,
        plugin_version: str = __version__,
        hook_command_template: str = DEFAULT_HOOK_CLI_COMMAND_TEMPLATE,
    ) -> None:
        self._db: PostgresDatabase | None = db
        self._redis: RedisSessionStore | None = redis
        self._mcp_url: str = mcp_url
        self._plugin_version: str = plugin_version
        self._hook_command_template: str = hook_command_template

    @staticmethod
    def _row_to_seed(row: Mapping[str, Any]) -> HarnessComponentSeed:
        """Преобразует строку из таблицы `harness_components` PostgreSQL в доменную модель `HarnessComponentSeed`."""
        raw_meta = row["metadata"]
        parsed_meta: dict[str, Any] = (
            json.loads(raw_meta)
            if isinstance(raw_meta, str)
            else dict(raw_meta or {})
        )
        return HarnessComponentSeed(
            slug=str(row["slug"]),
            component_type=ComponentType(str(row["component_type"])),
            scope=ScopeType(str(row["scope"])),
            stack_tags=tuple(row["stack_tags"] or (ScopeType.GLOBAL.value,)),
            title=str(row["title"]),
            description=str(row["description"] or row["title"]),
            content=str(row["content"]),
            metadata=parsed_meta,
            status=ComponentStatus(str(row["status"])),
            score=float(row["score"]),
            version=int(row["version"]),
        )

    async def _load_components(
        self,
        normalized_tags: tuple[str, ...],
        overrides: ProjectHarnessOverrides | None = None,
    ) -> tuple[list[HarnessComponentSeed], str]:
        """Загружает активные и принудительно включённые компоненты из PostgreSQL или базового Seed."""
        active_overrides = overrides or ProjectHarnessOverrides()
        disabled_set = set(active_overrides.disabled_components)

        if self._db is not None and self._db.is_connected:
            try:
                async with self._db.pool.acquire() as conn:
                    rows = await conn.fetch(
                        SELECT_COMPOSED_COMPONENTS_SQL,
                        ComponentStatus.COMPOSED.value,
                        ScopeType.GLOBAL.value,
                        list(normalized_tags),
                        list(active_overrides.enabled_components),
                    )
                if rows:
                    loaded: list[HarnessComponentSeed] = [
                        self._row_to_seed(row)
                        for row in rows
                        if str(row["slug"]).strip().lower() not in disabled_set
                    ]
                    return loaded, BUNDLE_SOURCE_POSTGRES
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to query harness_components from PostgreSQL (%s); using seed fallback",
                    exc,
                )

        return (
            resolve_seed_components_with_overrides(
                normalized_tags,
                active_overrides.enabled_components,
                active_overrides.disabled_components,
            ),
            BUNDLE_SOURCE_SEED_FALLBACK,
        )

    async def build_canonical_bundle(
        self,
        stack_tags: Sequence[str] | None = None,
        *,
        profile_overrides: Mapping[str, Any] | ProjectHarnessOverrides | None = None,
    ) -> CanonicalHarnessBundle:
        """Формирует вендоронезависимый бандл `CanonicalHarnessBundle` с кэшированием в Redis LangCache."""
        overrides = (
            profile_overrides
            if isinstance(profile_overrides, ProjectHarnessOverrides)
            else ProjectHarnessOverrides.from_mapping(profile_overrides)
        )
        effective_tags = resolve_effective_stack_tags(stack_tags, overrides)
        cache_tokens = compute_bundle_cache_tags(effective_tags, overrides)
        stack_hash = compute_stack_hash(cache_tokens)

        if self._redis is not None and self._redis.is_connected:
            cached_payload = await self._redis.get_cached_rules_bundle(cache_tokens)
            if cached_payload is not None:
                try:
                    return CanonicalHarnessBundle.model_validate(
                        {**cached_payload, "source": BUNDLE_SOURCE_REDIS_CACHE}
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Invalid cached rules bundle for %s: %s", stack_hash, exc)

        components, source = await self._load_components(effective_tags, overrides)

        rules_sections: list[str] = [
            (
                "# MCP-COOP Agentic Factory — Active Engineering Rules (`Harness²`)\n\n"
                f"> **Compiled Stack Tags:** `{', '.join(effective_tags)}`  \n"
                f"> **Stack Hash:** `{stack_hash}`"
            )
        ]
        guardrails: list[CompiledGuardrail] = []
        script_map: dict[str, dict[str, str]] = {}

        for comp in components:
            if comp.component_type == ComponentType.SCRIPT:
                target_skill = str(comp.metadata.get("skill_slug", "")).strip()
                filename = str(comp.metadata.get("filename", f"{comp.slug}.py")).strip()
                if target_skill:
                    script_map.setdefault(target_skill, {})[filename] = comp.content

        skills: list[CompiledSkillArtifact] = []
        for comp in components:
            if comp.component_type in (
                ComponentType.SYSTEM_PROMPT,
                ComponentType.GUARDRAIL,
                ComponentType.TOOL_GUIDANCE,
                ComponentType.SUBAGENT,
            ):
                rules_sections.append(comp.content.strip())

            if comp.component_type == ComponentType.GUARDRAIL:
                raw_action = str(comp.metadata.get("action", GuardrailAction.DENY.value))
                pattern = str(comp.metadata.get("pattern", "")).strip()
                if pattern:
                    guardrails.append(
                        CompiledGuardrail(
                            slug=comp.slug,
                            title=comp.title,
                            action=GuardrailAction(raw_action),
                            matcher=str(comp.metadata.get("matcher", SHELL_TOOL_MATCHER)),
                            pattern=pattern,
                            replacement=(
                                str(comp.metadata["replacement"])
                                if comp.metadata.get("replacement") is not None
                                else None
                            ),
                            description=comp.description,
                        )
                    )

            if comp.component_type == ComponentType.SKILL:
                skills.append(
                    CompiledSkillArtifact(
                        slug=comp.slug,
                        title=comp.title,
                        description=comp.description,
                        body_markdown=comp.content,
                        scripts=script_map.get(comp.slug, {}),
                    )
                )

        rules_markdown = enforce_markdown_byte_budget("\n\n---\n\n".join(rules_sections) + "\n")
        disabled_hooks_set = set(overrides.disabled_hooks)
        hook_events = tuple(
            event for event in HookEventType if event not in disabled_hooks_set
        )

        bundle = CanonicalHarnessBundle(
            stack_tags=effective_tags,
            stack_hash=stack_hash,
            rules_markdown=rules_markdown,
            guardrails=tuple(guardrails),
            skills=tuple(skills),
            mcp_url=self._mcp_url,
            hook_events=hook_events,
            source=source,
        )

        if self._redis is not None and self._redis.is_connected:
            await self._redis.cache_rules_bundle(cache_tokens, bundle.model_dump(mode="json"))

        return bundle

    async def list_catalog_components(
        self,
        stack_tags: Sequence[str] | None = None,
        profile_overrides: Mapping[str, Any] | ProjectHarnessOverrides | None = None,
    ) -> list[dict[str, Any]]:
        """Возвращает полный каталог компонентов `Harness²` с вычисленным `effective_state` для проекта."""
        overrides = (
            profile_overrides
            if isinstance(profile_overrides, ProjectHarnessOverrides)
            else ProjectHarnessOverrides.from_mapping(profile_overrides)
        )
        effective_tags = resolve_effective_stack_tags(stack_tags, overrides)
        effective_tags_set = set(effective_tags)
        enabled_set = set(overrides.enabled_components)
        disabled_set = set(overrides.disabled_components)

        catalog_seeds: list[HarnessComponentSeed] = []
        if self._db is not None and self._db.is_connected:
            try:
                async with self._db.pool.acquire() as conn:
                    rows = await conn.fetch(SELECT_ALL_COMPONENTS_SQL)
                if rows:
                    catalog_seeds = [self._row_to_seed(row) for row in rows]
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to query catalog from PostgreSQL (%s); using seed fallback",
                    exc,
                )

        if not catalog_seeds:
            catalog_seeds = list(DEFAULT_HARNESS_SEEDS)

        items: list[dict[str, Any]] = []
        for comp in catalog_seeds:
            comp_slug = comp.slug.strip().lower()
            comp_tags = {tag.strip().lower() for tag in comp.stack_tags if tag}
            if comp_slug in disabled_set:
                effective_state = ComponentEffectiveState.DISABLED_PROJECT.value
            elif comp_slug in enabled_set:
                effective_state = ComponentEffectiveState.ACTIVE_FORCED.value
            elif comp.status != ComponentStatus.COMPOSED:
                effective_state = ComponentEffectiveState.DISABLED_GLOBAL.value
            elif comp.scope == ScopeType.GLOBAL or bool(comp_tags & effective_tags_set):
                effective_state = ComponentEffectiveState.ACTIVE_AUTO.value
            else:
                effective_state = ComponentEffectiveState.UNMATCHED_STACK.value

            items.append(
                {
                    "slug": comp.slug,
                    "component_type": comp.component_type.value,
                    "scope": comp.scope.value,
                    "stack_tags": list(comp.stack_tags),
                    "title": comp.title,
                    "description": comp.description,
                    "status": comp.status.value,
                    "score": comp.score,
                    "version": comp.version,
                    "effective_state": effective_state,
                }
            )
        return items

    def _render_plugin_manifest(self, spec: PluginManifestSpec) -> RenderedFile:
        """Генерирует манифест плагина Antigravity 2.0 строго со списком из 6 разрешённых ключей."""
        payload: dict[str, Any] = {
            "name": spec.name,
            "displayName": spec.display_name,
            "version": self._plugin_version,
            "description": spec.description,
            "disabled": False,
            "suggestedPrompts": list(spec.suggested_prompts),
        }
        return RenderedFile(
            relative_path=spec.relative_path,
            content=json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            write_strategy=spec.write_strategy,
            description="Antigravity 2.0 plugin manifest",
        )

    def _render_rules_file(
        self,
        spec: RulesTargetSpec,
        rules_markdown: str,
    ) -> RenderedFile:
        """Форматирует скомпилированный Markdown правил под целевой формат агента (`plain_md`, `cursor_mdc`, `claude_scoped_md`)."""
        if spec.rules_format == RulesFormat.CURSOR_MDC:
            frontmatter = (
                "---\n"
                f"description: {spec.description or 'MCP-COOP Factory rules'}\n"
                f'globs: "{spec.globs}"\n'
                f"alwaysApply: {'true' if spec.always_apply else 'false'}\n"
                "---\n\n"
            )
            content = enforce_markdown_byte_budget(frontmatter + rules_markdown)
        elif spec.rules_format == RulesFormat.CLAUDE_SCOPED_MD and spec.scoped_paths:
            paths_yaml = "\n".join(f'  - "{path}"' for path in spec.scoped_paths)
            frontmatter = f"---\npaths:\n{paths_yaml}\n---\n\n"
            content = enforce_markdown_byte_budget(frontmatter + rules_markdown)
        else:
            content = rules_markdown

        return RenderedFile(
            relative_path=spec.relative_path,
            content=content,
            write_strategy=spec.write_strategy,
            description=spec.description or "Compiled Harness² engineering rules",
        )

    def _render_mcp_file(
        self,
        profile: AgentTargetProfile,
        mcp_url: str,
    ) -> RenderedFile:
        """Генерирует конфигурацию подключения к глобальному FastMCP-серверу в формате целевого агента."""
        spec: McpTargetSpec = profile.mcp
        if spec.config_format == McpConfigFormat.MCP_SERVERS_URL:
            payload: dict[str, Any] = {
                "mcpServers": {
                    spec.server_key: {
                        "url": mcp_url,
                    }
                }
            }
            content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        elif spec.config_format == McpConfigFormat.MCP_SERVERS_HTTP_TYPED:
            payload = {
                "mcpServers": {
                    spec.server_key: {
                        "type": "http",
                        "url": mcp_url,
                    }
                }
            }
            content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        elif spec.config_format == McpConfigFormat.CODEX_TOML:
            content = (
                "[features]\n"
                "codex_hooks = true\n\n"
                f"[mcp_servers.{spec.server_key}]\n"
                "enabled = true\n"
                f'url = "{mcp_url}"\n'
            )
        else:
            payload = {
                "$schema": OPENCODE_SCHEMA_URL,
                "instructions": list(profile.extra_instruction_paths),
                "mcp": {
                    spec.server_key: {
                        "type": "remote",
                        "url": mcp_url,
                        "enabled": True,
                    }
                },
            }
            content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

        return RenderedFile(
            relative_path=spec.relative_path,
            content=content,
            write_strategy=spec.write_strategy,
            description=f"MCP gateway configuration for {profile.display_name}",
        )

    def _build_hook_command(self, event: HookEventType, agent_type: AgentType) -> str:
        """Формирует CLI-команду проксирования жизненного хука в глобальное ядро фабрики."""
        return self._hook_command_template.format(
            event=event.value,
            agent=agent_type.value,
        )

    def _render_hooks_file(
        self,
        profile: AgentTargetProfile,
        events: Sequence[HookEventType],
    ) -> RenderedFile:
        """Транслирует канонические события `HookEventType` в диалект хуков целевого агента."""
        spec: HooksTargetSpec = profile.hooks
        if spec.dialect == HookDialect.ANTIGRAVITY_PROTOJSON:
            group_body: dict[str, Any] = {"enabled": True}
            for event in events:
                vendor_event = spec.event_mapping.get(event)
                if not vendor_event:
                    continue
                handler: dict[str, Any] = {
                    "type": "command",
                    "command": self._build_hook_command(event, profile.agent_type),
                    "timeout": spec.default_timeout_seconds,
                }
                if event in (HookEventType.PRE_TOOL_USE, HookEventType.POST_TOOL_USE):
                    group_body[vendor_event] = [
                        {
                            "matcher": PROTOJSON_WILDCARD_MATCHER,
                            "hooks": [handler],
                        }
                    ]
                else:
                    group_body[vendor_event] = [handler]
            content = (
                json.dumps({spec.hook_group_name: group_body}, indent=2, ensure_ascii=False)
                + "\n"
            )

        elif spec.dialect == HookDialect.CLAUDE_CODEX_V1:
            hooks_map = {}
            for event in events:
                vendor_event = spec.event_mapping.get(event)
                if not vendor_event:
                    continue
                entry = {
                    "matcher": GLOB_WILDCARD_MATCHER,
                    "hooks": [
                        {
                            "type": "command",
                            "command": self._build_hook_command(event, profile.agent_type),
                            "timeout": spec.default_timeout_seconds,
                        }
                    ],
                }
                hooks_map[vendor_event] = [entry]
            content = json.dumps({"hooks": hooks_map}, indent=2, ensure_ascii=False) + "\n"

        elif spec.dialect == HookDialect.CURSOR_V1:
            hooks_map = {}
            for event in events:
                vendor_event = spec.event_mapping.get(event)
                if not vendor_event:
                    continue
                hooks_map[vendor_event] = [
                    {
                        "command": self._build_hook_command(event, profile.agent_type),
                        "timeout": spec.default_timeout_seconds,
                    }
                ]
            payload = {
                "version": CURSOR_HOOKS_SCHEMA_VERSION,
                "hooks": hooks_map,
            }
            content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

        else:
            event_set = set(events)
            handler_lines: list[str] = [
                "// Auto-generated OpenCode lifecycle plugin for MCP-COOP Agentic Factory",
                "export const McpCoopFactoryPlugin = async ({ $ }: { $: any }) => ({",
            ]
            if HookEventType.PRE_TOOL_USE in event_set:
                pre_tool_cmd = self._build_hook_command(HookEventType.PRE_TOOL_USE, profile.agent_type)
                handler_lines.extend(
                    [
                        '  "tool.execute.before": async (input: unknown, output: unknown) => {',
                        f"    await $`{pre_tool_cmd}`;",
                        "  },",
                    ]
                )
            if HookEventType.POST_TOOL_USE in event_set:
                post_tool_cmd = self._build_hook_command(HookEventType.POST_TOOL_USE, profile.agent_type)
                handler_lines.extend(
                    [
                        '  "tool.execute.after": async (input: unknown, output: unknown) => {',
                        f"    await $`{post_tool_cmd}`;",
                        "  },",
                    ]
                )
            if HookEventType.STOP in event_set:
                stop_cmd = self._build_hook_command(HookEventType.STOP, profile.agent_type)
                handler_lines.extend(
                    [
                        '  "session.idle": async () => {',
                        f"    await $`{stop_cmd}`;",
                        "  },",
                    ]
                )
            handler_lines.extend(
                [
                    "});",
                    "export default McpCoopFactoryPlugin;",
                ]
            )
            content = "\n".join(handler_lines) + "\n"

        return RenderedFile(
            relative_path=spec.relative_path,
            content=content,
            write_strategy=spec.write_strategy,
            description=f"Lifecycle hooks adapter ({spec.dialect.value}) for {profile.display_name}",
        )

    def render_for_agent(
        self,
        bundle: CanonicalHarnessBundle,
        agent_type: AgentType | str,
        *,
        profile_overrides: Mapping[str, Any] | ProjectHarnessOverrides | None = None,
    ) -> RenderedHarnessArtifact:
        """Рендерит вендоронезависимый `CanonicalHarnessBundle` в набор файлов под конкретного агента."""
        agent_overrides = profile_overrides if isinstance(profile_overrides, Mapping) else None
        profile = get_agent_profile(agent_type, agent_overrides)
        files: list[RenderedFile] = []

        if profile.plugin_manifest is not None:
            files.append(self._render_plugin_manifest(profile.plugin_manifest))

        files.append(self._render_rules_file(profile.rules, bundle.rules_markdown))

        for skill in bundle.skills:
            skill_path = profile.skills.resolve_skill_path(skill.slug)
            files.append(
                RenderedFile(
                    relative_path=skill_path,
                    content=skill.render_skill_md(),
                    write_strategy=profile.skills.write_strategy,
                    description=f"Skill definition: {skill.title}",
                )
            )
            skill_dir = profile.skills.directory_template.format(slug=skill.slug).rstrip("/")
            for script_name, script_content in sorted(skill.scripts.items()):
                files.append(
                    RenderedFile(
                        relative_path=f"{skill_dir}/{profile.skills.scripts_subdir}/{script_name}",
                        content=script_content,
                        write_strategy=profile.skills.write_strategy,
                        description=f"Companion script {script_name} for skill {skill.slug}",
                    )
                )

        files.append(self._render_mcp_file(profile, bundle.mcp_url))
        files.append(self._render_hooks_file(profile, bundle.hook_events))

        return RenderedHarnessArtifact(
            agent_type=profile.agent_type,
            display_name=profile.display_name,
            stack_tags=bundle.stack_tags,
            stack_hash=bundle.stack_hash,
            source=bundle.source,
            guardrails=bundle.guardrails,
            files=tuple(files),
        )

    async def compile_for_project(
        self,
        *,
        agent_type: AgentType | str,
        stack_tags: Sequence[str] | None = None,
        profile_overrides: Mapping[str, Any] | ProjectHarnessOverrides | None = None,
    ) -> RenderedHarnessArtifact:
        """Полный цикл компиляции харнеса для регистрируемого или синхронизируемого проекта."""
        bundle = await self.build_canonical_bundle(
            stack_tags,
            profile_overrides=profile_overrides,
        )
        return self.render_for_agent(
            bundle,
            agent_type,
            profile_overrides=profile_overrides,
        )


__all__ = [
    "BUNDLE_SOURCE_POSTGRES",
    "BUNDLE_SOURCE_REDIS_CACHE",
    "BUNDLE_SOURCE_SEED_FALLBACK",
    "CACHE_TOKEN_DISABLED_COMPONENT_PREFIX",
    "CACHE_TOKEN_DISABLED_HOOK_PREFIX",
    "CACHE_TOKEN_ENABLED_COMPONENT_PREFIX",
    "CURSOR_HOOKS_SCHEMA_VERSION",
    "DEFAULT_HOOK_CLI_COMMAND_TEMPLATE",
    "DEFAULT_LOCALHOST_HOST",
    "DEFAULT_MCP_URL",
    "MAX_RULES_MARKDOWN_BYTES",
    "MCP_HTTP_PATH",
    "OPENCODE_SCHEMA_URL",
    "CanonicalHarnessBundle",
    "CompiledGuardrail",
    "CompiledSkillArtifact",
    "HarnessCompiler",
    "ProjectHarnessOverrides",
    "RenderedFile",
    "RenderedHarnessArtifact",
    "compute_bundle_cache_tags",
    "enforce_markdown_byte_budget",
    "normalize_stack_tags",
    "resolve_effective_stack_tags",
]
