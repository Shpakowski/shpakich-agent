"""Типизированные модели и контракты двухкоординатного контура принятия решений (`DecisionEngine`).

Определяет неизменяемые DTO для микро-вопросов и нормализованных ответов второго контура (Tier 2),
конфигураций и статусов рантайм-пула провайдеров, а также универсальных запросов и резолюций
цепочки ответственности (`Tier 0 -> Tier 1 -> Tier 2 -> Tier 3`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import (
    AgentType,
    DecisionDomain,
    DecisionProviderDialect,
    DecisionQuestionType,
    FailMode,
    ProviderHealthStatus,
    ScopeType,
    TierStepStatus,
    VerdictAction,
)


class DecisionQuestion(BaseModel):
    """Декларация атомарного микро-вопроса (`predicate | choice | score`) для оценки во втором контуре."""

    model_config = ConfigDict(frozen=True)

    name: str
    question_type: DecisionQuestionType
    instructions: str
    choices: list[str] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=list)
    predicate_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class NormalizedAnswer(BaseModel):
    """Нормализованный ответ провайдера Tier 2 с вероятностным распределением и уровнем уверенности."""

    model_config = ConfigDict(frozen=True)

    name: str
    question_type: DecisionQuestionType
    value: bool | str | float
    probability: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    distribution: dict[str, float] = Field(default_factory=dict)


class DecisionProviderConfig(BaseModel):
    """Конфигурация провайдера второго контура для рантайм-пула и таблицы `decision_providers`."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    dialect: DecisionProviderDialect
    base_url: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    api_key: str = ""
    priority: int = 100
    timeout_ms: int = Field(default=600, ge=50, le=10000)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionProviderStatusView(BaseModel):
    """Публичное представление статуса провайдера в рантайм-пуле (без раскрытия секрета `api_key`)."""

    model_config = ConfigDict(frozen=True)

    name: str
    dialect: DecisionProviderDialect
    base_url: str
    model: str
    api_key_configured: bool
    priority: int
    timeout_ms: int
    enabled: bool
    health_status: ProviderHealthStatus
    circuit_reason: str | None = None
    cooldown_seconds_remaining: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderAttempt(BaseModel):
    """Запись об одной попытке вызова конкретного провайдера в каскаде отказоустойчивости Tier 2."""

    model_config = ConfigDict(frozen=True)

    provider_name: str
    dialect: DecisionProviderDialect
    model: str
    status: ProviderHealthStatus
    latency_ms: float = 0.0
    error: str | None = None


class ProviderPoolResult(BaseModel):
    """Агрегированный результат выполнения батча микро-вопросов через `RuntimeProviderPool`."""

    model_config = ConfigDict(frozen=True)

    succeeded: bool
    provider_used: str | None = None
    answers: dict[str, NormalizedAnswer] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    attempts: list[ProviderAttempt] = Field(default_factory=list)
    all_providers_failed: bool = False


class DecisionRequest(BaseModel):
    """Входной запрос на вынесение решения в конкретном функциональном домене `DecisionDomain`."""

    domain: DecisionDomain
    conversation_id: str = "default"
    workspace_path: str = ""
    agent_type: AgentType = AgentType.ANTIGRAVITY
    stack_tags: list[str] = Field(default_factory=lambda: [ScopeType.GLOBAL.value])
    payload: dict[str, Any] = Field(default_factory=dict)
    fail_mode: FailMode = FailMode.FAIL_OPEN_ALLOW


class TierStepResult(BaseModel):
    """Результат вычисления отдельного тира (`TERMINAL | ESCALATE | ABSTAIN`) в цепочке `DecisionEngine`."""

    status: TierStepStatus
    tier_id: str
    action: VerdictAction | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    score: float | None = None
    choice: str | None = None
    reason: str | None = None
    overwrite_args: dict[str, Any] | None = None
    inject_system_message: str | None = None
    inject_ephemeral_message: str | None = None
    answers: dict[str, NormalizedAnswer] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    provider_trace: list[ProviderAttempt] = Field(default_factory=list)
    fail_mode_override: FailMode | None = None
    latency_ms: float = 0.0


class DecisionResolution(BaseModel):
    """Финальная резолюция `DecisionEngine`, готовая к сериализации в протокольный ответ хука или API."""

    domain: DecisionDomain
    action: VerdictAction
    decided_by_tier: str
    confidence: float = 1.0
    score: float | None = None
    choice: str | None = None
    reason: str | None = None
    overwrite_args: dict[str, Any] | None = None
    inject_system_message: str | None = None
    inject_ephemeral_message: str | None = None
    answers: dict[str, NormalizedAnswer] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    provider_trace: list[ProviderAttempt] = Field(default_factory=list)
    total_latency_ms: float = 0.0


__all__ = [
    "DecisionProviderConfig",
    "DecisionProviderStatusView",
    "DecisionQuestion",
    "DecisionRequest",
    "DecisionResolution",
    "NormalizedAnswer",
    "ProviderAttempt",
    "ProviderPoolResult",
    "TierStepResult",
]
