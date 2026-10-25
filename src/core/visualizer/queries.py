"""Параметризованные SQL-запросы и функции ранжирования визуализатора RAG-графа (Layer 2: Visualizer Engine).

Содержит оптимизированные запросы для выборки проектов, компонентов Harness², долгосрочной памяти,
сжатий контекста и узлов Graph-as-Code, а также CTE гибридного векторно-полнотекстового поиска
и чистые функции лексического ранжирования при оффлайн-работе.
"""

from __future__ import annotations

import re

from src.core.enums import ComponentType, VisualizerNodeKind
from src.core.visualizer.models import (
    GROUP_GUARDRAILS_ID,
    GROUP_PROMPTS_ID,
    GROUP_SCRIPTS_ID,
    GROUP_SKILLS_ID,
    HYBRID_FTS_WEIGHT,
    HYBRID_TRGM_WEIGHT,
    HYBRID_VEC_WEIGHT,
    LEXICAL_FTS_WEIGHT,
    LEXICAL_TRGM_WEIGHT,
    MAX_CONTENT_PREVIEW_CHARS,
)

# Выборка всех зарегистрированных локальных проектов с сортировкой по времени последней синхронизации
SELECT_PROJECTS_SQL: str = """
SELECT
    id::text AS id,
    workspace_path,
    name,
    agent_type,
    stack_tags,
    profile_overrides,
    last_synced_at,
    created_at
FROM projects
ORDER BY last_synced_at DESC NULLS LAST;
"""

# Выборка всех компонентов Harness² для построения узлов графа и раскладки по группам
SELECT_HARNESS_COMPONENTS_SQL: str = """
SELECT
    id::text AS id,
    slug,
    component_type,
    scope,
    stack_tags,
    title,
    description,
    content,
    content_hash,
    metadata,
    (embedding IS NOT NULL) AS has_embedding,
    status,
    score,
    usage_count,
    source_project_path,
    version,
    updated_at,
    created_at
FROM harness_components
ORDER BY updated_at DESC NULLS LAST, slug ASC;
"""

# Выборка последних записей долгосрочной памяти решений ошибок с лимитом для предотвращения переполнения графа
SELECT_MEMORIES_SQL: str = """
SELECT
    id::text AS id,
    title,
    error_symptom,
    solution,
    scope,
    stack_tags,
    (embedding IS NOT NULL) AS has_embedding,
    helpful_count,
    source_project_path,
    created_at
FROM memories
ORDER BY created_at DESC
LIMIT 200;
"""

# Выборка архивных чекпоинтов сжатия контекста с опциональной фильтрацией по рабочей папке проекта
SELECT_COMPACTIONS_SQL: str = """
SELECT
    id::text AS id,
    conversation_id,
    workspace_path,
    agent_type,
    step_idx,
    checkpoint_hash,
    summary_text,
    stack_tags,
    metadata,
    (embedding IS NOT NULL) AS has_embedding,
    created_at
FROM session_compactions
WHERE ($1::text IS NULL OR workspace_path IS NULL OR workspace_path = $1::text)
ORDER BY created_at DESC
LIMIT 100;
"""

# Выборка узлов контекстного графа проекта для отображения связей архитектурной онтологии
SELECT_CONTEXT_NODES_SQL: str = """
SELECT
    id::text AS id,
    project_id::text AS project_id,
    workspace_path,
    relative_path,
    node_id,
    entity_class,
    content,
    content_hash,
    base_hash,
    (embedding IS NOT NULL) AS has_embedding,
    file_updated_at,
    updated_at
FROM project_context_nodes
WHERE ($1::text IS NULL OR workspace_path = $1::text)
ORDER BY node_id ASC;
"""

# Агрегированный запрос вспомогательной статистики для карточки сводки репозитория
SELECT_AUX_COUNTS_SQL: str = """
SELECT
    (SELECT COUNT(*) FROM hook_events)::int AS hook_events_count,
    (SELECT COUNT(*) FROM decision_providers WHERE enabled = TRUE)::int AS providers_count;
"""

# Вычисление семантических связей между узлами на основе косинусного расстояния pgvector
SELECT_SEMANTIC_SIMILARITY_SQL: str = """
WITH embedded_entities AS (
    SELECT
        'component:' || slug AS node_id,
        embedding
    FROM harness_components
    WHERE embedding IS NOT NULL

    UNION ALL

    SELECT
        'memory:' || id::text AS node_id,
        embedding
    FROM memories
    WHERE embedding IS NOT NULL

    UNION ALL

    SELECT
        'compaction:' || id::text AS node_id,
        embedding
    FROM session_compactions
    WHERE embedding IS NOT NULL
      AND ($1::text IS NULL OR workspace_path IS NULL OR workspace_path = $1::text)

    UNION ALL

    SELECT
        'context:' || node_id AS node_id,
        embedding
    FROM project_context_nodes
    WHERE embedding IS NOT NULL
      AND ($1::text IS NULL OR workspace_path = $1::text)
)
SELECT
    a.node_id AS source_id,
    b.node_id AS target_id,
    (1.0 - (a.embedding <=> b.embedding))::float8 AS similarity
FROM embedded_entities a
JOIN embedded_entities b ON a.node_id < b.node_id
WHERE (1.0 - (a.embedding <=> b.embedding)) >= $2::float8
ORDER BY similarity DESC
LIMIT $3::int;
"""

