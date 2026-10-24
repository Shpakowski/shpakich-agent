---
type: GraphIndex
version: "2026.2"
project: mcp-coop-agentic-factory
updated_at: "2026-10-24"
---

# Карта Контекстного Графа (`index.md`)

> **Точка входа для ИИ-агента:** этот файл — живая карта реально существующих артефактов и узлов проекта `mcp-coop-agentic-factory`. Узлы в `.context/entities/` добавляются строго по факту создания реальных файлов кода и контрактов.

---

## 0. Базовые документы корня репозитория (Active)

* [`AGENTS.md`](../AGENTS.md) — Инженерные правила работы агента, режим менторства, стандарты `KISS / DRY / SOLID / YAGNI / Zero-Hardcode` и протокол обновления `.context/`.
* [`SPEC.md`](../SPEC.md) — Полная техническая спецификация системы (4 слоя, БД, MCP, `Harness²`, перехват `/compact` и `/plan` в хуках, опциональный контур `google-antigravity` SDK).
* [`README.md`](../README.md) — Краткий обзор проекта и команд CLI.
* [`.context/schema.md`](./schema.md) — Правила онтологии и валидации этого графа.

---

## 1. Архитектурные модули (`.context/entities/modules/`)

* **[[mod-context-hooks]]** ([`entities/modules/mod-context-hooks.md`](./entities/modules/mod-context-hooks.md)) — Локальные Python-хуки контроля и авто-инъекции графа `.context/` (`inject_context_map.py`, `track_file_changes.py`, `enforce_context_sync.py`).
* **[[mod-bun-cli]]** ([`entities/modules/mod-bun-cli.md`](./entities/modules/mod-bun-cli.md)) — Каркас кросс-рантаймового CLI-инжектора (`bunx` + `npx`, сборка через `tsc`): `package.json`, `tsconfig.json`, `src/cli/index.ts`.
* **[[mod-python-core]]** ([`entities/modules/mod-python-core.md`](./entities/modules/mod-python-core.md)) — Каркас глобального Python 3.12 ядра и слой хранения PostgreSQL (`FastAPI` + `lifespan` + `CoreSettings` + `StrEnum` + `PostgresDatabase`): `pyproject.toml`, `src/core/enums.py`, `src/core/config.py`, `src/core/main.py`, `src/core/db/postgres.py`.
* **[[mod-docker-stack]]** ([`entities/modules/mod-docker-stack.md`](./entities/modules/mod-docker-stack.md)) — Инфраструктурный Docker Compose стек (`mcp-coop-agentic-factory-core`, `pgvector:pg18`, `redis:7-alpine`) и правила исключений: `.gitignore`, `.dockerignore`, `docker/Dockerfile`, `docker/docker-compose.yml`.

## 2. Контракты и интерфейсы (`.context/entities/contracts/`)
*(Пока пусто — узлы создаются по мере реализации контрактов)*

## 3. Архитектурные решения (`.context/entities/decisions/`)
*(Пока пусто — узлы создаются по мере фиксации ADR)*
