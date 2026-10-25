"""Структурный AST-мержер Markdown-узлов Контекстного Графа `.context/` (Layer 2: Graph Merge Engine).

Отвечает за детерминированное вычисление SHA-256 хеша нормализованного текста и тройное слияние
архитектурных узлов `.context/entities/**/*.md` (объединение списков связей YAML Frontmatter,
выбор актуального Markdown-тела и хронологическая дедупликация журнала `AI Traces`) при
коллизиях двусторонней синхронизации `Workspace <-> PostgreSQL` (`SPEC-002`).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime

import yaml
from pydantic import ValidationError

from src.core.graph.context_graph import (
    _INDEX_UPDATED_AT_RE,
    AI_TRACES_HEADING,
    ContextNodeFrontmatter,
    _extract_existing_trace_lines,
    _render_node_markdown,
    extract_wiki_links,
    split_frontmatter_and_body,
)

DEFAULT_TRACE_DATE_SORT_KEY: str = "0000-00-00"
_TRACE_DATE_PREFIX_RE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2})\b")


def compute_normalized_content_hash(content: str) -> str:
    """Вычисляет 64-символьный SHA-256 дайджест после нормализации переводов строк в LF и обрезки краёв.

    Почему нормализация выполняется перед хешированием:
    - Исключает ложные конфликты 3-Way Sync между хост-машинами с CRLF/LF и убирает
      чувствительность к пустым строкам в конце файла при проверке Content-Hash Gate.
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _deduplicate_preserving_order(items: Sequence[str]) -> list[str]:
    """Формирует список уникальных непустых строк с сохранением порядка первого вхождения."""
    seen: set[str] = set()
    result: list[str] = []
    for raw_item in items:
        cleaned = str(raw_item).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _split_main_body_and_traces(body_md: str) -> tuple[str, list[str]]:
    """Отделяет основное описание узла от секции `## Логи контекста (AI Traces)`."""
    stripped = body_md.strip()
    if AI_TRACES_HEADING not in stripped:
        return stripped, []

    before_heading, _ = stripped.split(AI_TRACES_HEADING, maxsplit=1)
    traces = _extract_existing_trace_lines(stripped)
    return before_heading.strip(), traces


def _merge_trace_lines(
    local_traces: Sequence[str],
    db_traces: Sequence[str],
) -> list[str]:
    """Объединяет записи `AI Traces` из обеих версий без дублей в хронологическом порядке."""
    indexed_entries: list[tuple[str, int, str]] = []
    seen: set[str] = set()

    for idx, raw_line in enumerate([*db_traces, *local_traces]):
        cleaned = raw_line.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        date_match = _TRACE_DATE_PREFIX_RE.match(cleaned)
        date_key = date_match.group(1) if date_match else DEFAULT_TRACE_DATE_SORT_KEY
        indexed_entries.append((date_key, idx, cleaned))

    indexed_entries.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in indexed_entries]


def _try_parse_entity_markdown(
    markdown_text: str,
) -> tuple[ContextNodeFrontmatter, str] | None:
    """Пытается распарсить Markdown как узел `.context/entities/` с валидной шапкой `ContextNodeFrontmatter`."""
    try:
        raw_fm, body_md = split_frontmatter_and_body(markdown_text)
        frontmatter = ContextNodeFrontmatter.model_validate(raw_fm)
        return frontmatter, body_md
    except (TypeError, ValueError, ValidationError, yaml.YAMLError):
        return None


def _merge_non_entity_markdown(
    local_md: str,
    db_md: str,
    *,
    prefer_local_body: bool,
) -> str:
    """Сливает корневые файлы графа (`index.md`, `schema.md`) с актуализацией поля `updated_at`."""
    local_clean = local_md.replace("\r\n", "\n").strip()
    db_clean = db_md.replace("\r\n", "\n").strip()

    if prefer_local_body:
        selected = local_clean if local_clean else db_clean
    else:
        selected = db_clean if db_clean else local_clean

    today_iso = datetime.now(UTC).date().isoformat()
    updated = _INDEX_UPDATED_AT_RE.sub(
        rf'\1"{today_iso}"',
        selected,
        count=1,
    )
    return f"{updated}\n" if updated else ""


