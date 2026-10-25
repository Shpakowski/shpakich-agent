"""Сервис серверной консолидации знаний и дедупликации компонентов `HarnessConsolidator` (SPEC-002).

Выполняет кластеризацию семантических дубликатов (`pgvector >= 0.86` или лексический фолбэк `>= 0.60`)
через связные компоненты `networkx.Graph`, синтезирует канонический Markdown, валидирует результат
в контуре `DecisionDomain.HARNESS_PROBE` и архивирует поглощённые дубликаты с сохранением Lineage.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import (
    ComponentStatus,
    ComponentType,
    DecisionDomain,
    EmbeddingTaskType,
    ScopeType,
    ScriptExecutionMode,
    VerdictAction,
)
from src.core.harness.seed import HARNESS_ORCHESTRATOR_SLUG, compute_component_content_hash
from src.core.services.embeddings import EmbeddingClient, format_pgvector

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase
    from src.core.db.redis_client import RedisSessionStore
    from src.core.decision.engine import DecisionEngine

logger = logging.getLogger("mcp_coop.core.harness.consolidator")

DEFAULT_MIN_VECTOR_SIMILARITY: float = 0.86
DEFAULT_MIN_LEXICAL_SIMILARITY: float = 0.60
JACCARD_WEIGHT: float = 0.55
SEQUENCE_RATIO_WEIGHT: float = 0.45
MIN_VECTOR_NORM: float = 1e-12

CONSOLIDATABLE_COMPONENT_TYPES: tuple[str, ...] = (
    ComponentType.SKILL.value,
    ComponentType.SYSTEM_PROMPT.value,
    ComponentType.TOOL_GUIDANCE.value,
)
LEXICAL_TOKEN_PATTERN: re.Pattern[str] = re.compile(r"[a-z0-9а-яё_./-]+")
LEXICAL_STOP_WORDS: frozenset[str] = frozenset({"the", "and", "for", "with", "from", "that", "this", "into", "use", "when", "all"})

SELECT_CONSOLIDATION_CANDIDATES_SQL: str = """
SELECT slug, component_type, scope, stack_tags, title, description, content,
       content_hash, metadata, embedding::text AS embedding, score, usage_count, version
FROM harness_components
WHERE status = $1 AND component_type = ANY($2::text[])
  AND COALESCE(metadata->>'execution_mode', 'always') != $3
  AND ($4::text[] IS NULL OR stack_tags && $4::text[])
ORDER BY score DESC, usage_count DESC, slug ASC
"""

SELECT_PAIRWISE_VECTOR_SIMILARITY_SQL: str = """
SELECT a.slug AS slug_a, b.slug AS slug_b, (1.0 - (a.embedding <=> b.embedding)) AS vector_similarity,
       similarity(a.title || ' ' || a.description, b.title || ' ' || b.description) AS lexical_similarity
FROM harness_components a JOIN harness_components b ON a.component_type = b.component_type AND a.slug < b.slug AND a.stack_tags && b.stack_tags
WHERE a.status = $1 AND b.status = $1 AND a.component_type = ANY($2::text[])
  AND COALESCE(a.metadata->>'execution_mode', 'always') != $3 AND COALESCE(b.metadata->>'execution_mode', 'always') != $3
  AND ((a.embedding IS NOT NULL AND b.embedding IS NOT NULL AND (1.0 - (a.embedding <=> b.embedding)) >= $4)
       OR similarity(a.title || ' ' || a.description, b.title || ' ' || b.description) >= $5)
"""

UPDATE_CANONICAL_COMPONENT_SQL: str = """
UPDATE harness_components
SET title = $2, description = $3, content = $4, content_hash = $5, stack_tags = $6::text[],
    metadata = $7::jsonb, embedding = COALESCE($8::vector, embedding), score = $9, version = version + 1, updated_at = NOW()
