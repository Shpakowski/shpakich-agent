#!/usr/bin/env python3
"""
Слой: Локальный хук отслеживания изменений файлов (Контур 3 — часть 1: Наблюдатель).
Назначение: Вызывается после выполнения инструмента (PostToolUse). Отслеживает,
менял ли агент файлы проекта (`code_modified`) или обновлял граф `.context/` (`context_updated`),
сохраняя состояние текущей сессии во временный JSON-файл в `/tmp/`.
"""

import json
import sys
import tempfile
from pathlib import Path

# Инструменты Antigravity, которые создают или изменяют файлы на диске
FILE_MUTATION_TOOLS = {"write_file", "edit_file"}


def get_session_state_path(conversation_id: str) -> Path:
    """
    Возвращает путь к файлу состояния сессии в системной папке `/tmp`.
    Каждый чат (`conversationId`) изолирован в своём собственном файле.
    """
    safe_id = "".join(c for c in conversation_id if c.isalnum() or c in ("-", "_")) or "default"
    return Path(tempfile.gettempdir()) / f"mcp-coop-session-{safe_id}.json"


def load_session_state(state_path: Path) -> dict:
    """Читает текущее состояние сессии с диска или создаёт чистое дефолтное состояние."""
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
    """Атомарно сохраняет обновлённое состояние сессии на диск."""
    try:
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"[mcp-coop] Failed to save session state: {exc}\n")


def resolve_workspace_root(payload: dict) -> Path:
    """Определяет корневую директорию проекта."""
    workspace_paths = payload.get("workspacePaths")
    if isinstance(workspace_paths, list) and workspace_paths:
        return Path(workspace_paths[0]).resolve()
    return Path(__file__).resolve().parents[2]


def main() -> None:
    # 1. Читаем входной JSON от Antigravity
    try:
        raw_input = sys.stdin.read().strip()
        payload = json.loads(raw_input) if raw_input else {}
    except (OSError, json.JSONDecodeError, ValueError):
        print(json.dumps({}))
        return

    # 2. Если вызов тула завершился с ошибкой (`error` не пустой), файл не изменился — выходим
    if payload.get("error"):
        print(json.dumps({}))
        return

    tool_call = payload.get("toolCall") or {}
    tool_name = str(tool_call.get("name", "")).lower()

    # Интересуют только тулы записи и редактирования файлов
    if tool_name not in FILE_MUTATION_TOOLS:
        print(json.dumps({}))
        return

    # 3. Извлекаем путь к изменённому файлу из аргументов тула
    args = tool_call.get("args") or {}
    raw_file_path = args.get("Path") or args.get("path") or args.get("TargetFile")
    if not raw_file_path:
        print(json.dumps({}))
        return

    workspace_root = resolve_workspace_root(payload)
    target_path = Path(raw_file_path).resolve()

    # 4. Проверяем, лежит ли файл внутри нашего проекта (игнорируем системные артефакты в ~/.gemini/)
    try:
        rel_path = target_path.relative_to(workspace_root).as_posix()
    except ValueError:
        # Файл находится за пределами рабочей папки проекта
        print(json.dumps({}))
        return

    # 5. Обновляем флаги сессии в зависимости от того, КАКОЙ файл был изменён
    conversation_id = str(payload.get("conversationId", "default"))
    state_path = get_session_state_path(conversation_id)
    state = load_session_state(state_path)

    if rel_path.startswith(".context/"):
        # Агент обновил граф контекста — ставим отметку синхронизации
        state["context_updated"] = True
        state["reminder_sent"] = False
    else:
        # Агент изменил файл проекта вне `.context/` — требуем последующего обновления графа
        state["code_modified"] = True
        state["context_updated"] = False
        modified = state.get("modified_files", [])
        if rel_path not in modified:
            modified.append(rel_path)
        state["modified_files"] = modified

    save_session_state(state_path, state)

    # По контракту PostToolUse всегда возвращаем пустой объект `{}`
    print(json.dumps({}))


if __name__ == "__main__":
    main()

