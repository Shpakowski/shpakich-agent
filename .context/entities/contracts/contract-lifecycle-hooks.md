---
type: Entity
class: Contract
id: contract-lifecycle-hooks
name: Контракт перехвата жизненных хуков ИИ-агентов и тройного контура принуждения .context/
status: Active
stack: [fastapi, python, bun, typescript]
files:
  - src/core/main.py
  - src/core/api/routes_hooks.py
  - src/core/decision/transcript.py
  - src/core/decision/hook_dialects.py
  - src/core/harness/dynamic_rag.py
  - src/core/harness/script_runner.py
  - src/cli/hooks/stdin-reader.ts
  - src/cli/hooks/dialect-translator.ts
  - src/cli/hooks/hook-executor.ts
depends_on:
  - "[[contract-storage-schema]]"
implements: []
governed_by:
  - "[[adr-002-harness2-evolution-loop]]"
---

# Контракт перехвата жизненных хуков ИИ-агентов и тройного контура принуждения `.context/` (`contract-lifecycle-hooks`)

Формальный межслойный протокол взаимодействия между хостовым CLI-мостом [[mod-cli]] / [[mod-cli-hooks]] (`src/cli/hooks/hook-executor.ts`, `src/cli/hooks/dialect-translator.ts`, `src/cli/hooks/stdin-reader.ts`) и шлюзом контекста [[mod-python-core]] (`src/core/main.py`, `src/core/decision/hook_dialects.py`, `src/core/harness/script_runner.py`). Определяет унифицированную нормализацию входных событий, семантическую диспетчеризацию скриптов `HookScriptDispatcher`, трансляцию ответов в 3 протокольных диалекта ИИ-агентов и трёхуровневую защиту целостности графа `.context/`.

---

## 1. Входные и выходные контракты 5 событий жизненного цикла

Хостовый раннер `mcp-coop-agentic-factory hook <event>` читает JSON-контекст из `stdin`, обогащает его локальными метаданными (при `session-start` прикладывает содержимое `.context/index.md` в поле `context_index_md`) и отправляет `POST` в `http://127.0.0.1:8787/api/hooks/<event>` с жёстким таймаутом и **Fail-Open** фолбэком (`{}` или `{"decision": "allow"}`), если контейнер принудительно остановлен. Внутри ядра любой входной JSON приводится функцией `normalize_hook_payload()` к неизменяемой модели `CanonicalHookInput`.

| Событие (`HookEventType`) | HTTP-эндпоинт ядра | Ключевые входные поля (`CanonicalHookInput`) | Действия ядра (`DecisionEngine` + `HookScriptDispatcher`) | Выходной контракт (семантика) |
| :--- | :--- | :--- | :--- | :--- |
| **`session-start`** (`SessionStart`) | `POST /api/hooks/session-start` | `conversation_id`, `workspace_path`, `agent_type`, `context_index_md` | Инициализирует `session:{id}:meta` в Redis ([[contract-storage-schema]]), выполняет скрипт `inject-context-map`, компилирует актуальный артефакт `HarnessCompiler.compile_for_project()`. | Инжектит стартовый `systemMessage` (активные теги стека + карта `.context/index.md`) и возвращает массив `_syncFiles`, который CLI-раннер атомарно записывает в `.agents/plugins/...` до первого шага модели. |
| **`pre-tool-use`** (`PreToolUse`) | `POST /api/hooks/pre-tool-use` | `conversation_id`, `workspace_path`, `tool_name`, `tool_args`, `step_idx` | Проверяет `DecisionEngine` в домене `PLAN_VALIDATION` (при записи в `implementation_plan.md`) или `TOOL_SAFETY` (Guardrails `block_command`/`rewrite_command`, анти-зацикливание по `cmd_fails >= 2`, каскад Tier 2) + исполняет скрипты `pre-tool-use`. | Разрешает вызов (`allow`), блокирует опасную/зациклившуюся команду (`deny` с `reason`), запрашивает подтверждение (`ask` / `force_ask`) или прозрачно подменяет аргументы (`overwrite`). |
| **`post-tool-use`** (`PostToolUse`) | `POST /api/hooks/post-tool-use` | `conversation_id`, `workspace_path`, `tool_name`, `tool_args`, `error`, `step_idx` | При наличии `error` вызывает `record_tool_failure()` (`last_error` + `cmd_fails`). При успехе после сбоя вызывает `mark_error_resolved()` (создаёт `MemoryPromotionCandidate` в `session:{id}:promotion`). Запускает скрипт `track-file-changes`. | Возвращает пустой объект `{}` в `stdout`, фиксируя обновлённые флаги сессии (`code_modified`, `context_updated`, `resolved_error`) в Redis и аудит в `hook_events`. |
| **`pre-invocation`** (`PreInvocation`) | `POST /api/hooks/pre-invocation` | `conversation_id`, `workspace_path`, `transcript_path`, `prompt` | Анализирует хвост транскрипта (`inspect_transcript_tail`, последние 16 КБ) и состояние ошибок сессии в Redis; выполняет 3 сценария динамической инъекции контекста и скрипты `pre-invocation`. | Инжектит постоянный `systemMessage` (при ре-гидратации после `/compact`) и/или одношаговый транзиентный `ephemeralMessage` (подсказки `/plan` и Error Recovery RAG). |
| **`stop`** (`Stop`) | `POST /api/hooks/stop` | `conversation_id`, `workspace_path`, `termination_reason`, `stop_hook_active` | Исполняет скрипты `stop` (`enforce-context-sync`). Если граф синхронизирован и `stop_hook_active == False`, проверяет в Redis флаги `resolved_error == "1"` и `reflection_prompted == "0"`. | Возвращает `allow` или перехватывает завершение хода (`continue` / `block` с `reason`), требуя синхронизировать `.context/` или сохранить решённую ошибку через [[contract-mcp-and-http-api]]. |

