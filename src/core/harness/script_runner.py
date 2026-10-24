"""Динамический диспетчер и рантайм-исполнитель скриптов жизненных хуков (`HookScriptDispatcher`, Layer 2 & 4).

Реализует декаплированную архитектуру `Hook Event Bus + DB Script Registry with Embeddings`:
1. Жизненный хук (`session-start`, `pre-tool-use`, `post-tool-use`, `pre-invocation`, `stop`) выступает
   чистой шиной событий и не содержит захардкоженной логики конкретных проверок.
2. Все исполняемые скрипты (`ComponentType.SCRIPT`) хранятся в PostgreSQL (`harness_components`)
   и снабжены 768-мерными векторами `pgvector` и полнотекстовым индексом `search_tsv`.
3. При поступлении события хука диспетчер пропускает каталог скриптов через двухступенчатую воронку:
   - Воронка 1 (Проектный и событийный фильтр): отбирает скрипты со статусом `composed`, подписанные
     на данный `HookEventType` в `metadata.hook_events`, подходящие под `stack_tags` проекта из таблицы
     `projects` и не отключённые в `ProjectHarnessOverrides.disabled_components`.
   - Воронка 2 (Семантический фильтр через Embeddings / Hybrid Search): базовые инвариантные скрипты
     (`execution_mode = "always"` или принудительно включённые в `enabled_components`) проходят сразу,
     а из узкоспециализированных скриптов (`execution_mode = "semantic"`, которых в БД может быть 1000+)
     через `EmbeddingClient` (`embedding <=> $vec`) и `tsvector`/`pg_trgm` выбираются только Top-K
     наиболее релевантных текущему контексту вызова (промпту, команде, пути файла, ошибке).
4. Отобранные скрипты изолированно исполняются внутри контейнера с контролем таймаута, атомарно обновляют
   состояние сессии в Redis (`session:{conversation_id}:meta`) и агрегируют вердикты по правилу строгого вето.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums import (
    ComponentStatus,
    ComponentType,
    EmbeddingTaskType,
    HookEventType,
    ScopeType,
    ScriptExecutionMode,
    VerdictAction,
)
from src.core.harness.compiler import ProjectHarnessOverrides
from src.core.harness.seed import resolve_seed_components_with_overrides
from src.core.services.embeddings import EmbeddingClient, format_pgvector

if TYPE_CHECKING:
    from src.core.db.postgres import PostgresDatabase
    from src.core.db.redis_client import RedisSessionStore
    from src.core.decision.hook_dialects import CanonicalHookInput

logger = logging.getLogger("mcp_coop.core.harness.script_runner")

DEFAULT_SCRIPT_PRIORITY: int = 100
DEFAULT_SCRIPT_TIMEOUT_MS: int = 500
MIN_SCRIPT_TIMEOUT_MS: int = 50
MAX_SCRIPT_TIMEOUT_MS: int = 5_000
DEFAULT_MAX_SEMANTIC_SCRIPTS: int = 3
DEFAULT_MIN_SEMANTIC_SCORE: float = 0.15
MS_PER_SECOND: float = 1000.0

ACTION_PRECEDENCE: dict[VerdictAction, int] = {
    VerdictAction.ALLOW: 0,
    VerdictAction.OVERWRITE: 10,
    VerdictAction.ASK: 20,
    VerdictAction.FORCE_ASK: 30,
    VerdictAction.CONTINUE: 40,
    VerdictAction.DENY: 50,
}

SELECT_ALWAYS_SCRIPTS_SQL: str = """
SELECT
    slug,
    title,
    description,
    content,
    metadata,
    score
FROM harness_components
WHERE component_type = $1
  AND NOT (slug = ANY($5::text[]))
  AND (
      slug = ANY($4::text[])
      OR (
          status = $2
          AND (scope = $3 OR stack_tags && $6::text[])
          AND COALESCE(metadata->>'execution_mode', 'always') = 'always'
      )
  )
  AND (
      (jsonb_typeof(metadata->'hook_events') = 'array' AND (metadata->'hook_events') ? $7)
      OR metadata->>'hook_event' = $7
  )
ORDER BY COALESCE((metadata->>'priority')::int, 100) ASC, score DESC, created_at ASC
"""

SELECT_SEMANTIC_SCRIPTS_VECTOR_SQL: str = """
SELECT
    slug,
    title,
    description,
    content,
    metadata,
    score,
    (
        CASE
            WHEN embedding IS NOT NULL THEN (1.0 - (embedding <=> $8::vector)) * 0.65
            ELSE 0.0
        END
        + ts_rank_cd(search_tsv, websearch_to_tsquery('simple', $9)) * 0.20
        + word_similarity($9, title || ' ' || description) * 0.15
    ) AS relevance
