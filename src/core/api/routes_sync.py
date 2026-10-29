"""Маршрутизатор двусторонней синхронизации и консолидации знаний `Harness` (Layer 2: FastAPI Sync Routes).

Предоставляет эндпоинты `POST /api/sync/ingest` (инжест пользовательских скиллов и правил с `Content-Hash Gate`
и `HARNESS_PROBE`), `POST /api/sync/context` (3-Way Hash Sync и AST-мерж графа `.context/`) и
`POST /api/harness/consolidate` (серверная кластеризация и Lineage-слияние семантических дубликатов по `SPEC-002`).
"""

from __future__ import annotations

import time
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
from src.core.telemetry import log_sync_operation

STATUS_OK: str = "ok"

sync_router = APIRouter()


@sync_router.post("/api/sync/ingest", tags=["sync"])
async def ingest_workspace_components(
    payload: SyncIngestRequest,
    request: Request,
) -> dict[str, Any]:
    """Принимает кастомные скиллы и правила из рабочей директории и сохраняет в `harness_components`."""
    start_t = time.perf_counter()
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
    report_dict = report.model_dump(mode="json")
    log_sync_operation(
        operation="ingest",
        workspace_path=payload.workspace_path,
        duration_ms=(time.perf_counter() - start_t) * 1000.0,
        details={
            "received": len(payload.components),
            **{
                k: len(v) if isinstance(v, list) else v
                for k, v in report_dict.items()
                if isinstance(v, (int, float, str, bool, list))
            },
        },
    )
    return {
        "status": STATUS_OK,
        **report_dict,
    }


@sync_router.post("/api/sync/context", tags=["sync"])
async def sync_workspace_context_nodes(
    payload: SyncContextRequest,
    request: Request,
) -> dict[str, Any]:
    """Выполняет 3-Way Hash сверку `(local_hash, db_hash, base_hash)`, AST-мерж узлов `.context/` и доиндексацию `embedding IS NULL`."""
    start_t = time.perf_counter()
    db: PostgresDatabase | None = getattr(request.app.state, "db", None)
    embedding_client = getattr(request.app.state, "embedding_client", None)
    sync_engine = _get_sync_engine(request)
    report = await sync_engine.sync_context_nodes(
        payload.workspace_path,
        payload.nodes,
    )
    backfilled_embeddings = 0
    if embedding_client is not None and db is not None and db.is_connected:
        backfilled_embeddings = await embedding_client.backfill_missing_embeddings(db)
    report_dict = report.model_dump(mode="json")
    log_sync_operation(
        operation="context",
        workspace_path=payload.workspace_path,
        duration_ms=(time.perf_counter() - start_t) * 1000.0,
        details={
            "nodes_in": len(payload.nodes),
            "backfilled_embeddings": backfilled_embeddings,
            **{
                k: len(v) if isinstance(v, list) else v
                for k, v in report_dict.items()
                if isinstance(v, (int, float, str, bool, list))
            },
        },
    )
    return {
        "status": STATUS_OK,
        "backfilled_embeddings": backfilled_embeddings,
        **report_dict,
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