---

## 2. Матрица трансляции протокольных диалектов (`HookDialect`)

Функция `format_hook_response()` (`src/core/decision/hook_dialects.py`) транслирует внутреннюю резолюцию `DecisionResolution` в точный JSON-контракт целевого агента:

| Фаза / Событие | `ANTIGRAVITY_PROTOJSON` (`antigravity`) | `CLAUDE_CODEX_V1` (`claude-code`, `codex`) | `CURSOR_V1` (`cursor`) |
| :--- | :--- | :--- | :--- |
| **`SessionStart` & `PreInvocation`** | `{"injectSteps": [{"systemMessage": {"systemMessage": "..."}}, {"ephemeralMessage": "..."}], "_syncFiles": [...]}` | `{"hookSpecificOutput": {"hookEventName": "SessionStart" \| "UserPromptSubmit", "additionalContext": "..."}, "_syncFiles": [...]}` | `{"additional_context": "...", "_syncFiles": [...]}` |
| **`PreToolUse` (Allow / Deny / Ask)** | `{"decision": "allow" \| "deny" \| "ask" \| "force_ask", "reason": "..."}` *(строго без поля `ephemeralMessage`!)* | `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow" \| "deny" \| "ask", "permissionDecisionReason": "..."}}` | `{"permission": "allow" \| "deny" \| "ask", "user_message": "...", "agent_message": "..."}` |
| **`PreToolUse` (Overwrite)** | `{"decision": "allow", "overwrite": {"CommandLine": "..."}}` | `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {...}}}` | `{"permission": "allow", "updated_input": {...}}` |
| **`PostToolUse`** | `{}` | `{}` | `{}` |
| **`Stop` (Continue / Block)** | `{"decision": "continue", "reason": "...", "ephemeralMessage": "..."}` | `{"decision": "block", "reason": "..."}` | `{"followup_message": "..."}` |
| **`Stop` (Allow)** | `{"decision": "allow"}` | `{"decision": "allow"}` | `{"decision": "allow"}` |

> **Критическое ограничение `ANTIGRAVITY_PROTOJSON` для `PreToolUse`:** Протокол Antigravity ProtoJSON для ответа `PreToolUse` не поддерживает поле `ephemeralMessage` (его наличие вызывает ошибку десериализации на стороне хоста). Поэтому в `PreToolUse` любые диагностические сообщения и предупреждения (`[Tier 2 Provider Notice]`) сериализуются исключительно внутри поля `reason` (при вердиктах, отличных от `allow`), тогда как инъекция шагов `injectSteps` (`systemMessage` и `ephemeralMessage`) применяется только в `SessionStart` и `PreInvocation`.

---

## 3. Три сценария `PreInvocation` и двухступенчатый отбор `HookScriptDispatcher`

