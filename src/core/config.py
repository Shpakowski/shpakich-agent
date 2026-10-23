"""Модуль типизированной конфигурации Python-ядра (Layer 2: Core Engine).

Загружает и валидирует параметры окружения контейнера через Pydantic BaseSettings,
обеспечивая безопасный фолбэк при отсутствии внешних API-ключей (OpenAI / Embeddings).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core import __version__


class CoreSettings(BaseSettings):
    """Централизованная конфигурация ядра фабрики, синхронизированная с docker-compose.yml.

    Почему используется BaseSettings + AliasChoices:
    - Гарантирует строгую проверку типов на старте контейнера (Fail-Fast при неверном порте или DSN).
    - Позволяет задавать переменные окружения как с префиксом `MCP_COOP_`, так и напрямую
      (`POSTGRES_DSN`, `REDIS_URL`, `OPENAI_API_KEY`, `EMBEDDING_API_KEY`), как в `docker-compose.yml`.
    """

    model_config = SettingsConfigDict(
        env_prefix="MCP_COOP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Базовые метаданные сервиса и параметры сетевого биндинга внутри Docker-сети
    app_name: str = Field(
        default="mcp-coop-core",
        description="Logical service name reported in telemetry and health checks.",
    )
    version: str = Field(
        default=__version__,
        description="Semantic version of the Core Engine.",
    )
    host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("MCP_COOP_HOST", "HOST"),
        description="Bind address inside the container so the host CLI can reach port 8787.",
    )
    port: int = Field(
        default=8787,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("MCP_COOP_PORT", "PORT"),
        description="HTTP port exposed by FastAPI and FastMCP gateway.",
    )

    # Строки подключения к инфраструктуре хранения (имена хостов соответствуют сервисам в docker-compose.yml)
    postgres_dsn: str = Field(
        default="postgresql://mcp_coop:mcp_coop_secret@mcp-coop-postgres:5432/mcp_coop",
        validation_alias=AliasChoices("MCP_COOP_POSTGRES_DSN", "POSTGRES_DSN", "DATABASE_URL"),
        description="Asyncpg connection DSN for the hybrid vector and full-text memory store.",
    )
    redis_url: str = Field(
        default="redis://mcp-coop-redis:6379/0",
        validation_alias=AliasChoices("MCP_COOP_REDIS_URL", "REDIS_URL"),
        description="Redis connection URL for event queues, pub/sub, and ephemeral session state.",
    )

    # Опциональные ключи внешних тонких HTTP-клиентов.
    # Если ключи не заданы, ядро автоматически переключается на встроенный поиск PostgreSQL
    # (tsvector + pg_trgm) и детерминированные локальные правила оценки (без PyTorch в контейнере).
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MCP_COOP_OPENAI_API_KEY", "OPENAI_API_KEY"),
        description="Optional API key for external OpenAI Decisions evaluation in Harness² loop.",
    )
    embedding_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MCP_COOP_EMBEDDING_API_KEY",
            "EMBEDDING_API_KEY",
            "GEMINI_API_KEY",
            "HF_TOKEN",
        ),
        description="Optional API key for external HTTP vector embeddings (e.g., EmbeddingGemma 2).",
    )

    @property
    def decisions_api_configured(self) -> bool:
        """Проверяет, активирован ли внешний LLM-арбитр для фильтрации правил в цикле Harness²."""
        return bool(self.openai_api_key and self.openai_api_key.strip())

    @property
    def embedding_api_configured(self) -> bool:
        """Проверяет, доступен ли внешний генератор векторных эмбеддингов для pgvector."""
        return bool(self.embedding_api_key and self.embedding_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> CoreSettings:
    """Возвращает кэшированный синглтон настроек, избегая повторного чтения .env на каждый запрос."""
    return CoreSettings()
