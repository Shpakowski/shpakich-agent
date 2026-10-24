---
type: Entity
class: Module
id: mod-python-core
name: Базовый каркас Python-ядра, слой хранения и движок Контекстного Графа (FastAPI + FastMCP + asyncpg + Redis + NetworkX)
status: Active
stack: [python, fastapi, fastmcp, pydantic, postgresql, pgvector, asyncpg, redis, networkx, pyyaml]
files:
  - pyproject.toml
  - pyrightconfig.json
  - src/core/__init__.py
  - src/core/enums.py
  - src/core/config.py
  - src/core/main.py
  - src/core/db/__init__.py
  - src/core/db/postgres.py
  - src/core/db/redis_client.py
  - src/core/graph/__init__.py
  - src/core/graph/context_graph.py
depends_on: []
implements: []
governed_by: []
---

# Базовый каркас Python-ядра, слой хранения и движок Контекстного Графа (`mod-python-core`)

Модуль реализует фундамент **Layer 2 (Core Engine)** фабрики `mcp-coop-agentic-factory` на базе Python 3.12+, работающий внутри глобального Docker-стека (`~/.mcp-coop-agentic-factory/`). Обеспечивает типизированную конфигурацию окружения, управление жизненным циклом ресурсов (`lifespan`), нативный асинхронный пул соединений PostgreSQL 18 (`asyncpg`) с идемпотентной инициализацией схемы БД, асинхронное хранилище оперативной памяти сессий и кэширования Redis (`redis.asyncio`, паттерны Redis Iris), движок локального графа знаний `.context/` (`NetworkX DiGraph` + `Pydantic v2`) и диагностический HTTP-контур.

## Состав модуля
1. **`pyproject.toml`**: декларация пакета по стандарту PEP 621 (`hatchling`, `requires-python = ">=3.12"`). Включает исключительно легковесные сетевые и графовые зависимости (`fastapi`, `uvicorn[standard]`, `mcp`, `asyncpg`, `redis`, `networkx`, `httpx`, `pydantic`, `pydantic-settings`, `pyyaml`) без тяжёлых ML-библиотек (`PyTorch` / `CUDA`).
2. **`src/core/__init__.py`**: корневой пакет ядра с фиксацией семантической версии `__version__ = "0.1.0"`.
3. **`src/core/enums.py`**: единый реестр доменных перечислений на базе `StrEnum` (`AgentType`, `ComponentType`, `ScopeType`, `ComponentStatus`, `HookEventType`, `RedisNamespace`, `RedisKeySegment`, `SessionMetaField`, `RedisFlag`, `ContextEntityClass`, `ContextEntityStatus`, `ContextRelationType`, `ContextEntityFolder`), обеспечивающий валидацию типов, ключей Redis и сущностей графа `.context/` на уровне Python/Pydantic по принципу DRY & Zero-Hardcode.
4. **`src/core/config.py`**: класс `CoreSettings` (`BaseSettings` с префиксом `MCP_COOP_`) и константы верхнего уровня (`DEFAULT_REDIS_SESSION_TTL_SECONDS`, `DEFAULT_REDIS_CACHE_TTL_SECONDS`, `MIN_REDIS_TTL_SECONDS`, `DEFAULT_MAX_CONSECUTIVE_CMD_FAILURES`), разделяющий базовую инфраструктуру (`postgres_dsn`, `redis_url`, `redis_session_ttl_seconds`, `redis_cache_ttl_seconds`, `max_consecutive_cmd_failures`, `host`, `port`, `default_agent`) и опциональные ключи тонких внешних HTTP-интеграций (`openai_api_key`, `embedding_api_key`) с вычисляемыми флагами фолбэка.
5. **`src/core/db/__init__.py` и `src/core/db/postgres.py`**: нативный асинхронный адаптер `PostgresDatabase` поверх `asyncpg.Pool`, функция нормализации DSN (`normalize_asyncpg_dsn`), идемпотентная DDL-схема `SCHEMA_DDL` (расширения `vector` и `pg_trgm`, таблицы `projects`, `harness_components`, `memories`, `hook_events` без вендор-хардкода в `agent_type` и с подстановкой дефолтов из `ScopeType` и `ComponentStatus`) и единый экспорт классов хранения PostgreSQL и Redis в `src/core/db/__init__.py`.
6. **`src/core/db/redis_client.py`**: асинхронный клиент `RedisSessionStore` поверх `redis.asyncio`, модели `SessionLastError` и `MemoryPromotionCandidate`, а также чистые функции хеширования `compute_command_hash` и `compute_stack_hash`. Реализует три паттерна **Redis Iris**:
   - **Short-Term Working Memory** (`session:{id}:meta`, `last_error`, `cmd_fails`): атомарный трекинг состояния диалога и детектор зацикливания команд через `pipeline(transaction=True)`.
   - **Short-to-Long Memory Promotion** (`session:{id}:promotion`): формирование карточки `MemoryPromotionCandidate` при успешном устранении ошибки для последующего переноса в долгосрочную таблицу `memories` PostgreSQL.
   - **LangCache Lite** (`cache:decision:*`, `cache:rules:*`): субмиллисекундное кэширование решений хуков `PreToolUse` и бандлов правил по инвариантному SHA-256 хешу стека.