def merge_context_node_markdown(
    local_md: str,
    db_md: str,
    *,
    prefer_local_body: bool = True,
) -> str:
    """Выполняет структурное 3-Way слияние локальной и серверной версий Markdown-узла `.context/`.

    Алгоритм для узлов `.context/entities/**/*.md`:
    1. Объединяет списки связей в YAML Frontmatter (`stack`, `files`, `depends_on`, `implements`,
       `governed_by`) как упорядоченное объединение множеств без дубликатов.
    2. Выбирает основное текстовое тело (до заголовка `## Логи контекста (AI Traces)`)
       согласно флагу `prefer_local_body` с фолбэком на непустую версию.
    3. Извлекает все строки `- YYYY-MM-DD: ...` из секций `AI Traces` обоих документов,
       дедуплицирует их с сохранением хронологического порядка и рендерит узел через `_render_node_markdown`.
    Для корневых файлов (`index.md`, `schema.md`) возвращает приоритетную версию с обновлённой датой `updated_at`.
    """
    parsed_local = _try_parse_entity_markdown(local_md)
    parsed_db = _try_parse_entity_markdown(db_md)

    if parsed_local is None or parsed_db is None:
        return _merge_non_entity_markdown(
            local_md,
            db_md,
            prefer_local_body=prefer_local_body,
        )

    local_fm, local_body = parsed_local
    db_fm, db_body = parsed_db

    primary_fm = local_fm if prefer_local_body else db_fm
    secondary_fm = db_fm if prefer_local_body else local_fm

    merged_stack = _deduplicate_preserving_order(
        [*primary_fm.stack, *secondary_fm.stack]
    )
    merged_files = _deduplicate_preserving_order(
        [*primary_fm.files, *secondary_fm.files]
    )
    merged_depends_on = extract_wiki_links(
        [*primary_fm.depends_on, *secondary_fm.depends_on]
    )
    merged_implements = extract_wiki_links(
        [*primary_fm.implements, *secondary_fm.implements]
    )
    merged_governed_by = extract_wiki_links(
        [*primary_fm.governed_by, *secondary_fm.governed_by]
    )

    merged_frontmatter = ContextNodeFrontmatter(
        type=primary_fm.entity_type,
        **{"class": primary_fm.entity_class},
        id=primary_fm.id,
        name=primary_fm.name or secondary_fm.name,
        status=primary_fm.status,
        stack=merged_stack,
        files=merged_files,
        depends_on=merged_depends_on,
        implements=merged_implements,
        governed_by=merged_governed_by,
    )

    local_main, local_traces = _split_main_body_and_traces(local_body)
    db_main, db_traces = _split_main_body_and_traces(db_body)

    if prefer_local_body:
        selected_main = local_main if local_main else db_main
    else:
        selected_main = db_main if db_main else local_main

    merged_traces = _merge_trace_lines(local_traces, db_traces)
    if merged_traces:
        traces_block = "\n".join(merged_traces)
        body_with_traces = (
            f"{selected_main.rstrip()}\n\n{AI_TRACES_HEADING}\n{traces_block}\n"
            if selected_main
            else f"{AI_TRACES_HEADING}\n{traces_block}\n"
        )
    elif AI_TRACES_HEADING in local_body or AI_TRACES_HEADING in db_body:
        body_with_traces = (
            f"{selected_main.rstrip()}\n\n{AI_TRACES_HEADING}\n"
            if selected_main
            else f"{AI_TRACES_HEADING}\n"
        )
    else:
        body_with_traces = f"{selected_main.rstrip()}\n" if selected_main else ""

    return _render_node_markdown(merged_frontmatter, body_with_traces)


__all__ = [
    "DEFAULT_TRACE_DATE_SORT_KEY",
    "compute_normalized_content_hash",
    "merge_context_node_markdown",
]