WHERE slug = $1
"""

ARCHIVE_DUPLICATE_COMPONENTS_SQL: str = """
UPDATE harness_components SET status = $2, metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb, updated_at = NOW()
WHERE slug = ANY($1::text[])
"""


class ConsolidationCluster(BaseModel):
    """Кластер семантически эквивалентных компонентов с синтезированным эталоном и Lineage-ссылками."""

    model_config = ConfigDict(frozen=True)
    canonical_slug: str = Field(..., min_length=1)
    component_type: ComponentType
    duplicate_slugs: tuple[str, ...]
    merged_stack_tags: tuple[str, ...]
    max_similarity: float = Field(..., ge=0.0, le=1.0)
    synthesized_title: str = Field(..., min_length=1)
    synthesized_description: str = ""
    synthesized_content: str = Field(..., min_length=1)
    applied: bool = False


class ConsolidationReport(BaseModel):
    """Итоговый отчёт прогона `HarnessConsolidator` (в режиме `--dry-run` или транзакционного слияния)."""

    model_config = ConfigDict(frozen=True)
    dry_run: bool
    clusters_found: int = Field(..., ge=0)
    components_archived: int = Field(..., ge=0)
    clusters: list[ConsolidationCluster] = Field(default_factory=list)


def compute_lexical_similarity(text_a: str, text_b: str) -> float:
    """Вычисляет детерминированную лексическую близость (Jaccard токенов + `SequenceMatcher`)."""
    norm_a, norm_b = " ".join(text_a.strip().lower().split()), " ".join(text_b.strip().lower().split())
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0
    tokens_a = {t.strip(".-/") for t in LEXICAL_TOKEN_PATTERN.findall(norm_a) if len(t.strip(".-/")) >= 2 and t.strip(".-/") not in LEXICAL_STOP_WORDS}
    tokens_b = {t.strip(".-/") for t in LEXICAL_TOKEN_PATTERN.findall(norm_b) if len(t.strip(".-/")) >= 2 and t.strip(".-/") not in LEXICAL_STOP_WORDS}
    union_size = len(tokens_a | tokens_b)
    jaccard = (len(tokens_a & tokens_b) / union_size) if union_size > 0 else 0.0
    return round(min(1.0, (jaccard * JACCARD_WEIGHT) + (SequenceMatcher(None, norm_a, norm_b).ratio() * SEQUENCE_RATIO_WEIGHT)), 4)


def _parse_metadata(raw: Any) -> dict[str, Any]:
    """Приводит поле `metadata` из JSON-строки или отображения к изменяемому словарю."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except ValueError:
            return {}
    return {}


def _parse_embedding(raw: Any) -> list[float] | None:
    """Декодирует вектор эмбеддинга из списка чисел или строкового литерала `pgvector` `[x1,x2,...]`."""
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return [float(x) for x in raw] if raw else None
    if isinstance(raw, str) and raw.strip().strip("[]").strip():
        try:
            return [float(p.strip()) for p in raw.strip().strip("[]").split(",") if p.strip()] or None
        except ValueError:
            return None
    return None


def _compute_cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Вычисляет косинусную близость `1.0 - (a <=> b)` между двумя векторами одинаковой размерности."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a, norm_b = math.sqrt(sum(a * a for a in vec_a)), math.sqrt(sum(b * b for b in vec_b))
    return round(max(0.0, min(1.0, dot / (norm_a * norm_b))), 4) if norm_a > MIN_VECTOR_NORM and norm_b > MIN_VECTOR_NORM else 0.0


def _split_markdown_sections(markdown: str) -> list[tuple[str, list[str]]]:
    """Разбивает Markdown-документ на упорядоченный список секций `(heading_line, body_lines)`."""
    sections: list[tuple[str, list[str]]] = []
    heading, lines = "", []
    for raw_line in markdown.replace("\r\n", "\n").strip().splitlines():
        if raw_line.lstrip().startswith("## "):
            if heading or lines:
                sections.append((heading, lines))
            heading, lines = raw_line.strip(), []
        else:
            lines.append(raw_line.rstrip())
    if heading or lines:
        sections.append((heading, lines))
    return sections


