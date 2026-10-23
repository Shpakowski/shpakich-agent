---
type: Entity
class: Module
id: mod-context-hooks
name: Локальные хуки управления Контекстным Графом (.context/)
status: Active
stack: [python, antigravity-hooks]
files:
  - .agents/hooks.json
  - .agents/hooks/inject_context_map.py
  - .agents/hooks/track_file_changes.py
  - .agents/hooks/enforce_context_sync.py
depends_on: []
implements: []
governed_by: []
---

# Локальные хуки управления Контекстным Графом (`mod-context-hooks`)

Модуль реализует **Контур 2** (авто-инъекция карты графа на старте чата) и **Контур 3** (контроль обязательного обновления `.context/` при изменении файлов проекта) для нативного агента Antigravity.

## Состав модуля (1 скрипт = 1 действие)
1. **`inject_context_map.py`** (`SessionStart` / `PreInvocation`): читает `.context/index.md` и впрыскивает карту в чат через `injectSteps` (а если файл отсутствует или не читается — отправляет `build_warning_response`, обязывая агента предупредить пользователя на его языке).
2. **`track_file_changes.py`** (`PostToolUse` для `write_file|edit_file`): отслеживает модификацию файлов проекта и `.context/`, сохраняя флаги сессии в `/tmp/mcp-coop-session-<id>.json`.
3. **`enforce_context_sync.py`** (`Stop`): блокирует завершение хода (`decision: "continue"`), если файлы проекта были изменены, а `.context/` не синхронизирован.

## Логи контекста (AI Traces)
- 2026-10-24: Реализованы и подключены через `.agents/hooks.json` 3 локальных Python-скрипта контроля `.context/`.
- 2026-10-24: В `inject_context_map.py` и `enforce_context_sync.py` все строковые литералы в коде переведены на английский с защитой от Language Drift (ответ пользователю на его языке), комментарии сохранены на русском.
- 2026-10-24: Уточнён перехват исключений (`OSError`, `json.JSONDecodeError`, `ValueError` вместо слепого `Exception`) для прохождения строгих проверок `pyright` и `ruff`.

