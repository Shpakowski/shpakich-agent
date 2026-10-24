"""Пакет сервисных клиентов Python-ядра (`src/core/services/`).

Экспортирует тонкий мульти-диалектный HTTP-клиент векторных эмбеддингов `EmbeddingClient`
и вспомогательные функции нормализации/форматирования для `pgvector`.
"""

from __future__ import annotations

from src.core.services.embeddings import (
    EmbeddingClient,
    format_embeddinggemma_prompt,
    format_pgvector,
    normalize_l2,
)

__all__ = [
    "EmbeddingClient",
    "format_embeddinggemma_prompt",
    "format_pgvector",
    "normalize_l2",
]