FROM harness_components
WHERE component_type = $1
  AND status = $2
  AND NOT (slug = ANY($4::text[]))
  AND NOT (slug = ANY($5::text[]))
  AND (scope = $3 OR stack_tags && $6::text[])
  AND COALESCE(metadata->>'execution_mode', 'always') = 'semantic'
  AND (
      (jsonb_typeof(metadata->'hook_events') = 'array' AND (metadata->'hook_events') ? $7)
      OR metadata->>'hook_event' = $7
  )
ORDER BY relevance DESC, score DESC
LIMIT $10
"""

SELECT_SEMANTIC_SCRIPTS_LEXICAL_SQL: str = """
SELECT
    slug,
    title,
    description,
    content,
    metadata,
    score,
    (
        ts_rank_cd(search_tsv, websearch_to_tsquery('simple', $8)) * 0.60
        + word_similarity($8, title || ' ' || description) * 0.40
    ) AS relevance
FROM harness_components
WHERE component_type = $1
  AND status = $2
  AND NOT (slug = ANY($4::text[]))
  AND NOT (slug = ANY($5::text[]))
  AND (scope = $3 OR stack_tags && $6::text[])
  AND COALESCE(metadata->>'execution_mode', 'always') = 'semantic'
  AND (
      (jsonb_typeof(metadata->'hook_events') = 'array' AND (metadata->'hook_events') ? $7)
      OR metadata->>'hook_event' = $7
  )
ORDER BY relevance DESC, score DESC
LIMIT $9
"""

INCREMENT_SCRIPT_USAGE_SQL: str = """
UPDATE harness_components
SET usage_count = usage_count + 1,
    updated_at = NOW()
