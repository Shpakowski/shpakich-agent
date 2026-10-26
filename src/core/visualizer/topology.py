"""Сборщик графовой топологии Cytoscape.js для визуализатора RAG и Harness (Layer 2: Visualizer Topology).

Отвечает за извлечение узлов и связей из PostgreSQL (проекты, компоненты Harness, долговременная память,
чекпоинты сжатия сессий и онтология `.context/`), построение иерархических составных групп (Compound Nodes),
сборку семантических и структурных рёбер, а также изящную деградацию при оффлайн-работе без базы данных.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.enums import (
    ComponentType,
    VisualizerEdgeKind,
    VisualizerNodeKind,
)
from src.core.graph.context_graph import ContextGraphManager, ContextNode, extract_wiki_links
from src.core.harness.seed import DEFAULT_HARNESS_SEEDS
from src.core.visualizer.models import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_SINCE_MINUTES,
    GROUP_COMPACTIONS_ID,
    GROUP_CONTEXT_ID,
    GROUP_GUARDRAILS_ID,
    GROUP_MEMORIES_ID,
    GROUP_PROMPTS_ID,
    GROUP_SCRIPTS_ID,
    GROUP_SKILLS_ID,
    MAX_RECENT_CHANGES,
    MAX_SEMANTIC_EDGES,
    STATUS_OK,
    VisualizerEdgeData,
    VisualizerEdgeElement,
    VisualizerGraphSnapshot,
    VisualizerNodeData,
    VisualizerNodeElement,
    VisualizerRecentChange,
    VisualizerStatsSummary,
)
from src.core.visualizer.queries import (
    SELECT_AUX_COUNTS_SQL,
    SELECT_COMPACTIONS_SQL,
    SELECT_CONTEXT_NODES_SQL,
    SELECT_HARNESS_COMPONENTS_SQL,
    SELECT_MEMORIES_SQL,
    SELECT_PROJECTS_SQL,
    SELECT_SEMANTIC_SIMILARITY_SQL,
    resolve_component_group_id,
    truncate_preview,
)

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase

logger = logging.getLogger("mcp_coop.core.visualizer.topology")

# Названия родительских групп составных узлов Cytoscape.js (Compound Nodes)
LABEL_GROUP_SKILLS: str = "Skills (Harness)"
LABEL_GROUP_SCRIPTS: str = "Hook Scripts"
LABEL_GROUP_GUARDRAILS: str = "Guardrails & Tool Guidance"
LABEL_GROUP_PROMPTS: str = "System Prompts & Subagents"
LABEL_GROUP_MEMORIES: str = "Error Memories (RAG)"
LABEL_GROUP_COMPACTIONS: str = "Session Compactions (/compact)"
LABEL_GROUP_CONTEXT: str = "Context Graph (.context/)"

# Канонические метки событий журнала недавних изменений
CHANGE_TYPE_CREATED: str = "created"
CHANGE_TYPE_UPDATED: str = "updated"

# Канонические статусы и подтипы узлов графа по умолчанию
DEFAULT_PROJECT_STATUS: str = "active"
DEFAULT_MEMORY_STATUS: str = "active"
DEFAULT_COMPACTION_STATUS: str = "archived"
DEFAULT_CONTEXT_NODE_STATUS: str = "synced"
DEFAULT_MEMORY_SUBTYPE: str = "memory"
DEFAULT_FALLBACK_AGENT: str = "antigravity"
DEFAULT_FALLBACK_SCOPE: str = "global"

# Описание 7 канонических родительских контейнеров графа
GROUP_CONTAINER_DEFS: tuple[tuple[str, str], ...] = (
    (GROUP_SKILLS_ID, LABEL_GROUP_SKILLS),
    (GROUP_SCRIPTS_ID, LABEL_GROUP_SCRIPTS),
    (GROUP_GUARDRAILS_ID, LABEL_GROUP_GUARDRAILS),
    (GROUP_PROMPTS_ID, LABEL_GROUP_PROMPTS),
    (GROUP_MEMORIES_ID, LABEL_GROUP_MEMORIES),
    (GROUP_COMPACTIONS_ID, LABEL_GROUP_COMPACTIONS),
    (GROUP_CONTEXT_ID, LABEL_GROUP_CONTEXT),
)


def _normalize_workspace_path(workspace_path: str | None) -> str | None:
    """Приводит путь рабочей директории к канонической строке с раскрытием симлинков и тильды.

    Зачем:
    - Разные клиенты могут передавать относительные пути, тильду `~` или пути с завершающим слэшем.
    - Для корректного сопоставления с базой данных и файловой системой путь разрешается в абсолютный,
      если целевая директория физически существует на диске хоста.
    """
    if not workspace_path or not str(workspace_path).strip():
        return None
    raw_path = str(workspace_path).strip()
    try:
        resolved = Path(raw_path).expanduser().resolve()
        if resolved.exists():
            return str(resolved)
    except OSError as exc:
        logger.debug("Workspace path resolution fallback for %s: %s", raw_path, exc)
    return raw_path


def _to_utc_datetime(value: Any) -> datetime | None:
    """Преобразует произвольное значение даты (datetime или ISO-строка) в UTC datetime с tzinfo.

    Зачем:
    - Предотвращает ошибки сравнения Python `TypeError: can't compare offset-naive and offset-aware datetimes`,
      приводя любые форматы временных меток из asyncpg или моков к единому формату UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            return None
    return None


