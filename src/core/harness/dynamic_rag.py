"""Двухступенчатый динамический RAG-отбор правил и скиллов `Harness²` на хуке `PreInvocation` (SPEC-002).

Реализует Progressive Disclosure: в `PreInvocation` вычисляет гибридную векторно-лексическую
близость (`pgvector` + `tsvector` + `pg_trgm`) к запросу пользователя и формирует `ephemeralMessage`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import (
    ComponentStatus,
    ComponentType,
    EmbeddingTaskType,
    ScopeType,
    ScriptExecutionMode,
)
from src.core.harness.seed import (
    HARNESS_ORCHESTRATOR_SLUG,
    HarnessComponentSeed,
    load_prompts_config,
    resolve_seed_components_with_overrides,
)
from src.core.services.embeddings import EmbeddingClient, format_pgvector

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase
    from src.core.harness.compiler import ProjectHarnessOverrides

logger = logging.getLogger("mcp_coop.core.harness.dynamic_rag")

_DYNAMIC_RAG_PROMPTS: dict[str, Any] = dict(load_prompts_config().get("dynamic_rag") or {})
DYNAMIC_RULES_HEADER: str = str(
    _DYNAMIC_RAG_PROMPTS.get("rules_header") or "[Dynamic Harness Rules]:"
)
DYNAMIC_RULE_ITEM_TEMPLATE: str = str(
    _DYNAMIC_RAG_PROMPTS.get("rule_item_template") or "#### {title} (`{slug}`)\n{content}"
)
SKILL_BLUEPRINT_TEMPLATE: str = str(
    _DYNAMIC_RAG_PROMPTS.get("skill_blueprint_template")
    or "[Active Skill Blueprint: {slug}]:\n{content}"
)
SKILL_HINT_TEMPLATE: str = str(
    _DYNAMIC_RAG_PROMPTS.get("skill_hint_template")
    or "[Available Skill Hint]: {slug} — {description}"
)

DEFAULT_MAX_DYNAMIC_RULES: int = 3
DEFAULT_MAX_DYNAMIC_SKILLS: int = 2
MIN_DYNAMIC_RULE_RELEVANCE: float = 0.25
MIN_SKILL_BLUEPRINT_RELEVANCE: float = 0.45
MIN_SKILL_INDEX_RELEVANCE: float = 0.22
DYNAMIC_CANDIDATE_FETCH_LIMIT: int = 16

RAG_SOURCE_POSTGRES: str = "postgres"
RAG_SOURCE_SEED_FALLBACK: str = "seed_fallback"

DYNAMIC_RULE_TYPES: tuple[str, ...] = (
    ComponentType.SYSTEM_PROMPT.value,
    ComponentType.TOOL_GUIDANCE.value,
    ComponentType.GUARDRAIL.value,
)
DYNAMIC_QUERY_TYPES: tuple[str, ...] = (*DYNAMIC_RULE_TYPES, ComponentType.SKILL.value)

TOKEN_PATTERN: re.Pattern[str] = re.compile(r"[a-z0-9_./-]+")
STOP_TOKENS: frozenset[str] = frozenset(
    {"the", "and", "for", "with", "from", "that", "this", "into", "please", "how", "what"}
)

SELECT_DYNAMIC_COMPONENTS_VECTOR_SQL: str = """
SELECT slug, component_type, title, description, content, metadata, score,
    (
        CASE WHEN embedding IS NOT NULL THEN (1.0 - (embedding <=> $7::vector)) * 0.65 ELSE 0.0 END
        + ts_rank_cd(search_tsv, websearch_to_tsquery('simple', $8)) * 0.20
        + word_similarity($8, slug || ' ' || title || ' ' || description) * 0.15
    ) AS relevance
FROM harness_components
WHERE component_type = ANY($1::text[])
  AND status = $2
  AND NOT (slug = ANY($4::text[]))
  AND NOT (slug = ANY($5::text[]))
  AND (scope = $3 OR stack_tags && $6::text[])
  AND COALESCE(metadata->>'execution_mode', 'always') = 'semantic'
ORDER BY relevance DESC, score DESC
LIMIT $9
"""

SELECT_DYNAMIC_COMPONENTS_LEXICAL_SQL: str = """
SELECT slug, component_type, title, description, content, metadata, score,
    (
        ts_rank_cd(search_tsv, websearch_to_tsquery('simple', $7)) * 0.60
        + word_similarity($7, slug || ' ' || title || ' ' || description) * 0.40
    ) AS relevance
FROM harness_components
WHERE component_type = ANY($1::text[])
  AND status = $2
  AND NOT (slug = ANY($4::text[]))
  AND NOT (slug = ANY($5::text[]))
  AND (scope = $3 OR stack_tags && $6::text[])
  AND COALESCE(metadata->>'execution_mode', 'always') = 'semantic'
