"""Вычислительные уровни (`DecisionTier`) и спецификации доменов матрицы `DecisionEngine` (Layer 2).

Определяет расширяемый контракт `DecisionTier` (`OCP`), декларативные спецификации 5 доменов
(`DomainEvaluationSpec`) и три реализации уровней оценки: `LocalDeterministicTier` (<2 мс),
`AntigravityProbeDecisionTier` (песочница SDK) и `SystemOneDecisionTier` (пул `RuntimeProviderPool`).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from src.core.db.postgres import PostgresDatabase
from src.core.db.redis_client import RedisSessionStore
from src.core.decision.models import (
    DecisionQuestion,
    DecisionRequest,
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
    TierStepStatus,
    VerdictAction,
)
from src.core.harness.compiler import HarnessCompiler
from src.core.services.antigravity import AntigravitySDKService

logger = logging.getLogger("mcp_coop.core.decision.tiers")

TIER_1_ID: str = "tier1_local_deterministic"
TIER_ANTIGRAVITY_PROBE_ID: str = "tier2_antigravity_probe_auditor"
TIER_2_ID: str = "tier2_system_one_pool"
TIER_FALLBACK_ID: str = "tier3_heuristic_fallback"

TIER_1_PRIORITY: int = 10
TIER_ANTIGRAVITY_PROBE_PRIORITY: int = 15
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

PRO_ROUTING_WORD_THRESHOLD: int = 120
FLASH_ROUTING_WORD_THRESHOLD: int = 35
HARNESS_PROBE_MIN_CONTENT_CHARS: int = 20


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

        raw_overrides = request.payload.get("profile_overrides")
        profile_overrides: Mapping[str, Any] | None = (
            raw_overrides if isinstance(raw_overrides, Mapping) and raw_overrides else None
        )

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

            if profile_overrides is None:
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
            bundle = await self._compiler.build_canonical_bundle(
                request.stack_tags,
                profile_overrides=profile_overrides,
            )
            for rule in bundle.guardrails:
                if not re.search(rule.pattern, cmd):
                    continue

                if rule.action == GuardrailAction.DENY:
                    reason = f"[{rule.slug}] {rule.description or rule.title}"
                    if profile_overrides is None:
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
                    if profile_overrides is None:
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

        if any(kw in lowered for kw in PRO_ROUTING_KEYWORDS) or word_count > PRO_ROUTING_WORD_THRESHOLD:
            choice = SUBAGENT_PRO_CHOICE
            score = 0.85
        elif word_count > FLASH_ROUTING_WORD_THRESHOLD:
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
        has_substance = len(content) >= HARNESS_PROBE_MIN_CONTENT_CHARS
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


class AntigravityProbeDecisionTier:
    """Опциональный тир (`priority=15`): запускает `Read-Only Probe Auditor` для `AgentType.ANTIGRAVITY`."""

    def __init__(self, antigravity_sdk: AntigravitySDKService) -> None:
        self._antigravity_sdk: AntigravitySDKService = antigravity_sdk

    @property
    def tier_id(self) -> str:
        """Возвращает идентификатор тира `Read-Only Probe Auditor`."""
        return TIER_ANTIGRAVITY_PROBE_ID

    @property
    def priority(self) -> int:
        """Возвращает приоритет (15) между локальным `Tier 1` и общим пулом `Tier 2`."""
        return TIER_ANTIGRAVITY_PROBE_PRIORITY

    async def evaluate(
        self,
        request: DecisionRequest,
        context: dict[str, Any],
    ) -> TierStepResult:
        """Выполняет глубокий аудит кандидата в песочнице только для домена `HARNESS_PROBE` и `antigravity`."""
        if request.domain != DecisionDomain.HARNESS_PROBE:
            return TierStepResult(
                status=TierStepStatus.ABSTAIN,
                tier_id=self.tier_id,
                reason="Antigravity Probe Tier only evaluates HARNESS_PROBE domain.",
            )

        raw_agent = request.payload.get("agent_type") or context.get("agent_type")
        target_agent = str(raw_agent) if isinstance(raw_agent, str) else None
        if not self._antigravity_sdk.is_enabled_for_agent(target_agent):
            return TierStepResult(
                status=TierStepStatus.ABSTAIN,
                tier_id=self.tier_id,
                reason="Antigravity SDK is disabled for target agent or unconfigured.",
            )

        started = time.perf_counter()
        slug = str(request.payload.get("slug", "candidate")).strip() or "candidate"
        comp_type = str(request.payload.get("component_type", "skill")).strip() or "skill"
        title = str(request.payload.get("title", slug)).strip() or slug
        content_md = str(request.payload.get("content", "")).strip()
        rationale = str(request.payload.get("rationale", "")).strip()
        ws_raw = request.payload.get("workspace_path") or request.workspace_path
        workspace_path = str(ws_raw).strip() if isinstance(ws_raw, str) and ws_raw.strip() else None

        verdict = await self._antigravity_sdk.audit_harness_candidate(
            slug=slug,
            component_type=comp_type,
            title=title,
            content_md=content_md,
            rationale=rationale,
            workspace_path=workspace_path,
            target_agent=target_agent,
        )
        elapsed_ms = round((time.perf_counter() - started) * MS_PER_SECOND, 2)
        if verdict.fallback_used and not verdict.security_flag:
            return TierStepResult(
                status=TierStepStatus.ABSTAIN,
                tier_id=self.tier_id,
                latency_ms=elapsed_ms,
                reason="Antigravity SDK fell back to deterministic mode; deferring to Tier 2 provider pool.",
            )

        return TierStepResult(
            status=TierStepStatus.TERMINAL,
            tier_id=self.tier_id,
            action=VerdictAction.ALLOW if verdict.promote_to_composed else VerdictAction.DENY,
            score=verdict.utility_score,
            reason=verdict.reason,
            latency_ms=elapsed_ms,
        )


__all__ = [
    "COMMAND_ARG_KEYS",
    "FLASH_ROUTING_WORD_THRESHOLD",
    "GREY_ZONE_PATTERNS",
    "HARNESS_PROBE_MIN_CONTENT_CHARS",
    "MS_PER_SECOND",
    "PLAN_ITERATION_REGEX",
    "PRO_ROUTING_KEYWORDS",
    "PRO_ROUTING_WORD_THRESHOLD",
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
    "DecisionTier",
    "DomainEvaluationSpec",
    "LocalDeterministicTier",
    "SystemOneDecisionTier",
    "build_default_domain_specs",
]
