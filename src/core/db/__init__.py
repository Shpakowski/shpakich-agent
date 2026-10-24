"""Пакет персистентного и оперативного хранения данных (Layer 2: Core Database Layer).

Объединяет нативные асинхронные адаптеры для персистентного векторно-полнотекстового
хранилища PostgreSQL 18 (pgvector + pg_trgm) и оперативной памяти сессий / LangCache
на базе Redis в рамках жизненного цикла приложения FastAPI без использования тяжёлых ORM.
"""

from __future__ import annotations

from src.core.db.postgres import SCHEMA_DDL, PostgresDatabase, normalize_asyncpg_dsn
from src.core.db.redis_client import (
    HASH_DIGEST_LENGTH,
    MemoryPromotionCandidate,
    REDIS_KEY_SEPARATOR,
    RedisSessionStore,
    SessionLastError,
    compute_command_hash,
    compute_stack_hash,
)

__all__ = [
    "HASH_DIGEST_LENGTH",
    "MemoryPromotionCandidate",
    "PostgresDatabase",
    "REDIS_KEY_SEPARATOR",
    "RedisSessionStore",
    "SCHEMA_DDL",
    "SessionLastError",
    "compute_command_hash",
    "compute_stack_hash",
    "normalize_asyncpg_dsn",
]