def synthesize_cluster_markdown(canonical: Mapping[str, Any], duplicates: Sequence[Mapping[str, Any]]) -> str:
    """Детерминированно объединяет Markdown канонического компонента с уникальными секциями и пунктами дубликатов."""
    base_content = str(canonical.get("content") or "").replace("\r\n", "\n").strip()
    if not duplicates:
        return base_content
    sections = _split_markdown_sections(base_content) or [("", [])]
    heading_index = {h.lstrip("#").strip().lower(): i for i, (h, _) in enumerate(sections) if h}
    seen_lines = {" ".join(ln.strip().lower().split()) for _, lns in sections for ln in lns if ln.strip() and not ln.strip().startswith("```")}

    for dup in duplicates:
        for heading, lines in _split_markdown_sections(str(dup.get("content") or "")):
            norm_h = heading.lstrip("#").strip().lower() if heading else ""
            if heading and norm_h not in heading_index:
                unique_block = [ln for ln in lines if not ln.strip() or ln.strip().startswith("```") or " ".join(ln.strip().lower().split()) not in seen_lines]
                if any(ln.strip() for ln in unique_block):
                    heading_index[norm_h] = len(sections)
                    sections.append((heading, unique_block))
                    seen_lines.update(" ".join(ln.strip().lower().split()) for ln in unique_block if ln.strip() and not ln.strip().startswith("```"))
            else:
                target_lines = sections[heading_index.get(norm_h, 0)][1]
                for ln in lines:
                    s = ln.strip()
                    if s and not s.startswith("# ") and not s.startswith("```") and " ".join(s.lower().split()) not in seen_lines:
                        target_lines.append(ln.rstrip())
                        seen_lines.add(" ".join(s.lower().split()))

    canon_scripts = _parse_metadata(canonical.get("metadata")).get("helper_scripts")
    known_scripts = set(canon_scripts.keys()) if isinstance(canon_scripts, Mapping) else set()
    extra_scripts = [str(k).strip() for dup in duplicates for k in (_parse_metadata(dup.get("metadata")).get("helper_scripts") or {}) if str(k).strip() and str(k).strip() not in known_scripts]
    blocks = [f"{h}\n" + "\n".join(lns).strip() if h and "\n".join(lns).strip() else (h or "\n".join(lns).strip()) for h, lns in sections]
    merged_md = "\n\n".join(b for b in blocks if b).strip()
    unmentioned = list(dict.fromkeys(s for s in extra_scripts if s not in merged_md))
    return f"{merged_md}\n\n## Helper Scripts\n" + "\n".join(f"- `{s}`" for s in unmentioned) if unmentioned else merged_md


