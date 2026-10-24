---
type: Entity
class: Module
id: mod-python-core
name: Базовый каркас Python-ядра и слой хранения (FastAPI + FastMCP + asyncpg)
status: Active
stack: [python, fastapi, fastmcp, pydantic, postgresql, pgvector, asyncpg]
files:
  - pyproject.toml
  - pyrightconfig.json
  - src/core/__init__.py
  - src/core/enums.py
  - src/core/config.py
  - src/core/main.py
  - src/core/db/__init__.py
  - src/core/db/postgres.py
depends_on: []
implements: []
governed_by: []
---

# Базовый каркас Python-ядра и слой хранения (`mod-python-core`)

Модуль реализует фундамент **Layer 2 (Core Engine)** фабрики `mcp-coop-agentic-factory` на базе Python 3.12+, работающий внутри глобального Docker-стека (`~/.mcp-coop-agentic-factory/`). Обеспечивает типизированную конфигурацию окружения, управление жизненным циклом ресурсов (`lifespan`), нативный асинхронный пул соединений PostgreSQL 18 (`asyncpg`) с идемпотентной инициализацией схемы БД и диагностический HTTP-контур.

## Состав модуля
1. **`pyproject.toml`**: декларация пакета по стандарту PEP 621 (`hatchling`, `requires-python = ">=3.12"`). Включает исключительно легковесные сетевые и графовые зависимости (`fastapi`, `uvicorn[standard]`, `mcp`, `asyncpg`, `redis`, `networkx`, `httpx`, `pydantic`, `pydantic-settings`, `pyyaml`) без тяжёлых ML-библиотек (`PyTorch` / `CUDA`).
2. **`src/core/__init__.py`**: корневой пакет ядра с фиксацией семантической версии `__version__ = "0.1.0"`.
3. **`src/core/enums.py`**: единый реестр доменных перечислений на базе `StrEnum` (`AgentType`, `ComponentType`, `ScopeType`, `ComponentStatus`, `HookEventType`), обеспечивающий валидацию типов на уровне Python/Pydantic по принципу DRY & KISS без хардкода вендора в SQL-схеме.
4. **`src/core/config.py`**: класс `CoreSettings` (`BaseSettings` с префиксом `MCP_COOP_`), разделяющий базовую инфраструктуру (`postgres_dsn`, `redis_url`, `host`, `port`, `default_agent`) и опциональные ключи тонких внешних HTTP-интеграций (`openai_api_key`, `embedding_api_key`) с вычисляемыми флагами фолбэка.
5. **`src/core/db/__init__.py` и `src/core/db/postgres.py`**: нативный асинхронный адаптер `PostgresDatabase` поверх `asyncpg.Pool`, функция нормализации DSN (`normalize_asyncpg_dsn`) и идемпотентная DDL-схема `SCHEMA_DDL` (расширения `vector` и `pg_trgm`, таблицы `projects`, `harness_components`, `memories`, `hook_events` без вендор-хардкода в `agent_type` и с подстановкой дефолтов из `ScopeType` и `ComponentStatus`).
6. **`src/core/main.py`**: фабрика `create_app()` и ASGI-приложение FastAPI с контекстным менеджером `@asynccontextmanager async def lifespan(app: FastAPI)` (инициализация и закрытие `PostgresDatabase` в `app.state.db` с поддержкой Graceful Degradation при локальном запуске без БД) и диагностическим эндпоинтом `GET /health` (включая живую проверку `postgres_connected`).

## Логи контекста (AI Traces)
- 2026-10-24: Создан базовый скелет Python 3.12 Core (`pyproject.toml`, `src/core/__init__.py`, `src/core/config.py`, `src/core/main.py`) в строгом соответствии с PEP 621, паттерном FastAPI `lifespan` и языковым стандартом `AGENTS.md` (код на английском, архитектурные комментарии и докстринги на русском).
- 2026-10-24: Реализован слой персистентного хранения PostgreSQL (`src/core/db/__init__.py`, `src/core/db/postgres.py`) с идемпотентной накаткой схемы `SCHEMA_DDL` (`vector`, `pg_trgm`, 4 таблицы и GIN-индексы) и интеграцией `PostgresDatabase` в `lifespan` и `/health` (`src/core/main.py`).
- 2026-10-24: Выделен единый модуль перечислений `src/core/enums.py` (`StrEnum`), добавлено поле `default_agent` в `CoreSettings` и убран вендор-хардкод `DEFAULT 'antigravity'` из SQL-схемы `projects` в пользу валидации на уровне приложения (DRY & KISS).

