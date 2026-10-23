---
type: Entity
class: Module
id: mod-python-core
name: Базовый каркас Python-ядра (FastAPI + FastMCP Core Engine)
status: Active
stack: [python, fastapi, fastmcp, pydantic]
files:
  - pyproject.toml
  - pyrightconfig.json
  - src/core/__init__.py
  - src/core/config.py
  - src/core/main.py
depends_on: []
implements: []
governed_by: []
---

# Базовый каркас Python-ядра (`mod-python-core`)

Модуль реализует фундамент **Layer 2 (Core Engine)** фабрики `mcp-coop-agentic-factory` на базе Python 3.12+, работающий внутри глобального Docker-стека (`~/.mcp-coop/`). Обеспечивает типизированную конфигурацию окружения, управление жизненным циклом ресурсов (`lifespan`) и диагностический HTTP-контур.

## Состав модуля
1. **`pyproject.toml`**: декларация пакета по стандарту PEP 621 (`hatchling`, `requires-python = ">=3.12"`). Включает исключительно легковесные сетевые и графовые зависимости (`fastapi`, `uvicorn[standard]`, `mcp`, `asyncpg`, `redis`, `networkx`, `httpx`, `pydantic`, `pydantic-settings`, `pyyaml`) без тяжёлых ML-библиотек (`PyTorch` / `CUDA`).
2. **`src/core/__init__.py`**: корневой пакет ядра с фиксацией семантической версии `__version__ = "0.1.0"`.
3. **`src/core/config.py`**: класс `CoreSettings` (`BaseSettings` с префиксом `MCP_COOP_`), разделяющий базовую инфраструктуру (`postgres_dsn`, `redis_url`, `host`, `port`) и опциональные ключи тонких внешних HTTP-интеграций (`openai_api_key`, `embedding_api_key`) с вычисляемыми флагами фолбэка.
4. **`src/core/main.py`**: фабрика `create_app()` и ASGI-приложение FastAPI с контекстным менеджером `@asynccontextmanager async def lifespan(app: FastAPI)` (хранение состояния в `app.state` вместо глобальных переменных) и диагностическим эндпоинтом `GET /health`.

## Логи контекста (AI Traces)
- 2026-10-24: Создан базовый скелет Python 3.12 Core (`pyproject.toml`, `src/core/__init__.py`, `src/core/config.py`, `src/core/main.py`) в строгом соответствии с PEP 621, паттерном FastAPI `lifespan` и языковым стандартом `AGENTS.md` (код на английском, архитектурные комментарии и докстринги на русском).

