"""Маршрутизатор веб-интерфейса и API графового визуализатора (Layer 2: FastAPI Visualizer Routes).

Предоставляет HTTP-эндпоинты для отдачи интерактивного Cytoscape.js SPA-дашборда (`GET /ui`),
получения полного снимка топологии графа знаний и Harness² (`GET /api/visualizer/graph`) и
выполнения гибридного векторно-полнотекстового поиска (`GET /api/visualizer/search`).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from src.core.api.deps import _get_visualizer_service
from src.core.ui import render_visualizer_html
from src.core.visualizer.models import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_SEARCH_TOP_K,
    DEFAULT_SINCE_MINUTES,
    VisualizerGraphSnapshot,
    VisualizerSearchResponse,
)

UI_ROUTE_PATH: str = "/ui"
VISUALIZER_GRAPH_ROUTE_PATH: str = "/api/visualizer/graph"
VISUALIZER_SEARCH_ROUTE_PATH: str = "/api/visualizer/search"

MIN_SINCE_MINUTES: int = 1
MAX_SINCE_MINUTES: int = 43200
MIN_SIMILARITY_FLOOR: float = 0.50
MAX_SIMILARITY_CEIL: float = 0.99
MIN_TOP_K: int = 1
MAX_TOP_K: int = 50

visualizer_router = APIRouter()


@visualizer_router.get(UI_ROUTE_PATH, response_class=HTMLResponse, tags=["visualizer"])
async def serve_visualizer_ui() -> HTMLResponse:
    """Рендерит и возвращает автономную SPA-страницу интерактивного визуализатора."""
    return HTMLResponse(content=render_visualizer_html())


@visualizer_router.get(
    VISUALIZER_GRAPH_ROUTE_PATH,
    response_model=VisualizerGraphSnapshot,
    tags=["visualizer"],
)
async def get_visualizer_graph(
    request: Request,
    workspace_path: str | None = Query(default=None),
    since_minutes: int = Query(
        default=DEFAULT_SINCE_MINUTES,
        ge=MIN_SINCE_MINUTES,
        le=MAX_SINCE_MINUTES,
    ),
    include_semantic: bool = Query(default=False),
    min_similarity: float = Query(
        default=DEFAULT_MIN_SIMILARITY,
        ge=MIN_SIMILARITY_FLOOR,
        le=MAX_SIMILARITY_CEIL,
    ),
) -> VisualizerGraphSnapshot:
    """Формирует и возвращает полный топологический снимок графа знаний и компонентов."""
    cleaned_workspace = workspace_path.strip() if workspace_path and workspace_path.strip() else None
    service = _get_visualizer_service(request)
    return await service.build_snapshot(
        workspace_path=cleaned_workspace,
        since_minutes=since_minutes,
        include_semantic=include_semantic,
        min_similarity=min_similarity,
    )


@visualizer_router.get(
    VISUALIZER_SEARCH_ROUTE_PATH,
    response_model=VisualizerSearchResponse,
    tags=["visualizer"],
)
async def search_visualizer_knowledge(
    request: Request,
    q: str = Query(..., min_length=1),
    workspace_path: str | None = Query(default=None),
    top_k: int = Query(
        default=DEFAULT_SEARCH_TOP_K,
        ge=MIN_TOP_K,
        le=MAX_TOP_K,
    ),
) -> VisualizerSearchResponse:
    """Выполняет гибридный поиск по базе знаний, узлам контекста и компонентам Harness²."""
    cleaned_workspace = workspace_path.strip() if workspace_path and workspace_path.strip() else None
    service = _get_visualizer_service(request)
    return await service.search_knowledge(
        q,
        workspace_path=cleaned_workspace,
        top_k=top_k,
    )


__all__ = [
    "MAX_SIMILARITY_CEIL",
    "MAX_SINCE_MINUTES",
    "MAX_TOP_K",
    "MIN_SIMILARITY_FLOOR",
    "MIN_SINCE_MINUTES",
    "MIN_TOP_K",
    "UI_ROUTE_PATH",
    "VISUALIZER_GRAPH_ROUTE_PATH",
    "VISUALIZER_SEARCH_ROUTE_PATH",
    "get_visualizer_graph",
    "search_visualizer_knowledge",
    "serve_visualizer_ui",
    "visualizer_router",
]
