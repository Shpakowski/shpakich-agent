---
type: GraphIndex
version: "2026.2"
project: mcp-coop-agentic-factories
updated_at: "2026-10-24"
---

# Карта Контекстного Графа (`index.md`)

> **Точка входа для ИИ-агента:** этот файл — живая карта реально существующих артефактов и узлов проекта `mcp-coop-agentic-factories`. Узлы в `.context/entities/` добавляются строго по факту создания реальных файлов кода и контрактов.

---

## 0. Базовые документы корня репозитория (Active)

* [`AGENTS.md`](../AGENTS.md) — Инженерные правила работы агента, режим менторства и протокол обновления `.context/`.
* [`SPEC.md`](../SPEC.md) — Полная техническая спецификация системы (4 слоя, БД, MCP, `Harness²`).
* [`README.md`](../README.md) — Краткий обзор проекта и команд CLI.
* [`.context/schema.md`](./schema.md) — Правила онтологии и валидации этого графа.

---

## 1. Архитектурные модули (`.context/entities/modules/`)

* **[[mod-context-hooks]]** ([`entities/modules/mod-context-hooks.md`](./entities/modules/mod-context-hooks.md)) — Локальные Python-хуки контроля и авто-инъекции графа `.context/` (`inject_context_map.py`, `track_file_changes.py`, `enforce_context_sync.py`).

## 2. Контракты и интерфейсы (`.context/entities/contracts/`)
*(Пока пусто — узлы создаются по мере реализации контрактов)*

## 3. Архитектурные решения (`.context/entities/decisions/`)
*(Пока пусто — узлы создаются по мере фиксации ADR)*
