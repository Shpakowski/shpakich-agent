"""Движок парсинга, валидации и обхода локального Контекстного Графа (`.context/` Graph-as-Code).

Отвечает за преобразование Markdown-узлов (`.context/entities/**/*.md`) с YAML Frontmatter
и `[[wiki-links]]` в ориентированный граф `networkx.DiGraph`. Обеспечивает контроль
инвариантов целостности (No Phantom Nodes — физическое существование всех файлов из `files:`,
No Broken Links — разрешимость всех межмодульных ссылок), извлечение локальных подграфов
окрестности (`query_subgraph`) и атомарное обновление узлов с ведением `AI Traces` (`upsert_node`).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import networkx as nx
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.core.enums import (
    ContextEntityClass,
    ContextEntityFolder,
    ContextEntityStatus,
    ContextRelationType,
)

CONTEXT_DIR_NAME: str = ".context"
ENTITIES_DIR_NAME: str = "entities"
INDEX_FILE_NAME: str = "index.md"
SCHEMA_FILE_NAME: str = "schema.md"
FRONTMATTER_DELIMITER: str = "---"
ENTITY_TYPE_LITERAL: str = "Entity"
AI_TRACES_HEADING: str = "## Логи контекста (AI Traces)"
DEFAULT_SUBGRAPH_DEPTH: int = 2

ENTITY_CLASS_TO_FOLDER: dict[ContextEntityClass, ContextEntityFolder] = {
    ContextEntityClass.MODULE: ContextEntityFolder.MODULES,
    ContextEntityClass.CONTRACT: ContextEntityFolder.CONTRACTS,
    ContextEntityClass.DECISION: ContextEntityFolder.DECISIONS,
}

# Регулярные выражения для очистки кода перед поиском inline wiki-ссылок и парсинга [[node-id|label]]
_FENCED_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_SPAN_RE = re.compile(r"`[^`\n]+`")
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_INDEX_UPDATED_AT_RE = re.compile(r'^(updated_at:\s*)"?[0-9]{4}-[0-9]{2}-[0-9]{2}"?\s*$', re.MULTILINE)


class ContextNodeFrontmatter(BaseModel):
    """Типизированный контракт YAML-шапки архитектурного узла в `.context/entities/`."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    entity_type: str = Field(default=ENTITY_TYPE_LITERAL, alias="type")
    entity_class: ContextEntityClass = Field(alias="class")
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: ContextEntityStatus = Field(default=ContextEntityStatus.ACTIVE)
    stack: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    implements: list[str] = Field(default_factory=list)
    governed_by: list[str] = Field(default_factory=list)


class ContextNode(BaseModel):
    """Полное представление вершины Контекстного Графа (метаданные шапки, Markdown-тело и путь)."""

    model_config = ConfigDict(frozen=True)

    frontmatter: ContextNodeFrontmatter
    body_md: str
    relative_path: str


class GraphEdge(BaseModel):
    """Направленное типизированное ребро между двумя узлами Контекстного Графа."""

    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    relation: ContextRelationType


class GraphValidationReport(BaseModel):
    """Итоговый отчёт проверки целостности графа по правилам No Phantom Nodes и No Broken Links."""

    model_config = ConfigDict(frozen=True)

    is_valid: bool
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    missing_files: dict[str, list[str]] = Field(default_factory=dict)
    broken_links: dict[str, list[str]] = Field(default_factory=dict)
    parse_errors: dict[str, str] = Field(default_factory=dict)