### 3.1. Интеллектуальные сценарии `PreInvocation`
1. **Сценарий А — Ре-гидратация и архивация `<summary>` после `/compact` (`Post-Compact Re-Hydration & Summary Archival`):**
   * Функция `inspect_transcript_tail_detailed()` (`src/core/decision/transcript.py`) сканирует последние 64 КБ (`TRANSCRIPT_TAIL_MAX_BYTES = 65_536`) файла `transcript.jsonl` на наличие шага `"type": "CHECKPOINT"`, маркера `# Resuming from a compaction` или блока `<summary>...</summary>`, извлекая неизменяемый чекпоинт `CompactionCheckpoint` (`summary_text`, 16-символьный SHA-256 `checkpoint_hash`, `step_idx`).
   * В `hook_pre_invocation` (`src/core/api/routes_hooks.py`) дайджест `checkpoint_hash` сверяется с `last_compact_hash` в `session:{conversation_id}:meta` Redis (`RedisSessionStore.get_last_compaction_checkpoint`).
   * Если чекпоинт **новый**: ядро выполняет **Re-Hydration** карты `.context/index.md` в `systemMessage`, идемпотентно сохраняет сводку `<summary>` в таблицу `session_compactions` PostgreSQL через `CompactionArchiveStore.archive_compaction()` (с 768-мерным вектором `EmbeddingGemma 2`) и фиксирует `last_compact_hash` / `last_compact_step` в Redis, предотвращая повторную инъекцию `index.md` и дублирование записей на последующих шагах диалога.
2. **Сценарий Б — Инъекция паттернов проектирования при `/plan` (`Planning Best Practices Injection`):**
   * Если активный промпт пользователя содержит триггеры планирования (`/plan`, `implementation_plan`, `архитектурный план`, `пошаговый план`), функция `_build_plan_best_practices_hint()` выбирает до 3 активных компонентов `Harness` (`MAX_PLAN_PATTERNS_INJECTED = 3`), до 2 релевантных записей из `memories` (`MAX_PLAN_MEMORIES_INJECTED = 2`) и до 1 релевантной сводки прошлых сессий из `session_compactions` (`MAX_PLAN_COMPACTIONS_INJECTED = 1`) под стек проекта, инжектируя их вместе с правилом `[Plan Guardrail]` через транзиентный `ephemeralMessage`.
3. **Сценарий В — Подсказка решения при ошибке (`Error Recovery RAG`):**
   * Если в Redis (`session:{id}:last_error`) числится неразрешённый сбой инструмента с предыдущего шага, ядро выполняет 3-way гибридный поиск `engine.search_memories(last_err.error_text, stack_tags)` и передаёт найденное решение из прошлого опыта в `ephemeralMessage`.
4. **Сценарий Г — Двухступенчатый Progressive Disclosure семантических правил и скиллов (`Dynamic Prompt RAG`):**
   * На каждом шаге `PreInvocation` при непустом запросе пользователя функция `select_dynamic_components_for_prompt()` (`src/core/harness/dynamic_rag.py`) отбирает компоненты `harness_components` с `execution_mode = 'semantic'`, не входящие в статический Bootstrap на диске:
     * **Top-3 доменных правил** (`system_prompt`, `tool_guidance`, `guardrail`) при гибридной релевантности `relevance >= 0.25` инжектируются в секцию `[Dynamic Harness Rules]`.
     * **Top-2 прикладных скиллов** (`skill`): при `relevance >= 0.45` (`MIN_SKILL_BLUEPRINT_RELEVANCE`) инжектируется полный чертёж `[Active Skill Blueprint: <slug>]`, а при `0.22 <= relevance < 0.45` (`MIN_SKILL_INDEX_RELEVANCE`) — компактный однострочный индекс-намёк `[Available Skill Hint]: <slug> — <description>`.

