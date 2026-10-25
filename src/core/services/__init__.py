"""Пакет сервисных клиентов Python-ядра (`src/core/services/`).

Экспортирует тонкий мульти-диалектный HTTP-клиент векторных эмбеддингов `EmbeddingClient`,
модульный адаптер изолированных микро-агентов второго уровня `AntigravitySDKService` и утилиты `pgvector`.
"""

from __future__ import annotations

from src.core.services.antigravity import (
    AntigravityAgentRunnerProtocol,
    AntigravitySDKService,
    ContextHealPlan,
    ContextHealReport,
    ContextNodeHealEdit,
    DefaultAntigravityRuntime,
    E2EAgentProbeResult,
    ProbeAuditVerdict,
    SandboxExecutionSpec,
    extract_structured_model,
    stage_isolated_workspace,
    wrap_untrusted_payload,
)
from src.core.services.embeddings import (
    EmbeddingClient,
    format_embeddinggemma_prompt,
    format_pgvector,
    normalize_l2,
)

__all__ = [
    "AntigravityAgentRunnerProtocol",
    "AntigravitySDKService",
    "ContextHealPlan",
    "ContextHealReport",
    "ContextNodeHealEdit",
    "DefaultAntigravityRuntime",
    "E2EAgentProbeResult",
    "EmbeddingClient",
    "ProbeAuditVerdict",
    "SandboxExecutionSpec",
    "extract_structured_model",
    "format_embeddinggemma_prompt",
    "format_pgvector",
    "normalize_l2",
    "stage_isolated_workspace",
    "wrap_untrusted_payload",
]
