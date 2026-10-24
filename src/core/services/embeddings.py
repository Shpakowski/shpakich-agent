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
from typing import Any

import httpx

from src.core.config import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    CoreSettings,
    get_settings,
)
from src.core.enums import EmbeddingDialect, EmbeddingTaskType

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
    """Асинхронный мульти-диалектный клиент генерации эмбеддингов с встроенным Circuit Breaker."""

    def __init__(
        self,
        settings: CoreSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings: CoreSettings = settings or get_settings()
        self._transport: httpx.AsyncBaseTransport | None = transport
        self._cooldown_until: float = 0.0
        self._last_error: str | None = None

    @property
    def is_configured(self) -> bool:
        """Проверяет, сконфигурирован ли API-ключ или кастомный локальный URL эмбеддингов."""
        return self._settings.embedding_api_configured

    @property
    def resolved_dialect(self) -> EmbeddingDialect:
        """Определяет активный HTTP-диалект (явный или автоматически выведенный из `embedding_api_url`)."""
        configured = self._settings.embedding_dialect
        if configured != EmbeddingDialect.AUTO:
            return configured

        url_lower = self._settings.embedding_api_url.lower()
        if "generativelanguage.googleapis.com" in url_lower or ":embedcontent" in url_lower:
            return EmbeddingDialect.GEMINI_NATIVE
        return EmbeddingDialect.OPENAI_COMPATIBLE

    @property
    def is_circuit_open(self) -> bool:
        """Возвращает `True`, если Circuit Breaker находится в состоянии охлаждения после ошибки API."""
        return time.monotonic() < self._cooldown_until

    def reset_circuit(self) -> None:
        """Принудительно сбрасывает состояние Circuit Breaker (используется при смене настроек или в тестах)."""
        self._cooldown_until = 0.0
        self._last_error = None

    def get_status(self) -> dict[str, Any]:
        """Возвращает диагностический снимок состояния клиента эмбеддингов для `coop_get_factory_status`."""
        remaining = max(0.0, round(self._cooldown_until - time.monotonic(), 1))
        return {
            "configured": self.is_configured,
            "dialect": self.resolved_dialect.value,
            "model": self._settings.embedding_model,
            "dimensions": self._settings.embedding_dimensions,
            "circuit_open": self.is_circuit_open,
            "cooldown_remaining_s": remaining,
            "last_error": self._last_error,
        }

    def _trip_circuit(self, reason: str, cooldown_seconds: float) -> None:
        """Переводит Circuit Breaker в режим охлаждения на заданное число секунд."""
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
        """Генерирует L2-нормализованный вектор размерности `embedding_dimensions` или возвращает `None` при сбое."""
        if not text or not text.strip():
            return None
        if not self.is_configured or self.is_circuit_open:
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

    async def _embed_gemini_native(
        self,
        client: httpx.AsyncClient,
        *,
        text: str,
        task_type: EmbeddingTaskType,
        title: str | None,
    ) -> list[float]:
        """Выполняет запрос к нативному Gemini REST API (`:embedContent`)."""
        base = self._settings.embedding_api_url.rstrip("/")
        model = self._settings.embedding_model.strip()
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
        api_key = (self._settings.embedding_api_key or "").strip()
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
    ) -> list[float]:
        """Выполняет запрос к OpenAI-совместимому эндпоинту `/v1/embeddings` (`vLLM`, `TEI`, `Ollama`, `OpenAI`)."""
        base = self._settings.embedding_api_url.rstrip("/")
        endpoint = base if base.endswith("/embeddings") else f"{base}/embeddings"
        model = self._settings.embedding_model.strip()

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
        api_key = (self._settings.embedding_api_key or "").strip()
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


__all__ = [
    "EmbeddingClient",
    "format_embeddinggemma_prompt",
    "format_pgvector",
    "normalize_l2",
]
