"""Тонкий мульти-диалектный HTTP-клиент векторных эмбеддингов (Layer 2: Core Services).

Обеспечивает генерацию 768-мерных векторов для гибридного поиска `pgvector` без установки
тяжёлых локальных ML-фреймворков (`PyTorch`/`CUDA`) внутри Docker-контейнера:
- Поддерживает нативный Gemini REST API (`:embedContent` с `outputDimensionality: 768`)
  и OpenAI-совместимые серверы (`vLLM`, `HuggingFace TEI`, `Ollama`, `LiteLLM` для `EmbeddingGemma 2`).
- Автоматически применяет канонические промпт-префиксы `EmbeddingGemma 2` (`task: search result | query: ...`
  и `title: ... | text: ...`) и выполняет L2-нормализацию после Matryoshka-усечения (MRL).
- Включает легковесный Circuit Breaker с автоматическим переключением `DecisionEngine`
  на встроенный лексический поиск PostgreSQL (`tsvector + pg_trgm`) при недоступности API.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import httpx

from src.core.config import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    CoreSettings,
    get_settings,
)
from src.core.enums import (
    DecisionProviderDialect,
    EmbeddingDialect,
    EmbeddingTaskType,
)

if TYPE_CHECKING:
    from src.core.decision.providers import RuntimeProviderPool

logger = logging.getLogger("mcp_coop.core.services.embeddings")

MS_PER_SECOND: float = 1000.0
MIN_VECTOR_NORM: float = 1e-12
TRANSIENT_FAILURE_COOLDOWN_SECONDS: float = 60.0
QUOTA_AUTH_FAILURE_COOLDOWN_SECONDS: float = 600.0
QUOTA_AUTH_STATUS_CODES: frozenset[int] = frozenset({401, 402, 403, 429})
MAX_EMBEDDING_INPUT_CHARS: int = 8_000


def normalize_l2(
    vector: Sequence[float],
    target_dim: int = DEFAULT_EMBEDDING_DIMENSIONS,
) -> list[float]:
    """Выполняет Matryoshka-усечение (или дополнение нулями) до `target_dim` и L2-нормализацию.

    Почему это важно:
    - Модели с поддержкой Matryoshka Representation Learning (`EmbeddingGemma 2`, `gemini-embedding-001`)
      позволяют усекать выходной вектор до 768 измерений без потери семантического качества,
      но после усечения норма вектора перестаёт быть единичной, поэтому требуется повторная L2-нормализация
      для точного вычисления косинусного расстояния (`<=>`) в индексе HNSW `pgvector`.
    """
    if not vector or target_dim <= 0:
        return []

    sliced = [float(x) for x in vector[:target_dim]]
    if len(sliced) < target_dim:
        sliced.extend([0.0] * (target_dim - len(sliced)))

    norm = math.sqrt(sum(x * x for x in sliced))
    if norm <= MIN_VECTOR_NORM:
        return sliced
    return [round(x / norm, 6) for x in sliced]


def format_pgvector(vector: Sequence[float] | None) -> str | None:
    """Форматирует список чисел с плавающей точкой в строковый литерал `[x1,x2,...]` для `asyncpg` и `pgvector`."""
    if not vector:
        return None
    return f"[{','.join(f'{float(x):.6f}' for x in vector)}]"


def format_embeddinggemma_prompt(
    text: str,
    task_type: EmbeddingTaskType,
    title: str | None = None,
    model: str = "",
) -> str:
    """Форматирует входной текст по спецификации асимметричных промптов `EmbeddingGemma 2`.

    Для моделей семейства `embeddinggemma` применяются канонические префиксы:
    - Поисковый запрос: `task: search result | query: {content}`
    - Индексируемый документ памяти: `title: {title | none} | text: {content}`
    """
    cleaned = text.strip()[:MAX_EMBEDDING_INPUT_CHARS]
    if not cleaned:
        return ""

    if "embeddinggemma" in model.lower():
        if task_type == EmbeddingTaskType.RETRIEVAL_QUERY:
            return f"task: search result | query: {cleaned}"
        doc_title = (title or "none").strip() or "none"
        return f"title: {doc_title} | text: {cleaned}"

    return cleaned


class EmbeddingClient:
    """Асинхронный мульти-диалектный клиент генерации эмбеддингов с поддержкой каскадного пула провайдеров и Circuit Breaker."""

    def __init__(
        self,
        settings: CoreSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        provider_pool: RuntimeProviderPool | None = None,
    ) -> None:
        self._settings: CoreSettings = settings or get_settings()
        self._transport: httpx.AsyncBaseTransport | None = transport
        self._provider_pool: RuntimeProviderPool | None = provider_pool
        self._cooldown_until: float = 0.0
        self._last_error: str | None = None
        self._last_provider_used: str | None = None
        self._indexed_model: str | None = None

    @property
    def is_configured(self) -> bool:
        """Проверяет, есть ли активные провайдеры эмбеддингов в рантайм-пуле или задан ключ/URL в настройках."""
        if self._provider_pool is not None and self._provider_pool.get_enabled_embedding_providers():
            return True
        return self._settings.embedding_api_configured

    @property
    def resolved_dialect(self) -> EmbeddingDialect:
        """Определяет активный HTTP-диалект (из ведущего провайдера пула или из `CoreSettings`)."""
        if self._provider_pool is not None:
            pool_providers = self._provider_pool.get_enabled_embedding_providers()
            if pool_providers:
                primary = pool_providers[0]
                if primary.dialect == DecisionProviderDialect.GEMINI_NATIVE:
                    return EmbeddingDialect.GEMINI_NATIVE
                return EmbeddingDialect.OPENAI_COMPATIBLE

        configured = self._settings.embedding_dialect
        if configured != EmbeddingDialect.AUTO:
            return configured

        url_lower = self._settings.embedding_api_url.lower()
        if "generativelanguage.googleapis.com" in url_lower or ":embedcontent" in url_lower:
            return EmbeddingDialect.GEMINI_NATIVE
        return EmbeddingDialect.OPENAI_COMPATIBLE

    @property
    def is_circuit_open(self) -> bool:
        """Возвращает `True`, если локальный Circuit Breaker (без пула) находится в состоянии охлаждения."""
        return time.monotonic() < self._cooldown_until

    def reset_circuit(self) -> None:
        """Принудительно сбрасывает состояние локального Circuit Breaker."""
        self._cooldown_until = 0.0
        self._last_error = None

    def get_status(self) -> dict[str, Any]:
        """Возвращает диагностический снимок состояния клиента и пула эмбеддингов для `/health` и `coop_get_factory_status`."""
        pool_providers = (
            self._provider_pool.get_enabled_embedding_providers()
            if self._provider_pool is not None
            else []
        )
        primary = pool_providers[0] if pool_providers else None
        remaining = max(0.0, round(self._cooldown_until - time.monotonic(), 1))
        return {
            "configured": self.is_configured,
            "dialect": self.resolved_dialect.value,
            "model": primary.model if primary is not None else self._settings.embedding_model,
            "dimensions": self._settings.embedding_dimensions,
            "circuit_open": self.is_circuit_open if not pool_providers else False,
            "cooldown_remaining_s": remaining,
            "last_error": self._last_error,
            "pool_providers_count": len(pool_providers),
            "primary_provider": primary.name if primary is not None else None,
            "last_provider_used": self._last_provider_used,
        }

    def _trip_circuit(self, reason: str, cooldown_seconds: float) -> None:
        """Переводит локальный Circuit Breaker в режим охлаждения на заданное число секунд."""
        self._cooldown_until = time.monotonic() + cooldown_seconds
        self._last_error = reason
        logger.debug(
            "EmbeddingClient circuit tripped for %.0fs: %s",
            cooldown_seconds,
            reason,
        )

    async def embed_text(
        self,
        text: str,
        *,
        task_type: EmbeddingTaskType = EmbeddingTaskType.RETRIEVAL_QUERY,
        title: str | None = None,
    ) -> list[float] | None:
        """Генерирует L2-нормализованный вектор размерности `embedding_dimensions` с каскадным переключением по пулу провайдеров."""
        if not text or not text.strip():
            return None

        pool_providers = (
            self._provider_pool.get_enabled_embedding_providers()
            if self._provider_pool is not None
            else []
        )
        if pool_providers and self._provider_pool is not None:
            return await self._embed_via_provider_pool(
                pool_providers,
                text=text,
                task_type=task_type,
                title=title,
            )

        if not self._settings.embedding_api_configured or self.is_circuit_open:
            return None

        timeout_s = self._settings.embedding_timeout_ms / MS_PER_SECOND
        try:
            async with httpx.AsyncClient(
                timeout=timeout_s,
                transport=self._transport,
            ) as client:
                if self.resolved_dialect == EmbeddingDialect.GEMINI_NATIVE:
                    raw_vec = await self._embed_gemini_native(
                        client,
                        text=text,
                        task_type=task_type,
                        title=title,
                    )
                else:
                    raw_vec = await self._embed_openai_compatible(
                        client,
                        text=text,
                        task_type=task_type,
                        title=title,
                    )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            cooldown = (
                QUOTA_AUTH_FAILURE_COOLDOWN_SECONDS
                if status_code in QUOTA_AUTH_STATUS_CODES
                else TRANSIENT_FAILURE_COOLDOWN_SECONDS
            )
            self._trip_circuit(f"HTTP {status_code}", cooldown)
            return None
        except Exception as exc:  # noqa: BLE001
            self._trip_circuit(str(exc), TRANSIENT_FAILURE_COOLDOWN_SECONDS)
            return None

        if not raw_vec:
            return None

        self._last_error = None
        return normalize_l2(raw_vec, self._settings.embedding_dimensions)

    async def _embed_via_provider_pool(
        self,
        providers: Sequence[Any],
        *,
        text: str,
        task_type: EmbeddingTaskType,
        title: str | None,
    ) -> list[float] | None:
        """Выполняет каскадный обход эмбеддинг-провайдеров по приоритету с переключением при лимитах/сбоях."""
        assert self._provider_pool is not None
        for cfg in providers:
            circuit = await self._provider_pool.get_active_circuit(cfg.name)
            if circuit is not None:
                continue

            timeout_s = max(0.1, float(cfg.timeout_ms) / MS_PER_SECOND)
            try:
                async with httpx.AsyncClient(
                    timeout=timeout_s,
                    transport=self._transport,
                ) as client:
                    if cfg.dialect == DecisionProviderDialect.GEMINI_NATIVE:
                        raw_vec = await self._embed_gemini_native(
                            client,
                            text=text,
                            task_type=task_type,
                            title=title,
                            base_url_override=cfg.base_url,
                            model_override=cfg.model,
                            api_key_override=cfg.api_key,
                        )
                    else:
                        raw_vec = await self._embed_openai_compatible(
                            client,
                            text=text,
                            task_type=task_type,
                            title=title,
                            base_url_override=cfg.base_url,
                            model_override=cfg.model,
                            api_key_override=cfg.api_key,
                        )
                if raw_vec:
                    self._last_error = None
                    self._last_provider_used = cfg.name
                    return normalize_l2(raw_vec, self._settings.embedding_dimensions)
            except Exception as exc:  # noqa: BLE001
                status, cooldown_s, reason = await self._provider_pool.record_provider_failure(
                    cfg.name,
                    exc,
                )
                self._last_error = f"{cfg.name} [{status}]: {reason}"
                logger.warning(
                    "Embedding provider '%s' (%s) failed [%s] (%s); cooling down %ds and failing over",
                    cfg.name,
                    cfg.model,
                    status,
                    reason,
                    cooldown_s,
                )

        return None

    async def _embed_gemini_native(
        self,
        client: httpx.AsyncClient,
        *,
        text: str,
        task_type: EmbeddingTaskType,
        title: str | None,
        base_url_override: str | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
    ) -> list[float]:
        """Выполняет запрос к нативному Gemini REST API (`:embedContent`)."""
        base = (base_url_override or self._settings.embedding_api_url).rstrip("/")
        model = (model_override or self._settings.embedding_model).strip()
        model_path = model if model.startswith("models/") else f"models/{model}"
        endpoint = (
            base
            if base.endswith(":embedContent")
            else f"{base}/{model_path}:embedContent"
        )

        prompt = format_embeddinggemma_prompt(
            text,
            task_type=task_type,
            title=title,
            model=model,
        )
        payload: dict[str, Any] = {
            "model": model_path,
            "content": {"parts": [{"text": prompt}]},
            "taskType": task_type.value,
            "outputDimensionality": self._settings.embedding_dimensions,
        }
        if title and task_type == EmbeddingTaskType.RETRIEVAL_DOCUMENT:
            payload["title"] = title.strip()

        headers: dict[str, str] = {"Content-Type": "application/json"}
        raw_key = api_key_override if api_key_override is not None else (self._settings.embedding_api_key or "")
        api_key = raw_key.strip()
        if api_key:
            headers["x-goog-api-key"] = api_key

        resp = await client.post(endpoint, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        values = data.get("embedding", {}).get("values")
        if not isinstance(values, list):
            return []
        return [float(v) for v in values]

    async def _embed_openai_compatible(
        self,
        client: httpx.AsyncClient,
        *,
        text: str,
        task_type: EmbeddingTaskType,
        title: str | None,
        base_url_override: str | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
    ) -> list[float]:
        """Выполняет запрос к OpenAI-совместимому эндпоинту `/v1/embeddings` (`vLLM`, `TEI`, `Ollama`, `OpenAI`)."""
        base = (base_url_override or self._settings.embedding_api_url).rstrip("/")
        endpoint = base if base.endswith("/embeddings") else f"{base}/embeddings"
        model = (model_override or self._settings.embedding_model).strip()

        prompt = format_embeddinggemma_prompt(
            text,
            task_type=task_type,
            title=title,
            model=model,
        )
        payload: dict[str, Any] = {
            "model": model,
            "input": prompt,
            "dimensions": self._settings.embedding_dimensions,
        }

        headers: dict[str, str] = {"Content-Type": "application/json"}
        raw_key = api_key_override if api_key_override is not None else (self._settings.embedding_api_key or "")
        api_key = raw_key.strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        resp = await client.post(endpoint, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict) and isinstance(first.get("embedding"), list):
                return [float(v) for v in first["embedding"]]
        if isinstance(data.get("embedding"), list):
            return [float(v) for v in data["embedding"]]
        return []

    async def backfill_missing_embeddings(
        self,
        db: Any,
        *,
        batch_limit: int = 50,
        force_reindex: bool = False,
    ) -> int:
        """Досчитывает отсутствующие 768-мерные векторы (а при смене модели `force_reindex=True` — полностью пересчитывает индекс по текстам в БД)."""
        if db is None or not getattr(db, "is_connected", False) or not self.is_configured:
            return 0

        current_model = str(self.get_status().get("model") or "").strip()
        should_reset = force_reindex or (
            self._indexed_model is not None
            and current_model != ""
            and self._indexed_model != current_model
        )

        updated_total = 0
        try:
            if should_reset:
                logger.info(
                    "Active embedding model changed (%s -> %s); resetting vector index for full recalculation",
                    self._indexed_model,
                    current_model,
                )
                async with db.pool.acquire() as conn, conn.transaction():
                    await conn.execute("UPDATE harness_components SET embedding = NULL WHERE embedding IS NOT NULL")
                    await conn.execute("UPDATE project_context_nodes SET embedding = NULL WHERE embedding IS NOT NULL")
                    await conn.execute("UPDATE memories SET embedding = NULL WHERE embedding IS NOT NULL")
                    await conn.execute("UPDATE session_compactions SET embedding = NULL WHERE embedding IS NOT NULL")

            if current_model:
                self._indexed_model = current_model

            comp_rows = await db.pool.fetch(
                """
                SELECT slug, title, description, content
                FROM harness_components
                WHERE embedding IS NULL AND content <> ''
                ORDER BY score DESC, slug ASC
                LIMIT $1
                """,
                batch_limit,
            )
            for row in comp_rows:
                if not self.is_configured:
                    break
                text_body = f"{row['title']}\n{row['description']}\n{row['content'][:2000]}"
                vec = await self.embed_text(
                    text_body,
                    task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT,
                    title=str(row["title"]),
                )
                vec_lit = format_pgvector(vec)
                if vec_lit is None:
                    break
                await db.pool.execute(
                    "UPDATE harness_components SET embedding = $2::vector WHERE slug = $1",
                    str(row["slug"]),
                    vec_lit,
                )
                updated_total += 1

            node_rows = await db.pool.fetch(
                """
                SELECT id, node_id, content
                FROM project_context_nodes
                WHERE embedding IS NULL AND content <> ''
                LIMIT $1
                """,
                batch_limit,
            )
            for row in node_rows:
                if not self.is_configured:
                    break
                vec = await self.embed_text(
                    str(row["content"])[:3000],
                    task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT,
                    title=str(row["node_id"]),
                )
                vec_lit = format_pgvector(vec)
                if vec_lit is None:
                    break
                await db.pool.execute(
                    "UPDATE project_context_nodes SET embedding = $2::vector WHERE id = $1",
                    row["id"],
                    vec_lit,
                )
                updated_total += 1

            mem_rows = await db.pool.fetch(
                """
                SELECT id, title, error_symptom, solution
                FROM memories
                WHERE embedding IS NULL
                LIMIT $1
                """,
                batch_limit,
            )
            for row in mem_rows:
                if not self.is_configured:
                    break
                text_body = f"{row['title']}\n{row['error_symptom']}\n{row['solution']}"[:3000]
                vec = await self.embed_text(
                    text_body,
                    task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT,
                    title=str(row["title"]),
                )
                vec_lit = format_pgvector(vec)
                if vec_lit is None:
                    break
                await db.pool.execute(
                    "UPDATE memories SET embedding = $2::vector WHERE id = $1",
                    row["id"],
                    vec_lit,
                )
                updated_total += 1

            compaction_rows = await db.pool.fetch(
                """
                SELECT id, conversation_id, summary_text
                FROM session_compactions
                WHERE embedding IS NULL AND summary_text <> ''
                LIMIT $1
                """,
                batch_limit,
            )
            for row in compaction_rows:
                if not self.is_configured:
                    break
                vec = await self.embed_text(
                    str(row["summary_text"])[:3000],
                    task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT,
                    title=str(row["conversation_id"]),
                )
                vec_lit = format_pgvector(vec)
                if vec_lit is None:
                    break
                await db.pool.execute(
                    "UPDATE session_compactions SET embedding = $2::vector WHERE id = $1",
                    row["id"],
                    vec_lit,
                )
                updated_total += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("Background embedding backfill interrupted: %s", exc)

        if updated_total > 0:
            logger.info("Backfilled %d missing embeddings in PostgreSQL (model=%s)", updated_total, current_model)
        return updated_total


__all__ = [
    "EmbeddingClient",
    "format_embeddinggemma_prompt",
    "format_pgvector",
    "normalize_l2",
]
