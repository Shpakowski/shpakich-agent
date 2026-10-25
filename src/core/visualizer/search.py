"""Сервис векторно-полнотекстового поиска визуализатора RAG-графа (Layer 2: Visualizer Search).

Выполняет гибридный поиск по базе знаний (проекты, компоненты Harness², память решений ошибок,
сжатия сессий диалогов и онтология `.context/`) с использованием pgvector и PostgreSQL FTS,
а также реализует локальный лексический фолбэк при оффлайн-работе или недоступности базы данных.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.enums import EmbeddingTaskType, VisualizerNodeKind
from src.core.graph.context_graph import CONTEXT_DIR_NAME, ContextGraphManager, ContextNode
from src.core.harness.seed import DEFAULT_HARNESS_SEEDS
from src.core.services.embeddings import EmbeddingClient, format_pgvector
from src.core.visualizer.models import (
    DEFAULT_SEARCH_TOP_K,
    VisualizerSearchHit,
    VisualizerSearchResponse,
)
from src.core.visualizer.queries import (
    SEARCH_KNOWLEDGE_HYBRID_SQL,
    score_lexical_match,
    truncate_preview,
)
from src.core.visualizer.topology import _normalize_workspace_path

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase

logger = logging.getLogger("mcp_coop.core.visualizer.search")

MIN_SEARCH_TOP_K: int = 1
MAX_SEARCH_TOP_K: int = 100
MIN_LEXICAL_SCORE_THRESHOLD: float = 0.01
ROUND_DECIMALS: int = 4


class VisualizerSearchService:
    """Сервис гибридного векторно-полнотекстового и автономного лексического поиска по базе знаний."""

    def __init__(
        self,
        db: PostgresDatabase | None = None,
        embedding_client: EmbeddingClient | None = None,
        context_manager: ContextGraphManager | None = None,
    ) -> None:
        """Инициализирует сервис поиска с опциональным пулом PostgreSQL, клиентом эмбеддингов и менеджером графа."""
        self._db: PostgresDatabase | None = db
        self._embedding_client: EmbeddingClient | None = embedding_client
        self._context_manager: ContextGraphManager = context_manager or ContextGraphManager()

    async def search(
        self,
        query: str,
        *,
        workspace_path: str | None = None,
        top_k: int = DEFAULT_SEARCH_TOP_K,
    ) -> VisualizerSearchResponse:
        """Выполняет гибридный поиск по базе знаний либо откатывается на автономный лексический поиск.

        Алгоритмическая механика:
        1. Валидация запроса: пустые запросы мгновенно возвращают пустой `VisualizerSearchResponse`
           без лишних обращений к сетевым API и пулу PostgreSQL.
        2. Ограничение `top_k` в допустимых пределах `[MIN_SEARCH_TOP_K, MAX_SEARCH_TOP_K]`.
        3. Каноническая нормализация пути рабочего пространства.
        4. Попытка генерации эмбеддинга запроса через `EmbeddingClient` с префиксом `RETRIEVAL_QUERY`.
        5. При активном подключении к PostgreSQL: выполнение `SEARCH_KNOWLEDGE_HYBRID_SQL`
           с объединением косинусного расстояния векторов (`pgvector`), полнотекстового ранжирования (`ts_rank_cd`)
           и триграммного сходства (`pg_trgm`).
        6. При недоступности БД, исключениях в SQL или возврате 0 строк: деградация до оффлайн-поиска
           по неизменяемым сидам `DEFAULT_HARNESS_SEEDS` и локальным файлам `.context/`.
        """
        if not query or not query.strip():
            return VisualizerSearchResponse(query="", embedding_used=False, hits=[])

        cleaned_query = query.strip()
        clamped_top_k = max(MIN_SEARCH_TOP_K, min(MAX_SEARCH_TOP_K, int(top_k)))
        resolved_workspace = _normalize_workspace_path(workspace_path)

        # Попытка генерации вектора семантического запроса через внешний API эмбеддингов
        vector_str: str | None = None
        if self._embedding_client is not None:
            try:
                raw_vec = await self._embedding_client.embed_text(
                    cleaned_query,
                    task_type=EmbeddingTaskType.RETRIEVAL_QUERY,
                )
                if raw_vec:
                    vector_str = format_pgvector(raw_vec)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to generate embedding for visualizer search: %s", exc)

        hits: list[VisualizerSearchHit] = []
        db_succeeded = False

        # Выполнение гибридного SQL-запроса в пуле PostgreSQL
        if self._db is not None and self._db.is_connected:
            try:
                async with self._db.pool.acquire() as conn:
                    records = await conn.fetch(
                        SEARCH_KNOWLEDGE_HYBRID_SQL,
                        cleaned_query,
                        resolved_workspace,
                        vector_str,
                        clamped_top_k,
                    )
                    hits = [
                        VisualizerSearchHit(
                            node_id=str(r["node_id"]),
                            label=str(r["label"]),
                            kind=VisualizerNodeKind(str(r["kind"])),
                            subtype=str(r["subtype"]) if r["subtype"] is not None else None,
                            status=str(r["status"]) if r["status"] is not None else None,
                            score=round(float(r["score"]), ROUND_DECIMALS),
                            vec_score=round(float(r["vec_score"]), ROUND_DECIMALS),
                            fts_score=round(float(r["fts_score"]), ROUND_DECIMALS),
                            trgm_score=round(float(r["trgm_score"]), ROUND_DECIMALS),
                            snippet=truncate_preview(r.get("snippet_source")),
                        )
                        for r in records
                    ]
                db_succeeded = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Database hybrid search query failed, using offline fallback: %s", exc)
                db_succeeded = False
                hits = []

        # Оффлайн-фолбэк: переход на чистое лексическое сопоставление при сбое БД или отсутствии строк
        if not db_succeeded or len(hits) == 0:
            lexical_hits = self._search_lexical_fallback(
                cleaned_query=cleaned_query,
                workspace_path=resolved_workspace,
                top_k=clamped_top_k,
            )
            if not db_succeeded or (len(hits) == 0 and lexical_hits):
                hits = lexical_hits
                embedding_used = False
            else:
                embedding_used = bool(vector_str is not None)
        else:
            embedding_used = bool(vector_str is not None)

        return VisualizerSearchResponse(
            query=cleaned_query,
            embedding_used=embedding_used,
            hits=hits,
        )

    def _search_lexical_fallback(
        self,
        cleaned_query: str,
        workspace_path: str | None,
        top_k: int,
    ) -> list[VisualizerSearchHit]:
        """Выполняет локальное лексическое сопоставление по неизменяемым сидам и файлам `.context/`.

        Зачем:
        - Обеспечивает мгновенный отклик поиска и автономность агента в оффлайн-режиме без работающей БД
          или до выполнения первой синхронизации контекста проекта в PostgreSQL.
        """
        candidates: list[VisualizerSearchHit] = []

        # 1. Поиск по базовым компонентам Harness² (сид по умолчанию)
        for seed in DEFAULT_HARNESS_SEEDS:
            title = seed.title
            body = f"{seed.description or ''} {seed.content or ''}".strip()
            total_score, fts_score, trgm_score = score_lexical_match(cleaned_query, title, body)
            if total_score > MIN_LEXICAL_SCORE_THRESHOLD:
                candidates.append(
                    VisualizerSearchHit(
                        node_id=f"component:{seed.slug}",
                        label=seed.title,
                        kind=VisualizerNodeKind.HARNESS_COMPONENT,
                        subtype=seed.component_type.value,
                        status=seed.status.value,
                        score=round(total_score, ROUND_DECIMALS),
                        vec_score=0.0,
                        fts_score=round(fts_score, ROUND_DECIMALS),
                        trgm_score=round(trgm_score, ROUND_DECIMALS),
                        snippet=truncate_preview(seed.description or seed.content),
                    )
                )

        # 2. Поиск по локальным Markdown-узлам онтологии .context/
        local_nodes: dict[str, ContextNode] = {}
        ws_target: Path | None = None
        if workspace_path:
            p = Path(workspace_path)
            if p.is_dir():
                ws_target = p
        elif self._context_manager._workspace_path and self._context_manager._workspace_path.is_dir():
            ws_target = self._context_manager._workspace_path
        else:
            cwd = Path.cwd()
            if (cwd / CONTEXT_DIR_NAME).is_dir():
                ws_target = cwd

        if ws_target is not None:
            try:
                _, local_nodes, _ = self._context_manager.build_and_validate(ws_target)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Failed to read local context graph for lexical search in %s: %s",
                    ws_target,
                    exc,
                )

        for node in local_nodes.values():
            title = node.frontmatter.name or node.frontmatter.id
            body = node.body_md
            total_score, fts_score, trgm_score = score_lexical_match(cleaned_query, title, body)
            if total_score > MIN_LEXICAL_SCORE_THRESHOLD:
                candidates.append(
                    VisualizerSearchHit(
                        node_id=f"context:{node.frontmatter.id}",
                        label=title,
                        kind=VisualizerNodeKind.CONTEXT_NODE,
                        subtype=node.frontmatter.entity_class.value,
                        status=node.frontmatter.status.value,
                        score=round(total_score, ROUND_DECIMALS),
                        vec_score=0.0,
                        fts_score=round(fts_score, ROUND_DECIMALS),
                        trgm_score=round(trgm_score, ROUND_DECIMALS),
                        snippet=truncate_preview(node.body_md),
                    )
                )

        # Сортировка по убыванию релевантности и отсечение top_k
        candidates.sort(key=lambda hit: hit.score, reverse=True)
        return candidates[:top_k]


__all__ = [
    "MAX_SEARCH_TOP_K",
    "MIN_LEXICAL_SCORE_THRESHOLD",
    "MIN_SEARCH_TOP_K",
    "ROUND_DECIMALS",
    "VisualizerSearchService",
]