class SubgraphQueryResult(BaseModel):
    """Результат извлечения целевого подграфа окрестности для MCP-инструмента `coop_query_context_graph`."""

    model_config = ConfigDict(frozen=True)

    root_node_id: str | None = None
    depth: int = Field(ge=0)
    nodes: list[ContextNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    validation: GraphValidationReport


def extract_wiki_links(raw_value: str | Sequence[str]) -> list[str]:
    """Извлекает уникальные идентификаторы целевых узлов из `[[node-id]]` или `[[node-id|label]]`.

    Почему перед поиском в Markdown-строке удаляются блоки кода (````...```` и `` `...` ``):
    - Документация узлов может содержать примеры синтаксиса вроде `` `[[target-id]]` ``.
      Исключение code-спанов предотвращает ложные срабатывания детектора битых ссылок,
      сохраняя все реальные wiki-ссылки в тексте и в списках YAML-шапки.
    """
    seen: set[str] = set()
    extracted: list[str] = []

    def _add_target(candidate: str) -> None:
        cleaned = candidate.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            extracted.append(cleaned)

    if isinstance(raw_value, str):
        sanitized_text = _FENCED_CODE_BLOCK_RE.sub("", raw_value)
        sanitized_text = _INLINE_CODE_SPAN_RE.sub("", sanitized_text)
        for match in _WIKI_LINK_RE.finditer(sanitized_text):
            _add_target(match.group(1))
        return extracted

    for item in raw_value:
        item_str = str(item).strip()
        if not item_str:
            continue
        matches = _WIKI_LINK_RE.findall(item_str)
        if matches:
            for matched_id in matches:
                _add_target(matched_id)
        else:
            _add_target(item_str)

    return extracted


def split_frontmatter_and_body(markdown_text: str) -> tuple[dict[str, Any], str]:
    """Разделяет Markdown-файл узла на словарь YAML Frontmatter (между `---`) и тело документа."""
    normalized = markdown_text.lstrip("\ufeff")
    lines = normalized.splitlines()

    if not lines or lines[0].strip() != FRONTMATTER_DELIMITER:
        raise ValueError("Missing opening YAML frontmatter delimiter '---'")

    closing_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == FRONTMATTER_DELIMITER:
            closing_idx = idx
            break

    if closing_idx is None:
        raise ValueError("Missing closing YAML frontmatter delimiter '---'")

    yaml_block = "\n".join(lines[1:closing_idx])
    body_block = "\n".join(lines[closing_idx + 1 :]).strip()

    parsed_yaml = yaml.safe_load(yaml_block)
    if not isinstance(parsed_yaml, dict):
        raise TypeError("YAML frontmatter must deserialize into a key-value mapping")

    return dict(parsed_yaml), body_block


def parse_context_node_file(file_path: Path, workspace_root: Path) -> ContextNode:
    """Читает `.md`-файл сущности с диска и валидирует его через `ContextNodeFrontmatter`."""
    raw_text = file_path.read_text(encoding="utf-8")
    raw_frontmatter, body_md = split_frontmatter_and_body(raw_text)
    frontmatter = ContextNodeFrontmatter.model_validate(raw_frontmatter)

    try:
        rel_path = file_path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        rel_path = file_path.as_posix()

    return ContextNode(
        frontmatter=frontmatter,
        body_md=body_md,
        relative_path=rel_path,
    )


def _extract_existing_trace_lines(markdown_body: str) -> list[str]:
    """Извлекает существующие строки журнала `- YYYY-MM-DD: ...` из секции `AI Traces`."""
    if AI_TRACES_HEADING not in markdown_body:
        return []

    _, traces_part = markdown_body.split(AI_TRACES_HEADING, maxsplit=1)
    trace_lines: list[str] = []
    for line in traces_part.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped.startswith("- "):
            trace_lines.append(stripped)
    return trace_lines


def _merge_body_with_ai_traces(
    new_body_md: str,
    existing_trace_lines: Sequence[str],
    trace_entry: str,
) -> str:
    """Формирует тело Markdown-узла с гарантированным наличием секции `AI Traces` и новой записи."""
    stripped_body = new_body_md.strip()

    if AI_TRACES_HEADING in stripped_body:
        before_heading, after_heading = stripped_body.split(AI_TRACES_HEADING, maxsplit=1)
        current_traces = _extract_existing_trace_lines(AI_TRACES_HEADING + "\n" + after_heading)
        merged_traces = list(current_traces)
        if trace_entry not in merged_traces:
            merged_traces.append(trace_entry)
        traces_block = "\n".join(merged_traces)
        return f"{before_heading.rstrip()}\n\n{AI_TRACES_HEADING}\n{traces_block}\n"

    merged_traces = list(existing_trace_lines)
    if trace_entry not in merged_traces:
        merged_traces.append(trace_entry)
    traces_block = "\n".join(merged_traces)
    return f"{stripped_body}\n\n{AI_TRACES_HEADING}\n{traces_block}\n"


def _render_node_markdown(frontmatter: ContextNodeFrontmatter, body_md_with_traces: str) -> str:
    """Сериализует `ContextNodeFrontmatter` и Markdown-тело в канонический текстовый формат узла."""
    frontmatter_payload: dict[str, Any] = {
        "type": frontmatter.entity_type,
        "class": frontmatter.entity_class.value,
        "id": frontmatter.id,
        "name": frontmatter.name,
        "status": frontmatter.status.value,
        "stack": list(frontmatter.stack),
        "files": list(frontmatter.files),
        "depends_on": [f"[[{target}]]" for target in extract_wiki_links(frontmatter.depends_on)],
        "implements": [f"[[{target}]]" for target in extract_wiki_links(frontmatter.implements)],
        "governed_by": [f"[[{target}]]" for target in extract_wiki_links(frontmatter.governed_by)],
    }
    yaml_str = yaml.safe_dump(
        frontmatter_payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"{FRONTMATTER_DELIMITER}\n{yaml_str}\n{FRONTMATTER_DELIMITER}\n\n{body_md_with_traces.lstrip()}"


class ContextGraphManager:
    """Менеджер сборки, валидации и модификации ориентированного графа знаний `.context/` (`nx.DiGraph`)."""

    def __init__(self, workspace_path: str | Path | None = None) -> None:
        self._workspace_path: Path | None = (
            Path(workspace_path).expanduser().resolve()
            if workspace_path is not None
            else None
        )

    def validate(
        self,
        workspace_path: str | Path | None = None,
    ) -> GraphValidationReport:
        """Возвращает отчёт целостности графа `GraphValidationReport` для указанной или сохранённой рабочей директории."""
        target = workspace_path if workspace_path is not None else (self._workspace_path or Path.cwd())
        _, _, report = self.build_and_validate(target)
        return report

    def build_and_validate(
        self,
        workspace_path: str | Path,
    ) -> tuple[nx.DiGraph, dict[str, ContextNode], GraphValidationReport]:
        """Собирает `nx.DiGraph` из `.context/entities/**/*.md` и проверяет целостность файлов и ссылок.

        Проверяемые инварианты:
        1. `parse_errors`: все `.md`-файлы в `.context/entities/` имеют валидный YAML Frontmatter и уникальные `id`.
        2. `missing_files` (No Phantom Nodes): каждый путь из `frontmatter.files` физически существует в `workspace_root`.
        3. `broken_links` (No Broken Links): каждая ссылка в `depends_on`, `implements`, `governed_by` и `body_md`
           указывает на реально существующий узел графа.
        """
        workspace_root = Path(workspace_path).expanduser().resolve()
        entities_dir = workspace_root / CONTEXT_DIR_NAME / ENTITIES_DIR_NAME

        graph: nx.DiGraph = nx.DiGraph()
        nodes: dict[str, ContextNode] = {}
        missing_files: dict[str, list[str]] = {}
        broken_links: dict[str, list[str]] = {}
        parse_errors: dict[str, str] = {}

        if not entities_dir.is_dir():
            report = GraphValidationReport(
                is_valid=False,
                node_count=0,
                edge_count=0,
                parse_errors={
                    f"{CONTEXT_DIR_NAME}/{ENTITIES_DIR_NAME}": "Context entities directory does not exist"
                },
            )
            return graph, nodes, report

        md_files = sorted(
            entities_dir.rglob("*.md"),
            key=lambda p: p.relative_to(workspace_root).as_posix(),
        )

        for file_path in md_files:
            rel_file_key = file_path.relative_to(workspace_root).as_posix()
            try:
                node = parse_context_node_file(file_path, workspace_root)
            except (OSError, TypeError, ValueError, ValidationError, yaml.YAMLError) as exc:
                parse_errors[rel_file_key] = str(exc)
                continue

            node_id = node.frontmatter.id
            if node_id in nodes:
                parse_errors[rel_file_key] = (
                    f"Duplicate context node id '{node_id}' already defined in '{nodes[node_id].relative_path}'"
                )
                continue

            nodes[node_id] = node
            graph.add_node(
                node_id,
                entity_class=node.frontmatter.entity_class.value,
                name=node.frontmatter.name,
                status=node.frontmatter.status.value,
                stack=list(node.frontmatter.stack),
                files=list(node.frontmatter.files),
                relative_path=node.relative_path,
            )

            node_missing_files: list[str] = []
            for declared_file in node.frontmatter.files:
                cleaned_rel_path = declared_file.strip()
                if not cleaned_rel_path:
                    continue
                if not (workspace_root / cleaned_rel_path).exists():
                    node_missing_files.append(cleaned_rel_path)

            if node_missing_files:
                missing_files[node_id] = node_missing_files

        for node_id, node in nodes.items():
            relation_groups: list[tuple[ContextRelationType, list[str]]] = [
                (ContextRelationType.DEPENDS_ON, extract_wiki_links(node.frontmatter.depends_on)),
                (ContextRelationType.IMPLEMENTS, extract_wiki_links(node.frontmatter.implements)),
                (ContextRelationType.GOVERNED_BY, extract_wiki_links(node.frontmatter.governed_by)),
                (
                    ContextRelationType.REFERENCES,
                    [
                        target
                        for target in extract_wiki_links(node.body_md)
                        if target != node_id
                    ],
                ),
            ]

            node_broken_targets: list[str] = []
            for relation_type, targets in relation_groups:
                for target_id in targets:
                    if target_id not in nodes:
                        if target_id not in node_broken_targets:
                            node_broken_targets.append(target_id)
                        continue

                    if not graph.has_edge(node_id, target_id):
                        graph.add_edge(
                            node_id,
                            target_id,
                            relation=relation_type.value,
                        )

            if node_broken_targets:
                broken_links[node_id] = node_broken_targets

        is_valid = not missing_files and not broken_links and not parse_errors
        report = GraphValidationReport(
            is_valid=is_valid,
            node_count=graph.number_of_nodes(),
            edge_count=graph.number_of_edges(),
            missing_files=missing_files,
            broken_links=broken_links,
            parse_errors=parse_errors,
        )
        return graph, nodes, report

    def query_subgraph(
        self,
        workspace_path: str | Path,
        node_id: str = "",
        depth: int = DEFAULT_SUBGRAPH_DEPTH,
    ) -> SubgraphQueryResult:
        """Возвращает подграф окрестности узла `node_id` радиуса `depth` (или весь граф, если `node_id` пуст).

        Почему `nx.ego_graph` строится по неориентированной проекции `graph.to_undirected()`,
        а затем берётся `graph.subgraph(...)`:
        - При запросе контекста модуля агенту важно видеть как его исходящие зависимости (`depends_on`),
          так и входящих потребителей (модули, которые сами зависят от `node_id` или реализуют контракт),
          сохраняя при этом истинное направление рёбер в итоговом ответе.
        """
        graph, nodes, validation = self.build_and_validate(workspace_path)
        effective_depth = max(0, depth)
        clean_node_id = node_id.strip()

        if not clean_node_id:
            ordered_nodes = [nodes[nid] for nid in sorted(nodes)]
            all_edges = [
                GraphEdge(
                    source=str(source),
                    target=str(target),
                    relation=ContextRelationType(str(edge_data["relation"])),
                )
                for source, target, edge_data in sorted(
                    graph.edges(data=True),
                    key=lambda item: (str(item[0]), str(item[1])),
                )
            ]
            return SubgraphQueryResult(
                root_node_id=None,
                depth=effective_depth,
                nodes=ordered_nodes,
                edges=all_edges,
                validation=validation,
            )

        if clean_node_id not in nodes:
            raise KeyError(f"Context graph node '{clean_node_id}' not found")

        ego_nodes = nx.ego_graph(
            graph.to_undirected(),
            clean_node_id,
            radius=effective_depth,
        ).nodes
        induced_subgraph = graph.subgraph(ego_nodes)

        neighbor_ids = sorted(
            str(nid) for nid in induced_subgraph.nodes if str(nid) != clean_node_id
        )
        ordered_node_ids = [clean_node_id, *neighbor_ids]
        subgraph_nodes = [nodes[nid] for nid in ordered_node_ids if nid in nodes]

        subgraph_edges = [
            GraphEdge(
                source=str(source),
                target=str(target),
                relation=ContextRelationType(str(edge_data["relation"])),
            )
            for source, target, edge_data in sorted(
                induced_subgraph.edges(data=True),
                key=lambda item: (str(item[0]), str(item[1])),
            )
        ]

        return SubgraphQueryResult(
            root_node_id=clean_node_id,
            depth=effective_depth,
            nodes=subgraph_nodes,
            edges=subgraph_edges,
            validation=validation,
        )

    def upsert_node(
        self,
        workspace_path: str | Path,
        *,
        entity_class: ContextEntityClass,
        node_id: str,
        name: str,
        files: Sequence[str],
        body_md: str,
        trace_note: str,
        stack: Sequence[str] = (),
        depends_on: Sequence[str] = (),
        implements: Sequence[str] = (),
        governed_by: Sequence[str] = (),
        status: ContextEntityStatus = ContextEntityStatus.ACTIVE,
    ) -> ContextNode:
        """Создаёт или обновляет `.md`-файл узла в `.context/entities/`, проверяя существование файлов.

        Гарантирует соблюдение правила No Phantom Nodes до записи на диск, автоматически ведёт
        хронологический журнал `## Логи контекста (AI Traces)` и обновляет дату `updated_at` в `.context/index.md`.
        """
        workspace_root = Path(workspace_path).expanduser().resolve()
        clean_node_id = node_id.strip()
        if not clean_node_id:
            raise ValueError("Context node_id must not be empty")

        clean_trace_note = trace_note.strip()
        if not clean_trace_note:
            raise ValueError("Context trace_note must not be empty")

        normalized_files = [item.strip() for item in files if item.strip()]
        for rel_file in normalized_files:
            if not (workspace_root / rel_file).exists():
                raise FileNotFoundError(
                    f"Cannot upsert context node '{clean_node_id}': referenced file '{rel_file}' does not exist"
                )

        target_folder = ENTITY_CLASS_TO_FOLDER[entity_class]
        target_dir = workspace_root / CONTEXT_DIR_NAME / ENTITIES_DIR_NAME / target_folder.value
        target_dir.mkdir(parents=True, exist_ok=True)
        node_file_path = target_dir / f"{clean_node_id}.md"

        existing_traces: list[str] = []
        if node_file_path.exists():
            try:
                _, existing_body = split_frontmatter_and_body(
                    node_file_path.read_text(encoding="utf-8")
                )
                existing_traces = _extract_existing_trace_lines(existing_body)
            except (OSError, TypeError, ValueError, yaml.YAMLError):
                existing_traces = []

        today_iso = datetime.now(UTC).date().isoformat()
        trace_entry = f"- {today_iso}: {clean_trace_note}"
        final_body_md = _merge_body_with_ai_traces(body_md, existing_traces, trace_entry)

        frontmatter = ContextNodeFrontmatter(
            type=ENTITY_TYPE_LITERAL,
            **{"class": entity_class},
            id=clean_node_id,
            name=name.strip(),
            status=status,
            stack=[tag.strip() for tag in stack if tag.strip()],
            files=normalized_files,
            depends_on=extract_wiki_links(depends_on),
            implements=extract_wiki_links(implements),
            governed_by=extract_wiki_links(governed_by),
        )

        rendered_markdown = _render_node_markdown(frontmatter, final_body_md)
        node_file_path.write_text(rendered_markdown, encoding="utf-8")

        index_path = workspace_root / CONTEXT_DIR_NAME / INDEX_FILE_NAME
        if index_path.exists():
            index_content = index_path.read_text(encoding="utf-8")
            updated_index = _INDEX_UPDATED_AT_RE.sub(
                rf'\1"{today_iso}"',
                index_content,
                count=1,
            )
            if updated_index != index_content:
                index_path.write_text(updated_index, encoding="utf-8")

        return parse_context_node_file(node_file_path, workspace_root)


ContextGraphService = ContextGraphManager

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
    "ContextGraphService",
    "ContextNode",
    "ContextNodeFrontmatter",
    "GraphEdge",
    "GraphValidationReport",
    "SubgraphQueryResult",
    "extract_wiki_links",
    "parse_context_node_file",
    "split_frontmatter_and_body",
]
