"""Комплексный набор юнит- и интеграционных тестов интерактивного визуализатора графа RAG и Harness (SPEC-004).

Проверяет 4 ключевых архитектурных контура:
1. Автономный режим сборщика топологии (Offline Fallback) с локальной онтологией .context/ и сидами DEFAULT_HARNESS_SEEDS.
2. Сборку снимка графа при активной БД с вычислением дельты изменений (is_new / is_updated) и рёбер семантического подобия.
3. Сервис гибридного поиска (VisualizerSearchService) с векторно-полнотекстовым ранжированием и лексическим фолбэком.
4. HTTP-эндпоинты FastAPI (GET /ui, GET /api/visualizer/graph, GET /api/visualizer/search) и целостность SPA-шаблона.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from src.core.db.postgres import PostgresDatabase
from src.core.enums import (
    AgentType,
    ComponentStatus,
    ComponentType,
    EmbeddingTaskType,
    ScopeType,
    VisualizerEdgeKind,
    VisualizerNodeKind,
)
from src.core.harness.seed import DEFAULT_HARNESS_SEEDS
from src.core.main import create_app
from src.core.services.embeddings import EmbeddingClient
from src.core.visualizer.models import (
    GROUP_COMPACTIONS_ID,
    GROUP_CONTEXT_ID,
    GROUP_GUARDRAILS_ID,
    GROUP_MEMORIES_ID,
    GROUP_PROMPTS_ID,
    GROUP_SCRIPTS_ID,
    GROUP_SKILLS_ID,
    STATUS_OK,
    VisualizerGraphSnapshot,
    VisualizerSearchResponse,
)
from src.core.visualizer.queries import (
    SEARCH_KNOWLEDGE_HYBRID_SQL,
    SELECT_AUX_COUNTS_SQL,
    SELECT_COMPACTIONS_SQL,
    SELECT_CONTEXT_NODES_SQL,
    SELECT_HARNESS_COMPONENTS_SQL,
    SELECT_MEMORIES_SQL,
    SELECT_PROJECTS_SQL,
    SELECT_SEMANTIC_SIMILARITY_SQL,
    resolve_component_group_id,
    score_lexical_match,
    truncate_preview,
)
from src.core.visualizer.search import VisualizerSearchService
from src.core.visualizer.service import VisualizerGraphService
from src.core.visualizer.topology import (
    CHANGE_TYPE_CREATED,
    CHANGE_TYPE_UPDATED,
    VisualizerTopologyBuilder,
)

# Именованные константы мок-сущностей и проектных данных
FAKE_PROJECT_ID: str = "proj-00000000-0000-0000-0000-000000000001"
FAKE_WORKSPACE_PATH: str = "/fake/visualizer/workspace"
FAKE_PROJECT_NAME: str = "Visualizer Mock Project"

# Именованные константы компонентов Harness
FAKE_SCRIPT_SLUG: str = "pre-tool-guard-script"
FAKE_SCRIPT_TITLE: str = "Pre Tool Guard Script"
FAKE_SCRIPT_CONTENT: str = "echo checking guard script"
FAKE_EXECUTION_MODE_SCRIPT: str = "script"

FAKE_NEW_SKILL_SLUG: str = "fastapi-route-generator"
FAKE_NEW_SKILL_TITLE: str = "FastAPI Route Generator"
FAKE_NEW_SKILL_CONTENT: str = "Instructions for FastAPI routes generation"

FAKE_UPDATED_PROMPT_SLUG: str = "core-system-prompt"
FAKE_UPDATED_PROMPT_TITLE: str = "Core System Prompt"
FAKE_UPDATED_PROMPT_CONTENT: str = "System instructions for core agentic factory"

# Именованные константы записей памяти и сжатий контекста
FAKE_MEMORY_ID: str = "mem-11111111-1111-1111-1111-111111111111"
FAKE_MEMORY_TITLE: str = "Redis connection pool exhaustion"
FAKE_MEMORY_ERROR: str = "ConnectionPoolError: Too many open connections"
FAKE_MEMORY_SOLUTION: str = "Increase pool max_connections or recycle idle connections"

FAKE_COMPACTION_ID: str = "comp-22222222-2222-2222-2222-222222222222"
FAKE_CONVERSATION_ID: str = "conv-abcdef12-3456-7890"
FAKE_COMPACTION_SUMMARY: str = "Summary of conversation: visualizer architecture and Cytoscape"
FAKE_CHECKPOINT_HASH: str = "ckpt_hash_deadbeef1234"

# Именованные константы контекстных узлов онтологии
FAKE_CONTEXT_NODE_ALPHA: str = "mod-visualizer-engine"
FAKE_CONTEXT_NODE_BETA: str = "contract-visualizer-api"

# Счетчики вспомогательной статистики и параметры семантического сходства
FAKE_HOOK_EVENTS_COUNT: int = 7
FAKE_PROVIDERS_COUNT: int = 2
FAKE_EMBEDDING_DIMENSION: int = 768
FAKE_SEMANTIC_SIMILARITY_SCORE: float = 0.885

# Параметры запросов и лимиты
TEST_SINCE_MINUTES: int = 60
TEST_TOP_K: int = 5
TEST_MIN_SIMILARITY: float = 0.75
SEARCH_QUERY_HARNESS: str = "harness"
SEARCH_QUERY_SAMPLE: str = "generator"

# Метки для верификации отдачи монолитного HTML визуализатора
CYTOSCAPE_CANVAS_SELECTOR: str = "#cy"
CANVAS_CLASS_NAME: str = "CoopGraphCanvas"
APP_CLASS_NAME: str = "VisualizerApp"
PLACEHOLDER_STYLES: str = "__STYLES_CSS__"
PLACEHOLDER_CANVAS: str = "__GRAPH_CANVAS_JS__"
PLACEHOLDER_APP: str = "__APP_JS__"
HTML_CONTENT_TYPE: str = "text/html"


class FakeAsyncContextManager:
    """Асинхронный контекстный менеджер для эмуляции метода acquire() пула соединений."""

    def __init__(self, target: Any) -> None:
        self._target: Any = target

    async def __aenter__(self) -> Any:
        return self._target

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass


class FakeConnection:
    """Имитатор соединения asyncpg.Connection с диспетчеризацией SQL-запросов визуализатора."""

    def __init__(
        self,
        *,
        projects: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
        memories: list[dict[str, Any]] | None = None,
        compactions: list[dict[str, Any]] | None = None,
        context_nodes: list[dict[str, Any]] | None = None,
        aux_counts: dict[str, Any] | None = None,
        semantic_rows: list[dict[str, Any]] | None = None,
        search_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.projects: list[dict[str, Any]] = projects or []
        self.components: list[dict[str, Any]] = components or []
        self.memories: list[dict[str, Any]] = memories or []
        self.compactions: list[dict[str, Any]] = compactions or []
        self.context_nodes: list[dict[str, Any]] = context_nodes or []
        self.aux_counts: dict[str, Any] = aux_counts or {
            "hook_events_count": 0,
            "providers_count": 0,
        }
        self.semantic_rows: list[dict[str, Any]] = semantic_rows or []
        self.search_rows: list[dict[str, Any]] = search_rows or []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Маршрутизирует запросы на выборку данных графа и гибридного поиска."""
        # 1. Запрос списка зарегистрированных проектов
        if query == SELECT_PROJECTS_SQL or ("FROM projects" in query and "COUNT" not in query):
            return [dict(r) for r in self.projects]

        # 2. Запрос семантических рёбер схожести pgvector
        if query == SELECT_SEMANTIC_SIMILARITY_SQL or (
            "similarity" in query and "embedded_entities" in query
        ):
            return [dict(r) for r in self.semantic_rows]

        # 3. Запрос гибридного векторно-полнотекстового поиска
        if query == SEARCH_KNOWLEDGE_HYBRID_SQL or "candidates" in query:
            return [dict(r) for r in self.search_rows]

        # 4. Запрос компонентов каталога Harness
        if query == SELECT_HARNESS_COMPONENTS_SQL or (
            "FROM harness_components" in query and "candidates" not in query
        ):
            return [dict(r) for r in self.components]

        # 5. Запрос записей долгосрочной памяти решений ошибок
        if query == SELECT_MEMORIES_SQL or (
            "FROM memories" in query and "candidates" not in query
        ):
            return [dict(r) for r in self.memories]

        # 6. Запрос архивных чекпоинтов сжатия диалогов (/compact)
        if query == SELECT_COMPACTIONS_SQL or (
            "FROM session_compactions" in query and "candidates" not in query
        ):
            return [dict(r) for r in self.compactions]

        # 7. Запрос узлов архитектурного графа знаний (.context/)
        if query == SELECT_CONTEXT_NODES_SQL or (
            "FROM project_context_nodes" in query and "candidates" not in query
        ):
            return [dict(r) for r in self.context_nodes]

        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Возвращает агрегированные счетчики вспомогательных таблиц."""
        if query == SELECT_AUX_COUNTS_SQL or (
            "hook_events" in query and "decision_providers" in query
        ):
            return dict(self.aux_counts)
        return None


class FakePool:
    """Имитатор пула соединений asyncpg.Pool для изоляции юнит-тестов."""

    def __init__(self, conn: FakeConnection) -> None:
        self._conn: FakeConnection = conn

    def acquire(self) -> FakeAsyncContextManager:
        """Возвращает асинхронный контекстный менеджер соединения."""
        return FakeAsyncContextManager(self._conn)


class FakePostgresDatabase:
    """Имитатор интерфейса PostgresDatabase с активным статусом подключения."""

    def __init__(self, conn: FakeConnection) -> None:
        self.is_connected: bool = True
        self.pool: FakePool = FakePool(conn)


class FakeEmbeddingClient:
    """Имитатор HTTP-клиента эмбеддингов для верификации гибридного поиска."""

    def __init__(self, dimension: int = FAKE_EMBEDDING_DIMENSION) -> None:
        self.dimension: int = dimension
        self.last_query: str | None = None

    async def embed_text(
        self,
        text: str,
        *,
        task_type: EmbeddingTaskType = EmbeddingTaskType.RETRIEVAL_QUERY,
    ) -> list[float]:
        """Возвращает синтетический вектор фиксированной размерности."""
        self.last_query = text
        return [0.01] * self.dimension


@pytest.mark.asyncio
async def test_topology_offline_fallback_with_local_context() -> None:
    """Контур 1: Автономный режим построителя топологии (Offline Fallback) с локальной онтологией .context/.

    Бизнес-контракт:
    - При отсутствии подключения к PostgreSQL построитель топологии обязан вернуть базовый снимок:
      `stats.db_connected == False`, `stats.harness_components_count >= len(DEFAULT_HARNESS_SEEDS)`,
      `stats.context_nodes_count > 0` (за счёт чтения `.context/` текущей папки).
    - Составные группы-контейнеры (`GROUP_SKILLS_ID`, `GROUP_CONTEXT_ID` и др.) обязаны присутствовать в графе,
      если у них есть дочерние узлы.
    - Рёбра онтологии `CONTEXT_RELATION` должны связывать узлы по wiki-ссылкам без висячих рёбер (dangling edges).
    """
    builder = VisualizerTopologyBuilder(db=None)
    current_workspace = str(Path.cwd())
    snapshot = await builder.build_snapshot(workspace_path=current_workspace)

    # Базовая валидация снимка и статуса подключения
    assert snapshot.status == STATUS_OK
    assert snapshot.stats.db_connected is False
    assert snapshot.stats.harness_components_count >= len(DEFAULT_HARNESS_SEEDS)
    assert snapshot.stats.context_nodes_count > 0

    node_ids: set[str] = {n.data.id for n in snapshot.nodes}
    parents_with_children: set[str] = {
        n.data.parent for n in snapshot.nodes if n.data.parent is not None
    }

    # Проверка обязательного наличия родительских контейнеров при наличии дочерних элементов
    assert GROUP_SKILLS_ID in parents_with_children
    assert GROUP_SKILLS_ID in node_ids
    assert GROUP_CONTEXT_ID in parents_with_children
    assert GROUP_CONTEXT_ID in node_ids
    assert GROUP_GUARDRAILS_ID in parents_with_children
    assert GROUP_GUARDRAILS_ID in node_ids

    for parent_id in parents_with_children:
        assert parent_id in node_ids, f"Parent container {parent_id} is missing from nodes"

    # Проверка наличия рёбер связей архитектурного контекста и инварианта отсутствия висячих связей
    context_relation_edges = [
        e for e in snapshot.edges if e.data.kind == VisualizerEdgeKind.CONTEXT_RELATION
    ]
    assert len(context_relation_edges) > 0, "Expected CONTEXT_RELATION edges from local .context/ ontology"

    for edge in snapshot.edges:
        assert edge.data.source in node_ids, f"Dangling edge source detected: {edge.data.source}"
        assert edge.data.target in node_ids, f"Dangling edge target detected: {edge.data.target}"


@pytest.mark.asyncio
async def test_topology_connected_db_delta_and_semantic_edges() -> None:
    """Контур 2: Построение снимка топологии при подключенной БД с дельтой изменений и семантическими рёбрами.

    Бизнес-контракт:
    - Проверяет корректное разделение флагов `is_new` (создано менее 60 минут назад)
      и `is_updated` (создано ранее, но обновлено менее 60 минут назад).
    - Компонент типа `ComponentType.SCRIPT` с метаданными `execution_mode: script` обязан
      группироваться в родительский контейнер `GROUP_SCRIPTS_ID` и учитываться в `scripts_count`.
    - Агрегированные счетчики `hook_events_count` и `providers_count` извлекаются из `SELECT_AUX_COUNTS_SQL`.
    - При `include_semantic=True` формируются рёбра `VisualizerEdgeKind.SEMANTIC_SIMILARITY`.
    - Журнал недавних изменений `recent_changes` наполняется новыми и обновлёнными узлами.
    """
    now = datetime.now(UTC)
    time_5m_ago = (now - timedelta(minutes=5)).isoformat()
    time_10m_ago = (now - timedelta(minutes=10)).isoformat()
    time_20m_ago = (now - timedelta(minutes=20)).isoformat()
    time_3h_ago = (now - timedelta(hours=3)).isoformat()

    projects = [
        {
            "id": FAKE_PROJECT_ID,
            "workspace_path": FAKE_WORKSPACE_PATH,
            "name": FAKE_PROJECT_NAME,
            "agent_type": AgentType.ANTIGRAVITY.value,
            "stack_tags": ["python", "fastapi"],
            "profile_overrides": {},
            "last_synced_at": time_10m_ago,
            "created_at": time_3h_ago,
        }
    ]

    components = [
        # Новый компонент (создан 5 минут назад -> is_new=True, is_updated=False)
        {
            "id": "comp-uuid-new-1",
            "slug": FAKE_NEW_SKILL_SLUG,
            "component_type": ComponentType.SKILL.value,
            "scope": ScopeType.DOMAIN.value,
            "stack_tags": ["python", "fastapi"],
            "title": FAKE_NEW_SKILL_TITLE,
            "description": "Newly created skill",
            "content": FAKE_NEW_SKILL_CONTENT,
            "content_hash": "hash_new_skill",
            "metadata": {"tags": ["generator"]},
            "has_embedding": True,
            "status": ComponentStatus.COMPOSED.value,
            "score": 0.95,
            "usage_count": 5,
            "source_project_path": FAKE_WORKSPACE_PATH,
            "version": 1,
            "created_at": time_5m_ago,
            "updated_at": time_5m_ago,
        },
        # Обновлённый скрипт хука (создан 3 часа назад, обновлён 10 минут назад -> is_updated=True)
        {
            "id": "comp-uuid-script-2",
            "slug": FAKE_SCRIPT_SLUG,
            "component_type": ComponentType.SCRIPT.value,
            "scope": ScopeType.GLOBAL.value,
            "stack_tags": ["python"],
            "title": FAKE_SCRIPT_TITLE,
            "description": "Script component for tool auditing",
            "content": FAKE_SCRIPT_CONTENT,
            "content_hash": "hash_script_guard",
            "metadata": {"execution_mode": FAKE_EXECUTION_MODE_SCRIPT},
            "has_embedding": True,
            "status": ComponentStatus.COMPOSED.value,
            "score": 0.88,
            "usage_count": 12,
            "source_project_path": FAKE_WORKSPACE_PATH,
            "version": 2,
            "created_at": time_3h_ago,
            "updated_at": time_10m_ago,
        },
    ]

    memories = [
        {
            "id": FAKE_MEMORY_ID,
            "title": FAKE_MEMORY_TITLE,
            "error_symptom": FAKE_MEMORY_ERROR,
            "solution": FAKE_MEMORY_SOLUTION,
            "scope": ScopeType.GLOBAL.value,
            "stack_tags": ["python", "redis"],
            "has_embedding": True,
            "helpful_count": 4,
            "source_project_path": FAKE_WORKSPACE_PATH,
            "created_at": time_20m_ago,
        }
    ]

    compactions = [
        {
            "id": FAKE_COMPACTION_ID,
            "conversation_id": FAKE_CONVERSATION_ID,
            "workspace_path": FAKE_WORKSPACE_PATH,
            "agent_type": AgentType.ANTIGRAVITY.value,
            "step_idx": 3,
            "checkpoint_hash": FAKE_CHECKPOINT_HASH,
            "summary_text": FAKE_COMPACTION_SUMMARY,
            "stack_tags": ["python", "fastapi"],
            "metadata": {},
            "has_embedding": True,
            "created_at": time_20m_ago,
        }
    ]

    context_nodes = [
        {
            "id": "ctx-uuid-1",
            "project_id": FAKE_PROJECT_ID,
            "workspace_path": FAKE_WORKSPACE_PATH,
            "relative_path": ".context/entities/modules/mod-alpha.md",
            "node_id": FAKE_CONTEXT_NODE_ALPHA,
            "entity_class": "Module",
            "content": f"Module Alpha references [[{FAKE_CONTEXT_NODE_BETA}]]",
            "content_hash": "chash_alpha",
            "base_hash": "bhash_alpha",
            "has_embedding": True,
            "file_updated_at": time_10m_ago,
            "updated_at": time_10m_ago,
        },
        {
            "id": "ctx-uuid-2",
            "project_id": FAKE_PROJECT_ID,
            "workspace_path": FAKE_WORKSPACE_PATH,
            "relative_path": ".context/entities/contracts/contract-beta.md",
            "node_id": FAKE_CONTEXT_NODE_BETA,
            "entity_class": "Contract",
            "content": "Contract Beta implementation details",
            "content_hash": "chash_beta",
            "base_hash": "bhash_beta",
            "has_embedding": True,
            "file_updated_at": time_10m_ago,
            "updated_at": time_10m_ago,
        },
    ]

    aux_counts = {
        "hook_events_count": FAKE_HOOK_EVENTS_COUNT,
        "providers_count": FAKE_PROVIDERS_COUNT,
    }

    semantic_rows = [
        {
            "source_id": f"component:{FAKE_NEW_SKILL_SLUG}",
            "target_id": f"component:{FAKE_SCRIPT_SLUG}",
            "similarity": FAKE_SEMANTIC_SIMILARITY_SCORE,
        }
    ]

    fake_conn = FakeConnection(
        projects=projects,
        components=components,
        memories=memories,
        compactions=compactions,
        context_nodes=context_nodes,
        aux_counts=aux_counts,
        semantic_rows=semantic_rows,
    )
    fake_db = cast(PostgresDatabase, FakePostgresDatabase(fake_conn))
    builder = VisualizerTopologyBuilder(db=fake_db)

    snapshot = await builder.build_snapshot(
        workspace_path=FAKE_WORKSPACE_PATH,
        since_minutes=TEST_SINCE_MINUTES,
        include_semantic=True,
        min_similarity=TEST_MIN_SIMILARITY,
    )

    # Верификация базовых статусов и агрегированной статистики
    assert snapshot.status == STATUS_OK
    assert snapshot.stats.db_connected is True
    assert snapshot.stats.new_after_sync_count >= 1
    assert snapshot.stats.updated_after_sync_count >= 1
    assert snapshot.stats.scripts_count >= 1
    assert snapshot.stats.hook_events_count == FAKE_HOOK_EVENTS_COUNT
    assert snapshot.stats.providers_count == FAKE_PROVIDERS_COUNT

    # Верификация журнала недавних изменений
    assert len(snapshot.recent_changes) >= 2
    assert any(rc.change_type == CHANGE_TYPE_CREATED for rc in snapshot.recent_changes)
    assert any(rc.change_type == CHANGE_TYPE_UPDATED for rc in snapshot.recent_changes)

    # Верификация наличия семантических рёбер подобия pgvector
    semantic_edges = [
        e for e in snapshot.edges if e.data.kind == VisualizerEdgeKind.SEMANTIC_SIMILARITY
    ]
    assert len(semantic_edges) >= 1
    assert semantic_edges[0].data.source == f"component:{FAKE_NEW_SKILL_SLUG}"
    assert semantic_edges[0].data.target == f"component:{FAKE_SCRIPT_SLUG}"
    assert semantic_edges[0].data.weight == round(FAKE_SEMANTIC_SIMILARITY_SCORE, 4)

    # Верификация назначения скрипта в родительскую группу GROUP_SCRIPTS_ID
    script_node = next(n for n in snapshot.nodes if n.data.id == f"component:{FAKE_SCRIPT_SLUG}")
    assert script_node.data.parent == GROUP_SCRIPTS_ID
    assert script_node.data.execution_mode == FAKE_EXECUTION_MODE_SCRIPT
    assert script_node.data.is_updated is True
    assert script_node.data.is_new is False

    # Верификация флага нового компонента
    new_comp_node = next(n for n in snapshot.nodes if n.data.id == f"component:{FAKE_NEW_SKILL_SLUG}")
    assert new_comp_node.data.is_new is True
    assert new_comp_node.data.is_updated is False

    # Верификация группировки записей памяти и сжатия сессии
    memory_node = next(n for n in snapshot.nodes if n.data.id == f"memory:{FAKE_MEMORY_ID}")
    assert memory_node.data.parent == GROUP_MEMORIES_ID

    compaction_node = next(n for n in snapshot.nodes if n.data.id == f"compaction:{FAKE_COMPACTION_ID}")
    assert compaction_node.data.parent == GROUP_COMPACTIONS_ID

    # Инвариант отсутствия висячих рёбер
    all_node_ids = {n.data.id for n in snapshot.nodes}
    for edge in snapshot.edges:
        assert edge.data.source in all_node_ids, f"Dangling edge source: {edge.data.source}"
        assert edge.data.target in all_node_ids, f"Dangling edge target: {edge.data.target}"


@pytest.mark.asyncio
async def test_search_service_hybrid_and_lexical_fallback() -> None:
    """Контур 3: Сервис гибридного векторно-полнотекстового поиска и автономного лексического фолбэка.

    Бизнес-контракт:
    - При наличии подключения к БД и EmbeddingClient: генерирует эмбеддинг запроса, выполняет
      `SEARCH_KNOWLEDGE_HYBRID_SQL`, возвращает `embedding_used == True` и гибридные метрики
      (`vec_score`, `fts_score`, `trgm_score`, `score`).
    - При отсутствии БД и EmbeddingClient: деградирует до чистого оффлайн-поиска по сидам
      `DEFAULT_HARNESS_SEEDS` и локальной онтологии `.context/`, возвращая `embedding_used == False`
      и непустой список совпадений.
    """
    # 1. Проверка гибридного поиска с активной БД и вектором эмбеддингов
    hybrid_search_rows = [
        {
            "node_id": f"component:{FAKE_NEW_SKILL_SLUG}",
            "label": FAKE_NEW_SKILL_TITLE,
            "kind": VisualizerNodeKind.HARNESS_COMPONENT.value,
            "subtype": ComponentType.SKILL.value,
            "status": ComponentStatus.COMPOSED.value,
            "score": 0.8523,
            "vec_score": 0.9123,
            "fts_score": 0.7500,
            "trgm_score": 0.6200,
            "snippet_source": "FastAPI route generator instructions and models snippet",
        }
    ]

    fake_conn = FakeConnection(search_rows=hybrid_search_rows)
    fake_db = cast(PostgresDatabase, FakePostgresDatabase(fake_conn))
    fake_embedding_client = cast(EmbeddingClient, FakeEmbeddingClient(FAKE_EMBEDDING_DIMENSION))

    search_service = VisualizerSearchService(
        db=fake_db,
        embedding_client=fake_embedding_client,
    )

    response = await search_service.search(SEARCH_QUERY_SAMPLE, top_k=TEST_TOP_K)
    assert response.status == STATUS_OK
    assert response.embedding_used is True
    assert len(response.hits) == 1

    hit = response.hits[0]
    assert hit.node_id == f"component:{FAKE_NEW_SKILL_SLUG}"
    assert hit.vec_score == 0.9123
    assert hit.fts_score == 0.7500
    assert hit.trgm_score == 0.6200
    assert hit.score == 0.8523

    # 2. Проверка автономного лексического поиска без БД (Offline Lexical Fallback)
    offline_search_service = VisualizerSearchService(
        db=None,
        embedding_client=None,
    )
    current_workspace = str(Path.cwd())
    lex_response = await offline_search_service.search(
        SEARCH_QUERY_HARNESS,
        workspace_path=current_workspace,
        top_k=TEST_TOP_K,
    )

    assert lex_response.status == STATUS_OK
    assert lex_response.embedding_used is False
    assert len(lex_response.hits) > 0

    has_seed_match = any(h.node_id.startswith("component:") for h in lex_response.hits)
    has_context_match = any(h.node_id.startswith("context:") for h in lex_response.hits)
    assert has_seed_match or has_context_match, "Offline lexical search must hit seeds or .context/ files"
    assert all(h.score > 0 for h in lex_response.hits)


def test_fastapi_visualizer_endpoints() -> None:
    """Контур 4: Интеграционные тесты FastAPI HTTP-эндпоинтов дашборда и API визуализатора.

    Бизнес-контракт:
    - `GET /ui`: возвращает HTTP 200, Content-Type text/html, содержит ключевые селекторы DOM
      (`#cy`, `CoopGraphCanvas`, `VisualizerApp`) и гарантирует отсутствие незаменённых шаблонов
      (`__STYLES_CSS__`, `__GRAPH_CANVAS_JS__`, `__APP_JS__`).
    - `GET /api/visualizer/graph`: возвращает HTTP 200 и валидный `VisualizerGraphSnapshot`
      с узлами и рёбрами графа.
    - `GET /api/visualizer/search`: возвращает HTTP 200 и валидный `VisualizerSearchResponse`
      со списком найденных соответствий.
    """
    app = create_app()
    client = TestClient(app)

    # 1. Верификация монолитного HTML SPA дашборда
    resp_ui = client.get("/ui")
    assert resp_ui.status_code == 200
    assert HTML_CONTENT_TYPE in resp_ui.headers.get("content-type", "")

    html_text = resp_ui.text
    assert CYTOSCAPE_CANVAS_SELECTOR in html_text, "Missing #cy canvas selector in visualizer HTML"
    assert CANVAS_CLASS_NAME in html_text, "Missing CoopGraphCanvas class in visualizer HTML"
    assert APP_CLASS_NAME in html_text, "Missing VisualizerApp class in visualizer HTML"
    assert PLACEHOLDER_STYLES not in html_text, "Unreplaced styles placeholder in visualizer HTML"
    assert PLACEHOLDER_CANVAS not in html_text, "Unreplaced canvas js placeholder in visualizer HTML"
    assert PLACEHOLDER_APP not in html_text, "Unreplaced app js placeholder in visualizer HTML"

    # 2. Верификация эндпоинта снимка графа топологии
    resp_graph = client.get(
        "/api/visualizer/graph",
        params={
            "workspace_path": ".",
            "since_minutes": TEST_SINCE_MINUTES,
            "include_semantic": True,
            "min_similarity": TEST_MIN_SIMILARITY,
        },
    )
    assert resp_graph.status_code == 200
    snapshot_data = resp_graph.json()
    snapshot = VisualizerGraphSnapshot.model_validate(snapshot_data)
    assert snapshot.status == STATUS_OK
    assert len(snapshot.nodes) > 0
    assert len(snapshot.edges) > 0
    assert snapshot.stats.harness_components_count >= len(DEFAULT_HARNESS_SEEDS)
    assert snapshot.stats.context_nodes_count > 0

    # 3. Верификация эндпоинта поиска по графу знаний
    resp_search = client.get(
        "/api/visualizer/search",
        params={
            "q": SEARCH_QUERY_HARNESS,
            "top_k": TEST_TOP_K,
        },
    )
    assert resp_search.status_code == 200
    search_data = resp_search.json()
    search_resp = VisualizerSearchResponse.model_validate(search_data)
    assert search_resp.status == STATUS_OK
    assert search_resp.query == SEARCH_QUERY_HARNESS
    assert len(search_resp.hits) > 0


def test_resolve_component_group_id_mappings() -> None:
    """Проверяет детерминированное сопоставление типов компонентов Harness с родительскими группами."""
    assert resolve_component_group_id(ComponentType.SKILL.value) == GROUP_SKILLS_ID
    assert resolve_component_group_id(ComponentType.SCRIPT.value) == GROUP_SCRIPTS_ID
    assert resolve_component_group_id(ComponentType.GUARDRAIL.value) == GROUP_GUARDRAILS_ID
    assert resolve_component_group_id(ComponentType.TOOL_GUIDANCE.value) == GROUP_GUARDRAILS_ID
    assert resolve_component_group_id(ComponentType.SYSTEM_PROMPT.value) == GROUP_PROMPTS_ID
    assert resolve_component_group_id(ComponentType.SUBAGENT.value) == GROUP_PROMPTS_ID
    assert resolve_component_group_id(ComponentType.MIDDLEWARE.value) == GROUP_PROMPTS_ID


def test_truncate_preview_edge_cases() -> None:
    """Проверяет функцию форматирования и сжатия текстовых превью узлов."""
    assert truncate_preview(None) == ""
    assert truncate_preview("") == ""
    assert truncate_preview("   ") == ""
    assert truncate_preview("short text", max_chars=50) == "short text"
    assert truncate_preview("multiline\n\n  text  with   spaces", max_chars=50) == "multiline text with spaces"

    long_text = "a" * 100
    truncated = truncate_preview(long_text, max_chars=20)
    assert len(truncated) == 23  # 20 символов + '...'
    assert truncated.endswith("...")
    assert truncate_preview("text", max_chars=0) == ""


def test_score_lexical_match_heuristics() -> None:
    """Проверяет математику чистой функции лексического ранжирования (FTS + trigram)."""
    # Пустой запрос
    assert score_lexical_match("", "title", "body") == (0.0, 0.0, 0.0)
    assert score_lexical_match("  ", "title", "body") == (0.0, 0.0, 0.0)

    # Точное совпадение в заголовке
    total_score, fts_score, trgm_score = score_lexical_match("docker", "Docker Compose stack", "stack description")
    assert total_score > 0.5
    assert fts_score > 0.0
    assert trgm_score > 0.0

    # Частичное совпадение
    tot_part, _, _ = score_lexical_match("compose", "Title", "Body containing docker compose service")
    assert tot_part > 0.0


@pytest.mark.asyncio
async def test_search_service_empty_query_fast_return() -> None:
    """Проверяет мгновенный возврат пустого ответа поиска при пустой строке запроса."""
    service = VisualizerSearchService(db=None, embedding_client=None)
    response = await service.search("   ")
    assert response.status == STATUS_OK
    assert response.query == ""
    assert response.embedding_used is False
    assert len(response.hits) == 0


@pytest.mark.asyncio
async def test_visualizer_graph_service_facade() -> None:
    """Проверяет фасадный паттерн VisualizerGraphService и делегирование вызовов."""
    service = VisualizerGraphService(db=None, embedding_client=None)
    assert isinstance(service.topology_builder, VisualizerTopologyBuilder)
    assert isinstance(service.search_service, VisualizerSearchService)

    snapshot = await service.build_snapshot(workspace_path=str(Path.cwd()))
    assert snapshot.status == STATUS_OK
    assert snapshot.stats.db_connected is False

    search_resp = await service.search_knowledge(SEARCH_QUERY_HARNESS, workspace_path=str(Path.cwd()), top_k=3)
    assert search_resp.status == STATUS_OK
    assert len(search_resp.hits) > 0
