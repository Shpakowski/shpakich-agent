"""Контракты данных и структурные протоколы пакета `src/core/services/antigravity/`.

Отвечает за Layer 2 (Core Services): определяет неизменяемые модели Pydantic v2
для аудита компонентов `Harness`, самоисцеления графа `.context/` и E2E-тестов,
а также протоколы `typing.Protocol` для строгой типизации `google-antigravity` SDK без `Any`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from src.core.config import CoreSettings
from src.core.enums import (
    AntigravityAgentRole,
    AntigravityAuthMode,
    ContextEntityClass,
)
from src.core.graph.context_graph import GraphValidationReport


class SandboxExecutionSpec(BaseModel):
    """Иммутабельная спецификация запуска изолированного микро-агента в песочнице `/tmp`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AntigravityAgentRole
    workspace_path: str
    prompt: str
    system_instructions: str
    read_only: bool = True
    timeout_ms: int = Field(default=30_000, ge=1000, le=300_000)
    skills_paths: list[str] = Field(default_factory=list)


class ProbeAuditVerdict(BaseModel):
    """Строгий вердикт Read-Only Probe Auditor для шага `Probe` в цикле `Harness`."""

    model_config = ConfigDict(extra="forbid")

    promote_to_composed: bool = Field(
        description="Whether the candidate rule or skill is safe and aligned with project architecture.",
    )
    utility_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Normalized architectural alignment and utility score in [0.0, 1.0].",
    )
    reason: str = Field(
        min_length=1,
        description="Concise technical justification of the audit decision.",
    )
    security_flag: bool = Field(
        default=False,
        description="True if prompt injection or destructive command patterns were detected.",
    )
    findings: list[str] = Field(
        default_factory=list,
        description="Specific strengths or issues discovered during the audit.",
    )
    fallback_used: bool = Field(
        default=False,
        description="True when deterministic local audit was used instead of the live SDK.",
    )
    auth_mode: AntigravityAuthMode = Field(
        default=AntigravityAuthMode.UNCONFIGURED,
        description="Active authentication mode used during evaluation.",
    )


class ContextNodeHealEdit(BaseModel):
    """Атомарная операция обновления узла Контекстного Графа, предлагаемая микро-агентом."""

    model_config = ConfigDict(extra="forbid")

    entity_class: ContextEntityClass = Field(default=ContextEntityClass.MODULE)
    node_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    files: list[str] = Field(default_factory=list)
    body_md: str = Field(min_length=1)
    trace_note: str = Field(min_length=1)
    stack: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    implements: list[str] = Field(default_factory=list)
    governed_by: list[str] = Field(default_factory=list)


class ContextHealPlan(BaseModel):
    """Схема структурированного плана самоисцеления графа `.context/` от микро-агента."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    edits: list[ContextNodeHealEdit] = Field(default_factory=list)


class ContextHealReport(BaseModel):
    """Итоговый отчёт выполнения `Background Context Healer`."""

    model_config = ConfigDict(extra="forbid")

    healed: bool
    fallback_used: bool
    pruned_phantom_files: dict[str, list[str]] = Field(default_factory=dict)
    upserted_nodes: list[str] = Field(default_factory=list)
    validation_before: GraphValidationReport
    validation_after: GraphValidationReport
    summary: str


class E2EAgentProbeResult(BaseModel):
    """Результат тестового прогона микро-агента в изолированном E2E-окружении."""

    model_config = ConfigDict(extra="forbid")

    executed: bool
    fallback_used: bool
    response_text: str
    tool_calls_observed: list[str] = Field(default_factory=list)
    error: str | None = None


@runtime_checkable
class AntigravityToolCallProtocol(Protocol):
    """Структурный протокол элемента потока вызовов инструментов (`response.tool_calls`)."""

    @property
    def name(self) -> str:
        """Возвращает имя вызванного инструмента."""
        ...


@runtime_checkable
class AntigravityChatResponseProtocol(Protocol):
    """Структурный протокол ответа `agent.chat(prompt)` из `google-antigravity` SDK."""

    @property
    def tool_calls(self) -> AsyncIterator[AntigravityToolCallProtocol]:
        """Асинхронный поток вызовов инструментов в рамках хода агента."""
        ...

    async def text(self) -> str:
        """Собирает финальный текстовый ответ агента."""
        ...


@runtime_checkable
class AntigravityAgentSessionProtocol(Protocol):
    """Структурный протокол контекстного менеджера `async with Agent(config) as agent:`."""

    async def __aenter__(self) -> Self:
        """Запускает подпроцесс рантайма агента."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Корректно гасит подпроцесс рантайма агента при выходе из контекста."""
        ...

    async def chat(self, prompt: str) -> AntigravityChatResponseProtocol:
        """Отправляет промпт агенту и возвращает типизированный объект ответа."""
        ...


@runtime_checkable
class AntigravityAgentRunnerProtocol(Protocol):
    """Протокол исполнения изолированного микро-агента (позволяет инжектировать стабы в pytest)."""

    def is_sdk_installed(self) -> bool:
        """Проверяет физическое наличие пакета `google-antigravity` в окружении."""
        ...

    async def execute(
        self,
        spec: SandboxExecutionSpec,
        settings: CoreSettings,
        response_schema: type[BaseModel] | None = None,
    ) -> tuple[str, list[str]]:
        """Запускает сессию агента в песочнице и возвращает текст ответа и список вызванных тулов."""
        ...


__all__ = [
    "AntigravityAgentRunnerProtocol",
    "AntigravityAgentSessionProtocol",
    "AntigravityChatResponseProtocol",
    "AntigravityToolCallProtocol",
    "ContextHealPlan",
    "ContextHealReport",
    "ContextNodeHealEdit",
    "E2EAgentProbeResult",
    "ProbeAuditVerdict",
    "SandboxExecutionSpec",
]
