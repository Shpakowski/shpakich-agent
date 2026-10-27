"""Модуль типизированной конфигурации Python-ядра (Layer 2: Core Engine).

Загружает и валидирует параметры окружения контейнера через Pydantic BaseSettings,
обеспечивая безопасный фолбэк при отсутствии внешних API-ключей (OpenAI / Embeddings).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core import __version__
from src.core.enums import AgentType, AntigravityAuthMode, EmbeddingDialect

# Параметры времени жизни (TTL) для оперативной памяти сессий (24 часа),
# кэша решений/правил LangCache (1 час) и порога детектора зацикливания команд.
DEFAULT_REDIS_SESSION_TTL_SECONDS: int = 86_400
DEFAULT_REDIS_CACHE_TTL_SECONDS: int = 3_600
MIN_REDIS_TTL_SECONDS: int = 60
DEFAULT_MAX_CONSECUTIVE_CMD_FAILURES: int = 2

# Базовые параметры тонкого HTTP-клиента векторных эмбеддингов (EmbeddingGemma 2 / Gemini / OpenAI)
DEFAULT_EMBEDDING_API_URL: str = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_EMBEDDING_MODEL: str = "models/gemini-embedding-001"
DEFAULT_EMBEDDING_DIMENSIONS: int = 768
DEFAULT_EMBEDDING_TIMEOUT_MS: int = 1500

# Базовые параметры опционального контура google-antigravity Python SDK (SPEC.md §6.3)
DEFAULT_ANTIGRAVITY_MODEL: str = "gemini-2.5-flash"
DEFAULT_ANTIGRAVITY_LOCATION: str = "us-central1"
DEFAULT_ANTIGRAVITY_TIMEOUT_MS: int = 30_000
DEFAULT_ANTIGRAVITY_MAX_CONCURRENCY: int = 2

# Базовые параметры логирования и телеметрии Docker-ядра
DEFAULT_LOG_FORMAT: str = "pretty"
DEFAULT_LOG_LEVEL: str = "INFO"


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
        default="mcp-coop-agentic-factory-core",
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
    default_agent: AgentType = Field(
        default=AgentType.ANTIGRAVITY,
        validation_alias=AliasChoices("MCP_COOP_DEFAULT_AGENT", "DEFAULT_AGENT"),
        description="Default target AI coding agent when not explicitly specified by the client.",
    )
    log_format: str = Field(
        default=DEFAULT_LOG_FORMAT,
        validation_alias=AliasChoices("MCP_COOP_LOG_FORMAT", "LOG_FORMAT"),
        description="Telemetry output format for Docker logs ('pretty' expandable tree or 'json').",
    )
    log_level: str = Field(
        default=DEFAULT_LOG_LEVEL,
        validation_alias=AliasChoices("MCP_COOP_LOG_LEVEL", "LOG_LEVEL"),
        description="Minimum logging level for the mcp_coop.core logger (e.g. INFO, DEBUG).",
    )

    # Строки подключения к инфраструктуре хранения (имена хостов соответствуют сервисам в docker-compose.yml)
    postgres_dsn: str = Field(
        default="postgresql://mcp_coop:mcp_coop_secret@mcp-coop-agentic-factory-postgres:5432/mcp_coop_agentic_factory",
        validation_alias=AliasChoices("MCP_COOP_POSTGRES_DSN", "POSTGRES_DSN", "DATABASE_URL"),
        description="Asyncpg connection DSN for the hybrid vector and full-text memory store.",
    )
    redis_url: str = Field(
        default="redis://mcp-coop-agentic-factory-redis:6379/0",
        validation_alias=AliasChoices("MCP_COOP_REDIS_URL", "REDIS_URL"),
        description="Redis connection URL for event queues, pub/sub, and ephemeral session state.",
    )
    redis_session_ttl_seconds: int = Field(
        default=DEFAULT_REDIS_SESSION_TTL_SECONDS,
        ge=MIN_REDIS_TTL_SECONDS,
        validation_alias=AliasChoices(
            "MCP_COOP_REDIS_SESSION_TTL_SECONDS",
            "REDIS_SESSION_TTL_SECONDS",
        ),
        description="Time-to-live in seconds for ephemeral session keys in Redis Working Memory.",
    )
    redis_cache_ttl_seconds: int = Field(
        default=DEFAULT_REDIS_CACHE_TTL_SECONDS,
        ge=MIN_REDIS_TTL_SECONDS,
        validation_alias=AliasChoices(
            "MCP_COOP_REDIS_CACHE_TTL_SECONDS",
            "REDIS_CACHE_TTL_SECONDS",
        ),
        description="Time-to-live in seconds for cached hook decisions and rule bundles in Redis LangCache.",
    )
    max_consecutive_cmd_failures: int = Field(
        default=DEFAULT_MAX_CONSECUTIVE_CMD_FAILURES,
        ge=1,
        validation_alias=AliasChoices(
            "MCP_COOP_MAX_CONSECUTIVE_CMD_FAILURES",
            "MAX_CONSECUTIVE_CMD_FAILURES",
        ),
        description="Threshold of identical failed tool executions before triggering anti-loop blockage.",
    )

    # Опциональные ключи внешних тонких HTTP-клиентов.
    # Если ключи не заданы, ядро автоматически переключается на встроенный поиск PostgreSQL
    # (tsvector + pg_trgm) и детерминированные локальные правила оценки (без PyTorch в контейнере).
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MCP_COOP_OPENAI_API_KEY", "OPENAI_API_KEY"),
        description="Optional API key for external OpenAI Decisions evaluation in Harness loop.",
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
    embedding_api_url: str = Field(
        default=DEFAULT_EMBEDDING_API_URL,
        validation_alias=AliasChoices(
            "MCP_COOP_EMBEDDING_API_URL",
            "EMBEDDING_API_URL",
        ),
        description="Base URL or endpoint for the external HTTP embedding provider.",
    )
    embedding_model: str = Field(
        default=DEFAULT_EMBEDDING_MODEL,
        validation_alias=AliasChoices(
            "MCP_COOP_EMBEDDING_MODEL",
            "EMBEDDING_MODEL",
        ),
        description="Target embedding model identifier (e.g., google/embeddinggemma-300m or models/gemini-embedding-001).",
    )
    embedding_dimensions: int = Field(
        default=DEFAULT_EMBEDDING_DIMENSIONS,
        ge=64,
        le=3072,
        validation_alias=AliasChoices(
            "MCP_COOP_EMBEDDING_DIMENSIONS",
            "EMBEDDING_DIMENSIONS",
        ),
        description="Target output dimensionality for Matryoshka/MRL vector truncation (default 768 for pgvector).",
    )
    embedding_timeout_ms: int = Field(
        default=DEFAULT_EMBEDDING_TIMEOUT_MS,
        ge=100,
        le=15000,
        validation_alias=AliasChoices(
            "MCP_COOP_EMBEDDING_TIMEOUT_MS",
            "EMBEDDING_TIMEOUT_MS",
        ),
        description="HTTP request timeout in milliseconds for embedding generation.",
    )
    embedding_dialect: EmbeddingDialect = Field(
        default=EmbeddingDialect.AUTO,
        validation_alias=AliasChoices(
            "MCP_COOP_EMBEDDING_DIALECT",
            "EMBEDDING_DIALECT",
        ),
        description="Protocol dialect for the HTTP embedding client (auto, openai_compatible, gemini_native).",
    )

    # Опциональные параметры микро-агентов второго уровня на базе google-antigravity Python SDK (SPEC.md §6.3).
    # Активируются только при выборе целевого агента AgentType.ANTIGRAVITY и наличии ключа/ADC.
    antigravity_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MCP_COOP_ANTIGRAVITY_API_KEY",
            "ANTIGRAVITY_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        ),
        description="Optional API key for google-antigravity Python SDK (Gemini API or Vertex Express Mode).",
    )
    antigravity_model: str = Field(
        default=DEFAULT_ANTIGRAVITY_MODEL,
        validation_alias=AliasChoices(
            "MCP_COOP_ANTIGRAVITY_MODEL",
            "ANTIGRAVITY_MODEL",
        ),
        description="Target model identifier for google-antigravity micro-agents.",
    )
    antigravity_vertex: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "MCP_COOP_ANTIGRAVITY_VERTEX",
            "ANTIGRAVITY_VERTEX",
        ),
        description="Whether to route google-antigravity SDK requests through Vertex AI (Express or ADC).",
    )
    antigravity_project: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MCP_COOP_ANTIGRAVITY_PROJECT",
            "ANTIGRAVITY_PROJECT",
            "GOOGLE_CLOUD_PROJECT",
        ),
        description="Optional Google Cloud project ID for Vertex AI standard ADC mode.",
    )
    antigravity_location: str = Field(
        default=DEFAULT_ANTIGRAVITY_LOCATION,
        validation_alias=AliasChoices(
            "MCP_COOP_ANTIGRAVITY_LOCATION",
            "ANTIGRAVITY_LOCATION",
        ),
        description="Target Google Cloud region for Vertex AI standard ADC mode.",
    )
    antigravity_timeout_ms: int = Field(
        default=DEFAULT_ANTIGRAVITY_TIMEOUT_MS,
        ge=1000,
        le=300_000,
        validation_alias=AliasChoices(
            "MCP_COOP_ANTIGRAVITY_TIMEOUT_MS",
            "ANTIGRAVITY_TIMEOUT_MS",
        ),
        description="Execution timeout in milliseconds for isolated google-antigravity micro-agent runs.",
    )
    antigravity_max_concurrency: int = Field(
        default=DEFAULT_ANTIGRAVITY_MAX_CONCURRENCY,
        ge=1,
        le=8,
        validation_alias=AliasChoices(
            "MCP_COOP_ANTIGRAVITY_MAX_CONCURRENCY",
            "ANTIGRAVITY_MAX_CONCURRENCY",
        ),
        description="Maximum number of concurrent google-antigravity subprocesses inside the container.",
    )

    @property
    def decisions_api_configured(self) -> bool:
        """Проверяет, активирован ли внешний LLM-арбитр для фильтрации правил в цикле Harness."""
        return bool(self.openai_api_key and self.openai_api_key.strip())

    @property
    def embedding_api_configured(self) -> bool:
        """Проверяет, доступен ли внешний генератор векторных эмбеддингов для pgvector."""
        has_key = bool(self.embedding_api_key and self.embedding_api_key.strip())
        custom_url = self.embedding_api_url.strip()
        has_custom_endpoint = bool(
            custom_url and custom_url != DEFAULT_EMBEDDING_API_URL
        )
        return has_key or has_custom_endpoint

    @property
    def antigravity_auth_mode(self) -> AntigravityAuthMode:
        """Определяет активный режим аутентификации для google-antigravity Python SDK."""
        has_key = bool(self.antigravity_api_key and self.antigravity_api_key.strip())
        has_project = bool(self.antigravity_project and self.antigravity_project.strip())
        if self.antigravity_vertex and has_key:
            return AntigravityAuthMode.VERTEX_EXPRESS
        if self.antigravity_vertex and has_project:
            return AntigravityAuthMode.VERTEX_ADC
        if has_key:
            return AntigravityAuthMode.GEMINI_API_KEY
        return AntigravityAuthMode.UNCONFIGURED

    @property
    def antigravity_sdk_configured(self) -> bool:
        """Проверяет, заданы ли учётные данные для запуска микро-агентов google-antigravity SDK."""
        return self.antigravity_auth_mode != AntigravityAuthMode.UNCONFIGURED


@lru_cache(maxsize=1)
def get_settings() -> CoreSettings:
    """Возвращает кэшированный синглтон настроек, избегая повторного чтения .env на каждый запрос."""
    return CoreSettings()
