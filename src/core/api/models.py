"""Модели запросов и константы HTTP-шлюза управления и жизненных хуков (Layer 2: FastAPI Core API).

Содержит строго типизированные Pydantic-контракты входных запросов REST API (`projects`,
`harness`, `providers`, `context/heal`), константы инъекции знаний при планировании (`/plan`)
и параметризованные SQL-запросы модификации каталога без магических строк и чисел.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.core.enums import AgentType, ComponentStatus, ComponentType, ScopeType
from src.core.graph.sync_engine import (
    ContextNodeSyncInput,
    WorkspaceComponentCandidate,
)
from src.core.harness.consolidator import (
    DEFAULT_MIN_LEXICAL_SIMILARITY,
    DEFAULT_MIN_VECTOR_SIMILARITY,
)

HARNESS_CONFIG_REL_PATH: str = ".context/harness.json"
MAX_PLAN_PATTERNS_INJECTED: int = 3
MAX_PLAN_MEMORIES_INJECTED: int = 2
MAX_PLAN_COMPACTIONS_INJECTED: int = 1
PLAN_KEYWORDS: tuple[str, ...] = (
    "/plan",
    "implementation_plan",
    "архитектурный план",
    "пошаговый план",
)

UPSERT_PROJECT_SQL: str = """
INSERT INTO projects (workspace_path, name, agent_type, stack_tags, profile_overrides, last_synced_at)
VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
ON CONFLICT (workspace_path) DO UPDATE SET
    name = EXCLUDED.name,
    agent_type = EXCLUDED.agent_type,
    stack_tags = EXCLUDED.stack_tags,
    profile_overrides = EXCLUDED.profile_overrides,
    last_synced_at = NOW()
RETURNING id
"""

UPDATE_COMPONENT_STATUS_SQL: str = """
UPDATE harness_components
SET status = $2,
    version = version + 1,
    updated_at = NOW()
WHERE slug = $1
RETURNING slug, component_type, scope, stack_tags, title, status, score, version
"""


class ProjectRegistrationRequest(BaseModel):
    """Запрос от хостового CLI-инжектора на регистрацию рабочего пространства и компиляцию харнеса."""

    workspace_path: str = Field(..., min_length=1)
    project_name: str = Field(..., min_length=1)
    agent_type: AgentType = AgentType.ANTIGRAVITY
    stack_tags: list[str] = Field(default_factory=lambda: [ScopeType.GLOBAL.value])
    profile_overrides: dict[str, Any] | None = None


class ComponentPatchRequest(BaseModel):
    """Запрос на переключение глобального статуса компонента `Harness` в каталоге."""

    status: ComponentStatus | None = None
    enabled: bool | None = None


class ProviderPatchRequest(BaseModel):
    """Запрос на частичное обновление провайдера Tier 2 в рантайм-пуле без перезапуска ядра."""

    priority: int | None = None
    enabled: bool | None = None
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    timeout_ms: int | None = Field(default=None, ge=50, le=10000)
    reset_circuit: bool = False


class ContextHealRequest(BaseModel):
    """Запрос на запуск фонового самоисцеления графа `.context/` (`Background Context Healer`)."""

    workspace_path: str = Field(min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    diff_summary: str = Field(default="")
    agent_type: AgentType | None = None


class SyncIngestRequest(BaseModel):
    """Запрос на двусторонний инжест кастомных скиллов и правил рабочей директории (`POST /api/sync/ingest`)."""

    workspace_path: str = Field(..., min_length=1)
    stack_tags: list[str] = Field(default_factory=lambda: [ScopeType.GLOBAL.value])
    components: list[WorkspaceComponentCandidate] = Field(default_factory=list)


class SyncContextRequest(BaseModel):
    """Запрос на 3-Way Hash синхронизацию локальных узлов `.context/` с PostgreSQL (`POST /api/sync/context`)."""

    workspace_path: str = Field(..., min_length=1)
    nodes: list[ContextNodeSyncInput] = Field(default_factory=list)


class HarnessConsolidateRequest(BaseModel):
    """Запрос на кластеризацию и Lineage-слияние семантических дубликатов (`POST /api/harness/consolidate`)."""

    stack_tags: list[str] | None = None
    component_type: ComponentType | None = None
    min_vector_similarity: float = Field(
        default=DEFAULT_MIN_VECTOR_SIMILARITY,
        ge=0.1,
        le=1.0,
    )
    min_lexical_similarity: float = Field(
        default=DEFAULT_MIN_LEXICAL_SIMILARITY,
        ge=0.1,
        le=1.0,
    )
    dry_run: bool = False


__all__ = [
    "HARNESS_CONFIG_REL_PATH",
    "MAX_PLAN_COMPACTIONS_INJECTED",
    "MAX_PLAN_MEMORIES_INJECTED",
    "MAX_PLAN_PATTERNS_INJECTED",
    "PLAN_KEYWORDS",
    "UPDATE_COMPONENT_STATUS_SQL",
    "UPSERT_PROJECT_SQL",
    "ComponentPatchRequest",
    "ContextHealRequest",
    "HarnessConsolidateRequest",
    "ProjectRegistrationRequest",
    "ProviderPatchRequest",
    "SyncContextRequest",
    "SyncIngestRequest",
]