# Гибридный векторно-полнотекстовый поиск по всем доступным сущностям базы знаний
SEARCH_KNOWLEDGE_HYBRID_SQL: str = f"""
WITH candidates AS (
    SELECT
        'component:' || slug AS node_id,
        title AS label,
        '{VisualizerNodeKind.HARNESS_COMPONENT.value}' AS kind,
        component_type AS subtype,
        status AS status,
        coalesce(description, '') || ' ' || left(coalesce(content, ''), 300) AS snippet_source,
        search_tsv,
        coalesce(title, '') || ' ' || coalesce(description, '') AS trgm_target,
        embedding
    FROM harness_components

    UNION ALL

    SELECT
        'memory:' || id::text AS node_id,
        title AS label,
        '{VisualizerNodeKind.MEMORY.value}' AS kind,
        'memory' AS subtype,
        'active' AS status,
        coalesce(error_symptom, '') || ' -> ' || left(coalesce(solution, ''), 240) AS snippet_source,
        search_tsv,
        coalesce(title, '') || ' ' || coalesce(error_symptom, '') AS trgm_target,
        embedding
    FROM memories

    UNION ALL

    SELECT
        'compaction:' || id::text AS node_id,
        'Compaction #' || step_idx::text || ' (' || left(conversation_id, 8) || ')' AS label,
        '{VisualizerNodeKind.COMPACTION.value}' AS kind,
        agent_type AS subtype,
        'archived' AS status,
        left(coalesce(summary_text, ''), 320) AS snippet_source,
        search_tsv,
        left(coalesce(summary_text, ''), 500) AS trgm_target,
        embedding
    FROM session_compactions
    WHERE ($2::text IS NULL OR workspace_path IS NULL OR workspace_path = $2::text)

    UNION ALL

    SELECT
        'context:' || node_id AS node_id,
        node_id AS label,
        '{VisualizerNodeKind.CONTEXT_NODE.value}' AS kind,
        entity_class AS subtype,
        'synced' AS status,
        left(coalesce(content, ''), 320) AS snippet_source,
        search_tsv,
        coalesce(node_id, '') || ' ' || left(coalesce(content, ''), 500) AS trgm_target,
        embedding
    FROM project_context_nodes
    WHERE ($2::text IS NULL OR workspace_path = $2::text)
),
scored AS (
    SELECT
        node_id,
        label,
        kind,
        subtype,
        status,
        snippet_source,
        CASE
            WHEN $3::text IS NOT NULL AND embedding IS NOT NULL THEN
                GREATEST(0.0::float8, (1.0::float8 - (embedding <=> $3::text::vector))::float8)
            ELSE 0.0::float8
        END AS vec_score,
        LEAST(1.0::float8, (ts_rank_cd(search_tsv, websearch_to_tsquery('simple', $1::text)) * 2.0)::float8) AS fts_score,
        GREATEST(similarity(label, $1::text), word_similarity($1::text, trgm_target))::float8 AS trgm_score
    FROM candidates
),
ranked AS (
    SELECT
        node_id,
        label,
        kind,
        subtype,
        status,
        snippet_source,
        vec_score,
        fts_score,
        trgm_score,
        (
            CASE
                WHEN $3::text IS NOT NULL THEN
                    (vec_score * {HYBRID_VEC_WEIGHT} + fts_score * {HYBRID_FTS_WEIGHT} + trgm_score * {HYBRID_TRGM_WEIGHT})
                ELSE
                    (fts_score * {LEXICAL_FTS_WEIGHT} + trgm_score * {LEXICAL_TRGM_WEIGHT})
            END
        )::float8 AS score
    FROM scored
)
SELECT
    node_id,
    label,
    kind,
    subtype,
    status,
    snippet_source,
    vec_score,
    fts_score,
    trgm_score,
    score
FROM ranked
WHERE score > 0.01
ORDER BY score DESC
LIMIT $4::int;
"""


def truncate_preview(text: str | None, max_chars: int = MAX_CONTENT_PREVIEW_CHARS) -> str:
    """Сворачивает переносы строк и пробелы, аккуратно обрезая превью с добавлением троеточия.

    Зачем:
    - В графе Cytoscape.js длинные описания и полные тексты правил замедляют рендеринг узлов
      и перегружают всплывающие подсказки. Функция формирует лаконичный и плоский снимок текста.
    """
    if not text:
        return ""
    if max_chars <= 0:
        return ""

    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed

    return f"{collapsed[:max_chars].rstrip()}..."


