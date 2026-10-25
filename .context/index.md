---
type: GraphIndex
version: "2026.2"
project: mcp-coop-agentic-factory
updated_at: "2026-10-25"
---

# Карта Контекстного Графа (`index.md`)

> **Точка входа для ИИ-агента:** этот файл — живая карта реально существующих артефактов и узлов проекта `mcp-coop-agentic-factory`. Узлы в `.context/entities/` добавляются строго по факту создания реальных файлов кода и контрактов.

---

## 0. Базовые документы и спецификации репозитория (Active)

* [`AGENTS.md`](../AGENTS.md) — Инженерные правила работы агента, режим менторства, предметный стиль комментариев, стандарты `KISS / DRY / SOLID / YAGNI / Zero-Hardcode` и протокол обновления `.context/`.
* [`README.md`](../README.md) — Краткий обзор проекта и команд CLI.
* [`.context/schema.md`](./schema.md) — Формальная онтология и правила валидации графа (`Graph-as-Code`, классы `Module | Contract | Decision`, рёбра `nx.DiGraph`, инварианты `No Phantom Nodes` и `No Broken Links`).
* [`specs/`](../specs/) — Директория архитектурных спецификаций проекта (выполненные вехи и черновики будущих итераций развития).

---

## 1. Архитектурные модули (`.context/entities/modules/`)

* **[[mod-context-hooks]]** ([`entities/modules/mod-context-hooks.md`](./entities/modules/mod-context-hooks.md)) — *(Deprecated / Мигрирован в `mod-python-core`)* Базовые скрипты контроля и авто-инъекции графа `.context/` (`inject-context-map`, `track-file-changes`, `enforce-context-sync`), перенесённые в таблицу `harness_components` PostgreSQL (`ComponentType.SCRIPT`) под управление `HookScriptDispatcher`.
* **[[mod-bun-cli]]** ([`entities/modules/mod-bun-cli.md`](./entities/modules/mod-bun-cli.md)) — Кросс-рантаймовый CLI-инжектор (`init`, `up`, `down`, `sync`, `status`, `harness`, `providers`, `hook`), детектор стека/агента, менеджер глобального Docker-стека, материализатор файлов адаптеров, исполнитель хуков и TUI дизайн-система на `@clack/prompts` + `styleText` (`bunx` + `npx`, сборка через `tsc`): `package.json`, `package-lock.json`, `tsconfig.json`, `src/cli/index.ts`, `src/cli/detector.ts`, `src/cli/wizard.ts`, `src/cli/docker.ts`, `src/cli/materializer.ts`, `src/cli/hook_runner.ts`.
* **[[mod-python-core]]** ([`entities/modules/mod-python-core.md`](./entities/modules/mod-python-core.md)) — Каркас глобального Python 3.12 ядра, слои хранения PostgreSQL + Redis + `CompactionArchiveStore` (`SPEC-003`), движок Контекстного Графа, Seed базовых компонентов и скриптов `Harness²`, декларативные профили 5 агентов, компилятор `HarnessCompiler` с пер-проектными оверрайдами `ProjectHarnessOverrides`, векторно-лексический диспетчер скриптов хуков `HookScriptDispatcher`, рантайм-пул провайдеров `RuntimeProviderPool`, мульти-тировый `DecisionEngine` (`tiers.py` + `engine.py`), парсер транскрипта `transcript.py`, тонкий клиент `EmbeddingClient`, модульный пакет микро-агентов `AntigravitySDKService`, локальный шлюз `FastMCP` (`/mcp`, 8 инструментов) и модульный пакет HTTP-маршрутов `src/core/api/*`: `pyproject.toml`, `src/core/enums.py`, `src/core/config.py`, `src/core/main.py`, `src/core/api/__init__.py`, `src/core/api/models.py`, `src/core/api/deps.py`, `src/core/api/routes_management.py`, `src/core/api/routes_hooks.py`, `src/core/db/__init__.py`, `src/core/db/postgres.py`, `src/core/db/redis_client.py`, `src/core/db/compactions.py`, `src/core/graph/__init__.py`, `src/core/graph/context_graph.py`, `src/core/harness/__init__.py`, `src/core/harness/seed.py`, `src/core/harness/profiles.py`, `src/core/harness/compiler.py`, `src/core/harness/script_runner.py`, `src/core/decision/__init__.py`, `src/core/decision/models.py`, `src/core/decision/providers.py`, `src/core/decision/tiers.py`, `src/core/decision/engine.py`, `src/core/decision/transcript.py`, `src/core/decision/hook_dialects.py`, `src/core/services/__init__.py`, `src/core/services/embeddings.py`, `src/core/services/antigravity/__init__.py`, `src/core/services/antigravity/models.py`, `src/core/services/antigravity/sandbox.py`, `src/core/services/antigravity/runtime.py`, `src/core/services/antigravity/service.py`, `src/core/mcp/__init__.py`, `src/core/mcp/tools.py`, `src/core/mcp/server.py`, `tests/unit/test_spec003_compactions.py`.
* **[[mod-docker-stack]]** ([`entities/modules/mod-docker-stack.md`](./entities/modules/mod-docker-stack.md)) — Инфраструктурный Docker Compose стек (`mcp-coop-agentic-factory-core`, `pgvector:pg18`, `redis:7-alpine`) и правила исключений: `.gitignore`, `.dockerignore`, `docker/Dockerfile`, `docker/docker-compose.yml`.