### 3.2. Двухступенчатая воронка скриптов (`HookScriptDispatcher`)
Диспетчер `HookScriptDispatcher` (`src/core/harness/script_runner.py`) превращает жизненные хуки в декаплированную шину событий:
* **Воронка 1 (Проектный и событийный фильтр + `execution_mode = 'always'`):** SQL-запрос `SELECT_ALWAYS_SCRIPTS_SQL` отбирает из таблицы `harness_components` (`component_type = 'script'`, `status = 'composed'`) обязательные инвариантные и стековые скрипты, подписанные на текущее событие в `metadata.hook_events` и не отключённые в `ProjectHarnessOverrides.disabled_components`.
* **Воронка 2 (Семантический Top-K отбор `execution_mode = 'semantic'`):** Формируется контекстный запрос `build_hook_semantic_query(hook_in)` из имени события, инструмента, аргументов, промпта и текста ошибки. При наличии вектора от `EmbeddingClient` выполняется гибридный запрос `SELECT_SEMANTIC_SCRIPTS_VECTOR_SQL` (`0.65 * cosine_similarity + 0.20 * ts_rank_cd + 0.15 * word_similarity`), иначе лексический фолбэк `SELECT_SEMANTIC_SCRIPTS_LEXICAL_SQL`. Отбираются до `DEFAULT_MAX_SEMANTIC_SCRIPTS = 3` скриптов с порогом `relevance >= 0.15`.
* **Изолированное исполнение и агрегация вердиктов:** Скрипты запускаются в подпроцессах с жёстким таймаутом (`DEFAULT_SCRIPT_TIMEOUT_MS = 500`), получают на `stdin` JSON со слепком `session_state` из Redis, возвращают `state_updates` (атомарно сохраняемые в `session:{id}:meta`) и агрегируют итоговый `VerdictAction` по приоритету строгого вето (`ALLOW < OVERWRITE < ASK < FORCE_ASK < CONTINUE < DENY`).

---

## 4. Тройной контур принуждения `.context/` (`3-Layer Context Enforcement`)

Для гарантированной синхронизации графа знаний `.context/` с кодом репозитория действует трёхуровневая защита:
1. **Контур 1 (Декларативный инвариант в `AGENTS.md`):** Компонент `context-graph-discipline` (`Tool Guidance`) компилируется в `rules/AGENTS.md` и обязывает агента начинать задачу с чтения `.context/index.md` и атомарно обновлять `.context/entities/*` + `AI Traces` в том же ходе, где был изменён код.
2. **Контур 2 (Проактивная инъекция карты на `SessionStart` и после `/compact`):** Скрипт `inject-context-map` на `session-start` и механизм Post-Compact Re-Hydration на `pre-invocation` автоматически доставляют содержимое `.context/index.md` в `systemMessage` агента без ручного вызова чтения файла.
3. **Контур 3 (Реактивный контроль изменений `PostToolUse` + блокирующий гейт `Stop`):**
   * На каждом `post-tool-use` скрипт `track-file-changes` анализирует целевой путь в модифицирующих инструментах (`write_file`, `edit_file`, `replace_in_file` и др.): при изменении исходного кода выставляет в `session:{id}:meta` флаг `code_modified = "1"` и сбрасывает `context_updated = "0"`, а при обновлении файлов внутри `.context/` выставляет `context_updated = "1"`.
   * На хуке `stop` скрипт `enforce-context-sync` проверяет эти флаги: если `code_modified == "1"` и `context_updated != "1"` (и не активен `stop_hook_active`), он возвращает вердикт `action: "continue"` с требованием синхронизировать узлы `.context/entities/` и `.context/index.md` перед завершением ответа.

## Логи контекста (AI Traces)
- 2026-10-25: Зафиксирован контракт жизненных хуков `contract-lifecycle-hooks` (5 событий, таблица трансляции 3 диалектов с учётом ограничения `ANTIGRAVITY_PROTOJSON` в `PreToolUse`, 3 сценария `PreInvocation`, двухступенчатая воронка `HookScriptDispatcher` и тройной контур принуждения `.context/`).
- 2026-10-25: Обновлён Сценарий А и Сценарий Б `PreInvocation` для долгосрочной архивации сводок сжатия контекста `/compact` (`src/core/decision/transcript.py`, `src/core/api/routes_hooks.py`: окно хвоста 64 КБ, дедупликация чекпоинтов `/compact` через `last_compact_hash` в Redis, архивация `<summary>` в `session_compactions` и инъекция прошлых сводок при `/plan`).
- 2026-10-25: Добавлен Сценарий Г в `PreInvocation` (`src/core/harness/dynamic_rag.py`): двухступенчатый Progressive Disclosure семантических правил (`relevance >= 0.25`) и скиллов (`relevance >= 0.45` для полного blueprint, `0.22 <= relevance < 0.45` для индекс-намёка).
- 2026-10-26: Синхронизированы ссылки и пути после модульной декомпозиции CLI в рантайм-агностичные узлы `mod-cli` и `mod-cli-hooks` (`src/cli/hooks/stdin-reader.ts`, `src/cli/hooks/dialect-translator.ts`, `src/cli/hooks/hook-executor.ts`).