def resolve_component_group_id(component_type: str) -> str:
    """Сопоставляет тип компонента Harness² с идентификатором родительского контейнера Cytoscape.js.

    Зачем:
    - Визуализатор организует компоненты в составные группы (Compound Nodes) для наглядной
      навигации: навыки объединяются в Skills, скрипты в Scripts, защитные правила и руководства
      в Guardrails, а системные промпты и микро-агенты — в общую группу Prompts.
    """
    normalized = str(component_type).strip().lower()
    if normalized == ComponentType.SKILL.value:
        return GROUP_SKILLS_ID
    if normalized == ComponentType.SCRIPT.value:
        return GROUP_SCRIPTS_ID
    if normalized in (ComponentType.GUARDRAIL.value, ComponentType.TOOL_GUIDANCE.value):
        return GROUP_GUARDRAILS_ID
    return GROUP_PROMPTS_ID


def _extract_tokens(text: str) -> list[str]:
    """Извлекает нормализованные алфавитно-цифровые токены длиной от 2 символов для поиска."""
    return [tok for tok in re.findall(r"[\w-]+", text.lower()) if len(tok) >= 2]


def _generate_trigrams(text: str) -> set[str]:
    """Формирует набор символьных 3-грамм с граничными пробелами по алгоритму pg_trgm."""
    normalized = f"  {text.lower().strip()} "
    if len(normalized) < 3:
        return set()
    return {normalized[i : i + 3] for i in range(len(normalized) - 2)}


def score_lexical_match(query: str, title: str, body: str) -> tuple[float, float, float]:
    """Вычисляет оценку лексического соответствия (FTS + trigram) для оффлайн-фолбэка без PostgreSQL.

    Как работает алгоритм:
    1. Полнотекстовая составляющая (`fts_score`): вычисляет долю токенов поискового запроса,
       присутствующих в заголовке и теле документа, с приоритетным весом совпадений в заголовке.
    2. Триграммная составляющая (`trgm_score`): генерирует символьные 3-граммы в стиле pg_trgm
       и вычисляет максимум из коэффициента Жаккара заголовка и доли покрытия 3-грамм запроса
       в общем тексте (`word_similarity` эквивалент), учитывая прямые подстрочные вхождения.
    3. Итоговый скор (`total_score`): взвешивает FTS и Trigram с весами `LEXICAL_FTS_WEIGHT`
       и `LEXICAL_TRGM_WEIGHT`.
    """
    clean_query = query.strip()
    if not clean_query:
        return 0.0, 0.0, 0.0

    query_tokens = _extract_tokens(clean_query)
    if not query_tokens:
        return 0.0, 0.0, 0.0

    unique_query_tokens = set(query_tokens)
    title_tokens = set(_extract_tokens(title))
    body_tokens = set(_extract_tokens(body))
    all_tokens = title_tokens | body_tokens

    # 1. Вычисление полнотекстового покрытия токенов (FTS)
    title_hits = sum(1 for tok in unique_query_tokens if tok in title_tokens)
    all_hits = sum(1 for tok in unique_query_tokens if tok in all_tokens)

    all_ratio = all_hits / len(unique_query_tokens)
    title_ratio = title_hits / len(unique_query_tokens)
    fts_score = min(1.0, (all_ratio * 0.70) + (title_ratio * 0.30))

    query_lower = clean_query.lower()
    title_lower = title.lower()
    body_lower = body.lower()
    combined_target = f"{title_lower} {body_lower}"

    # Бонус за точное подстрочное совпадение фразы
    if query_lower in title_lower:
        fts_score = max(fts_score, 0.90)
    elif query_lower in combined_target:
        fts_score = max(fts_score, 0.70)

    # 2. Вычисление триграммной схожести (Trigram similarity & containment)
    q_trigrams = _generate_trigrams(clean_query)
    if not q_trigrams:
        trgm_score = 0.0
    else:
        title_trigrams = _generate_trigrams(title)
        target_trigrams = _generate_trigrams(combined_target)

        # Коэффициент Жаккара с заголовком (эквивалент similarity(label, query))
        title_union = q_trigrams | title_trigrams
        jaccard_title = (len(q_trigrams & title_trigrams) / len(title_union)) if title_union else 0.0

        # Покрытие триграмм запроса в тексте (эквивалент word_similarity(query, target))
        containment_target = len(q_trigrams & target_trigrams) / len(q_trigrams)

        if query_lower in title_lower:
            containment_target = max(containment_target, 1.0)
        elif query_lower in combined_target:
            containment_target = max(containment_target, 0.80)

        trgm_score = min(1.0, max(jaccard_title, containment_target))

    # 3. Взвешенное объединение по лексическим весам
    total_score = round(
        (fts_score * LEXICAL_FTS_WEIGHT) + (trgm_score * LEXICAL_TRGM_WEIGHT),
        4,
    )
    return total_score, round(fts_score, 4), round(trgm_score, 4)


__all__ = [
    "SEARCH_KNOWLEDGE_HYBRID_SQL",
    "SELECT_AUX_COUNTS_SQL",
    "SELECT_COMPACTIONS_SQL",
    "SELECT_CONTEXT_NODES_SQL",
    "SELECT_HARNESS_COMPONENTS_SQL",
    "SELECT_MEMORIES_SQL",
    "SELECT_PROJECTS_SQL",
    "SELECT_SEMANTIC_SIMILARITY_SQL",
    "resolve_component_group_id",
    "score_lexical_match",
    "truncate_preview",
]
