#!/usr/bin/env python3
"""
Слой: Локальный хук инъекции контекста (Контур 2 — Graph-as-Code).
Назначение: Читает карту графа `.context/index.md` и впрыскивает её в контекст
ИИ-агента через контракт `injectSteps` (совместим с хуками SessionStart и PreInvocation).
Если файл отсутствует или повреждён, отправляет системное предупреждение, чтобы агент
сообщил об этом пользователю на языке пользователя.
"""

import json
import sys
from pathlib import Path


def resolve_workspace_root(payload: dict) -> Path:
    """
    Определяет корень проекта из входного JSON от Antigravity (`workspacePaths`).
    Если поле отсутствует (например, при ручном запуске в терминале),
    вычисляет корень относительно расположения самого скрипта (`.agents/hooks/` -> корень).
    """
    workspace_paths = payload.get("workspacePaths")
    if isinstance(workspace_paths, list) and workspace_paths:
        return Path(workspace_paths[0])
    return Path(__file__).resolve().parents[2]


def build_system_step(message: str) -> dict:
    """
    Упаковывает текст в формат `injectSteps` по контракту Antigravity.
    Вложенный объект `{"systemMessage": {"systemMessage": ...}}` сохраняется в истории сессии.
    """
    return {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": message
                }
            }
        ]
    }


def build_injection_response(index_content: str) -> dict:
    """Формирует штатный JSON-ответ с картой контекстного графа."""
    message = (
        "[MCP-COOP Context Graph]: Active project context graph map (`.context/index.md`).\n"
        "Use links from this map for targeted node reading and always update the graph "
        "after modifying project files:\n\n"
        f"{index_content.strip()}"
    )
    return build_system_step(message)


def build_warning_response(reason: str) -> dict:
    """
    Формирует предупреждение (Loud Graceful Degradation), если `.context/index.md`
    не найден или не может быть прочитан. Инструктирует агента предупредить пользователя
    на том языке, на котором общается пользователь.
    """
    warning = (
        f"[MCP-COOP WARNING]: Context graph entry point `.context/index.md` could not be loaded ({reason}).\n"
        "IMPORTANT: At the very beginning of your reply, warn the user in their own language "
        "that `.context/index.md` is missing or unreadable, and ask whether you should restore or initialize it."
    )
    return build_system_step(warning)


def main() -> None:
    # 1. Безопасно читаем входной JSON из stdin от Antigravity
    try:
        raw_input = sys.stdin.read().strip()
        payload = json.loads(raw_input) if raw_input else {}
    except Exception:
        payload = {}

    # 2. Находим карту графа `.context/index.md` в корне рабочего пространства
    workspace_root = resolve_workspace_root(payload)
    index_path = workspace_root / ".context" / "index.md"

    # Если файла карты нет на диске — отправляем предупреждение через агента (без тихого отказа)
    if not index_path.is_file():
        response = build_warning_response(f"file not found at {index_path}")
        print(json.dumps(response, ensure_ascii=False))
        return

    try:
        index_content = index_path.read_text(encoding="utf-8")
    except Exception as exc:
        response = build_warning_response(f"read error: {exc}")
        print(json.dumps(response, ensure_ascii=False))
        return

    # 3. Отдаём сформированный контракт `injectSteps` в stdout
    response = build_injection_response(index_content)
    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