def _format_iso(dt_val: datetime | None) -> str | None:
    """Форматирует метку времени в каноническую строку стандарта ISO 8601."""
    if dt_val is None:
        return None
    return dt_val.isoformat()


def _parse_metadata(raw_metadata: Any) -> dict[str, Any]:
    """Безопасно преобразует поле метаданных БД в словарь Python.

    Зачем:
    - В зависимости от версии драйвера asyncpg поле JSONB может приходить как готовый dict,
      строка JSON или None. Функция обеспечивает гарантированный возврат типизированного словаря.
    """
    if isinstance(raw_metadata, dict):
        return raw_metadata
    if isinstance(raw_metadata, str) and raw_metadata.strip():
        try:
            parsed = json.loads(raw_metadata)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError) as exc:
            logger.debug("Invalid metadata JSON payload: %s", exc)
    return {}


def _add_edge(
    edges_by_id: dict[str, VisualizerEdgeElement],
    nodes_by_id: dict[str, VisualizerNodeElement],
    source: str,
    target: str,
    kind: VisualizerEdgeKind,
    weight: float = 1.0,
    label: str | None = None,
) -> None:
    """Добавляет направленное ребро в граф только при гарантированном наличии обоих узлов.

    Зачем:
    - Cytoscape.js выбрасывает фатальные ошибки рендеринга при наличии «висячих» рёбер (dangling edges).
    - Проверка наличия `source` и `target` в `nodes_by_id` обеспечивает топологический инвариант целостности.
    """
    if source not in nodes_by_id or target not in nodes_by_id:
        return
    edge_id = f"{kind.value}:{source}->{target}"
    if edge_id not in edges_by_id:
        edges_by_id[edge_id] = VisualizerEdgeElement(
            data=VisualizerEdgeData(
                id=edge_id,
                source=source,
                target=target,
                kind=kind,
                weight=weight,
                label=label,
            )
        )