WHERE slug = ANY($1::text[])
"""


class ResolvedHookScript(BaseModel):
    """Нормализованная карточка скрипта `ComponentType.SCRIPT`, отобранного для исполнения на хуке."""

    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    content: str
    execution_mode: ScriptExecutionMode = ScriptExecutionMode.ALWAYS
    priority: int = DEFAULT_SCRIPT_PRIORITY
    timeout_ms: int = DEFAULT_SCRIPT_TIMEOUT_MS
    relevance: float = 1.0


class ScriptPipelineResult(BaseModel):
    """Агрегированный результат выполнения цепочки скриптов жизненного хука."""

    model_config = ConfigDict(frozen=True)

    action: VerdictAction = VerdictAction.ALLOW
    reason: str | None = None
    system_messages: tuple[str, ...] = ()
    ephemeral_messages: tuple[str, ...] = ()
    overwrite_args: dict[str, Any] | None = None
    executed_slugs: tuple[str, ...] = ()
    state_updates: dict[str, str] = Field(default_factory=dict)


def _parse_metadata_dict(raw_meta: Any) -> dict[str, Any]:
    """Безопасно десериализует JSONB-метаданные компонента из строки или словаря."""
    if isinstance(raw_meta, Mapping):
        return dict(raw_meta)
    if isinstance(raw_meta, str) and raw_meta.strip():
        try:
            parsed = json.loads(raw_meta)
            if isinstance(parsed, Mapping):
                return dict(parsed)
        except json.JSONDecodeError:
            return {}
    return {}


def _script_matches_hook_event(metadata: Mapping[str, Any], event: HookEventType) -> bool:
    """Проверяет, подписан ли скрипт на целевое событие жизненного хука."""
    target = event.value
    events_list = metadata.get("hook_events")
    if (
        isinstance(events_list, Sequence)
        and not isinstance(events_list, (str, bytes))
        and any(str(item).strip().lower() == target for item in events_list)
    ):
        return True
    single_event = metadata.get("hook_event")
    return isinstance(single_event, str) and single_event.strip().lower() == target


def _build_resolved_script(
    slug: str,
    title: str,
    content: str,
    raw_metadata: Any,
    relevance: float = 1.0,
) -> ResolvedHookScript:
    """Формирует валидированную модель `ResolvedHookScript` с нормализованным приоритетом и таймаутом."""
    meta = _parse_metadata_dict(raw_metadata)
    raw_mode = str(meta.get("execution_mode") or ScriptExecutionMode.ALWAYS.value).strip().lower()
    mode = (
        ScriptExecutionMode.SEMANTIC
        if raw_mode == ScriptExecutionMode.SEMANTIC.value
        else ScriptExecutionMode.ALWAYS
    )
    raw_prio = meta.get("priority", DEFAULT_SCRIPT_PRIORITY)
    try:
        priority = int(raw_prio)
    except (TypeError, ValueError):
        priority = DEFAULT_SCRIPT_PRIORITY

    raw_timeout = meta.get("timeout_ms", DEFAULT_SCRIPT_TIMEOUT_MS)
    try:
        timeout_ms = max(MIN_SCRIPT_TIMEOUT_MS, min(MAX_SCRIPT_TIMEOUT_MS, int(raw_timeout)))
    except (TypeError, ValueError):
        timeout_ms = DEFAULT_SCRIPT_TIMEOUT_MS

    return ResolvedHookScript(
        slug=slug,
        title=title,
        content=content,
        execution_mode=mode,
        priority=priority,
        timeout_ms=timeout_ms,
        relevance=round(float(relevance), 4),
    )


def build_hook_semantic_query(hook_in: CanonicalHookInput) -> str:
    """Собирает информативный поисковый запрос из контекста хука для векторно-лексического отбора скриптов."""
    parts: list[str] = [f"hook:{hook_in.event_type.value}"]
    if hook_in.tool_name:
        parts.append(f"tool:{hook_in.tool_name}")
    if hook_in.tool_args:
        for key in ("CommandLine", "command", "cmd", "Path", "path", "file_path", "TargetFile"):
            val = hook_in.tool_args.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip()[:300])
    if hook_in.prompt:
        parts.append(hook_in.prompt.strip()[:500])
    if hook_in.error:
        parts.append(hook_in.error.strip()[:400])
    return " | ".join(parts)


class HookScriptDispatcher:
    """Диспетчер динамического отбора (SQL + pgvector) и изолированного исполнения Python-скриптов хуков."""

    def __init__(
        self,
        db: PostgresDatabase | None = None,
        redis: RedisSessionStore | None = None,
        embedding_client: EmbeddingClient | None = None,
        *,
        max_semantic_scripts: int = DEFAULT_MAX_SEMANTIC_SCRIPTS,
        min_semantic_score: float = DEFAULT_MIN_SEMANTIC_SCORE,
    ) -> None:
        self._db: PostgresDatabase | None = db
        self._redis: RedisSessionStore | None = redis
        self._embedding_client: EmbeddingClient | None = embedding_client
        self._max_semantic_scripts: int = max(1, max_semantic_scripts)
        self._min_semantic_score: float = min_semantic_score

    async def select_scripts_for_hook(
        self,
        hook_in: CanonicalHookInput,
        stack_tags: Sequence[str],
        profile_overrides: Mapping[str, Any] | None = None,
    ) -> list[ResolvedHookScript]:
        """Отбирает скрипты из PostgreSQL для заданного события хука, стека проекта и семантического контекста."""
        overrides = ProjectHarnessOverrides.from_mapping(profile_overrides)
        if hook_in.event_type in overrides.disabled_hooks:
            return []

        normalized_tags = sorted(
            {tag.strip().lower() for tag in stack_tags if tag and tag.strip()}
            | {ScopeType.GLOBAL.value}
        )
        enabled_list = list(overrides.enabled_components)
        disabled_list = list(overrides.disabled_components)

        if self._db is not None and self._db.is_connected:
            try:
                return await self._select_from_postgres(
                    hook_in=hook_in,
                    normalized_tags=normalized_tags,
                    enabled_list=enabled_list,
                    disabled_list=disabled_list,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PostgreSQL script selection failed (%s); falling back to seed scripts",
                    exc,
                )

        return self._select_from_seeds(
            event=hook_in.event_type,
            normalized_tags=normalized_tags,
            enabled_list=enabled_list,
            disabled_list=disabled_list,
        )

    async def _select_from_postgres(
        self,
        hook_in: CanonicalHookInput,
        normalized_tags: list[str],
        enabled_list: list[str],
        disabled_list: list[str],
    ) -> list[ResolvedHookScript]:
        """Выполняет двухступенчатый отбор (`always` + `semantic` Top-K) в таблице `harness_components`."""
        assert self._db is not None
        event_val = hook_in.event_type.value

        always_rows = await self._db.pool.fetch(
            SELECT_ALWAYS_SCRIPTS_SQL,
            ComponentType.SCRIPT.value,
            ComponentStatus.COMPOSED.value,
            ScopeType.GLOBAL.value,
            enabled_list,
            disabled_list,
            normalized_tags,
            event_val,
        )
        selected: list[ResolvedHookScript] = [
            _build_resolved_script(
                slug=str(row["slug"]),
                title=str(row["title"]),
                content=str(row["content"]),
                raw_metadata=row["metadata"],
                relevance=1.0,
            )
            for row in always_rows
        ]
        selected_slugs = {s.slug for s in selected}

        query_text = build_hook_semantic_query(hook_in)
        vector_literal: str | None = None
        if self._embedding_client is not None:
            vec = await self._embedding_client.embed_text(
                query_text,
                task_type=EmbeddingTaskType.RETRIEVAL_QUERY,
            )
            vector_literal = format_pgvector(vec)

        if vector_literal is not None:
            sem_rows = await self._db.pool.fetch(
                SELECT_SEMANTIC_SCRIPTS_VECTOR_SQL,
                ComponentType.SCRIPT.value,
                ComponentStatus.COMPOSED.value,
                ScopeType.GLOBAL.value,
                enabled_list,
                disabled_list,
                normalized_tags,
                event_val,
                vector_literal,
                query_text,
                self._max_semantic_scripts,
            )
        else:
            sem_rows = await self._db.pool.fetch(
                SELECT_SEMANTIC_SCRIPTS_LEXICAL_SQL,
                ComponentType.SCRIPT.value,
                ComponentStatus.COMPOSED.value,
                ScopeType.GLOBAL.value,
                enabled_list,
                disabled_list,
                normalized_tags,
                event_val,
                query_text,
                self._max_semantic_scripts,
            )

        for row in sem_rows:
            slug = str(row["slug"])
            relevance = float(row["relevance"] or 0.0)
            if slug not in selected_slugs and relevance >= self._min_semantic_score:
                selected.append(
                    _build_resolved_script(
                        slug=slug,
                        title=str(row["title"]),
                        content=str(row["content"]),
                        raw_metadata=row["metadata"],
                        relevance=relevance,
                    )
                )
                selected_slugs.add(slug)

        selected.sort(key=lambda item: (item.priority, -item.relevance, item.slug))
        return selected

    @staticmethod
    def _select_from_seeds(
        event: HookEventType,
        normalized_tags: list[str],
        enabled_list: list[str],
        disabled_list: list[str],
    ) -> list[ResolvedHookScript]:
        """Аварийный фолбэк-отбор скриптов из `DEFAULT_HARNESS_SEEDS`, если пул PostgreSQL не инициализирован."""
        seeds = resolve_seed_components_with_overrides(
            stack_tags=normalized_tags,
            enabled_components=enabled_list,
            disabled_components=disabled_list,
        )
        matched: list[ResolvedHookScript] = []
        for seed in seeds:
            if seed.component_type != ComponentType.SCRIPT:
                continue
            if not _script_matches_hook_event(seed.metadata, event):
                continue
            matched.append(
                _build_resolved_script(
                    slug=seed.slug,
                    title=seed.title,
                    content=seed.content,
                    raw_metadata=seed.metadata,
                    relevance=1.0,
                )
            )
        matched.sort(key=lambda item: (item.priority, item.slug))
        return matched

    async def execute_hook_scripts(
        self,
        hook_in: CanonicalHookInput,
        stack_tags: Sequence[str],
        profile_overrides: Mapping[str, Any] | None = None,
    ) -> ScriptPipelineResult:
        """Отбирает и последовательно исполняет релевантные скрипты для события хука."""
        scripts = await self.select_scripts_for_hook(
            hook_in=hook_in,
            stack_tags=stack_tags,
            profile_overrides=profile_overrides,
        )
        if not scripts:
            return ScriptPipelineResult()

        session_state: dict[str, str] = {}
        if self._redis is not None and self._redis.is_connected:
            with contextlib.suppress(Exception):
                session_state = await self._redis.get_session_meta(hook_in.conversation_id)

        context_index_md = str(
            hook_in.raw_payload.get("context_index_md") or ""
        ).strip()

        final_action = VerdictAction.ALLOW
        final_reason: str | None = None
        final_overwrite: dict[str, Any] | None = None
        sys_messages: list[str] = []
        eph_messages: list[str] = []
        executed_slugs: list[str] = []
        accumulated_state_updates: dict[str, str] = {}

        for script in scripts:
            stdin_obj: dict[str, Any] = {
                "hook_event": hook_in.event_type.value,
                "conversation_id": hook_in.conversation_id,
                "workspace_path": hook_in.workspace_path,
                "agent_type": hook_in.agent_type.value,
                "stack_tags": list(stack_tags),
                "tool_name": hook_in.tool_name,
                "tool_args": dict(hook_in.tool_args),
                "prompt": hook_in.prompt,
                "error": hook_in.error,
                "stop_hook_active": hook_in.stop_hook_active,
                "context_index_md": context_index_md,
                "session_state": dict(session_state),
                "raw_payload": hook_in.raw_payload,
            }
            output = await self._run_single_script(script, stdin_obj)
            if output is None:
                continue

            executed_slugs.append(script.slug)

            raw_updates = output.get("state_updates")
            if isinstance(raw_updates, Mapping) and raw_updates:
                normalized_updates = {str(k): str(v) for k, v in raw_updates.items()}
                session_state.update(normalized_updates)
                accumulated_state_updates.update(normalized_updates)

            sys_msg = output.get("system_message")
            if isinstance(sys_msg, str) and sys_msg.strip():
                sys_messages.append(sys_msg.strip())

            eph_msg = output.get("ephemeral_message")
            if isinstance(eph_msg, str) and eph_msg.strip():
                eph_messages.append(eph_msg.strip())

            raw_overwrite = output.get("overwrite_args") or output.get("overwrite")
            if isinstance(raw_overwrite, Mapping) and raw_overwrite:
                final_overwrite = dict(raw_overwrite)

            raw_action = str(
                output.get("action") or output.get("decision") or ""
            ).strip().lower()
            if raw_action in VerdictAction._value2member_map_:
                candidate_action = VerdictAction(raw_action)
                if ACTION_PRECEDENCE[candidate_action] > ACTION_PRECEDENCE[final_action]:
                    final_action = candidate_action
                    raw_reason = output.get("reason")
                    if isinstance(raw_reason, str) and raw_reason.strip():
                        final_reason = raw_reason.strip()

        if (
            accumulated_state_updates
            and self._redis is not None
            and self._redis.is_connected
        ):
            with contextlib.suppress(Exception):
                await self._redis.update_session_meta(
                    hook_in.conversation_id,
                    accumulated_state_updates,
                )

        if executed_slugs and self._db is not None and self._db.is_connected:
            with contextlib.suppress(Exception):
                await self._db.pool.execute(INCREMENT_SCRIPT_USAGE_SQL, executed_slugs)

        return ScriptPipelineResult(
            action=final_action,
            reason=final_reason,
            system_messages=tuple(sys_messages),
            ephemeral_messages=tuple(eph_messages),
            overwrite_args=final_overwrite,
            executed_slugs=tuple(executed_slugs),
            state_updates=accumulated_state_updates,
        )

    @staticmethod
    async def _run_single_script(
        script: ResolvedHookScript,
        stdin_payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Запускает Python-код скрипта в изолированном подпроцессе с жёстким лимитом времени."""
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                script.content,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdin_bytes = json.dumps(stdin_payload, ensure_ascii=False).encode("utf-8")
            timeout_sec = script.timeout_ms / MS_PER_SECOND
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes),
                timeout=timeout_sec,
            )
            if proc.returncode != 0:
                logger.warning(
                    "Hook script '%s' exited with code %s: %s",
                    script.slug,
                    proc.returncode,
                    stderr_bytes.decode("utf-8", errors="replace").strip(),
                )
                return None

            raw_out = stdout_bytes.decode("utf-8", errors="replace").strip()
            if not raw_out:
                return {}
            parsed = json.loads(raw_out)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except TimeoutError:
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            logger.warning(
                "Hook script '%s' timed out after %d ms",
                script.slug,
                script.timeout_ms,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Hook script '%s' execution failed: %s", script.slug, exc)
            return None


__all__ = [
    "DEFAULT_MAX_SEMANTIC_SCRIPTS",
    "DEFAULT_MIN_SEMANTIC_SCORE",
    "DEFAULT_SCRIPT_PRIORITY",
    "DEFAULT_SCRIPT_TIMEOUT_MS",
    "HookScriptDispatcher",
    "ResolvedHookScript",
    "ScriptPipelineResult",
    "build_hook_semantic_query",
]
