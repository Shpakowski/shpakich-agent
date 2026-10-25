"""Модульный пакет адаптера микро-агентов второго уровня `google-antigravity` SDK.

Отвечает за Layer 2 (Core Services): объединяет контракты `models.py`, безопасную песочницу
`sandbox.py`, ленивый рантайм-адаптер `runtime.py` и сервис-оркестратор `service.py`.
"""

from __future__ import annotations

from src.core.services.antigravity.models import (
    AntigravityAgentRunnerProtocol,
    AntigravityAgentSessionProtocol,
    AntigravityChatResponseProtocol,
    AntigravityToolCallProtocol,
    ContextHealPlan,
    ContextHealReport,
    ContextNodeHealEdit,
    E2EAgentProbeResult,
    ProbeAuditVerdict,
    SandboxExecutionSpec,
)
from src.core.services.antigravity.runtime import DefaultAntigravityRuntime
from src.core.services.antigravity.sandbox import (
    MAX_SANDBOX_FILE_BYTES,
    MAX_SANDBOX_SOURCE_FILES,
    SANDBOX_DIR_PREFIX,
    extract_structured_model,
    stage_isolated_workspace,
    wrap_untrusted_payload,
)
from src.core.services.antigravity.service import AntigravitySDKService

__all__ = [
    "MAX_SANDBOX_FILE_BYTES",
    "MAX_SANDBOX_SOURCE_FILES",
    "SANDBOX_DIR_PREFIX",
    "AntigravityAgentRunnerProtocol",
    "AntigravityAgentSessionProtocol",
    "AntigravityChatResponseProtocol",
    "AntigravitySDKService",
    "AntigravityToolCallProtocol",
    "ContextHealPlan",
    "ContextHealReport",
    "ContextNodeHealEdit",
    "DefaultAntigravityRuntime",
    "E2EAgentProbeResult",
    "ProbeAuditVerdict",
    "SandboxExecutionSpec",
    "extract_structured_model",
    "stage_isolated_workspace",
    "wrap_untrusted_payload",
]