7. **`src/core/graph/__init__.py` и `src/core/graph/context_graph.py`**: движок `ContextGraphManager` на базе `NetworkX` (`nx.DiGraph`) и `Pydantic v2`, неизменяемые модели `ContextNodeFrontmatter`, `ContextNode`, `GraphEdge`, `GraphValidationReport`, `SubgraphQueryResult` и чистые функции `extract_wiki_links`, `split_frontmatter_and_body`, `parse_context_node_file`. Обеспечивает:
   - **`build_and_validate`**: сборку ориентированного графа из `.context/entities/**/*.md` с проверкой правил **No Phantom Nodes** (`missing_files`) и **No Broken Links** (`broken_links`).
   - **`query_subgraph`**: извлечение двунаправленной окрестности узла радиуса `depth` через `nx.ego_graph` с сохранением направленных рёбер.
   - **`upsert_node`**: атомарное создание/обновление Markdown-узла с проверкой существования файлов, автоматическим ведением журнала `## Логи контекста (AI Traces)` и обновлением `updated_at` в `.context/index.md`.
8. **`src/core/main.py`**: фабрика `create_app()` и ASGI-приложение FastAPI с контекстным менеджером `@asynccontextmanager async def lifespan(app: FastAPI)` (инициализация и закрытие `PostgresDatabase` в `app.state.db` и `RedisSessionStore` в `app.state.redis` с поддержкой Graceful Degradation при локальном запуске без контейнеров) и диагностическим эндпоинтом `GET /health` (включая живую проверку `postgres_connected` и `redis_connected`).

## Логи контекста (AI Traces)
- 2026-10-24: Создан базовый скелет Python 3.12 Core (`pyproject.toml`, `src/core/__init__.py`, `src/core/config.py`, `src/core/main.py`) в строгом соответствии с PEP 621, паттерном FastAPI `lifespan` и языковым стандартом `AGENTS.md` (код на английском, архитектурные комментарии и докстринги на русском).
- 2026-10-24: Реализован слой персистентного хранения PostgreSQL (`src/core/db/__init__.py`, `src/core/db/postgres.py`) с идемпотентной накаткой схемы `SCHEMA_DDL` (`vector`, `pg_trgm`, 4 таблицы и GIN-индексы) и интеграцией `PostgresDatabase` в `lifespan` и `/health` (`src/core/main.py`).
- 2026-10-24: Выделен единый модуль перечислений `src/core/enums.py` (`StrEnum`), добавлено поле `default_agent` в `CoreSettings` и убран вендор-хардкод `DEFAULT 'antigravity'` из SQL-схеемы `projects` в пользу валидации на уровне приложения (DRY & KISS).
- 2026-10-24: Реализована Итерация 2.1 (Оперативная память сессий Redis + паттерны Redis Iris): расширены `src/core/enums.py` и `src/core/config.py`, создан `src/core/db/redis_client.py` (`RedisSessionStore`, `SessionLastError`, `MemoryPromotionCandidate`, `compute_command_hash`, `compute_stack_hash`).
- 2026-10-24: Завершена Итерация 2.2: `RedisSessionStore` и связанные модели экспортированы в `src/core/db/__init__.py` и интегрированы в `lifespan` (`app.state.redis`) и диагностический эндпоинт `GET /health` (`redis_connected`) в `src/core/main.py`, синхронизирован Раздел 4.2 `SPEC.md`.
- 2026-10-24: Реализованы Итерации 4.1 и 4.2 (Онтология `.context/schema.md` и Движок Контекстного Графа `src/core/graph/context_graph.py` на базе `NetworkX` + `Pydantic v2`): добавлены перечисления графа в `src/core/enums.py` и реализован `ContextGraphManager` (`build_and_validate`, `query_subgraph`, `upsert_node`), пройдены проверки `pyright` и `ruff`.

