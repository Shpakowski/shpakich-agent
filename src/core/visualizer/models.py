"""Модели данных и неизменяемые DTO интерактивного визуализатора графа RAG и Harness.

Определяет структуры узлов и рёбер Cytoscape.js, контейнеров группировки, агрегатов
статистики базы данных, журнала недавних изменений и результатов гибридного поиска.
Все модели неизменяемы (`frozen=True`) для обеспечения потокобезопасности и целостности.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import VisualizerEdgeKind, VisualizerLayoutMode, VisualizerNodeKind

# Константы идентификаторов родительских контейнеров Cytoscape.js (Compound Nodes)
GROUP_SKILLS_ID = "group:skills"
GROUP_SCRIPTS_ID = "group:scripts"
GROUP_GUARDRAILS_ID = "group:guardrails"
GROUP_PROMPTS_ID = "group:prompts"
GROUP_MEMORIES_ID = "group:memories"
GROUP_COMPACTIONS_ID = "group:compactions"
GROUP_CONTEXT_ID = "group:context"

# Константы фильтрации по времени, лимитов и порогов схожести графа
DEFAULT_SINCE_MINUTES = 60
DEFAULT_MIN_SIMILARITY = 0.78
MAX_SEMANTIC_EDGES = 120
MAX_RECENT_CHANGES = 50
MAX_CONTENT_PREVIEW_CHARS = 320
DEFAULT_SEARCH_TOP_K = 15
DEFAULT_TELEMETRY_LIMIT = 80
DEFAULT_TOP_COMPONENTS_LIMIT = 25

# Весовые коэффициенты гибридного и лексического ранжирования
HYBRID_VEC_WEIGHT = 0.65
HYBRID_FTS_WEIGHT = 0.20
HYBRID_TRGM_WEIGHT = 0.15
LEXICAL_FTS_WEIGHT = 0.60
LEXICAL_TRGM_WEIGHT = 0.40

# Системные статусы и дефолтный режим раскладки
STATUS_OK = "ok"
DEFAULT_LAYOUT_MODE = VisualizerLayoutMode.FCOSE


class VisualizerImmutableModel(BaseModel):
    """Базовый класс неизменяемых DTO визуализатора с замороженным состоянием."""

    model_config = ConfigDict(frozen=True)


class VisualizerNodeData(VisualizerImmutableModel):
    """Данные отдельного узла графа Cytoscape.js с метаданными и атрибутами."""

    id: str
    label: str
    kind: VisualizerNodeKind
    parent: str | None = None
    subtype: str | None = None
    status: str | None = None
    scope: str | None = None
    execution_mode: str | None = None
    stack_tags: list[str] = Field(default_factory=list)
    content_preview: str | None = None
    content_full: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    is_new: bool = False
    is_updated: bool = False


class VisualizerEdgeData(VisualizerImmutableModel):
    """Данные ребра графа Cytoscape.js, описывающие связь между двумя узлами."""

    id: str
    source: str
    target: str
    kind: VisualizerEdgeKind
    weight: float = 1.0
    label: str | None = None


class VisualizerNodeElement(VisualizerImmutableModel):
    """Элемент узла в каноническом формате Cytoscape.js (`{ data: ... }`)."""

    data: VisualizerNodeData


class VisualizerEdgeElement(VisualizerImmutableModel):
    """Элемент связи в каноническом формате Cytoscape.js (`{ data: ... }`)."""

    data: VisualizerEdgeData


class VisualizerRecentChange(VisualizerImmutableModel):
    """Запись журнала недавних изменений компонентов или контекстных узлов после синхронизации."""

    node_id: str
    label: str
    kind: VisualizerNodeKind
    subtype: str | None = None
    status: str | None = None
    change_type: str
    timestamp: str
    stack_tags: list[str] = Field(default_factory=list)


class VisualizerStatsSummary(VisualizerImmutableModel):
    """Сводная статистика состояния репозитория, БД ядра и векторов эмбеддингов."""

    projects_count: int = 0
    harness_components_count: int = 0
    skills_count: int = 0
    scripts_count: int = 0
    guardrails_count: int = 0
    prompts_count: int = 0
    memories_count: int = 0
    compactions_count: int = 0
    context_nodes_count: int = 0
    hook_events_count: int = 0
    providers_count: int = 0
    embedded_nodes_count: int = 0
    new_after_sync_count: int = 0
    updated_after_sync_count: int = 0
    status_breakdown: dict[str, int] = Field(default_factory=dict)
    db_connected: bool = False


class VisualizerGraphSnapshot(VisualizerImmutableModel):
    """Полный снимок графа знаний и компонентов Harness для передачи в веб-интерфейс."""

    status: str = STATUS_OK
    workspace_path: str | None = None
    generated_at: str
    since_minutes: int = DEFAULT_SINCE_MINUTES
    include_semantic: bool = False
    min_similarity: float = DEFAULT_MIN_SIMILARITY
    stats: VisualizerStatsSummary
    nodes: list[VisualizerNodeElement]
    edges: list[VisualizerEdgeElement]
    recent_changes: list[VisualizerRecentChange]


class VisualizerSearchHit(VisualizerImmutableModel):
    """Результат ранжирования сущности при семантическом или гибридном поиске."""

    node_id: str
    label: str
    kind: VisualizerNodeKind
    subtype: str | None = None
    status: str | None = None
    score: float
    vec_score: float = 0.0
    fts_score: float = 0.0
    trgm_score: float = 0.0
    snippet: str = ""


class VisualizerSearchResponse(VisualizerImmutableModel):
    """Ответ поискового эндпоинта визуализатора со списком найденных совпадений."""

    status: str = STATUS_OK
    query: str
    embedding_used: bool = False
    hits: list[VisualizerSearchHit] = Field(default_factory=list)


class TelemetrySummaryStats(VisualizerImmutableModel):
    """Сводные KPI-метрики токен-экономики и телеметрии жизненных хуков."""

    total_events: int = 0
    total_user_in_tokens: int = 0
    total_harness_injected_tokens: int = 0
    total_output_tokens: int = 0
    avg_saved_pct: float = 100.0
    avg_duration_ms: float = 0.0
    denied_count: int = 0
    mcp_calls_count: int = 0


class TelemetryComponentRank(VisualizerImmutableModel):
    """Ранг частоты инъекции и средней релевантности компонента Harness (правила, скилла, скрипта)."""

    slug: str
    kind: str
    inject_count: int = 0
    avg_relevance: float = 1.0


class TelemetryEventRow(VisualizerImmutableModel):
    """Отдельная запись потока событий жизненных хуков и вызовов MCP с оценкой токенов."""

    id: int
    created_at: str
    event_type: str
    agent_type: str = "antigravity"
    tool_name: str = ""
    target: str = ""
    verdict: str = "allow"
    duration_ms: float = 0.0
    user_in_tokens: int = 0
    harness_injected_tokens: int = 0
    output_tokens: int = 0
    saved_pct: float = 100.0
    rules: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    scripts: list[str] = Field(default_factory=list)
    summary_line: str = ""


class VisualizerTelemetryResponse(VisualizerImmutableModel):
    """Ответ аналитического эндпоинта токен-экономики и живой телеметрии хуков."""

    status: str = STATUS_OK
    workspace_path: str | None = None
    since_minutes: int = DEFAULT_SINCE_MINUTES
    summary: TelemetrySummaryStats = Field(default_factory=TelemetrySummaryStats)
    top_components: list[TelemetryComponentRank] = Field(default_factory=list)
    events: list[TelemetryEventRow] = Field(default_factory=list)


__all__ = [
    "DEFAULT_LAYOUT_MODE",
    "DEFAULT_MIN_SIMILARITY",
    "DEFAULT_SEARCH_TOP_K",
    "DEFAULT_SINCE_MINUTES",
    "DEFAULT_TELEMETRY_LIMIT",
    "DEFAULT_TOP_COMPONENTS_LIMIT",
    "GROUP_COMPACTIONS_ID",
    "GROUP_CONTEXT_ID",
    "GROUP_GUARDRAILS_ID",
    "GROUP_MEMORIES_ID",
    "GROUP_PROMPTS_ID",
    "GROUP_SCRIPTS_ID",
    "GROUP_SKILLS_ID",
    "HYBRID_FTS_WEIGHT",
    "HYBRID_TRGM_WEIGHT",
    "HYBRID_VEC_WEIGHT",
    "LEXICAL_FTS_WEIGHT",
    "LEXICAL_TRGM_WEIGHT",
    "MAX_CONTENT_PREVIEW_CHARS",
    "MAX_RECENT_CHANGES",
    "MAX_SEMANTIC_EDGES",
    "STATUS_OK",
    "TelemetryComponentRank",
    "TelemetryEventRow",
    "TelemetrySummaryStats",
    "VisualizerEdgeData",
    "VisualizerEdgeElement",
    "VisualizerGraphSnapshot",
    "VisualizerImmutableModel",
    "VisualizerNodeData",
    "VisualizerNodeElement",
    "VisualizerRecentChange",
    "VisualizerSearchHit",
    "VisualizerSearchResponse",
    "VisualizerStatsSummary",
    "VisualizerTelemetryResponse",
]
