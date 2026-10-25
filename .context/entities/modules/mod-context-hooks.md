---
type: Entity
class: Module
id: mod-context-hooks
name: Базовые скрипты контроля Контекстного Графа (.context/)
status: Deprecated
stack: [python, antigravity-hooks]
files:
  - src/core/harness/seed.py
  - src/core/harness/script_runner.py
depends_on:
  - mod-python-core
implements: []
governed_by: []
---

# Базовые скрипты контроля Контекстного Графа (`mod-context-hooks`)

> **Статус:** Мигрирован в централизованный реестр PostgreSQL `harness_components` (`ComponentType.SCRIPT`) и диспетчер [`src/core/harness/script_runner.py`](../../../src/core/harness/script_runner.py) внутри **[[mod-python-core]]**.

Модуль определял прототипные Python-скрипты **Контура 2** (авто-инъекция карты `.context/index.md` на старте сессии) и **Контура 3** (контроль обязательного обновления `.context/` при модификации исходного кода проекта).

## Текущее расположение и исполнение (внутри Docker Core)
Все 3 скрипта хранятся как независимые записи `component_type = 'script'` в таблице `harness_components` PostgreSQL (см. [`src/core/harness/seed.py`](../../../src/core/harness/seed.py)) и динамически запускаются внутри контейнера `mcp-coop-agentic-factory-core` через `HookScriptDispatcher`:
1. **`inject-context-map`** (`hook_events: ["session-start"]`, `execution_mode: "always"`): получает содержимое `.context/index.md` и возвращает `system_message` с актуальной картой графа.
2. **`track-file-changes`** (`hook_events: ["post-tool-use"]`, `execution_mode: "always"`): отслеживает вызовы инструментов модификации файлов (`write_file`, `edit_file` и др.) и обновляет флаги `code_modified`, `context_updated`, `modified_files` в Redis (`session:{conversation_id}:meta`).
3. **`enforce-context-sync`** (`hook_events: ["stop"]`, `execution_mode: "always"`): проверяет флаги сессии в Redis на событии `stop` и возвращает `action: "continue"`, если код проекта менялся без синхронизации `.context/`.

## Логи контекста (AI Traces)
- 2026-10-24: Реализованы 3 прототипных локальных Python-скрипта контроля `.context/`.
- 2026-10-24: Все строковые литералы переведены на английский с защитой от Language Drift, комментарии сохранены на русском.
- 2026-10-25: Выполнена полная декомпозиция: все 3 скрипта перенесены в таблицу `harness_components` PostgreSQL (`ComponentType.SCRIPT`) с хранением состояния в Redis (`session:{id}:meta`) и динамическим отбором/исполнением через `HookScriptDispatcher` (`src/core/harness/script_runner.py`).