ORDER BY relevance DESC, score DESC
LIMIT $8
"""


class DynamicMatchedRule(BaseModel):
    """Отобранное по семантической близости доменное правило (`system_prompt`, `tool_guidance`, `guardrail`)."""

    model_config = ConfigDict(frozen=True)

    slug: str = Field(..., min_length=1)
    component_type: ComponentType
    title: str = Field(..., min_length=1)
    description: str = ""
    content: str = Field(..., min_length=1)
    relevance: float = Field(..., ge=0.0)


class DynamicMatchedSkill(BaseModel):
    """Отобранный по семантической близости прикладной навык (`skill`) с флагом глубины раскрытия."""

    model_config = ConfigDict(frozen=True)

    slug: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    relevance: float = Field(..., ge=0.0)
    is_full_blueprint: bool = False


class DynamicPromptRAGResult(BaseModel):
    """Результат двухступенчатого Progressive Disclosure отбора знаний под активный запрос пользователя."""

    model_config = ConfigDict(frozen=True)

    rules: tuple[DynamicMatchedRule, ...] = ()
    skills: tuple[DynamicMatchedSkill, ...] = ()
    source: str = RAG_SOURCE_SEED_FALLBACK

    def format_ephemeral_hint(self) -> str | None:
        """Форматирует отобранные правила и навыки в блок `ephemeralMessage` для `PreInvocation`."""
        if not self.rules and not self.skills:
            return None

        sections: list[str] = []
        if self.rules:
            rule_items = [
                DYNAMIC_RULE_ITEM_TEMPLATE.format(
                    title=r.title,
                    slug=r.slug,
                    content=r.content.strip(),
                )
                for r in self.rules
            ]
            sections.append(f"{DYNAMIC_RULES_HEADER}\n" + "\n\n".join(rule_items))

        for skill in self.skills:
            if skill.is_full_blueprint:
                sections.append(
                    SKILL_BLUEPRINT_TEMPLATE.format(
                        slug=skill.slug,
                        content=skill.content.strip(),
                    )
                )
            else:
                clean_desc = " ".join(skill.description.strip().splitlines())
                sections.append(
                    SKILL_HINT_TEMPLATE.format(
                        slug=skill.slug,
                        description=clean_desc,
                    )
                )

        return "\n\n".join(sections) if sections else None


def _tokenize_text(text: str) -> list[str]:
    """Разбивает строку на нормализованные поисковые токены длиной от 2 символов без стоп-слов."""
    return [
        tok.strip(".-/")
        for tok in TOKEN_PATTERN.findall(text.lower())
        if len(tok.strip(".-/")) >= 2 and tok.strip(".-/") not in STOP_TOKENS
    ]


def _compute_seed_relevance(prompt: str, seed: HarnessComponentSeed) -> float:
    """Вычисляет детерминированную лексическую близость запроса к seed-компоненту в оффлайн-режиме."""
    query_tokens = _tokenize_text(prompt)
    if not query_tokens:
        return 0.0

    prompt_lower = prompt.lower()
    header_tokens = set(_tokenize_text(f"{seed.slug} {seed.title} {seed.description}"))
    body_tokens = set(_tokenize_text(seed.content))

    header_hits = sum(
        1
        for tok in query_tokens
        if tok in header_tokens
        or any((tok in h or h in tok) for h in header_tokens if len(h) >= 4 and len(tok) >= 4)
    )
    body_hits = sum(1 for tok in query_tokens if tok in body_tokens)

    header_ratio = header_hits / len(query_tokens)
    body_ratio = body_hits / len(query_tokens)
    slug_bonus = 0.50 if seed.slug.lower() in prompt_lower else 0.0
    hit_bonus = 0.06 * min(header_hits, 3)
    return round(min(1.0, (header_ratio * 0.65) + (body_ratio * 0.35) + hit_bonus + slug_bonus), 4)


def _classify_candidates(
    candidates: Sequence[tuple[str, ComponentType, str, str, str, float]],
    *,
    max_rules: int,
    max_skills: int,
    source: str,
) -> DynamicPromptRAGResult:
    """Распределяет кандидатов по порогам Progressive Disclosure (Top-K правил и 2 уровня скиллов)."""
    matched_rules: list[DynamicMatchedRule] = []
    matched_skills: list[DynamicMatchedSkill] = []
    seen_slugs: set[str] = set()

    for slug, comp_type, title, description, content, relevance in candidates:
        if not content or not content.strip():
            continue
        norm_slug = slug.strip().lower()
        if norm_slug in seen_slugs or norm_slug == HARNESS_ORCHESTRATOR_SLUG:
            continue

        if comp_type.value in DYNAMIC_RULE_TYPES:
            if len(matched_rules) < max_rules and relevance >= MIN_DYNAMIC_RULE_RELEVANCE:
                matched_rules.append(
                    DynamicMatchedRule(
                        slug=slug, component_type=comp_type, title=title,
                        description=description, content=content, relevance=round(relevance, 4),
                    )
                )
                seen_slugs.add(norm_slug)
        elif comp_type == ComponentType.SKILL and len(matched_skills) < max_skills and relevance >= MIN_SKILL_INDEX_RELEVANCE:
            matched_skills.append(
                DynamicMatchedSkill(
                    slug=slug, title=title, description=description or title, content=content,
                    relevance=round(relevance, 4), is_full_blueprint=relevance >= MIN_SKILL_BLUEPRINT_RELEVANCE,
                )
            )
            seen_slugs.add(norm_slug)

    return DynamicPromptRAGResult(rules=tuple(matched_rules), skills=tuple(matched_skills), source=source)


async def select_dynamic_components_for_prompt(
    prompt: str,
    stack_tags: Sequence[str],
    profile_overrides: Mapping[str, Any] | ProjectHarnessOverrides | None = None,
    *,
    db: PostgresDatabase | None = None,
    embedding_client: EmbeddingClient | None = None,
    max_rules: int = DEFAULT_MAX_DYNAMIC_RULES,
    max_skills: int = DEFAULT_MAX_DYNAMIC_SKILLS,
) -> DynamicPromptRAGResult:
    """Отбирает семантические правила и скиллы (`execution_mode = 'semantic'`) под запрос пользователя."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        return DynamicPromptRAGResult()

    from src.core.harness.compiler import ProjectHarnessOverrides, resolve_effective_stack_tags

    overrides = (
        profile_overrides
        if isinstance(profile_overrides, ProjectHarnessOverrides)
        else ProjectHarnessOverrides.from_mapping(profile_overrides)
    )
    effective_tags = list(resolve_effective_stack_tags(stack_tags, overrides))
    enabled_list = list(overrides.enabled_components)
    disabled_list = list(overrides.disabled_components)
    fetch_limit = max(DYNAMIC_CANDIDATE_FETCH_LIMIT, (max_rules + max_skills) * 3)

    if db is not None and db.is_connected:
        try:
            vec = (
                await embedding_client.embed_text(clean_prompt, task_type=EmbeddingTaskType.RETRIEVAL_QUERY)
                if embedding_client is not None
                else None
            )
            vector_literal = format_pgvector(vec)
            common_args = (
                list(DYNAMIC_QUERY_TYPES),
                ComponentStatus.COMPOSED.value,
                ScopeType.GLOBAL.value,
                enabled_list,
                disabled_list,
                effective_tags,
            )
            rows = (
                await db.pool.fetch(SELECT_DYNAMIC_COMPONENTS_VECTOR_SQL, *common_args, vector_literal, clean_prompt, fetch_limit)
                if vector_literal is not None
                else await db.pool.fetch(SELECT_DYNAMIC_COMPONENTS_LEXICAL_SQL, *common_args, clean_prompt, fetch_limit)
            )
            if rows:
                pg_candidates = [
                    (
                        str(r["slug"]),
                        ComponentType(str(r["component_type"])),
                        str(r["title"]),
                        str(r["description"] or r["title"]),
                        str(r["content"]),
                        float(r["relevance"] or 0.0),
                    )
                    for r in rows
                ]
                return _classify_candidates(
                    pg_candidates,
                    max_rules=max(0, max_rules),
                    max_skills=max(0, max_skills),
                    source=RAG_SOURCE_POSTGRES,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dynamic prompt RAG PostgreSQL query failed (%s); using seed fallback", exc)

    enabled_set = {slug.strip().lower() for slug in enabled_list}
    seeds = resolve_seed_components_with_overrides(effective_tags, enabled_components=(), disabled_components=disabled_list)
    seed_candidates: list[tuple[str, ComponentType, str, str, str, float]] = []
    for seed in seeds:
        if (
            not seed.has_content
            or seed.slug.strip().lower() in enabled_set
            or seed.component_type.value not in DYNAMIC_QUERY_TYPES
        ):
            continue
        raw_meta = seed.metadata if isinstance(seed.metadata, Mapping) else json.loads(str(seed.metadata or "{}"))
        if str(raw_meta.get("execution_mode", ScriptExecutionMode.ALWAYS.value)).strip().lower() != ScriptExecutionMode.SEMANTIC.value:
            continue
        seed_candidates.append(
            (seed.slug, seed.component_type, seed.title, seed.description, seed.content, _compute_seed_relevance(clean_prompt, seed))
        )

    seed_candidates.sort(key=lambda item: (-item[5], item[0]))
    return _classify_candidates(
        seed_candidates,
        max_rules=max(0, max_rules),
        max_skills=max(0, max_skills),
        source=RAG_SOURCE_SEED_FALLBACK,
    )


__all__ = [
    "DEFAULT_MAX_DYNAMIC_RULES",
    "DEFAULT_MAX_DYNAMIC_SKILLS",
    "DYNAMIC_QUERY_TYPES",
    "DYNAMIC_RULE_TYPES",
    "MIN_DYNAMIC_RULE_RELEVANCE",
    "MIN_SKILL_BLUEPRINT_RELEVANCE",
    "MIN_SKILL_INDEX_RELEVANCE",
    "RAG_SOURCE_POSTGRES",
    "RAG_SOURCE_SEED_FALLBACK",
    "DynamicMatchedRule",
    "DynamicMatchedSkill",
    "DynamicPromptRAGResult",
    "select_dynamic_components_for_prompt",
]
