"""Фасадный сервис интерактивного визуализатора RAG-графа и Harness² (Layer 2: Visualizer Facade).

Предоставляет единую унифицированную точку входа (Facade Pattern) для построения графовой
топологии Cytoscape.js и выполнения гибридного векторно-полнотекстового поиска по базе знаний,
делегируя низкоуровневые операции `VisualizerTopologyBuilder` и `VisualizerSearchService`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.graph.context_graph import ContextGraphManager
from src.core.services.embeddings import EmbeddingClient
from src.core.visualizer.models import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_SEARCH_TOP_K,
    DEFAULT_SINCE_MINUTES,
    VisualizerGraphSnapshot,
    VisualizerSearchResponse,
)
from src.core.visualizer.search import VisualizerSearchService
from src.core.visualizer.topology import VisualizerTopologyBuilder

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase


class VisualizerGraphService:
    """Единый фасад графовой визуализации топологии и семантического поиска."""

    def __init__(
        self,
        db: PostgresDatabase | None = None,
        embedding_client: EmbeddingClient | None = None,
        context_manager: ContextGraphManager | None = None,
    ) -> None:
        """Инициализирует фасад со связыванием построителя топологии и поискового сервиса.

        Зачем:
        - Изолирует вызывающий слой API (`routes_visualizer.py` и MCP-инструменты) от деталей
          конструирования узлов графа, SQL-запросов и генерации эмбеддингов, предоставляя
          простой и стабильный высокоуровневый контракт.
        """
        self._db: PostgresDatabase | None = db
        self._embedding_client: EmbeddingClient | None = embedding_client
        self._context_manager: ContextGraphManager = context_manager or ContextGraphManager()

        self._topology_builder: VisualizerTopologyBuilder = VisualizerTopologyBuilder(
            db=self._db,
            context_manager=self._context_manager,
        )
        self._search_service: VisualizerSearchService = VisualizerSearchService(
            db=self._db,
            embedding_client=self._embedding_client,
            context_manager=self._context_manager,
        )

    @property
    def topology_builder(self) -> VisualizerTopologyBuilder:
        """Возвращает низкоуровневый построитель графовой топологии Cytoscape.js."""
        return self._topology_builder

    @property
    def search_service(self) -> VisualizerSearchService:
        """Возвращает внутренний сервис гибридного поиска по базе знаний."""
        return self._search_service

    async def build_snapshot(
        self,
        *,
        workspace_path: str | None = None,
        since_minutes: int = DEFAULT_SINCE_MINUTES,
        include_semantic: bool = False,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
    ) -> VisualizerGraphSnapshot:
        """Формирует полный снимок графа знаний и компонентов через `VisualizerTopologyBuilder`."""
        return await self._topology_builder.build_snapshot(
            workspace_path=workspace_path,
            since_minutes=since_minutes,
            include_semantic=include_semantic,
            min_similarity=min_similarity,
        )

    async def search_knowledge(
        self,
        query: str,
        *,
        workspace_path: str | None = None,
        top_k: int = DEFAULT_SEARCH_TOP_K,
    ) -> VisualizerSearchResponse:
        """Выполняет гибридный или лексический поиск по сущностям через `VisualizerSearchService`."""
        return await self._search_service.search(
            query=query,
            workspace_path=workspace_path,
            top_k=top_k,
        )


__all__ = ["VisualizerGraphService"]
