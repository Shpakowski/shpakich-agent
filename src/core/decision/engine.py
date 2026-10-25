"""Оркестратор мульти-тирового контура принятия решений (`DecisionEngine`, Layer 2).

Координирует вертикальную цепочку уровней (`Tier 1 -> Tier 2 -> Tier N`) и горизонтальные
домены (`DecisionDomain`), управляя эскалацией, политиками отказоустойчивости (`FailMode`)
и 3-way гибридным поиском долгосрочного опыта в таблице `memories` (`pgvector + tsvector + pg_trgm`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

from src.core.db.postgres import PostgresDatabase
from src.core.db.redis_client import RedisSessionStore
from src.core.decision.models import (
    DecisionRequest,
    DecisionResolution,
    ProviderAttempt,
    TierStepResult,
)
from src.core.decision.providers import RuntimeProviderPool
from src.core.decision.tiers import (
    COMMAND_ARG_KEYS,
    FLASH_ROUTING_WORD_THRESHOLD,
    GREY_ZONE_PATTERNS,
    HARNESS_PROBE_MIN_CONTENT_CHARS,
    MS_PER_SECOND,
    PLAN_ITERATION_REGEX,
    PRO_ROUTING_KEYWORDS,
    PRO_ROUTING_WORD_THRESHOLD,
    SUBAGENT_FLASH_CHOICE,
    SUBAGENT_LITE_CHOICE,
    SUBAGENT_PRO_CHOICE,
    TIER_1_ID,
    TIER_1_PRIORITY,
    TIER_2_ID,
    TIER_2_PRIORITY,
    TIER_ANTIGRAVITY_PROBE_ID,
    TIER_ANTIGRAVITY_PROBE_PRIORITY,
    TIER_FALLBACK_ID,
    AntigravityProbeDecisionTier,
    DecisionTier,
    DomainEvaluationSpec,
    LocalDeterministicTier,
    SystemOneDecisionTier,
    build_default_domain_specs,
)
from src.core.enums import (
    DecisionDomain,
    EmbeddingTaskType,
    FailMode,
    ScopeType,
    TierStepStatus,
    VerdictAction,
)
from src.core.harness.compiler import HarnessCompiler
from src.core.services.antigravity import AntigravitySDKService
from src.core.services.embeddings import EmbeddingClient, format_pgvector

logger = logging.getLogger("mcp_coop.core.decision.engine")

SEARCH_MODE_HYBRID_VECTOR: str = "hybrid_vector"
SEARCH_MODE_LEXICAL_FALLBACK: str = "lexical_fallback"
DEFAULT_FALLBACK_CONFIDENCE: float = 0.7
MAX_EMBED_QUERY_CHARS: int = 1000
MAX_SEARCH_QUERY_CHARS: int = 500

HYBRID_VECTOR_MEMORY_SEARCH_SQL: str = """
WITH scored AS (
    SELECT
        id,
        title,
        error_symptom,
        solution,
        helpful_count,
        (embedding IS NOT NULL) AS has_vec,
        CASE
            WHEN embedding IS NOT NULL THEN GREATEST(0.0, 1.0 - (embedding <=> $4::vector))
            ELSE 0.0
        END AS vec_sim,
        ts_rank_cd(search_tsv, plainto_tsquery('simple', $1)) AS ts_r,
        GREATEST(similarity(error_symptom, $1), similarity(title, $1)) AS trgm_sim
    FROM memories
    WHERE (scope = $2 OR stack_tags && $3::text[])
)
SELECT
    id,
    title,
    error_symptom,
    solution,
    CASE
        WHEN has_vec THEN (
            vec_sim * 0.55
            + (ts_r / (ts_r + 1.0)) * 0.25
            + trgm_sim * 0.20
        )
        ELSE (
            (ts_r / (ts_r + 1.0)) * 0.60
            + trgm_sim * 0.40
        )
    END AS relevance
FROM scored
WHERE vec_sim > 0.35 OR ts_r > 0.0 OR trgm_sim > 0.15
ORDER BY relevance DESC, helpful_count DESC
LIMIT $5
"""

LEXICAL_MEMORY_SEARCH_SQL: str = """
SELECT
    id,
    title,
    error_symptom,
    solution,
    (
        ts_rank(search_tsv, plainto_tsquery('simple', $1)) * 0.6
        + similarity(error_symptom, $1) * 0.4
    ) AS relevance
