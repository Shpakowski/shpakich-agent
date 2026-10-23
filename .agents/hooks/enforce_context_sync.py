#!/usr/bin/env python3
"""
Слой: Локальный хук контроля синхронизации графа контекста (Контур 3 — часть 2: Шлагбаум).
Назначение: Вызывается при попытке агента завершить ответ (Stop).
Если в текущей сессии агент изменял файлы проекта (`code_modified == True`), но не обновил
граф `.context/` (`context_updated == False`), скрипт возвращает `{"decision": "continue"}`
и заставляет агента актуализировать `.context/` перед завершением шага.
"""

import json
import sys
import tempfile
from pathlib import Path

# Причины остановки, при которых НЕЛЬЗЯ блокировать выход (отмена пользователем, лимит шагов, сбой)
BYPASS_TERMINATION_REASONS = {
    "USER_CANCELED",
    "ERROR",
    "MAX_INVOCATIONS",
    "MAX_FORCED_INVOCATIONS",
    "MAX_TOKEN_BUDGET_EXCEEDED",
}


def get_session_state_path(conversation_id: str) -> Path:
    """Возвращает путь к файлу состояния сессии в `/tmp`."""
    safe_id = "".join(c for c in conversation_id if c.isalnum() or c in ("-", "_")) or "default"
    return Path(tempfile.gettempdir()) / f"mcp-coop-session-{safe_id}.json"


def load_session_state(state_path: Path) -> dict:
    """Читает состояние сессии из `/tmp`."""
    if state_path.is_file():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return {
                "code_modified": False,
                "context_updated": False,
                "reminder_sent": False,
                "modified_files": [],
            }
    return {
        "code_modified": False,
        "context_updated": False,
        "reminder_sent": False,
        "modified_files": [],
    }


def save_session_state(state_path: Path, state: dict) -> None:
    """Сохраняет состояние сессии на диск."""
    try:
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"[mcp-coop] Failed to save session state: {exc}\n")


def main() -> None:
    # 1. Читаем входной JSON от Antigravity
    try:
        raw_input = sys.stdin.read().strip()
        payload = json.loads(raw_input) if raw_input else {}
    except (OSError, json.JSONDecodeError, ValueError):
        print(json.dumps({"decision": "allow"}))
        return

    # 2. Если остановка вызвана отменой пользователя или ошибкой — сразу разрешаем выход
    termination_reason = str(payload.get("terminationReason", ""))
    if termination_reason in BYPASS_TERMINATION_REASONS or payload.get("error"):
        print(json.dumps({"decision": "allow"}))
        return

    conversation_id = str(payload.get("conversationId", "default"))
    state_path = get_session_state_path(conversation_id)
    state = load_session_state(state_path)

    code_modified = bool(state.get("code_modified"))
    context_updated = bool(state.get("context_updated"))
    reminder_sent = bool(state.get("reminder_sent"))
    modified_files = state.get("modified_files", [])

    # 3. Если код менялся, а граф `.context/` НЕ обновлён (и мы ещё не отправляли напоминание)
    if code_modified and not context_updated and not reminder_sent:
        # Ставим флаг `reminder_sent = True`, чтобы гарантированно избежать вечного цикла
        state["reminder_sent"] = True
        save_session_state(state_path, state)

        files_list = ", ".join(f"`{f}`" for f in modified_files[:5])
        reason = (
            f"[MCP-COOP Context Gate]: Project files were modified in this turn ({files_list}), "
            "but the context graph in `.context/` (`index.md` or the corresponding node in `.context/entities/`) "
            "was not synchronized. Please update `.context/` and append an entry to `AI Traces` before finishing. "
            "Always reply to the user in the language they use in chat."
        )
        print(json.dumps({"decision": "continue", "reason": reason}, ensure_ascii=False))
        return

    # 4. Если граф уже обновлён (или напоминание отработало) — очищаем флаги до следующих правок кода
    if context_updated or reminder_sent:
        state["code_modified"] = False
        state["context_updated"] = False
        state["reminder_sent"] = False
        state["modified_files"] = []
        save_session_state(state_path, state)

    print(json.dumps({"decision": "allow"}))


if __name__ == "__main__":
    main()