## 2. Контракты и интерфейсы (`.context/entities/contracts/`)

* **[[contract-storage-schema]]** ([`entities/contracts/contract-storage-schema.md`](./entities/contracts/contract-storage-schema.md)) — Схема 5 таблиц PostgreSQL 18 (`projects`, `harness_components`, `memories`, `hook_events`, `decision_providers`), индексов `pgvector` HNSW + `tsvector` + `pg_trgm` и 4 контуров оперативной памяти Redis 7 (паттерны Redis Iris + Circuit Breaker).
* **[[contract-lifecycle-hooks]]** ([`entities/contracts/contract-lifecycle-hooks.md`](./entities/contracts/contract-lifecycle-hooks.md)) — Контракт входов/выходов 5 жизненных хуков (`SessionStart`, `PreToolUse`, `PostToolUse`, `PreInvocation`, `Stop`), матрицы трансляции 3 диалектов (`ANTIGRAVITY_PROTOJSON`, `CLAUDE_CODEX_V1`, `CURSOR_V1`), сценариев `/compact` и `/plan`, воронки `HookScriptDispatcher` и тройного контура принуждения `.context/`.
* **[[contract-mcp-and-http-api]]** ([`entities/contracts/contract-mcp-and-http-api.md`](./entities/contracts/contract-mcp-and-http-api.md)) — Контракт 8 инструментов локального шлюза `FastMCP` (`http://127.0.0.1:8787/mcp` Streamable HTTP) и управляющих REST API эндпоинтов `FastAPI` (`/health`, `/api/projects/register`, `/api/harness/components`, `/api/providers`, `/api/decisions/evaluate`, `/api/context/heal`).

## 3. Архитектурные решения (`.context/entities/decisions/`)

* **[[adr-001-clean-host-global-docker]]** ([`entities/decisions/adr-001-clean-host-global-docker.md`](./entities/decisions/adr-001-clean-host-global-docker.md)) — Чистая хост-машина (тонкий Dual-Runtime CLI на Bun/Node) и изолированное глобальное Docker-ядро (`~/.mcp-coop-agentic-factory/`) без локальных ML-весов (`PyTorch`/`CUDA`) с автономным фолбэком на `tsvector + pg_trgm`.
* **[[adr-002-harness2-evolution-loop]]** ([`entities/decisions/adr-002-harness2-evolution-loop.md`](./entities/decisions/adr-002-harness2-evolution-loop.md)) — Эволюция окружения агента по методологии `Harness²` (8 компонентов, `invariant` vs `domain-specific`, стейт-машина `Propose -> Probe -> Compose`) и декаплированная шина хуков `HookScriptDispatcher`.
* **[[adr-003-isolated-antigravity-sdk-tier]]** ([`entities/decisions/adr-003-isolated-antigravity-sdk-tier.md`](./entities/decisions/adr-003-isolated-antigravity-sdk-tier.md)) — Модульная изоляция `google-antigravity` SDK во втором контуре микро-агентов (`src/core/services/antigravity/`), строгий гейтинг для `AgentType.ANTIGRAVITY` и защита песочницы `/tmp` от симлинков и Prompt Injection.