FROM memories
WHERE (scope = $2 OR stack_tags && $3::text[])
  AND (
      search_tsv @@ plainto_tsquery('simple', $1)
      OR similarity(error_symptom, $1) > 0.15
  )
ORDER BY relevance DESC, helpful_count DESC
LIMIT $4
"""

INSERT_MEMORY_SQL: str = """
INSERT INTO memories (
    title,
    error_symptom,
    solution,
    scope,
    stack_tags,
    embedding,
    source_project_path
)
VALUES ($1, $2, $3, $4, $5::text[], $6::vector, $7)
RETURNING id, created_at
"""


class DecisionEngine:
    """Координатор цепочки ответственности (`Tier 1 -> Tier 2 -> Tier N`) и гибридного поиска опыта."""

    def __init__(
        self,
        db: PostgresDatabase | None = None,
        redis: RedisSessionStore | None = None,
        provider_pool: RuntimeProviderPool | None = None,
        compiler: HarnessCompiler | None = None,
        embedding_client: EmbeddingClient | None = None,
        antigravity_sdk: AntigravitySDKService | None = None,
        *,
        max_consecutive_failures: int = 2,
    ) -> None:
        self._db: PostgresDatabase | None = db
        self._redis: RedisSessionStore | None = redis
        self._compiler: HarnessCompiler | None = compiler
        self._embedding_client: EmbeddingClient | None = embedding_client
        self._antigravity_sdk: AntigravitySDKService | None = antigravity_sdk
        self._provider_pool: RuntimeProviderPool = provider_pool or RuntimeProviderPool(
            db=db,
            redis=redis,
        )
        self._domain_specs: dict[DecisionDomain, DomainEvaluationSpec] = (
            build_default_domain_specs()
        )
        self._tiers: list[DecisionTier] = []

        self.register_tier(
            LocalDeterministicTier(
                db=db,
                redis=redis,
                compiler=compiler,
                max_consecutive_failures=max_consecutive_failures,
            )
        )
        if self._antigravity_sdk is not None:
            self.register_tier(AntigravityProbeDecisionTier(self._antigravity_sdk))
        self.register_tier(
            SystemOneDecisionTier(
                provider_pool=self._provider_pool,
                domain_specs=self._domain_specs,
            )
        )

    @property
    def provider_pool(self) -> RuntimeProviderPool:
        """Возвращает активный рантайм-пул провайдеров Tier 2."""
        return self._provider_pool

    @property
    def embedding_client(self) -> EmbeddingClient | None:
        """Возвращает опциональный HTTP-клиент векторных эмбеддингов."""
        return self._embedding_client

    @property
    def antigravity_sdk(self) -> AntigravitySDKService | None:
        """Возвращает опциональный сервис микро-агентов второго уровня `google-antigravity` SDK."""
        return self._antigravity_sdk

    def register_tier(self, tier: DecisionTier) -> None:
        """Регистрирует или заменяет вычислительный тир в цепочке ответственности с сортировкой по `priority`."""
        self._tiers = [t for t in self._tiers if t.tier_id != tier.tier_id]
        self._tiers.append(tier)
        self._tiers.sort(key=lambda item: item.priority)

    def register_domain_spec(self, spec: DomainEvaluationSpec) -> None:
        """Регистрирует или обновляет декларативную спецификацию домена `DecisionDomain` (`OCP`)."""
        self._domain_specs[spec.domain] = spec

    async def evaluate(self, request: DecisionRequest) -> DecisionResolution:
        """Проводит запрос по всем зарегистрированным тирам и формирует итоговую резолюцию."""
        started = time.perf_counter()
        context: dict[str, Any] = {}
        warnings: list[str] = []
        provider_trace: list[ProviderAttempt] = []

        domain_spec = self._domain_specs.get(request.domain)
        active_fail_mode = (
            domain_spec.default_fail_mode
            if domain_spec is not None
            else request.fail_mode
        )
        last_step: TierStepResult | None = None

        for tier in self._tiers:
            step = await tier.evaluate(request, context)
            warnings.extend(step.warnings)
            provider_trace.extend(step.provider_trace)
            if step.fail_mode_override is not None:
                active_fail_mode = step.fail_mode_override

            if step.status == TierStepStatus.TERMINAL and step.action is not None:
                total_ms = round((time.perf_counter() - started) * MS_PER_SECOND, 2)
                return DecisionResolution(
                    domain=request.domain,
                    action=step.action,
                    decided_by_tier=step.tier_id,
                    confidence=step.confidence,
                    score=step.score,
                    choice=step.choice,
                    reason=step.reason,
                    overwrite_args=step.overwrite_args,
                    inject_system_message=step.inject_system_message,
                    inject_ephemeral_message=step.inject_ephemeral_message,
                    answers=step.answers,
                    warnings=warnings,
                    provider_trace=provider_trace,
                    total_latency_ms=total_ms,
                )

            if step.status == TierStepStatus.ESCALATE:
                last_step = step

        total_ms = round((time.perf_counter() - started) * MS_PER_SECOND, 2)
        if active_fail_mode == FailMode.FAIL_CLOSED_DENY:
            fallback_action = VerdictAction.DENY
            fallback_reason = (
                "Fail-closed policy (DENY): Tier 2 Decision API providers are unavailable or out of quota."
            )
        elif active_fail_mode == FailMode.FAIL_CLOSED_ASK:
            fallback_action = VerdictAction.ASK
            fallback_reason = (
                "Grey-zone command requires manual user confirmation because Tier 2 Decision API "
                "providers are unavailable or out of token quota."
            )
        else:
            fallback_action = (
                last_step.action
                if (last_step is not None and last_step.action is not None)
                else VerdictAction.ALLOW
            )
            fallback_reason = (
                last_step.reason
                if (last_step is not None and last_step.reason)
                else "Resolved by Tier 3 local heuristic fallback."
            )

        return DecisionResolution(
            domain=request.domain,
            action=fallback_action,
            decided_by_tier=TIER_FALLBACK_ID,
            confidence=(
                last_step.confidence
                if last_step is not None
                else DEFAULT_FALLBACK_CONFIDENCE
            ),
            score=last_step.score if last_step is not None else None,
            choice=last_step.choice if last_step is not None else None,
            reason=fallback_reason,
            warnings=warnings,
            provider_trace=provider_trace,
            total_latency_ms=total_ms,
        )

    async def search_memories(
        self,
        error_query: str,
        stack_tags: Sequence[str],
        *,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Ищет релевантные решения ошибок в `memories` через 3-way гибридный поиск (`pgvector + tsvector + pg_trgm`)."""
        clean_query = error_query.strip()
        if not clean_query or self._db is None or not self._db.is_connected:
            return []

        tags = [t.strip().lower() for t in stack_tags if t.strip()] or [
            ScopeType.GLOBAL.value
        ]
        safe_limit = max(1, limit)

        vec_literal: str | None = None
        if self._embedding_client is not None:
            query_vec = await self._embedding_client.embed_text(
                clean_query[:MAX_EMBED_QUERY_CHARS],
                task_type=EmbeddingTaskType.RETRIEVAL_QUERY,
            )
            vec_literal = format_pgvector(query_vec)

        if vec_literal is not None:
            try:
                rows = await self._db.pool.fetch(
                    HYBRID_VECTOR_MEMORY_SEARCH_SQL,
                    clean_query[:MAX_SEARCH_QUERY_CHARS],
                    ScopeType.GLOBAL.value,
                    tags,
                    vec_literal,
                    safe_limit,
                )
                return self._format_memory_rows(rows, search_mode=SEARCH_MODE_HYBRID_VECTOR)
            except Exception as exc:  # noqa: BLE001
                logger.debug("3-way vector memory search failed, falling back to lexical: %s", exc)

        try:
            rows = await self._db.pool.fetch(
                LEXICAL_MEMORY_SEARCH_SQL,
                clean_query[:MAX_SEARCH_QUERY_CHARS],
                ScopeType.GLOBAL.value,
                tags,
                safe_limit,
            )
            return self._format_memory_rows(rows, search_mode=SEARCH_MODE_LEXICAL_FALLBACK)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Lexical memory search failed: %s", exc)
            return []

    @staticmethod
    def _format_memory_rows(
        rows: Sequence[Mapping[str, Any]],
        *,
        search_mode: str,
    ) -> list[dict[str, Any]]:
        """Преобразует строки выборки PostgreSQL в унифицированный словарь результатов памяти."""
        results: list[dict[str, Any]] = []
        for r in rows:
            item: dict[str, Any] = {
                "title": str(r["title"]),
                "error_symptom": str(r["error_symptom"]),
                "solution": str(r["solution"]),
                "relevance": round(float(r["relevance"] or 0.0), 3),
                "search_mode": search_mode,
            }
            if "id" in r and r["id"] is not None:
                item["id"] = str(r["id"])
            results.append(item)
        return results

    async def store_memory(
        self,
        *,
        title: str,
        error_symptom: str,
        solution: str,
        stack_tags: Sequence[str] | None = None,
        scope: str = ScopeType.GLOBAL.value,
        source_project_path: str | None = None,
    ) -> dict[str, Any]:
        """Индексирует новое решение ошибки в `memories` с автоматической генерацией вектора `EmbeddingGemma 2`."""
        clean_title = title.strip()
        clean_symptom = error_symptom.strip()
        clean_solution = solution.strip()
        tags = [t.strip().lower() for t in (stack_tags or []) if t.strip()] or [
            ScopeType.GLOBAL.value
        ]

        vec_literal: str | None = None
        if self._embedding_client is not None:
            doc_vec = await self._embedding_client.embed_text(
                f"{clean_symptom}\n{clean_solution}",
                task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT,
                title=clean_title,
            )
            vec_literal = format_pgvector(doc_vec)

        if self._db is None or not self._db.is_connected:
            return {
                "id": None,
                "stored": False,
                "title": clean_title,
                "scope": scope,
                "stack_tags": tags,
                "embedded": vec_literal is not None,
            }

        row = await self._db.pool.fetchrow(
            INSERT_MEMORY_SQL,
            clean_title,
            clean_symptom,
            clean_solution,
            scope,
            tags,
            vec_literal,
            source_project_path,
        )
        return {
            "id": str(row["id"]) if row and row.get("id") is not None else None,
            "stored": True,
            "title": clean_title,
            "scope": scope,
            "stack_tags": tags,
            "embedded": vec_literal is not None,
        }


