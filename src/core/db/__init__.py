"""Пакет персистентного и оперативного хранения данных (Layer 2: Core Database Layer).

Объединяет нативные асинхронные адаптеры для работы с PostgreSQL 18 (pgvector + pg_trgm)
и Redis в рамках жизненного цикла приложения FastAPI без использования тяжёлых ORM.
"""

from __future__ import annotations

from src.core.db.postgres import SCHEMA_DDL, PostgresDatabase, normalize_asyncpg_dsn

__all__ = [
    "PostgresDatabase",
    "SCHEMA_DDL",
    "normalize_asyncpg_dsn",
]
