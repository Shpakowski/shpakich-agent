"""Пакет работы с локальным Контекстным Графом проекта (`.context/` Graph-as-Code).

Экспортирует менеджер `ContextGraphManager`, неизменяемые контракты узлов, рёбер
и отчётов валидации (`Pydantic v2`), а также чистые функции парсинга YAML Frontmatter
и извлечения `[[wiki-links]]`.
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
    ContextNode,
    ContextNodeFrontmatter,
    GraphEdge,
    GraphValidationReport,
    SubgraphQueryResult,
    extract_wiki_links,
    parse_context_node_file,
    split_frontmatter_and_body,
)

__all__ = [
    "AI_TRACES_HEADING",
    "CONTEXT_DIR_NAME",
    "DEFAULT_SUBGRAPH_DEPTH",
    "ENTITIES_DIR_NAME",
    "ENTITY_CLASS_TO_FOLDER",
    "ENTITY_TYPE_LITERAL",
    "FRONTMATTER_DELIMITER",
    "INDEX_FILE_NAME",
    "SCHEMA_FILE_NAME",
    "ContextGraphManager",
    "ContextNode",
    "ContextNodeFrontmatter",
    "GraphEdge",
    "GraphValidationReport",
    "SubgraphQueryResult",
    "extract_wiki_links",
    "parse_context_node_file",
    "split_frontmatter_and_body",
]
