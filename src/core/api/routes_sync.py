"""Маршрутизатор двусторонней синхронизации и консолидации знаний `Harness²` (Layer 2: FastAPI Sync Routes).

Предоставляет эндпоинты `POST /api/sync/ingest` (инжест пользовательских скиллов и правил с `Content-Hash Gate`
и `HARNESS_PROBE`), `POST /api/sync/context` (3-Way Hash Sync и AST-мерж графа `.context/`) и
`POST /api/harness/consolidate` (серверная кластеризация и Lineage-слияние семантических дубликатов по `SPEC-002`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from src.core.api.deps import (
    _get_consolidator,
    _get_sync_engine,
    _resolve_project_context,
)
from src.core.api.models import (
    HarnessConsolidateRequest,
    SyncContextRequest,
    SyncIngestRequest,
)
from src.core.db import PostgresDatabase

STATUS_OK: str = "ok"

sync_router = APIRouter()


@sync_router.post("/api/sync/ingest", tags=["sync"])
async def ingest_workspace_components(
    payload: SyncIngestRequest,
    request: Request,
) -> dict[str, Any]:
    """Принимает кастомные скиллы и правила из рабочей директории и сохраняет в `harness_components`."""
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    sync_engine = _get_sync_engine(request)
    raw_lookup: dict[str, Any] = (
        {"stack_tags": payload.stack_tags} if payload.stack_tags else {}
    )
    resolved_tags, _ = await _resolve_project_context(
        db,
        payload.workspace_path.strip(),
        raw_lookup,
    )
    report = await sync_engine.ingest_components(
        payload.workspace_path,
        resolved_tags,
        payload.components,
    )
    return {
        "status": STATUS_OK,
        **report.model_dump(mode="json"),
    }


@sync_router.post("/api/sync/context", tags=["sync"])
async def sync_workspace_context_nodes(
    payload: SyncContextRequest,
    request: Request,
) -> dict[str, Any]:
    """Выполняет 3-Way Hash сверку `(local_hash, db_hash, base_hash)` и AST-мерж узлов `.context/`."""
    sync_engine = _get_sync_engine(request)
    report = await sync_engine.sync_context_nodes(
        payload.workspace_path,
        payload.nodes,
    )
    return {
        "status": STATUS_OK,
        **report.model_dump(mode="json"),
    }


@sync_router.post("/api/harness/consolidate", tags=["harness"])
async def consolidate_harness_components(
    payload: HarnessConsolidateRequest,
    request: Request,
) -> dict[str, Any]:
    """Запускает кластеризацию семантических дубликатов `HarnessConsolidator` (`--dry-run` или транзакция)."""
    consolidator = _get_consolidator(request)
    report = await consolidator.consolidate(
        stack_tags=payload.stack_tags,
        component_type=payload.component_type,
        min_vector_similarity=payload.min_vector_similarity,
        min_lexical_similarity=payload.min_lexical_similarity,
        dry_run=payload.dry_run,
    )
    return {
        "status": STATUS_OK,
        **report.model_dump(mode="json"),
    }


__all__ = [
    "STATUS_OK",
    "consolidate_harness_components",
    "ingest_workspace_components",
    "sync_router",
    "sync_workspace_context_nodes",
]
