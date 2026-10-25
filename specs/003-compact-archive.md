# SPEC-003: Архивация сжатий контекста (Post-Compact Summary Archival)

> **Статус:** Draft (В очереди на реализацию)  
> **Проект:** `mcp-coop-agentic-factory`  
> **Назначение:** Извлечение, структурирование и долгосрочная архивация `<summary>` чекпоинтов после выполнения команды `/compact` в базу знаний PostgreSQL.

---

## 1. Суть задачи

В базовой реализации хука `PreInvocation` ([`src/core/main.py`](../src/core/main.py) и [`src/core/decision/hook_dialects.py`](../src/core/decision/hook_dialects.py)) система уже детектирует событие сжатия контекста (`"type": "CHECKPOINT"` / `# Resuming from a compaction` в хвосте `transcript.jsonl`) и автоматически выполняет **Re-Hydration** — заново инжектит актуальную карту `.context/index.md` в `systemMessage`.

Данная спецификация описывает вторую половину контура — **долгосрочную архивацию самого сгенерированного `<summary>` сессии в PostgreSQL**.

---

## 2. Архитектура решения

1. **Парсинг `<summary>` из `transcript.jsonl`:**
   - При обнаружении нового чекпоинта сжатия в `inspect_transcript_tail` извлекается содержимое блока `<summary>...</summary>` вместе с `conversation_id`, `step_index` и `workspace_path`.
   - Чтобы избежать повторной записи одного и того же чекпоинта на последующих шагах, в Redis (`session:{conversationId}:meta`) фиксируется хеш или `step_index` последнего сархивированного чекпоинта.
2. **Хранение в PostgreSQL (`session_compactions` / `memories`):**
   - Сводка сохраняется в таблицу истории сессий с привязкой к проекту (`workspace_path`), тегам стека (`stack_tags`) и опциональным эмбеддингом `vector(768)` для последующего кросс-сессионного поиска решений.
