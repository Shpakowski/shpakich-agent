"""Расширяемый мульти-тировый контур принятия решений (`DecisionEngine`, Layer 2).

Реализует двухкоординатную матрицу оценки:
1. Вертикаль (`Tier 1 -> Tier 2 -> Tier N`): цепочка ответственности от локальных детерминированных
   барьеров и кэша Redis (<2 мс) к рантайм-пулу `RuntimeProviderPool` (`OpenAI Decisions API / CLEF / Chat Schema`)
   и локальному эвристическому арбитру (Graceful Degradation при исчерпании квоты или оффлайне).
2. Горизонталь (`DecisionDomain`): 5 функциональных доменов (`TOOL_SAFETY`, `PLAN_VALIDATION`,
   `SUBAGENT_ROUTING`, `CONTEXT_PRUNING`, `HARNESS_PROBE`), открытых к расширению по принципу `OCP`
   через `register_domain_spec()` и `register_tier()`.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from src.core.db.postgres import PostgresDatabase
from src.core.db.redis_client import RedisSessionStore
from src.core.decision.models import (
    DecisionQuestion,
    DecisionRequest,
    DecisionResolution,
    NormalizedAnswer,
    ProviderAttempt,
    TierStepResult,
)
from src.core.decision.providers import RuntimeProviderPool
from src.core.enums import (
    DecisionDomain,
    DecisionQuestionType,
    FailMode,
    GuardrailAction,
    ScopeType,
    TierStepStatus,
    VerdictAction,
)
from src.core.harness.compiler import HarnessCompiler

logger = logging.getLogger("mcp_coop.core.decision.engine")

TIER_1_ID: str = "tier1_local_deterministic"
TIER_2_ID: str = "tier2_system_one_pool"
TIER_FALLBACK_ID: str = "tier3_heuristic_fallback"

TIER_1_PRIORITY: int = 10
TIER_2_PRIORITY: int = 20
MS_PER_SECOND: float = 1000.0

COMMAND_ARG_KEYS: tuple[str, ...] = ("CommandLine", "command", "cmd")

GREY_ZONE_PATTERNS: tuple[str, ...] = (
    r"\bcurl\b.*\|\s*(ba)?sh\b",
    r"\bwget\b.*\|\s*(ba)?sh\b",
    r"\bgit\s+push\b.*--force-with-lease\b",
    r"\bchmod\s+-R\s+777\b",
    r"\bdocker\s+system\s+prune\b",
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\b",
)

PLAN_ITERATION_REGEX: str = r"(?i)(iteration|итерация|step|шаг|этап)"

SUBAGENT_LITE_CHOICE: str = "subagent_flash_lite"
SUBAGENT_FLASH_CHOICE: str = "subagent_flash"
SUBAGENT_PRO_CHOICE: str = "subagent_pro"

PRO_ROUTING_KEYWORDS: tuple[str, ...] = (
    "architect",
    "security",
    "refactor",
    "migration",
    "distributed",
    "concurren",
    "design",
    "multi-module",
    "архитектур",
    "безопасност",
    "рефактор",
)


@runtime_checkable
class DecisionTier(Protocol):
    """Протокол подключаемого уровня (Tier) в цепочке ответственности `DecisionEngine` (`OCP`)."""

    @property
    def tier_id(self) -> str:
        """Уникальный идентификатор тира для трассировки вердикта (`decided_by_tier`)."""
        ...

    @property
    def priority(self) -> int:
        """Порядок вызова тира в цепочке (меньшее число = более ранний вызов)."""
        ...

    async def evaluate(
        self,
        request: DecisionRequest,
        context: dict[str, Any],
    ) -> TierStepResult:
        """Оценивает запрос и возвращает `TERMINAL`, `ESCALATE` или `ABSTAIN`."""
        ...


class DomainEvaluationSpec(BaseModel):
    """Декларативная спецификация функционального домена `DecisionDomain` и его микро-вопросов Tier 2."""

    model_config = ConfigDict(frozen=True)

    domain: DecisionDomain
    default_fail_mode: FailMode
    tier2_questions: list[DecisionQuestion] = Field(default_factory=list)


def build_default_domain_specs() -> dict[DecisionDomain, DomainEvaluationSpec]:
    """Создаёт стандартный реестр спецификаций для 5 доменов матрицы принятия решений."""
    return {
        DecisionDomain.TOOL_SAFETY: DomainEvaluationSpec(
            domain=DecisionDomain.TOOL_SAFETY,
            default_fail_mode=FailMode.FAIL_CLOSED_ASK,
            tier2_questions=[
                DecisionQuestion(
                    name="is_safe_to_execute",
                    question_type=DecisionQuestionType.PREDICATE,
                    instructions=(
                        "Is this tool invocation safe to execute without data loss, "
                        "privilege escalation, or destructive side effects?"
                    ),
                    predicate_threshold=0.6,
                ),
                DecisionQuestion(
                    name="recommended_action",
                    question_type=DecisionQuestionType.CHOICE,
                    instructions="Choose the safest execution policy for this command.",
                    choices=[
                        VerdictAction.ALLOW.value,
                        VerdictAction.ASK.value,
                        VerdictAction.DENY.value,
                    ],
                ),
                DecisionQuestion(
                    name="risk_score",
                    question_type=DecisionQuestionType.SCORE,
                    instructions="Estimate operational risk from 0.0 (harmless) to 1.0 (destructive).",
                    levels=["low", "medium", "high", "critical"],
                ),
            ],
        ),
        DecisionDomain.PLAN_VALIDATION: DomainEvaluationSpec(
            domain=DecisionDomain.PLAN_VALIDATION,
            default_fail_mode=FailMode.FAIL_OPEN_ALLOW,
            tier2_questions=[
                DecisionQuestion(
                    name="is_plan_compliant",
                    question_type=DecisionQuestionType.PREDICATE,
                    instructions=(
                        "Does the implementation plan follow iterative Learning-First steps "
                        "and include .context/ graph synchronization?"
                    ),
                    predicate_threshold=0.5,
                ),
                DecisionQuestion(
                    name="plan_quality_score",
                    question_type=DecisionQuestionType.SCORE,
                    instructions="Score the engineering clarity and modularity of the plan from 0.0 to 1.0.",
                    levels=["poor", "acceptable", "good", "excellent"],
                ),
            ],
        ),
        DecisionDomain.SUBAGENT_ROUTING: DomainEvaluationSpec(
            domain=DecisionDomain.SUBAGENT_ROUTING,
            default_fail_mode=FailMode.FAIL_OPEN_ALLOW,
            tier2_questions=[
                DecisionQuestion(
                    name="target_tier",
                    question_type=DecisionQuestionType.CHOICE,
                    instructions="Select the optimal subagent model tier for the delegated task.",
                    choices=[
                        SUBAGENT_LITE_CHOICE,
                        SUBAGENT_FLASH_CHOICE,
                        SUBAGENT_PRO_CHOICE,
                    ],
                ),
                DecisionQuestion(
                    name="complexity_score",
                    question_type=DecisionQuestionType.SCORE,
                    instructions="Estimate task complexity from 0.0 (trivial lookup) to 1.0 (deep architecture).",
                    levels=["trivial", "moderate", "complex"],
                ),
            ],
        ),
        DecisionDomain.CONTEXT_PRUNING: DomainEvaluationSpec(
            domain=DecisionDomain.CONTEXT_PRUNING,
            default_fail_mode=FailMode.FAIL_OPEN_ALLOW,
            tier2_questions=[
                DecisionQuestion(
                    name="keep_in_working_memory",
                    question_type=DecisionQuestionType.PREDICATE,
                    instructions="Should this context item be retained in active session memory after compaction?",
                    predicate_threshold=0.5,
                ),
                DecisionQuestion(
                    name="relevance_score",
                    question_type=DecisionQuestionType.SCORE,
                    instructions="Score relevance of the context item to current task from 0.0 to 1.0.",
                    levels=["irrelevant", "useful", "critical"],
                ),
            ],
        ),
        DecisionDomain.HARNESS_PROBE: DomainEvaluationSpec(
            domain=DecisionDomain.HARNESS_PROBE,
            default_fail_mode=FailMode.FAIL_OPEN_ALLOW,
            tier2_questions=[
                DecisionQuestion(
                    name="promote_to_composed",
                    question_type=DecisionQuestionType.PREDICATE,
                    instructions="Is this proposed harness rule or memory general, accurate, and safe to promote?",
                    predicate_threshold=0.7,
                ),
                DecisionQuestion(
                    name="utility_score",
                    question_type=DecisionQuestionType.SCORE,
                    instructions="Score long-term engineering utility of the candidate component from 0.0 to 1.0.",
                    levels=["low", "medium", "high"],
                ),
            ],
        ),
    }



class LocalDeterministicTier:
    """Первый контур (`Tier 1`, <2 мс): локальные детерминированные правила, анти-луп и кэш Redis."""

    def __init__(
        self,
        db: PostgresDatabase | None = None,
        redis: RedisSessionStore | None = None,
        compiler: HarnessCompiler | None = None,
        *,
        max_consecutive_failures: int = 2,
    ) -> None:
        self._db: PostgresDatabase | None = db
        self._redis: RedisSessionStore | None = redis
        self._compiler: HarnessCompiler = compiler or HarnessCompiler(db=db, redis=redis)
        self._max_consecutive_failures: int = max(1, max_consecutive_failures)

    @property
    def tier_id(self) -> str:
        """Возвращает идентификатор детерминированного тира."""
        return TIER_1_ID

    @property
    def priority(self) -> int:
        """Возвращает наивысший приоритет (10) в цепочке `DecisionEngine`."""
        return TIER_1_PRIORITY

    async def evaluate(
        self,
        request: DecisionRequest,
        context: dict[str, Any],
    ) -> TierStepResult:
        """Выполняет быструю локальную проверку запроса в зависимости от `request.domain`."""
        started = time.perf_counter()
        if request.domain == DecisionDomain.TOOL_SAFETY:
            result = await self._evaluate_tool_safety(request)
        elif request.domain == DecisionDomain.PLAN_VALIDATION:
            result = self._evaluate_plan_validation(request)
        elif request.domain == DecisionDomain.SUBAGENT_ROUTING:
            result = self._evaluate_subagent_routing(request)
        elif request.domain == DecisionDomain.CONTEXT_PRUNING:
            result = self._evaluate_context_pruning(request)
        else:
            result = self._evaluate_harness_probe(request)

        elapsed_ms = round((time.perf_counter() - started) * MS_PER_SECOND, 2)
        return result.model_copy(update={"latency_ms": elapsed_ms})

    async def _evaluate_tool_safety(
        self,
        request: DecisionRequest,
    ) -> TierStepResult:
        """Проверяет безопасность вызова инструмента по анти-лупу, LangCache и регулярным выражениям."""
        tool_name = str(request.payload.get("tool_name", "")).strip()
        raw_args = request.payload.get("tool_args")
        tool_args: dict[str, Any] = dict(raw_args) if isinstance(raw_args, Mapping) else {}

        cmd = ""
        cmd_arg_key = "command"
        for key in COMMAND_ARG_KEYS:
            val = tool_args.get(key)
            if isinstance(val, str) and val.strip():
                cmd = val.strip()
                cmd_arg_key = key
                break

        if not cmd and isinstance(request.payload.get("command"), str):
            cmd = str(request.payload["command"]).strip()

        if cmd and self._redis is not None and self._redis.is_connected:
            try:
                fail_count = await self._redis.get_command_failure_count(
                    request.conversation_id,
                    tool_name,
                    cmd,
                )
                if fail_count >= self._max_consecutive_failures:
                    return TierStepResult(
                        status=TierStepStatus.TERMINAL,
                        tier_id=self.tier_id,
                        action=VerdictAction.DENY,
                        reason=(
                            f"Anti-loop protection: tool '{tool_name}' with identical command "
                            f"failed {fail_count} times consecutively in this session. "
                            "Change your approach instead of retrying the same failing command."
                        ),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Redis anti-loop check failed: %s", exc)

            try:
                cached = await self._redis.get_cached_decision(tool_name, cmd)
                if cached is not None and "action" in cached:
                    return TierStepResult(
                        status=TierStepStatus.TERMINAL,
                        tier_id=self.tier_id,
                        action=VerdictAction(str(cached["action"])),
                        reason=str(cached.get("reason", "Loaded from Redis LangCache.")),
                        overwrite_args=cached.get("overwrite_args"),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Redis LangCache lookup failed: %s", exc)

        if cmd:
            bundle = await self._compiler.build_canonical_bundle(request.stack_tags)
            for rule in bundle.guardrails:
                if not re.search(rule.pattern, cmd):
                    continue

                if rule.action == GuardrailAction.DENY:
                    reason = f"[{rule.slug}] {rule.description or rule.title}"
                    await self._cache_tool_decision(
                        tool_name,
                        cmd,
                        {"action": VerdictAction.DENY.value, "reason": reason},
                    )
                    return TierStepResult(
                        status=TierStepStatus.TERMINAL,
                        tier_id=self.tier_id,
                        action=VerdictAction.DENY,
                        reason=reason,
                    )

                if rule.action == GuardrailAction.OVERWRITE and rule.replacement is not None:
                    rewritten_cmd = re.sub(rule.pattern, rule.replacement, cmd)
                    overwrite_args = {**tool_args, cmd_arg_key: rewritten_cmd}
                    reason = f"[{rule.slug}] Rewrote command: '{cmd}' -> '{rewritten_cmd}'"
                    await self._cache_tool_decision(
                        tool_name,
                        cmd,
                        {
                            "action": VerdictAction.OVERWRITE.value,
                            "reason": reason,
                            "overwrite_args": overwrite_args,
                        },
                    )
                    return TierStepResult(
                        status=TierStepStatus.TERMINAL,
                        tier_id=self.tier_id,
                        action=VerdictAction.OVERWRITE,
                        overwrite_args=overwrite_args,
                        reason=reason,
                    )

            for grey_pattern in GREY_ZONE_PATTERNS:
                if re.search(grey_pattern, cmd):
                    return TierStepResult(
                        status=TierStepStatus.ESCALATE,
                        tier_id=self.tier_id,
                        action=VerdictAction.ASK,
                        reason=(
                            "Command matches grey-zone security pattern; escalating to Tier 2 Decision API."
                        ),
                        fail_mode_override=FailMode.FAIL_CLOSED_ASK,
                    )

        return TierStepResult(
            status=TierStepStatus.TERMINAL,
            tier_id=self.tier_id,
            action=VerdictAction.ALLOW,
            reason="Passed Tier 1 deterministic guardrails.",
        )

    async def _cache_tool_decision(
        self,
        tool_name: str,
        cmd: str,
        payload: dict[str, Any],
    ) -> None:
        """Сохраняет детерминированное решение в Redis LangCache (если Redis подключён)."""
        if self._redis is not None and self._redis.is_connected:
            try:
                await self._redis.cache_decision(tool_name, cmd, payload)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Redis LangCache store failed: %s", exc)

    @staticmethod
    def _evaluate_plan_validation(request: DecisionRequest) -> TierStepResult:
        """Валидирует `implementation_plan.md` на пошаговость (`Learning-First`) и синхронизацию `.context/`."""
        content = str(
            request.payload.get("content")
            or request.payload.get("plan_text")
            or ""
        ).strip()
        if not content:
            return TierStepResult(
                status=TierStepStatus.TERMINAL,
                tier_id=TIER_1_ID,
                action=VerdictAction.DENY,
                reason="Implementation plan content is empty.",
            )

        has_iterations = bool(re.search(PLAN_ITERATION_REGEX, content))
        has_context_sync = ".context" in content

        if not has_iterations or not has_context_sync:
            missing: list[str] = []
            if not has_iterations:
                missing.append("explicit step/iteration breakdown (Learning-First)")
            if not has_context_sync:
                missing.append(".context/ graph synchronization step")
            return TierStepResult(
                status=TierStepStatus.TERMINAL,
                tier_id=TIER_1_ID,
                action=VerdictAction.DENY,
                reason=(
                    "Plan validation failed: implementation_plan.md is missing "
                    + " and ".join(missing)
                    + "."
                ),
            )

        return TierStepResult(
            status=TierStepStatus.TERMINAL,
            tier_id=TIER_1_ID,
            action=VerdictAction.ALLOW,
            score=1.0,
            reason="Implementation plan satisfies Learning-First and .context/ synchronization requirements.",
        )

    @staticmethod
    def _evaluate_subagent_routing(request: DecisionRequest) -> TierStepResult:
        """Вычисляет локальную эвристику маршрутизации субагента и эскалирует в Tier 2 для уточнения."""
        prompt = str(request.payload.get("prompt", "")).strip()
        lowered = prompt.lower()
        word_count = len(prompt.split())

        if any(kw in lowered for kw in PRO_ROUTING_KEYWORDS) or word_count > 120:
            choice = SUBAGENT_PRO_CHOICE
            score = 0.85
        elif word_count > 35:
            choice = SUBAGENT_FLASH_CHOICE
            score = 0.55
        else:
            choice = SUBAGENT_LITE_CHOICE
            score = 0.25

        return TierStepResult(
            status=TierStepStatus.ESCALATE,
            tier_id=TIER_1_ID,
            action=VerdictAction.ALLOW,
            choice=choice,
            score=score,
            reason=f"Tier 1 heuristic selected '{choice}' (complexity={score}); escalating to Tier 2 if available.",
        )

    @staticmethod
    def _evaluate_context_pruning(request: DecisionRequest) -> TierStepResult:
        """Вычисляет базовую оценку релевантности контекста перед эскалацией во второй контур."""
        text = str(request.payload.get("content", "")).strip()
        score = 0.8 if (".context" in text or "error" in text.lower()) else 0.5
        return TierStepResult(
            status=TierStepStatus.ESCALATE,
            tier_id=TIER_1_ID,
            action=VerdictAction.ALLOW,
            score=score,
            reason="Computed baseline context relevance score; escalating to Tier 2 pool.",
        )

    @staticmethod
    def _evaluate_harness_probe(request: DecisionRequest) -> TierStepResult:
        """Оценивает полноту кандидата `Harness²` перед эскалацией в `SystemOneDecisionTier`."""
        content = str(request.payload.get("content", "")).strip()
        has_substance = len(content) >= 20
        return TierStepResult(
            status=TierStepStatus.ESCALATE,
            tier_id=TIER_1_ID,
            action=VerdictAction.ALLOW if has_substance else VerdictAction.DENY,
            score=0.75 if has_substance else 0.1,
            reason="Evaluated baseline harness probe structure; escalating to Tier 2 pool.",
        )


class SystemOneDecisionTier:
    """Второй контур (`Tier 2`): структурированная оценка микро-вопросов через `RuntimeProviderPool`."""

    def __init__(
        self,
        provider_pool: RuntimeProviderPool,
        domain_specs: Mapping[DecisionDomain, DomainEvaluationSpec],
    ) -> None:
        self._provider_pool: RuntimeProviderPool = provider_pool
        self._domain_specs: Mapping[DecisionDomain, DomainEvaluationSpec] = domain_specs

    @property
    def tier_id(self) -> str:
        """Возвращает идентификатор второго контура."""
        return TIER_2_ID

    @property
    def priority(self) -> int:
        """Возвращает приоритет второго контура (20)."""
        return TIER_2_PRIORITY

    async def evaluate(
        self,
        request: DecisionRequest,
        context: dict[str, Any],
    ) -> TierStepResult:
        """Выполняет батч микро-вопросов в `RuntimeProviderPool` и маппирует ответы в `TierStepResult`."""
        started = time.perf_counter()
        spec = self._domain_specs.get(request.domain)
        questions = list(spec.tier2_questions) if spec is not None else []

        if not questions:
            return TierStepResult(
                status=TierStepStatus.ABSTAIN,
                tier_id=self.tier_id,
                reason="No Tier 2 micro-questions configured for domain.",
            )

        pool_result = await self._provider_pool.evaluate_batch(
            evidence=request.payload,
            questions=questions,
        )
        elapsed_ms = round((time.perf_counter() - started) * MS_PER_SECOND, 2)

        if not pool_result.succeeded:
            return TierStepResult(
                status=TierStepStatus.ABSTAIN,
                tier_id=self.tier_id,
                warnings=pool_result.warnings,
                provider_trace=pool_result.attempts,
                latency_ms=elapsed_ms,
                reason="Tier 2 provider pool abstained (no healthy provider available).",
            )

        return self._interpret_answers(
            request=request,
            answers=pool_result.answers,
            provider_used=pool_result.provider_used or TIER_2_ID,
            warnings=pool_result.warnings,
            attempts=pool_result.attempts,
            latency_ms=elapsed_ms,
        )

    def _interpret_answers(
        self,
        *,
        request: DecisionRequest,
        answers: dict[str, NormalizedAnswer],
        provider_used: str,
        warnings: list[str],
        attempts: list[ProviderAttempt],
        latency_ms: float,
    ) -> TierStepResult:
        """Преобразует нормализованные ответы микро-вопросов в терминальный `TierStepResult`."""
        tier_label = f"{self.tier_id}:{provider_used}"

        if request.domain == DecisionDomain.TOOL_SAFETY:
            safe_ans = answers.get("is_safe_to_execute")
            action_ans = answers.get("recommended_action")
            risk_ans = answers.get("risk_score")

            is_safe = bool(safe_ans.value) if safe_ans is not None else False
            risk_score = float(risk_ans.value) if risk_ans is not None else 0.5
            confidence = safe_ans.confidence if safe_ans is not None else 1.0

            if action_ans is not None and str(action_ans.value) in (
                VerdictAction.ALLOW.value,
                VerdictAction.ASK.value,
                VerdictAction.DENY.value,
            ):
                chosen_action = VerdictAction(str(action_ans.value))
            else:
                chosen_action = VerdictAction.ALLOW if is_safe else VerdictAction.ASK

            if not is_safe and chosen_action == VerdictAction.ALLOW:
                chosen_action = VerdictAction.ASK

            return TierStepResult(
                status=TierStepStatus.TERMINAL,
                tier_id=tier_label,
                action=chosen_action,
                confidence=confidence,
                score=risk_score,
                reason=f"Tier 2 provider '{provider_used}' evaluated safety={is_safe}, risk={risk_score:.2f}.",
                answers=answers,
                warnings=warnings,
                provider_trace=attempts,
                latency_ms=latency_ms,
            )

        if request.domain == DecisionDomain.SUBAGENT_ROUTING:
            tier_ans = answers.get("target_tier")
            comp_ans = answers.get("complexity_score")
            choice = str(tier_ans.value) if tier_ans is not None else SUBAGENT_FLASH_CHOICE
            score = float(comp_ans.value) if comp_ans is not None else 0.5
            return TierStepResult(
                status=TierStepStatus.TERMINAL,
                tier_id=tier_label,
                action=VerdictAction.ALLOW,
                choice=choice,
                score=score,
                reason=f"Tier 2 provider '{provider_used}' routed task to '{choice}'.",
                answers=answers,
                warnings=warnings,
                provider_trace=attempts,
                latency_ms=latency_ms,
            )

        first_pred = next(
            (a for a in answers.values() if a.question_type == DecisionQuestionType.PREDICATE),
            None,
        )
        first_score = next(
            (a for a in answers.values() if a.question_type == DecisionQuestionType.SCORE),
            None,
        )
        passed = bool(first_pred.value) if first_pred is not None else True
        score_val = float(first_score.value) if first_score is not None else (1.0 if passed else 0.0)
        return TierStepResult(
            status=TierStepStatus.TERMINAL,
            tier_id=tier_label,
            action=VerdictAction.ALLOW if passed else VerdictAction.DENY,
            score=score_val,
            reason=f"Tier 2 provider '{provider_used}' completed domain evaluation.",
            answers=answers,
            warnings=warnings,
            provider_trace=attempts,
            latency_ms=latency_ms,
        )


class DecisionEngine:
    """Координатор цепочки ответственности (`Tier 1 -> Tier 2 -> Tier N`) и гибридного поиска опыта."""

    def __init__(
        self,
        db: PostgresDatabase | None = None,
        redis: RedisSessionStore | None = None,
        provider_pool: RuntimeProviderPool | None = None,
        compiler: HarnessCompiler | None = None,
        *,
        max_consecutive_failures: int = 2,
    ) -> None:
        self._db: PostgresDatabase | None = db
        self._redis: RedisSessionStore | None = redis
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
            confidence=last_step.confidence if last_step is not None else 0.7,
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
        """Ищет релевантные решения ошибок в таблице `memories` через гибридный поиск (`tsvector + pg_trgm`)."""
        clean_query = error_query.strip()
        if not clean_query or self._db is None or not self._db.is_connected:
            return []

        tags = [t.strip().lower() for t in stack_tags if t.strip()] or [
            ScopeType.GLOBAL.value
        ]
        try:
            rows = await self._db.pool.fetch(
                """
                SELECT
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
                """,
                clean_query[:500],
                ScopeType.GLOBAL.value,
                tags,
                max(1, limit),
            )
            return [
                {
                    "title": str(r["title"]),
                    "error_symptom": str(r["error_symptom"]),
                    "solution": str(r["solution"]),
                    "relevance": round(float(r["relevance"] or 0.0), 3),
                }
                for r in rows
            ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Hybrid memory search failed: %s", exc)
            return []


__all__ = [
    "COMMAND_ARG_KEYS",
    "GREY_ZONE_PATTERNS",
    "SUBAGENT_FLASH_CHOICE",
    "SUBAGENT_LITE_CHOICE",
    "SUBAGENT_PRO_CHOICE",
    "TIER_1_ID",
    "TIER_2_ID",
    "TIER_FALLBACK_ID",
    "DecisionEngine",
    "DecisionTier",
    "DomainEvaluationSpec",
    "LocalDeterministicTier",
    "SystemOneDecisionTier",
    "build_default_domain_specs",
]