class HarnessConsolidator:
    """Сервис кластеризации и Lineage-слияния дублирующихся знаний в таблице `harness_components`."""

    def __init__(self, db: PostgresDatabase | None = None, redis: RedisSessionStore | None = None, engine: DecisionEngine | None = None, embedding_client: EmbeddingClient | None = None) -> None:
        self._db, self._redis, self._engine, self._embedding_client = db, redis, engine, embedding_client

    async def consolidate(
        self,
        *,
        stack_tags: Sequence[str] | None = None,
        component_type: ComponentType | str | None = None,
        min_vector_similarity: float = DEFAULT_MIN_VECTOR_SIMILARITY,
        min_lexical_similarity: float = DEFAULT_MIN_LEXICAL_SIMILARITY,
        dry_run: bool = False,
        candidates: Sequence[Mapping[str, Any]] | None = None,
    ) -> ConsolidationReport:
        """Находит кластеры дубликатов, синтезирует канонические компоненты и архивирует дубликаты."""
        target_types = [ComponentType(component_type).value] if component_type is not None else list(CONSOLIDATABLE_COMPONENT_TYPES)
        filter_tags = sorted({str(t).strip().lower() for t in stack_tags if str(t).strip()}) if stack_tags else None
        active_items = await self._load_candidates(target_types, filter_tags, candidates)
        if len(active_items) < 2:
            return ConsolidationReport(dry_run=dry_run, clusters_found=0, components_archived=0, clusters=[])

        graph = self._build_similarity_graph(active_items, min_vector_similarity=min_vector_similarity, min_lexical_similarity=min_lexical_similarity)
        by_slug = {str(item["slug"]): item for item in active_items}
        clusters: list[ConsolidationCluster] = []
        total_archived, any_applied = 0, False

        for component_nodes in nx.connected_components(graph):
            if len(component_nodes) < 2:
                continue
            members = sorted((by_slug[s] for s in component_nodes if s in by_slug), key=lambda m: (-float(m.get("score") or 1.0), -int(m.get("usage_count") or 0), len(str(m["slug"])), str(m["slug"])))
            canonical, duplicates = members[0], members[1:]
            canonical_slug, dup_slugs = str(canonical["slug"]), tuple(str(d["slug"]) for d in duplicates)
            comp_type = ComponentType(str(canonical["component_type"]))
            merged_tags = tuple(sorted({str(t).strip().lower() for m in members for t in (m.get("stack_tags") or [ScopeType.GLOBAL.value]) if str(t).strip()}) or [ScopeType.GLOBAL.value])
            max_sim = max((float(d.get("similarity", 0.0)) for _, _, d in graph.subgraph(component_nodes).edges(data=True)), default=0.0)
            synth_title = str(canonical.get("title") or canonical_slug).strip()
            synth_desc = str(canonical.get("description") or "").strip() or next((str(d.get("description") or "").strip() for d in duplicates if str(d.get("description") or "").strip()), synth_title)
            synth_content = synthesize_cluster_markdown(canonical, duplicates)
            merged_meta = self._merge_metadata(canonical, duplicates, dup_slugs)
            max_score = max(float(m.get("score") or 1.0) for m in members)

            approved = await self._probe_cluster(canonical_slug, comp_type, synth_title, synth_desc, synth_content, merged_tags, merged_meta)
            applied = False
            if approved and not dry_run and self._db is not None and self._db.is_connected:
                await self._persist_cluster(canonical_slug, synth_title, synth_desc, synth_content, merged_tags, merged_meta, max_score, dup_slugs)
                applied, any_applied = True, True
                total_archived += len(dup_slugs)

            clusters.append(ConsolidationCluster(canonical_slug=canonical_slug, component_type=comp_type, duplicate_slugs=dup_slugs, merged_stack_tags=merged_tags, max_similarity=round(max_sim, 4), synthesized_title=synth_title, synthesized_description=synth_desc, synthesized_content=synth_content, applied=applied))

        clusters.sort(key=lambda c: (-c.max_similarity, c.canonical_slug))
        if any_applied and self._redis is not None and self._redis.is_connected:
            await self._redis.invalidate_rules_cache()
        return ConsolidationReport(dry_run=dry_run, clusters_found=len(clusters), components_archived=total_archived, clusters=clusters)

    async def _load_candidates(self, target_types: list[str], filter_tags: list[str] | None, explicit_candidates: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
        """Загружает и фильтрует активные семантические компоненты для консолидации."""
        raw_rows: Sequence[Mapping[str, Any]] = explicit_candidates if explicit_candidates is not None else (await self._db.pool.fetch(SELECT_CONSOLIDATION_CANDIDATES_SQL, ComponentStatus.COMPOSED.value, target_types, ScriptExecutionMode.ALWAYS.value, filter_tags) if self._db is not None and self._db.is_connected else ())
        filtered: list[dict[str, Any]] = []
        for row in raw_rows:
            slug, comp_type, status = str(row.get("slug") or "").strip(), str(row.get("component_type") or "").strip(), str(row.get("status") or ComponentStatus.COMPOSED.value).strip()
            meta = _parse_metadata(row.get("metadata"))
            exec_mode = str(meta.get("execution_mode") or ScriptExecutionMode.SEMANTIC.value).strip().lower()
            if not slug or slug == HARNESS_ORCHESTRATOR_SLUG or status != ComponentStatus.COMPOSED.value or comp_type not in target_types or exec_mode == ScriptExecutionMode.ALWAYS.value:
                continue
            tags = [str(t).strip().lower() for t in (row.get("stack_tags") or [ScopeType.GLOBAL.value]) if str(t).strip()] or [ScopeType.GLOBAL.value]
            if not filter_tags or (set(tags) & set(filter_tags)):
                filtered.append({**dict(row), "stack_tags": tags, "metadata": meta})
        return filtered

    @staticmethod
    def _build_similarity_graph(items: Sequence[Mapping[str, Any]], *, min_vector_similarity: float, min_lexical_similarity: float) -> nx.Graph:
        """Строит неориентированный граф близости между компонентами одного типа с общими `stack_tags`."""
        graph = nx.Graph()
        for item in items:
            graph.add_node(str(item["slug"]))
        for idx_a, item_a in enumerate(items):
            slug_a, type_a, tags_a = str(item_a["slug"]), str(item_a["component_type"]), set(item_a.get("stack_tags") or [ScopeType.GLOBAL.value])
            vec_a = _parse_embedding(item_a.get("embedding"))
            head_a, full_a = f"{item_a.get('title', '')} {item_a.get('description', '')}".strip(), f"{item_a.get('title', '')} {item_a.get('description', '')}\n{item_a.get('content', '')}".strip()
            for item_b in items[idx_a + 1 :]:
                if str(item_b["component_type"]) != type_a or not (tags_a & set(item_b.get("stack_tags") or [ScopeType.GLOBAL.value])):
                    continue
                vec_b = _parse_embedding(item_b.get("embedding"))
                if vec_a is not None and vec_b is not None:
                    sim = _compute_cosine_similarity(vec_a, vec_b)
                    if sim >= min_vector_similarity:
                        graph.add_edge(slug_a, str(item_b["slug"]), similarity=sim)
                else:
                    head_b, full_b = f"{item_b.get('title', '')} {item_b.get('description', '')}".strip(), f"{item_b.get('title', '')} {item_b.get('description', '')}\n{item_b.get('content', '')}".strip()
                    lex_sim = max(compute_lexical_similarity(head_a, head_b), compute_lexical_similarity(full_a, full_b))
                    if lex_sim >= min_lexical_similarity:
                        graph.add_edge(slug_a, str(item_b["slug"]), similarity=lex_sim)
        return graph

    @staticmethod
    def _merge_metadata(canonical: Mapping[str, Any], duplicates: Sequence[Mapping[str, Any]], duplicate_slugs: Sequence[str]) -> dict[str, Any]:
        """Объединяет `metadata` кластера (`merged_from` и `helper_scripts`) без потери истории."""
        merged_meta = _parse_metadata(canonical.get("metadata"))
        merged_from: list[str] = []
        for item in (canonical, *duplicates):
            prev = _parse_metadata(item.get("metadata")).get("merged_from")
            if isinstance(prev, (list, tuple, set)):
                merged_from.extend(str(s).strip() for s in prev if str(s).strip() and str(s).strip() not in merged_from)
        merged_from.extend(s for s in duplicate_slugs if s not in merged_from)
        merged_meta["merged_from"] = list(dict.fromkeys(merged_from))
        merged_scripts = {str(k).strip(): str(v) for item in (*duplicates, canonical) for k, v in (_parse_metadata(item.get("metadata")).get("helper_scripts") or {}).items() if isinstance(_parse_metadata(item.get("metadata")).get("helper_scripts"), Mapping) and str(k).strip()}
        if merged_scripts:
            merged_meta["helper_scripts"] = merged_scripts
        merged_meta.setdefault("execution_mode", ScriptExecutionMode.SEMANTIC.value)
        return merged_meta

    async def _probe_cluster(self, slug: str, comp_type: ComponentType, title: str, desc: str, content: str, tags: Sequence[str], meta: Mapping[str, Any]) -> bool:
        """Валидирует синтезированный компонент через `DecisionDomain.HARNESS_PROBE`."""
        if self._engine is None:
            return True
        from src.core.decision.models import DecisionRequest

        req = DecisionRequest(domain=DecisionDomain.HARNESS_PROBE, stack_tags=list(tags), payload={"slug": slug, "component_type": comp_type.value, "title": title, "description": desc, "content": content, "metadata": dict(meta)})
        return (await self._engine.evaluate(req)).action == VerdictAction.ALLOW

    async def _persist_cluster(self, slug: str, title: str, desc: str, content: str, tags: Sequence[str], meta: Mapping[str, Any], score: float, dup_slugs: Sequence[str]) -> None:
        """Атомарно обновляет канонический компонент и архивирует дубликаты внутри транзакции PostgreSQL."""
        if self._db is None or not self._db.is_connected:
            return
        vec = await self._embedding_client.embed_text(f"{title}\n{desc}\n{content}", task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT, title=title) if self._embedding_client else None
        update_args = (UPDATE_CANONICAL_COMPONENT_SQL, slug, title, desc, content, compute_component_content_hash(content), list(tags), json.dumps(dict(meta), ensure_ascii=False), format_pgvector(vec), score)
        archive_args = (ARCHIVE_DUPLICATE_COMPONENTS_SQL, list(dup_slugs), ComponentStatus.ARCHIVED.value, json.dumps({"merged_into": slug}, ensure_ascii=False))
        pool = self._db.pool
        if hasattr(pool, "acquire"):
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute(*update_args)
                await conn.execute(*archive_args)
        else:
            await pool.execute(*update_args)
            await pool.execute(*archive_args)


__all__ = [
    "ARCHIVE_DUPLICATE_COMPONENTS_SQL",
    "CONSOLIDATABLE_COMPONENT_TYPES",
    "DEFAULT_MIN_LEXICAL_SIMILARITY",
    "DEFAULT_MIN_VECTOR_SIMILARITY",
    "SELECT_CONSOLIDATION_CANDIDATES_SQL",
    "SELECT_PAIRWISE_VECTOR_SIMILARITY_SQL",
    "UPDATE_CANONICAL_COMPONENT_SQL",
    "ConsolidationCluster",
    "ConsolidationReport",
    "HarnessConsolidator",
    "compute_lexical_similarity",
    "synthesize_cluster_markdown",
]
