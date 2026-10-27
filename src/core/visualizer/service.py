"""Фасадный сервис интерактивного визуализатора RAG-графа и Harness (Layer 2: Visualizer Facade).

Предоставляет единую унифицированную точку входа (Facade Pattern) для построения графовой
топологии Cytoscape.js и выполнения гибридного векторно-полнотекстового поиска по базе знаний,
делегируя низкоуровневые операции `VisualizerTopologyBuilder` и `VisualizerSearchService`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.graph.context_graph import ContextGraphManager
from src.core.services.embeddings import EmbeddingClient
from src.core.telemetry import (
    compute_progressive_disclosure_savings_pct,
    estimate_tokens,
    extract_tool_target_preview,
)
from src.core.visualizer.models import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_SEARCH_TOP_K,
    DEFAULT_SINCE_MINUTES,
    DEFAULT_TELEMETRY_LIMIT,
    DEFAULT_TOP_COMPONENTS_LIMIT,
    TelemetryComponentRank,
    TelemetryEventRow,
    TelemetrySummaryStats,
    VisualizerGraphSnapshot,
    VisualizerSearchResponse,
    VisualizerTelemetryResponse,
)
from src.core.visualizer.queries import FETCH_RECENT_HOOK_TELEMETRY_SQL, truncate_preview
from src.core.visualizer.search import VisualizerSearchService
from src.core.visualizer.topology import VisualizerTopologyBuilder

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase

logger = logging.getLogger(__name__)

MIN_TELEMETRY_LIMIT: int = 1
MAX_TELEMETRY_LIMIT: int = 500
MIN_TELEMETRY_SINCE_MINUTES: int = 1
MAX_TELEMETRY_SINCE_MINUTES: int = 43200
TARGET_PREVIEW_MAX_LEN: int = 72


def _parse_jsonb_dict(raw_value: Any) -> dict[str, Any]:
    """Безопасно преобразует значение колонки JSONB (`dict` или JSON-строку) в словарь."""
    if isinstance(raw_value, Mapping):
        return dict(raw_value)
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, Mapping):
                return dict(parsed)
        except ValueError:
            return {}
    return {}


def _normalize_optional_workspace(workspace_path: str | None) -> str | None:
    """Нормализует путь рабочей директории проекта для фильтрации SQL-запросов."""
    if not workspace_path or not workspace_path.strip():
        return None
    cleaned = workspace_path.strip()
    try:
        return str(Path(cleaned).expanduser().resolve())
    except OSError:
        return cleaned


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

    async def get_telemetry_analytics(
        self,
        *,
        workspace_path: str | None = None,
        since_minutes: int = DEFAULT_SINCE_MINUTES,
        limit: int = DEFAULT_TELEMETRY_LIMIT,
    ) -> VisualizerTelemetryResponse:
        """Агрегирует живую телеметрию хуков, вызовов MCP и экономику токенов из `hook_events`.

        Как работает:
        1. Выполняет параметризованный запрос `FETCH_RECENT_HOOK_TELEMETRY_SQL` с фильтрацией
           по `workspace_path` и временному окну `since_minutes`.
        2. Извлекает структурированный блок `_telemetry` из колонки `decision` JSONB; для архивных
           записей без `_telemetry` вычисляет детерминированную оценку BPE-токенов через
           `estimate_tokens(payload)` и `estimate_tokens(decision)`.
        3. Формирует сводные KPI (`TelemetrySummaryStats`), ранжированный топ инъектируемых
           правил/скиллов/скриптов (`top_components`) и хронологический поток событий (`events`).
        """
        resolved_workspace = _normalize_optional_workspace(workspace_path)
        clamped_since = max(
            MIN_TELEMETRY_SINCE_MINUTES,
            min(MAX_TELEMETRY_SINCE_MINUTES, int(since_minutes)),
        )
        clamped_limit = max(MIN_TELEMETRY_LIMIT, min(MAX_TELEMETRY_LIMIT, int(limit)))

        if self._db is None or not self._db.is_connected:
            return VisualizerTelemetryResponse(
                workspace_path=resolved_workspace,
                since_minutes=clamped_since,
                summary=TelemetrySummaryStats(),
                top_components=[],
                events=[],
            )

        try:
            async with self._db.pool.acquire() as conn:
                records = await conn.fetch(
                    FETCH_RECENT_HOOK_TELEMETRY_SQL,
                    resolved_workspace,
                    clamped_since,
                    clamped_limit,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to query hook_events telemetry in visualizer: %s", exc)
            return VisualizerTelemetryResponse(
                workspace_path=resolved_workspace,
                since_minutes=clamped_since,
                summary=TelemetrySummaryStats(),
                top_components=[],
                events=[],
            )

        events: list[TelemetryEventRow] = []
        component_acc: dict[tuple[str, str], list[float]] = {}

        def _record_component(kind: str, raw_slug: str, relevance: float = 1.0) -> None:
            clean_slug = raw_slug.strip()
            if not clean_slug:
                return
            key = (kind, clean_slug)
            if key not in component_acc:
                component_acc[key] = [0.0, 0.0]
            component_acc[key][0] += 1.0
            component_acc[key][1] += max(0.0, min(1.0, float(relevance)))

        for row in records:
            row_id = int(row["id"])
            raw_created = row["created_at"]
            created_at_str = (
                raw_created.isoformat()
                if hasattr(raw_created, "isoformat")
                else str(raw_created or "")
            )
            db_event_type = str(row["event_type"] or "hook")
            db_tool_name = str(row["tool_name"] or "")
            payload_map = _parse_jsonb_dict(row["payload"])
            decision_map = _parse_jsonb_dict(row["decision"])

            raw_tel = decision_map.get("_telemetry")
            if isinstance(raw_tel, Mapping):
                tel_map = dict(raw_tel)
                event_type = str(tel_map.get("event_type") or db_event_type)
                agent_type = str(
                    tel_map.get("agent_type")
                    or payload_map.get("agent_type")
                    or "antigravity"
                )
                tool_name = str(
                    tel_map.get("tool_name")
                    or db_tool_name
                    or payload_map.get("tool_name")
                    or ""
                )
                target = str(tel_map.get("target") or "")
                verdict = str(
                    tel_map.get("verdict")
                    or decision_map.get("decision")
                    or "allow"
                )
                duration_ms = round(float(tel_map.get("duration_ms") or 0.0), 1)

                tokens_raw = tel_map.get("tokens")
                tokens_map = dict(tokens_raw) if isinstance(tokens_raw, Mapping) else {}
                user_in_tokens = int(tokens_map.get("user_in") or 0)
                harness_injected_tokens = int(tokens_map.get("harness_injected") or 0)
                output_tokens = int(tokens_map.get("model_out") or 0)
                saved_pct_raw = tokens_map.get("saved_pct")
                saved_pct = (
                    round(float(saved_pct_raw), 1)
                    if saved_pct_raw is not None
                    else compute_progressive_disclosure_savings_pct(harness_injected_tokens)
                )

                rules_labels: list[str] = []
                for r_item in tel_map.get("rules") or []:
                    if isinstance(r_item, Mapping):
                        slug = str(r_item.get("slug") or "").strip()
                        raw_rel = r_item.get("relevance")
                        rel = float(raw_rel) if raw_rel is not None else 1.0
                        if slug:
                            rules_labels.append(slug)
                            _record_component("rule", slug, rel)
                    elif isinstance(r_item, str) and r_item.strip():
                        slug = r_item.strip()
                        rules_labels.append(slug)
                        _record_component("rule", slug, 1.0)

                skills_labels: list[str] = []
                for s_item in tel_map.get("skills") or []:
                    if isinstance(s_item, Mapping):
                        slug = str(s_item.get("slug") or "").strip()
                        raw_rel = s_item.get("relevance")
                        rel = float(raw_rel) if raw_rel is not None else 1.0
                        mode = str(s_item.get("mode") or "").strip()
                        if slug:
                            skills_labels.append(f"{slug}:{mode}" if mode else slug)
                            _record_component("skill", slug, rel)
                    elif isinstance(s_item, str) and s_item.strip():
                        slug = s_item.strip()
                        skills_labels.append(slug)
                        _record_component("skill", slug, 1.0)

                scripts_labels: list[str] = []
                for sc_item in tel_map.get("scripts") or []:
                    if isinstance(sc_item, Mapping):
                        slug = str(sc_item.get("slug") or "").strip()
                        raw_rel = sc_item.get("relevance")
                        rel = float(raw_rel) if raw_rel is not None else 1.0
                    else:
                        slug = str(sc_item).strip()
                        rel = 1.0
                    if slug:
                        scripts_labels.append(slug)
                        _record_component("script", slug, rel)

                summary_line = str(tel_map.get("summary_line") or "")
            else:
                # Фолбэк-оценка токенов для архивных событий без встроенного блока `_telemetry`
                event_type = db_event_type
                agent_type = str(payload_map.get("agent_type") or "antigravity")
                tool_name = str(db_tool_name or payload_map.get("tool_name") or "")
                raw_tool_args = payload_map.get("tool_args")
                tool_args_map = (
                    dict(raw_tool_args) if isinstance(raw_tool_args, Mapping) else {}
                )
                prompt_text = str(payload_map.get("prompt") or "")
                target = (
                    extract_tool_target_preview(tool_name, tool_args_map)
                    if tool_name
                    else truncate_preview(prompt_text, TARGET_PREVIEW_MAX_LEN)
                )
                verdict = str(
                    decision_map.get("decision")
                    or decision_map.get("verdict")
                    or "allow"
                )
                duration_ms = round(float(decision_map.get("duration_ms") or 0.0), 1)

                user_in_tokens = (
                    estimate_tokens(prompt_text)
                    if prompt_text
                    else (
                        estimate_tokens(tool_args_map)
                        if tool_args_map
                        else estimate_tokens(payload_map)
                    )
                )
                sys_msg = decision_map.get("system_message")
                eph_msg = decision_map.get("ephemeral_message")
                harness_injected_tokens = estimate_tokens(sys_msg) + estimate_tokens(eph_msg)
                output_tokens = int(decision_map.get("output_tokens") or 0)
                saved_pct = compute_progressive_disclosure_savings_pct(
                    harness_injected_tokens
                )

                rules_labels = []
                skills_labels = []
                scripts_labels = []
                raw_scripts = decision_map.get("executed_scripts")
                if isinstance(raw_scripts, list):
                    for sc_raw in raw_scripts:
                        slug = str(sc_raw).strip()
                        if slug:
                            scripts_labels.append(slug)
                            _record_component("script", slug, 1.0)

                summary_line = (
                    f"[{event_type}] {verdict.upper()} │ "
                    f"tok: in={user_in_tokens} +inj={harness_injected_tokens} "
                    f"(saved {saved_pct}%)"
                )

            events.append(
                TelemetryEventRow(
                    id=row_id,
                    created_at=created_at_str,
                    event_type=event_type,
                    agent_type=agent_type,
                    tool_name=tool_name,
                    target=target,
                    verdict=verdict,
                    duration_ms=duration_ms,
                    user_in_tokens=user_in_tokens,
                    harness_injected_tokens=harness_injected_tokens,
                    output_tokens=output_tokens,
                    saved_pct=saved_pct,
                    rules=rules_labels,
                    skills=skills_labels,
                    scripts=scripts_labels,
                    summary_line=summary_line,
                )
            )

        total_events = len(events)
        total_user_in = sum(e.user_in_tokens for e in events)
        total_injected = sum(e.harness_injected_tokens for e in events)
        total_out = sum(e.output_tokens for e in events)
        avg_saved = (
            round(sum(e.saved_pct for e in events) / total_events, 1)
            if total_events > 0
            else 100.0
        )
        avg_duration = (
            round(sum(e.duration_ms for e in events) / total_events, 1)
            if total_events > 0
            else 0.0
        )
        denied_count = sum(
            1
            for e in events
            if e.verdict.strip().lower() in ("deny", "block", "denied")
        )
        mcp_calls_count = sum(
            1
            for e in events
            if e.event_type == "mcp-tool" or e.agent_type == "mcp"
        )

        top_components = [
            TelemetryComponentRank(
                slug=slug,
                kind=kind,
                inject_count=int(counts[0]),
                avg_relevance=round(counts[1] / counts[0], 3) if counts[0] > 0 else 1.0,
            )
            for (kind, slug), counts in component_acc.items()
        ]
        top_components.sort(
            key=lambda item: (-item.inject_count, -item.avg_relevance, item.slug)
        )

        summary = TelemetrySummaryStats(
            total_events=total_events,
            total_user_in_tokens=total_user_in,
            total_harness_injected_tokens=total_injected,
            total_output_tokens=total_out,
            avg_saved_pct=avg_saved,
            avg_duration_ms=avg_duration,
            denied_count=denied_count,
            mcp_calls_count=mcp_calls_count,
        )

        return VisualizerTelemetryResponse(
            workspace_path=resolved_workspace,
            since_minutes=clamped_since,
            summary=summary,
            top_components=top_components[:DEFAULT_TOP_COMPONENTS_LIMIT],
            events=events,
        )


__all__ = ["VisualizerGraphService"]