class VisualizerTopologyBuilder:
    """Построитель интерактивного графа топологии RAG и компонентов Harness для веб-интерфейса.

    Объединяет данные PostgreSQL (проекты, компоненты, память, чекпоинты сжатия контекста,
    семантические косинусные расстояния pgvector) и локального графа `.context/` на диске
    в единую унифицированную структуру узлов и рёбер Cytoscape.js.
    """

    def __init__(
        self,
        db: PostgresDatabase | None = None,
        context_manager: ContextGraphManager | None = None,
    ) -> None:
        """Инициализирует построитель топологии с опциональным пулом БД и менеджером `.context/`."""
        self._db: PostgresDatabase | None = db
        self._context_manager: ContextGraphManager = context_manager or ContextGraphManager()

    async def build_snapshot(
        self,
        *,
        workspace_path: str | None = None,
        since_minutes: int = DEFAULT_SINCE_MINUTES,
        include_semantic: bool = False,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
    ) -> VisualizerGraphSnapshot:
        """Асинхронно собирает полный снимок графа с групповыми контейнерами, связями и статистикой.

        Алгоритмическая механика:
        1. Нормализация рабочего пространства и расчёт временного порога `since_cutoff`.
        2. Извлечение сущностей из PostgreSQL либо переход в автономный режим при недоступности БД
           (дефолтный сид `DEFAULT_HARNESS_SEEDS` и локальное чтение `.context/`).
        3. Первый проход: формирование всех узлов (контейнеры, стековые теги, проекты, компоненты,
           память, сжатия, онтология).
        4. Второй проход: связывание узлов рёбрами (`HAS_STACK_TAG`, `COMPILED_INTO`, `ORIGINATED_FROM`,
           `CONTEXT_RELATION`, `SEMANTIC_SIMILARITY`).
        5. Расчёт недавних изменений и итоговой агрегированной аналитики `VisualizerStatsSummary`.
        """
        resolved_workspace = _normalize_workspace_path(workspace_path)
        safe_since = max(1, since_minutes)
        since_cutoff = datetime.now(UTC) - timedelta(minutes=safe_since)

        db_connected = False
        project_rows: list[dict[str, Any]] = []
        component_rows: list[dict[str, Any]] = []
        memory_rows: list[dict[str, Any]] = []
        compaction_rows: list[dict[str, Any]] = []
        context_rows: list[dict[str, Any]] = []
        semantic_rows: list[dict[str, Any]] = []
        hook_events_count = 0
        providers_count = 0

        # Попытка выборки актуального состояния из пула соединений PostgreSQL
        if self._db is not None and self._db.is_connected:
            try:
                async with self._db.pool.acquire() as conn:
                    p_records = await conn.fetch(SELECT_PROJECTS_SQL)
                    project_rows = [dict(r) for r in p_records]

                    c_records = await conn.fetch(SELECT_HARNESS_COMPONENTS_SQL)
                    component_rows = [dict(r) for r in c_records]

                    m_records = await conn.fetch(SELECT_MEMORIES_SQL)
                    memory_rows = [dict(r) for r in m_records]

                    comp_records = await conn.fetch(SELECT_COMPACTIONS_SQL, resolved_workspace)
                    compaction_rows = [dict(r) for r in comp_records]

                    ctx_records = await conn.fetch(SELECT_CONTEXT_NODES_SQL, resolved_workspace)
                    context_rows = [dict(r) for r in ctx_records]

                    aux_record = await conn.fetchrow(SELECT_AUX_COUNTS_SQL)
                    if aux_record:
                        hook_events_count = int(aux_record["hook_events_count"] or 0)
                        providers_count = int(aux_record["providers_count"] or 0)

                    if include_semantic:
                        sim_records = await conn.fetch(
                            SELECT_SEMANTIC_SIMILARITY_SQL,
                            resolved_workspace,
                            min_similarity,
                            MAX_SEMANTIC_EDGES,
                        )
                        semantic_rows = [dict(r) for r in sim_records]

                db_connected = True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to fetch topology data from PostgreSQL, falling back to offline mode: %s",
                    exc,
                )
                db_connected = False

        # Оффлайн-фолбэк: генерация базового графа компонентов из неизменяемого сида ядра
        if not db_connected:
            component_rows = [
                {
                    "id": f"seed-{seed.slug}",
                    "slug": seed.slug,
                    "component_type": seed.component_type.value,
                    "scope": seed.scope.value,
                    "stack_tags": list(seed.stack_tags),
                    "title": seed.title,
                    "description": seed.description,
                    "content": seed.content,
                    "metadata": seed.metadata,
                    "has_embedding": False,
                    "status": seed.status.value,
                    "score": seed.score,
                    "usage_count": 0,
                    "source_project_path": None,
                    "version": seed.version,
                    "created_at": None,
                    "updated_at": None,
                }
                for seed in DEFAULT_HARNESS_SEEDS
            ]

        # Дополнение проекта для целевой рабочей директории, если она существует локально
        existing_ws_paths = {str(r.get("workspace_path")) for r in project_rows if r.get("workspace_path")}
        if resolved_workspace and resolved_workspace not in existing_ws_paths:
            ws_path_obj = Path(resolved_workspace)
            if ws_path_obj.is_dir():
                project_rows.append(
                    {
                        "workspace_path": resolved_workspace,
                        "name": ws_path_obj.name or "Workspace",
                        "agent_type": DEFAULT_FALLBACK_AGENT,
                        "stack_tags": [DEFAULT_FALLBACK_SCOPE],
                        "last_synced_at": None,
                        "created_at": None,
                    }
                )

        # Локальное сканирование графа .context/ для обнаружения несинхронизированных узлов и wiki-ссылок
        local_context_nodes: dict[str, ContextNode] = {}
        if resolved_workspace:
            ws_target = Path(resolved_workspace)
            if ws_target.is_dir():
                try:
                    _, local_nodes, _ = self._context_manager.build_and_validate(ws_target)
                    local_context_nodes = local_nodes
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to inspect local .context/ graph in %s: %s", ws_target, exc)

        # Интеграция локальных файлов .context/, отсутствующих в таблице базы данных
        existing_context_ids = {str(r.get("node_id")) for r in context_rows if r.get("node_id")}
        for node_id, ctx_node in local_context_nodes.items():
            if node_id not in existing_context_ids:
                context_rows.append(
                    {
                        "id": f"local-{node_id}",
                        "project_id": None,
                        "workspace_path": resolved_workspace,
                        "relative_path": ctx_node.relative_path,
                        "node_id": node_id,
                        "entity_class": ctx_node.frontmatter.entity_class.value,
                        "content": ctx_node.body_md,
                        "content_hash": "",
                        "base_hash": "",
                        "has_embedding": False,
                        "file_updated_at": None,
                        "updated_at": None,
                    }
                )

        # Словари для накопления уникальных элементов графа
        nodes_by_id: dict[str, VisualizerNodeElement] = {}
        edges_by_id: dict[str, VisualizerEdgeElement] = {}

        # 1. Построение 7 родительских составных групп Cytoscape.js (Compound Nodes)
        for group_id, group_label in GROUP_CONTAINER_DEFS:
            nodes_by_id[group_id] = VisualizerNodeElement(
                data=VisualizerNodeData(
                    id=group_id,
                    label=group_label,
                    kind=VisualizerNodeKind.GROUP_CONTAINER,
                )
            )

        tag_usage_projects: dict[str, list[str]] = {}
        tag_usage_components: dict[str, list[str]] = {}

        def _register_stack_tag(tag: str) -> str:
            """Регистрирует узел тега стека в графе и возвращает очищенное имя тега."""
            cleaned = str(tag).strip().lower()
            if not cleaned:
                return ""
            tag_node_id = f"tag:{cleaned}"
            if tag_node_id not in nodes_by_id:
                nodes_by_id[tag_node_id] = VisualizerNodeElement(
                    data=VisualizerNodeData(
                        id=tag_node_id,
                        label=f"#{cleaned}",
                        kind=VisualizerNodeKind.STACK_TAG,
                        subtype="stack_tag",
                        status=DEFAULT_PROJECT_STATUS,
                        stack_tags=[cleaned],
                    )
                )
            return cleaned

        # 2. Построение узлов зарегистрированных проектов
        for row in project_rows:
            ws_path = str(row.get("workspace_path") or "").strip()
            if not ws_path:
                continue
            project_node_id = f"project:{ws_path}"
            name = str(row.get("name") or Path(ws_path).name or "Workspace")
            agent_type = str(row.get("agent_type") or DEFAULT_FALLBACK_AGENT)
            raw_tags = row.get("stack_tags") or []
            cleaned_tags = [_register_stack_tag(t) for t in raw_tags if str(t).strip()]
            cleaned_tags = [t for t in cleaned_tags if t]
            for t in cleaned_tags:
                tag_usage_projects.setdefault(t, []).append(name)

            dt_created = _to_utc_datetime(row.get("created_at"))
            dt_last_synced = _to_utc_datetime(row.get("last_synced_at"))
            is_new = bool(dt_created and dt_created >= since_cutoff)
            is_updated = bool(not is_new and dt_last_synced and dt_last_synced >= since_cutoff)

            tags_str = ", ".join(f"#{t}" for t in cleaned_tags) if cleaned_tags else "global"
            proj_summary = (
                f"### Project: {name}\n"
                f"- **Workspace Path:** `{ws_path}`\n"
                f"- **Target Agent:** `{agent_type}`\n"
                f"- **Active Stack Tags ({len(cleaned_tags)}):** {tags_str}\n"
                f"- **Context Graph Nodes:** {len(context_rows)}"
            )

            nodes_by_id[project_node_id] = VisualizerNodeElement(
                data=VisualizerNodeData(
                    id=project_node_id,
                    label=name,
                    kind=VisualizerNodeKind.PROJECT,
                    subtype=agent_type,
                    status=DEFAULT_PROJECT_STATUS,
                    stack_tags=cleaned_tags,
                    content_preview=truncate_preview(f"Workspace: {ws_path} | Agent: {agent_type} | Stack: {tags_str}"),
                    content_full=proj_summary,
                    metrics={
                        "workspace_path": ws_path,
                        "agent_type": agent_type,
                        "stack_tags_count": len(cleaned_tags),
                        "context_nodes_count": len(context_rows),
                    },
                    created_at=_format_iso(dt_created),
                    updated_at=_format_iso(dt_last_synced),
                    is_new=is_new,
                    is_updated=is_updated,
                )
            )

        # 3. Построение узлов компонентов Harness
        for row in component_rows:
            slug = str(row.get("slug") or "").strip()
            if not slug:
                continue
            comp_node_id = f"component:{slug}"
            comp_type = str(row.get("component_type") or ComponentType.SYSTEM_PROMPT.value)
            scope = str(row.get("scope") or DEFAULT_FALLBACK_SCOPE)
            title = str(row.get("title") or slug)
            desc = str(row.get("description") or "")
            content = str(row.get("content") or "")
            status = str(row.get("status") or "")
            raw_tags = row.get("stack_tags") or []
            cleaned_tags = [_register_stack_tag(t) for t in raw_tags if str(t).strip()]
            cleaned_tags = [t for t in cleaned_tags if t]
            for t in cleaned_tags:
                tag_usage_components.setdefault(t, []).append(title)

            meta_dict = _parse_metadata(row.get("metadata"))
            execution_mode = meta_dict.get("execution_mode")
            if execution_mode is not None:
                execution_mode = str(execution_mode)

            raw_score = row.get("score")
            score = float(raw_score) if raw_score is not None else 1.0
            usage_count = int(row.get("usage_count") or 0)
            version = int(row.get("version") or 1)
            has_embedding = bool(row.get("has_embedding"))

            dt_created = _to_utc_datetime(row.get("created_at"))
            dt_updated = _to_utc_datetime(row.get("updated_at"))
            is_new = bool(dt_created and dt_created >= since_cutoff)
            is_updated = bool(not is_new and dt_updated and dt_updated >= since_cutoff)

            preview = truncate_preview(desc or content)

            nodes_by_id[comp_node_id] = VisualizerNodeElement(
                data=VisualizerNodeData(
                    id=comp_node_id,
                    label=title,
                    kind=VisualizerNodeKind.HARNESS_COMPONENT,
                    parent=resolve_component_group_id(comp_type),
                    subtype=comp_type,
                    status=status,
                    scope=scope,
                    execution_mode=execution_mode,
                    stack_tags=cleaned_tags,
                    content_preview=preview,
                    content_full=content,
                    metrics={
                        "score": score,
                        "usage_count": usage_count,
                        "version": version,
                        "has_embedding": has_embedding,
                        "slug": slug,
                    },
                    created_at=_format_iso(dt_created),
                    updated_at=_format_iso(dt_updated),
                    is_new=is_new,
                    is_updated=is_updated,
                )
            )

        # 4. Построение узлов долгосрочной памяти решений ошибок (RAG Memories)
        for row in memory_rows:
            mem_id = str(row.get("id") or "").strip()
            if not mem_id:
                continue
            mem_node_id = f"memory:{mem_id}"
            error_symptom = str(row.get("error_symptom") or "")
            solution = str(row.get("solution") or "")
            title = str(row.get("title") or truncate_preview(error_symptom, 50) or f"Memory {mem_id[:8]}")
            scope = str(row.get("scope") or DEFAULT_FALLBACK_SCOPE)
            raw_tags = row.get("stack_tags") or []
            cleaned_tags = [_register_stack_tag(t) for t in raw_tags if str(t).strip()]
            cleaned_tags = [t for t in cleaned_tags if t]
            for t in cleaned_tags:
                tag_usage_components.setdefault(t, []).append(title)

            helpful_count = int(row.get("helpful_count") or 0)
            has_embedding = bool(row.get("has_embedding"))

            dt_created = _to_utc_datetime(row.get("created_at"))
            is_new = bool(dt_created and dt_created >= since_cutoff)

            preview = truncate_preview(error_symptom)
            full_content = f"### Error Symptom\n{error_symptom}\n\n### Solution\n{solution}"

            nodes_by_id[mem_node_id] = VisualizerNodeElement(
                data=VisualizerNodeData(
                    id=mem_node_id,
                    label=title,
                    kind=VisualizerNodeKind.MEMORY,
                    parent=GROUP_MEMORIES_ID,
                    subtype=DEFAULT_MEMORY_SUBTYPE,
                    status=DEFAULT_MEMORY_STATUS,
                    scope=scope,
                    stack_tags=cleaned_tags,
                    content_preview=preview,
                    content_full=full_content,
                    metrics={
                        "helpful_count": helpful_count,
                        "has_embedding": has_embedding,
                    },
                    created_at=_format_iso(dt_created),
                    updated_at=None,
                    is_new=is_new,
                    is_updated=False,
                )
            )

        # 5. Построение узлов архивных чекпоинтов сжатия контекста (/compact)
        for row in compaction_rows:
            comp_id = str(row.get("id") or "").strip()
            if not comp_id:
                continue
            comp_node_id = f"compaction:{comp_id}"
            conversation_id = str(row.get("conversation_id") or "")
            step_idx = int(row.get("step_idx") or 0)
            agent_type = str(row.get("agent_type") or DEFAULT_FALLBACK_AGENT)
            summary_text = str(row.get("summary_text") or "")
            checkpoint_hash = str(row.get("checkpoint_hash") or "")
            has_embedding = bool(row.get("has_embedding"))
            raw_tags = row.get("stack_tags") or []
            cleaned_tags = [_register_stack_tag(t) for t in raw_tags if str(t).strip()]
            cleaned_tags = [t for t in cleaned_tags if t]

            dt_created = _to_utc_datetime(row.get("created_at"))
            is_new = bool(dt_created and dt_created >= since_cutoff)

            label = (
                f"Compaction #{step_idx} ({conversation_id[:8]})"
                if conversation_id
                else f"Compaction #{step_idx}"
            )
            for t in cleaned_tags:
                tag_usage_components.setdefault(t, []).append(label)
            preview = truncate_preview(summary_text)

            nodes_by_id[comp_node_id] = VisualizerNodeElement(
                data=VisualizerNodeData(
                    id=comp_node_id,
                    label=label,
                    kind=VisualizerNodeKind.COMPACTION,
                    parent=GROUP_COMPACTIONS_ID,
                    subtype=agent_type,
                    status=DEFAULT_COMPACTION_STATUS,
                    stack_tags=cleaned_tags,
                    content_preview=preview,
                    content_full=summary_text,
                    metrics={
                        "step_idx": step_idx,
                        "conversation_id": conversation_id,
                        "checkpoint_hash": checkpoint_hash,
                        "has_embedding": has_embedding,
                    },
                    created_at=_format_iso(dt_created),
                    updated_at=None,
                    is_new=is_new,
                    is_updated=False,
                )
            )

        # 6. Построение узлов онтологии Контекстного Графа (.context/)
        for row in context_rows:
            node_id = str(row.get("node_id") or "").strip()
            if not node_id:
                continue
            ctx_node_id = f"context:{node_id}"
            entity_class = str(row.get("entity_class") or "Module")
            content = str(row.get("content") or "")
            rel_path = str(row.get("relative_path") or "")
            has_embedding = bool(row.get("has_embedding"))

            dt_updated = _to_utc_datetime(row.get("updated_at") or row.get("file_updated_at"))
            is_updated = bool(dt_updated and dt_updated >= since_cutoff)

            preview = truncate_preview(content)

            nodes_by_id[ctx_node_id] = VisualizerNodeElement(
                data=VisualizerNodeData(
                    id=ctx_node_id,
                    label=node_id,
                    kind=VisualizerNodeKind.CONTEXT_NODE,
                    parent=GROUP_CONTEXT_ID,
                    subtype=entity_class,
                    status=DEFAULT_CONTEXT_NODE_STATUS,
                    content_preview=preview,
                    content_full=content,
                    metrics={
                        "relative_path": rel_path,
                        "has_embedding": has_embedding,
                    },
                    created_at=None,
                    updated_at=_format_iso(dt_updated),
                    is_new=False,
                    is_updated=is_updated,
                )
            )

        # Обогащение узлов тегов стека аналитической сводкой по проектам и компонентам
        for node_id_key, node_elem in list(nodes_by_id.items()):
            if node_elem.data.kind != VisualizerNodeKind.STACK_TAG:
                continue
            tag_name = node_id_key.removeprefix("tag:")
            proj_list = tag_usage_projects.get(tag_name, [])
            comp_list = tag_usage_components.get(tag_name, [])
            proj_lines = "\n".join(f"- {p}" for p in proj_list) if proj_list else "- None"
            comp_lines = "\n".join(f"- {c}" for c in comp_list) if comp_list else "- None (Project-level stack tag)"
            tag_summary = (
                f"### Stack Tag: `#{tag_name}`\n\n"
                f"#### Registered Projects ({len(proj_list)})\n{proj_lines}\n\n"
                f"#### Linked Components & Knowledge ({len(comp_list)})\n{comp_lines}"
            )
            nodes_by_id[node_id_key] = VisualizerNodeElement(
                data=VisualizerNodeData(
                    id=node_id_key,
                    label=f"#{tag_name}",
                    kind=VisualizerNodeKind.STACK_TAG,
                    subtype="stack_tag",
                    status=DEFAULT_PROJECT_STATUS,
                    stack_tags=[tag_name],
                    content_preview=truncate_preview(
                        f"Projects ({len(proj_list)}), Components ({len(comp_list)})"
                    ),
                    content_full=tag_summary,
                    metrics={
                        "projects_count": len(proj_list),
                        "components_count": len(comp_list),
                    },
                )
            )

        # =========================================================================
        # ВТОРОЙ ПРОХОД: Построение ориентированных связей (Edges) между узлами
        # =========================================================================

        # Связи проектов со стековыми тегами (project -> tag)
        project_tags_by_ws: dict[str, set[str]] = {}
        for row in project_rows:
            ws_path = str(row.get("workspace_path") or "").strip()
            if not ws_path:
                continue
            project_node_id = f"project:{ws_path}"
            raw_tags = row.get("stack_tags") or []
            ws_tag_set: set[str] = { DEFAULT_FALLBACK_SCOPE }
            for tag in raw_tags:
                cleaned_tag = str(tag).strip().lower()
                if cleaned_tag:
                    ws_tag_set.add(cleaned_tag)
                    _add_edge(
                        edges_by_id,
                        nodes_by_id,
                        source=project_node_id,
                        target=f"tag:{cleaned_tag}",
                        kind=VisualizerEdgeKind.HAS_STACK_TAG,
                    )
            project_tags_by_ws[ws_path] = ws_tag_set

        # Связи компонентов: компиляция из тегов (tag -> component), привязка к проекту и происхождение
        for row in component_rows:
            slug = str(row.get("slug") or "").strip()
            if not slug:
                continue
            comp_node_id = f"component:{slug}"
            raw_tags = row.get("stack_tags") or []
            comp_tag_set: set[str] = set()
            for tag in raw_tags:
                cleaned_tag = str(tag).strip().lower()
                if cleaned_tag:
                    comp_tag_set.add(cleaned_tag)
                    _add_edge(
                        edges_by_id,
                        nodes_by_id,
                        source=f"tag:{cleaned_tag}",
                        target=comp_node_id,
                        kind=VisualizerEdgeKind.COMPILED_INTO,
                    )

            for ws_path, ws_tags in project_tags_by_ws.items():
                if comp_tag_set & ws_tags:
                    _add_edge(
                        edges_by_id,
                        nodes_by_id,
                        source=f"project:{ws_path}",
                        target=comp_node_id,
                        kind=VisualizerEdgeKind.COMPILED_INTO,
                    )

            src_project = row.get("source_project_path")
            if src_project:
                proj_node_id = f"project:{src_project}"
                if proj_node_id in nodes_by_id:
                    _add_edge(
                        edges_by_id,
                        nodes_by_id,
                        source=comp_node_id,
                        target=proj_node_id,
                        kind=VisualizerEdgeKind.ORIGINATED_FROM,
                    )

        # Связи долгосрочной памяти: (tag -> memory) и (memory -> project)
        for row in memory_rows:
            mem_id = str(row.get("id") or "").strip()
            if not mem_id:
                continue
            mem_node_id = f"memory:{mem_id}"
            raw_tags = row.get("stack_tags") or []
            for tag in raw_tags:
                cleaned_tag = str(tag).strip().lower()
                if cleaned_tag:
                    _add_edge(
                        edges_by_id,
                        nodes_by_id,
                        source=f"tag:{cleaned_tag}",
                        target=mem_node_id,
                        kind=VisualizerEdgeKind.COMPILED_INTO,
                    )

            src_project = row.get("source_project_path")
            if src_project:
                proj_node_id = f"project:{src_project}"
                if proj_node_id in nodes_by_id:
                    _add_edge(
                        edges_by_id,
                        nodes_by_id,
                        source=mem_node_id,
                        target=proj_node_id,
                        kind=VisualizerEdgeKind.ORIGINATED_FROM,
                    )

        # Связи чекпоинтов сжатия: (tag -> compaction) и (compaction -> project)
        for row in compaction_rows:
            comp_id = str(row.get("id") or "").strip()
            if not comp_id:
                continue
            comp_node_id = f"compaction:{comp_id}"
            raw_tags = row.get("stack_tags") or []
            for tag in raw_tags:
                cleaned_tag = str(tag).strip().lower()
                if cleaned_tag:
                    _add_edge(
                        edges_by_id,
                        nodes_by_id,
                        source=f"tag:{cleaned_tag}",
                        target=comp_node_id,
                        kind=VisualizerEdgeKind.COMPILED_INTO,
                    )

            ws_p = row.get("workspace_path")
            if ws_p:
                proj_node_id = f"project:{ws_p}"
                if proj_node_id in nodes_by_id:
                    _add_edge(
                        edges_by_id,
                        nodes_by_id,
                        source=comp_node_id,
                        target=proj_node_id,
                        kind=VisualizerEdgeKind.ORIGINATED_FROM,
                    )

        # Связи контекстных узлов: (context:index <-> project) и иерархия wiki-ссылок онтологии (.context/)
        has_context_index = "context:index" in nodes_by_id
        for row in context_rows:
            node_id = str(row.get("node_id") or "").strip()
            if not node_id:
                continue
            ctx_node_id = f"context:{node_id}"

            ws_p = row.get("workspace_path") or resolved_workspace
            if ws_p and (not has_context_index or node_id == "index"):
                proj_node_id = f"project:{ws_p}"
                if proj_node_id in nodes_by_id:
                    _add_edge(
                        edges_by_id,
                        nodes_by_id,
                        source=proj_node_id,
                        target=ctx_node_id,
                        kind=VisualizerEdgeKind.ORIGINATED_FROM,
                    )

            # Извлечение исходящих wiki-ссылок [[target-node-id]] из контента и шапки
            wiki_targets: list[str] = extract_wiki_links(row.get("content") or "")
            if node_id in local_context_nodes:
                local_ctx = local_context_nodes[node_id]
                wiki_targets.extend(extract_wiki_links(local_ctx.body_md))
                wiki_targets.extend(extract_wiki_links(local_ctx.frontmatter.depends_on))
                wiki_targets.extend(extract_wiki_links(local_ctx.frontmatter.implements))
                wiki_targets.extend(extract_wiki_links(local_ctx.frontmatter.governed_by))

            for target_id in dict.fromkeys(wiki_targets):
                if node_id != "index" and target_id in ("index", "schema"):
                    continue
                target_node_id = f"context:{target_id}"
                if target_node_id in nodes_by_id and target_node_id != ctx_node_id:
                    _add_edge(
                        edges_by_id,
                        nodes_by_id,
                        source=ctx_node_id,
                        target=target_node_id,
                        kind=VisualizerEdgeKind.CONTEXT_RELATION,
                    )

        # Семантические рёбра близости pgvector (source -> target)
        for row in semantic_rows:
            source_id = str(row.get("source_id") or "")
            target_id = str(row.get("target_id") or "")
            sim = float(row.get("similarity") or 0.0)
            if source_id in nodes_by_id and target_id in nodes_by_id and source_id != target_id:
                _add_edge(
                    edges_by_id,
                    nodes_by_id,
                    source=source_id,
                    target=target_id,
                    kind=VisualizerEdgeKind.SEMANTIC_SIMILARITY,
                    weight=round(sim, 4),
                    label=f"{sim:.2f}",
                )

        # =========================================================================
        # Журнал недавних изменений и агрегированная статистика репозитория
        # =========================================================================
        recent_changes: list[VisualizerRecentChange] = []
        for node_elem in nodes_by_id.values():
            ndata = node_elem.data
            if ndata.is_new:
                recent_changes.append(
                    VisualizerRecentChange(
                        node_id=ndata.id,
                        label=ndata.label,
                        kind=ndata.kind,
                        subtype=ndata.subtype,
                        status=ndata.status,
                        change_type=CHANGE_TYPE_CREATED,
                        timestamp=ndata.created_at or datetime.now(UTC).isoformat(),
                        stack_tags=list(ndata.stack_tags),
                    )
                )
            elif ndata.is_updated:
                recent_changes.append(
                    VisualizerRecentChange(
                        node_id=ndata.id,
                        label=ndata.label,
                        kind=ndata.kind,
                        subtype=ndata.subtype,
                        status=ndata.status,
                        change_type=CHANGE_TYPE_UPDATED,
                        timestamp=ndata.updated_at or datetime.now(UTC).isoformat(),
                        stack_tags=list(ndata.stack_tags),
                    )
                )

        # Сортировка изменений по времени (сначала самые свежие) с ограничением лимита
        recent_changes.sort(key=lambda r: r.timestamp, reverse=True)
        if len(recent_changes) > MAX_RECENT_CHANGES:
            recent_changes = recent_changes[:MAX_RECENT_CHANGES]

        status_breakdown: dict[str, int] = {}
        for node_elem in nodes_by_id.values():
            st = node_elem.data.status
            if st:
                status_breakdown[st] = status_breakdown.get(st, 0) + 1

        stats = VisualizerStatsSummary(
            projects_count=sum(
                1 for n in nodes_by_id.values() if n.data.kind == VisualizerNodeKind.PROJECT
            ),
            harness_components_count=sum(
                1 for n in nodes_by_id.values() if n.data.kind == VisualizerNodeKind.HARNESS_COMPONENT
            ),
            skills_count=sum(
                1
                for n in nodes_by_id.values()
                if n.data.kind == VisualizerNodeKind.HARNESS_COMPONENT
                and n.data.subtype == ComponentType.SKILL.value
            ),
            scripts_count=sum(
                1
                for n in nodes_by_id.values()
                if n.data.kind == VisualizerNodeKind.HARNESS_COMPONENT
                and n.data.subtype == ComponentType.SCRIPT.value
            ),
            guardrails_count=sum(
                1
                for n in nodes_by_id.values()
                if n.data.kind == VisualizerNodeKind.HARNESS_COMPONENT
                and n.data.subtype in (ComponentType.GUARDRAIL.value, ComponentType.TOOL_GUIDANCE.value)
            ),
            prompts_count=sum(
                1
                for n in nodes_by_id.values()
                if n.data.kind == VisualizerNodeKind.HARNESS_COMPONENT
                and n.data.subtype
                in (
                    ComponentType.SYSTEM_PROMPT.value,
                    ComponentType.SUBAGENT.value,
                    ComponentType.MIDDLEWARE.value,
                )
            ),
            memories_count=sum(
                1 for n in nodes_by_id.values() if n.data.kind == VisualizerNodeKind.MEMORY
            ),
            compactions_count=sum(
                1 for n in nodes_by_id.values() if n.data.kind == VisualizerNodeKind.COMPACTION
            ),
            context_nodes_count=sum(
                1 for n in nodes_by_id.values() if n.data.kind == VisualizerNodeKind.CONTEXT_NODE
            ),
            hook_events_count=hook_events_count,
            providers_count=providers_count,
            embedded_nodes_count=sum(
                1 for n in nodes_by_id.values() if n.data.metrics.get("has_embedding") is True
            ),
            new_after_sync_count=sum(1 for n in nodes_by_id.values() if n.data.is_new),
            updated_after_sync_count=sum(1 for n in nodes_by_id.values() if n.data.is_updated),
            status_breakdown=status_breakdown,
            db_connected=db_connected,
        )

        return VisualizerGraphSnapshot(
            status=STATUS_OK,
            workspace_path=resolved_workspace,
            generated_at=datetime.now(UTC).isoformat(),
            since_minutes=safe_since,
            include_semantic=include_semantic,
            min_similarity=min_similarity,
            stats=stats,
            nodes=list(nodes_by_id.values()),
            edges=list(edges_by_id.values()),
            recent_changes=recent_changes,
        )


__all__ = [
    "CHANGE_TYPE_CREATED",
    "CHANGE_TYPE_UPDATED",
    "DEFAULT_COMPACTION_STATUS",
    "DEFAULT_CONTEXT_NODE_STATUS",
    "DEFAULT_FALLBACK_AGENT",
    "DEFAULT_FALLBACK_SCOPE",
    "DEFAULT_MEMORY_STATUS",
    "DEFAULT_MEMORY_SUBTYPE",
    "DEFAULT_PROJECT_STATUS",
    "GROUP_CONTAINER_DEFS",
    "LABEL_GROUP_COMPACTIONS",
    "LABEL_GROUP_CONTEXT",
    "LABEL_GROUP_GUARDRAILS",
    "LABEL_GROUP_MEMORIES",
    "LABEL_GROUP_PROMPTS",
    "LABEL_GROUP_SCRIPTS",
    "LABEL_GROUP_SKILLS",
    "VisualizerTopologyBuilder",
]
