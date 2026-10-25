"""Пакет маршрутизаторов, моделей запросов и зависимостей HTTP-шлюза (Layer 2: FastAPI Core API).

Объединяет контракты запросов и SQL-константы (`models.py`), резолверы состояния `app.state`
(`deps.py`), маршрутизатор управляющих эндпоинтов (`routes_management.py`) и шлюз 5 жизненных
хуков ИИ-агентов с архивацией сводок `/compact` (`routes_hooks.py`).
"""

from __future__ import annotations

from src.core.api.deps import (
    _build_local_mcp_url,
    _get_compaction_store,
    _get_consolidator,
    _get_engine,
    _get_script_dispatcher,
    _get_sync_engine,
    _log_hook_event,
    _resolve_project_context,
    _resolve_project_stack_tags,
)
from src.core.api.models import (
    HARNESS_CONFIG_REL_PATH,
    MAX_PLAN_COMPACTIONS_INJECTED,
    MAX_PLAN_MEMORIES_INJECTED,
    MAX_PLAN_PATTERNS_INJECTED,
    PLAN_KEYWORDS,
    UPDATE_COMPONENT_STATUS_SQL,
    UPSERT_PROJECT_SQL,
    ComponentPatchRequest,
    ContextHealRequest,
    HarnessConsolidateRequest,
    ProjectRegistrationRequest,
    ProviderPatchRequest,
    SyncContextRequest,
    SyncIngestRequest,
)
from src.core.api.routes_hooks import (
    _build_plan_best_practices_hint,
    hook_post_tool_use,
    hook_pre_invocation,
    hook_pre_tool_use,
    hook_session_start,
    hook_stop,
    hooks_router,
)
from src.core.api.routes_management import (
    delete_provider,
    evaluate_decision,
    heal_project_context,
    health_check,
    list_harness_components,
    list_providers,
    management_router,
    patch_harness_component,
    patch_provider,
    register_project,
    upsert_provider,
)
from src.core.api.routes_sync import (
    consolidate_harness_components,
    ingest_workspace_components,
    sync_router,
    sync_workspace_context_nodes,
)

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
    "_build_local_mcp_url",
    "_build_plan_best_practices_hint",
    "_get_compaction_store",
    "_get_consolidator",
    "_get_engine",
    "_get_script_dispatcher",
    "_get_sync_engine",
    "_log_hook_event",
    "_resolve_project_context",
    "_resolve_project_stack_tags",
    "consolidate_harness_components",
    "delete_provider",
    "evaluate_decision",
    "heal_project_context",
    "health_check",
    "hook_post_tool_use",
    "hook_pre_invocation",
    "hook_pre_tool_use",
    "hook_session_start",
    "hook_stop",
    "hooks_router",
    "ingest_workspace_components",
    "list_harness_components",
    "list_providers",
    "management_router",
    "patch_harness_component",
    "patch_provider",
    "register_project",
    "sync_router",
    "sync_workspace_context_nodes",
    "upsert_provider",
]