__all__ = [
    "COMMAND_ARG_KEYS",
    "DEFAULT_FALLBACK_CONFIDENCE",
    "FLASH_ROUTING_WORD_THRESHOLD",
    "GREY_ZONE_PATTERNS",
    "HARNESS_PROBE_MIN_CONTENT_CHARS",
    "HYBRID_VECTOR_MEMORY_SEARCH_SQL",
    "INSERT_MEMORY_SQL",
    "LEXICAL_MEMORY_SEARCH_SQL",
    "MAX_EMBED_QUERY_CHARS",
    "MAX_SEARCH_QUERY_CHARS",
    "MS_PER_SECOND",
    "PLAN_ITERATION_REGEX",
    "PRO_ROUTING_KEYWORDS",
    "PRO_ROUTING_WORD_THRESHOLD",
    "SEARCH_MODE_HYBRID_VECTOR",
    "SEARCH_MODE_LEXICAL_FALLBACK",
    "SUBAGENT_FLASH_CHOICE",
    "SUBAGENT_LITE_CHOICE",
    "SUBAGENT_PRO_CHOICE",
    "TIER_1_ID",
    "TIER_1_PRIORITY",
    "TIER_2_ID",
    "TIER_2_PRIORITY",
    "TIER_ANTIGRAVITY_PROBE_ID",
    "TIER_ANTIGRAVITY_PROBE_PRIORITY",
    "TIER_FALLBACK_ID",
    "AntigravityProbeDecisionTier",
    "DecisionEngine",
    "DecisionTier",
    "DomainEvaluationSpec",
    "LocalDeterministicTier",
    "SystemOneDecisionTier",
    "build_default_domain_specs",
]
