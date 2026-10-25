"""Пакет работы с локальным Контекстным Графом проекта (`.context/` Graph-as-Code) и 3-Way синхронизацией.

Экспортирует менеджер `ContextGraphManager`, неизменяемые контракты узлов, рёбер и отчётов
валидации (`Pydantic v2`), функции парсинга YAML Frontmatter и `[[wiki-links]]`, структурный
AST-мержер `merge_context_node_markdown` и оркестратор двусторонней синхронизации `WorkspaceSyncEngine`.
"""

from __future__ import annotations

from src.core.graph.context_graph import (
    AI_TRACES_HEADING,
    CONTEXT_DIR_NAME,
    DEFAULT_SUBGRAPH_DEPTH,
    ENTITIES_DIR_NAME,
    ENTITY_CLASS_TO_FOLDER,
    ENTITY_TYPE_LITERAL,
    FRONTMATTER_DELIMITER,
    INDEX_FILE_NAME,
    SCHEMA_FILE_NAME,
    ContextGraphManager,
    ContextGraphService,
    ContextNode,
    ContextNodeFrontmatter,
    GraphEdge,
    GraphValidationReport,
    SubgraphQueryResult,
    extract_wiki_links,
    parse_context_node_file,
    split_frontmatter_and_body,
)
from src.core.graph.markdown_merge import (
    DEFAULT_TRACE_DATE_SORT_KEY,
    compute_normalized_content_hash,
    merge_context_node_markdown,
)
from src.core.graph.sync_engine import (
    INGEST_ACTION_CREATED,
    INGEST_ACTION_REJECTED_PROBE,
    INGEST_ACTION_SKIPPED_SYSTEM,
    INGEST_ACTION_UNCHANGED,
    INGEST_ACTION_UPDATED,
    MIN_CANDIDATE_CONTENT_LENGTH,
    SYSTEM_BOOTSTRAP_SLUGS,
    ComponentIngestItemResult,
    ComponentIngestReport,
    ContextNodeSyncInput,
    ContextNodeSyncItemResult,
    ContextSyncReport,
    WorkspaceComponentCandidate,
    WorkspaceSyncEngine,
    normalize_relative_context_path,
)

__all__ = [
    "AI_TRACES_HEADING",
    "CONTEXT_DIR_NAME",
    "DEFAULT_SUBGRAPH_DEPTH",
    "DEFAULT_TRACE_DATE_SORT_KEY",
    "ENTITIES_DIR_NAME",
    "ENTITY_CLASS_TO_FOLDER",
    "ENTITY_TYPE_LITERAL",
    "FRONTMATTER_DELIMITER",
    "INDEX_FILE_NAME",
    "INGEST_ACTION_CREATED",
    "INGEST_ACTION_REJECTED_PROBE",
    "INGEST_ACTION_SKIPPED_SYSTEM",
    "INGEST_ACTION_UNCHANGED",
    "INGEST_ACTION_UPDATED",
    "MIN_CANDIDATE_CONTENT_LENGTH",
    "SCHEMA_FILE_NAME",
    "SYSTEM_BOOTSTRAP_SLUGS",
    "ComponentIngestItemResult",
    "ComponentIngestReport",
    "ContextGraphManager",
    "ContextGraphService",
    "ContextNode",
    "ContextNodeFrontmatter",
    "ContextNodeSyncInput",
    "ContextNodeSyncItemResult",
    "ContextSyncReport",
    "GraphEdge",
    "GraphValidationReport",
    "SubgraphQueryResult",
    "WorkspaceComponentCandidate",
    "WorkspaceSyncEngine",
    "compute_normalized_content_hash",
    "extract_wiki_links",
    "merge_context_node_markdown",
    "normalize_relative_context_path",
    "parse_context_node_file",
    "split_frontmatter_and_body",
]
