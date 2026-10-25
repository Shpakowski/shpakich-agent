# SPEC-001: Архитектура `mcp-coop-agentic-factory` (v1.0)

> **Статус:** Implemented (v1.0 — Полностью реализовано, включая архивацию `/compact`, и разложено в граф `.context/`)  
> **Проект:** `mcp-coop-agentic-factory`  
> **Назначение:** Executive Summary выполненной базовой спецификации v1.0 (включая долгосрочную архивацию сводок сжатия контекста `/compact`) и навигационный указатель по живым узлам инженерного графа `.context/`.

---

## 1. Суть вехи (Executive Summary)

В рамках вехи **v1.0** спроектирован и полностью реализован четырёхслойный автономный инфраструктурный слой («Фабрика агентов» `shpakich-agent` / `mcp-coop-agentic-factory`) для ИИ-ассистированной разработки. На хост-машине работает легковесный кросс-рантаймовый CLI-инжектор на **Bun / Node.js** (`bunx` / `npx`), который автоматически детектирует стек и целевого ИИ-агента (**Antigravity**, **Claude Code**, **Cursor**, **OpenCode**, **Codex**), материализует нативные адаптеры и оставляет хост абсолютно чистым от тяжёлых зависимостей и локальных ML-библиотек. Вся аналитика, долговременная память и координация изолированы внутри глобального Docker-стека (`~/.mcp-coop-agentic-factory/`), включающего асинхронное ядро **Python 3.12 (`FastAPI` + `FastMCP`)**, **PostgreSQL 18 (`pgvector` + `tsvector` + `pg_trgm`)** и **Redis 7 Alpine**.

Поверх этого фундамента развёрнут контур перехвата 5 фаз жизненного цикла агента (`SessionStart`, `PreInvocation`, `PreToolUse`, `PostToolUse`, `Stop`) под управлением модульного HTTP-шлюза (`src/core/api/*`), многоуровневого `DecisionEngine` (`src/core/decision/*`) и векторно-лексического диспетчера `HookScriptDispatcher`. Система реализует:
* Замкнутый цикл самообучения **`Harness²` (`Propose → Probe → Compose`)** с разделением знаний на инвариантные (`global`) и доменно-специфичные (`domain` по тегам стека) и поддержкой пер-проектных оверрайдов (`ProjectHarnessOverrides`).
* **Контур Re-Hydration и долгосрочной архивации сводок `/compact` (`session_compactions`)**: при обнаружении маркера сжатия контекста (`"type": "CHECKPOINT"` / `# Resuming from a compaction` / `<summary>`) в хвосте `transcript.jsonl` хук `PreInvocation` автоматически восстанавливает карту `.context/index.md` в `systemMessage`, идемпотентно архивирует само сгенерированное `<summary>` в таблицу `session_compactions` PostgreSQL 18 с 768-мерным эмбеддингом `EmbeddingGemma 2` и фиксирует SHA-256 чекпоинт в Redis (`session:{conversation_id}:meta` -> `last_compact_hash`, `last_compact_step`) для исключения повторной архивации на следующих шагах.
* Локальный MCP-шлюз (`/mcp`, 8 инструментов, включая гибридный поиск по `memories` и `session_compactions` через `coop_search_memory(include_compactions=True)`) и опциональный изолированный контур микро-агентов на базе `google-antigravity` SDK (`Context Healer`, `Probe Auditor`, `E2E Agent Probe`).

---

## 2. Чеклист реализованных возможностей v1.0

| Слой / Контур | Реализованные компоненты и возможности | Статус |
| :--- | :--- | :--- |
| **Слой 1: Dual-Runtime CLI (`src/cli/`)** | Команды `init`, `up`, `down`, `sync`, `status`, `harness`, `providers`, `hook`; авто-детектор стека и 5 ИИ-агентов; генератор плагинов; TUI-визард на `@clack/prompts` + `styleText`; fail-open мост хуков (`stdin/stdout`). | ✅ Выполнено |
| **Слой 2: Глобальный Docker-стек (`docker/`)** | Оркестрация 3 контейнеров (`mcp-coop-agentic-factory-core` на Python 3.12 Slim, `pgvector/pgvector:pg18`, `redis:7-alpine`); потребление RAM ядра ~50–70 МБ без PyTorch/CUDA. | ✅ Выполнено |
| **Хранилище: PostgreSQL 18 + Redis 7 (`src/core/db/`)** | 6 таблиц: `projects`, `harness_components`, `memories`, `hook_events`, `decision_providers`, `session_compactions` (`vector(768)` + `tsvector` + `pg_trgm`); `CompactionArchiveStore`; Redis-ключи сессий (`last_compact_hash`, `last_compact_step`), анти-зацикливания (`cmd_fails`), Iris Short-to-Long Promotion и LangCache Lite. | ✅ Выполнено |
| **Слой 3: Перехват 5 жизненных хуков (`src/core/api/` + `src/core/decision/`)** | Модульный пакет `src/core/api/` (`models.py`, `deps.py`, `routes_management.py`, `routes_hooks.py`) и `src/core/decision/` (`tiers.py`, `engine.py`, `transcript.py`, `hook_dialects.py`); эндпоинты `/api/hooks/{session-start,pre-invocation,pre-tool-use,post-tool-use,stop}`; Guardrails (`block_command`, `rewrite_command`, контроль `implementation_plan.md`), перехват `/compact` (Re-Hydration + архивация `<summary>` в `session_compactions`), инъекция прошлых сводок при `/plan`, Error Recovery RAG. | ✅ Выполнено |
| **Слой 4: Контур `Harness²` и Контекстный Граф (`src/core/harness/`, `src/core/graph/`)** | Стейт-машина `Propose → Probe → Compose` для компонентов `system_prompt`, `guardrail`, `skill`, `tool_guidance`, `script`, `subagent`; компилятор `HarnessCompiler`; диспетчер `HookScriptDispatcher`; парсер и валидатор `.context/` (`ContextGraphService` / `ContextGraphManager`) на `NetworkX` + `Pydantic`. | ✅ Выполнено |
| **Локальный шлюз `FastMCP` (`src/core/mcp/`)** | Streamable HTTP сервер (`/mcp`) с 8 инструментами: `coop_search_memory` (с поддержкой `include_compactions=True`), `coop_learn_memory`, `coop_propose_harness_edit`, `coop_publish_skill`, `coop_query_context_graph`, `coop_upsert_context_node`, `coop_get_factory_status` (включая счётчик `session_compactions`), `coop_manage_decision_providers`. | ✅ Выполнено |
| **Тонкие клиенты и изолированный SDK (`src/core/services/`, `src/core/decision/`)** | Пул провайдеров `RuntimeProviderPool` + OpenAI `Decisions API` (`Predicate`, `Choice`, `Score`); тонкий HTTP-клиент `EmbeddingClient` (`EmbeddingGemma 2` / Gemini 768-dim с FTS-фолбэком); сервис микро-агентов `AntigravitySDKService` в sandbox `/tmp/mcp_coop_agy_*`. | ✅ Выполнено |

---

## 3. Архитектура архивации сжатий контекста (Post-Compact Summary Archival)

1. **Неблокирующий парсинг `<summary>` из `transcript.jsonl` (`src/core/decision/transcript.py`):**
   - Читаются последние `64 КБ` (`TRANSCRIPT_TAIL_MAX_BYTES = 65_536`) файла транскрипта с поддержкой обрезанных UTF-8 строк и многострочных XML-блоков `<summary>...</summary>`.
   - При обнаружении чекпоинта сжатия (`"type": "CHECKPOINT"`, `# Resuming from a compaction` или `<summary>`) формируется иммутабельный `CompactionCheckpoint` с нормализованным текстом сводки, `step_idx` и 16-символьным SHA-256 дайджестом (`compute_checkpoint_hash`).
2. **Дедупликация в Redis (`src/core/db/redis_client.py`):**
   - В хэше сессии `session:{conversation_id}:meta` хранятся поля `last_compact_hash` и `last_compact_step`.
   - Если `checkpoint.checkpoint_hash == last_compact_hash`, хук `PreInvocation` пропускает повторную ре-гидратацию и запись в БД на последующих шагах диалога.
3. **Долгосрочное хранение и гибридный поиск в PostgreSQL 18 (`src/core/db/compactions.py`):**
   - Таблица `session_compactions` хранит `conversation_id`, `workspace_path`, `agent_type`, `step_idx`, `checkpoint_hash`, `summary_text`, `stack_tags`, `embedding vector(768)` и генерируемый столбец `search_tsv` с ограничением идемпотентности `UNIQUE (conversation_id, checkpoint_hash)`.
   - Поиск `CompactionArchiveStore.search_compactions()` ранжирует сводки по формуле `0.55 * vec_sim + 0.25 * ts_norm + 0.20 * trgm_sim` (с автоматическим лексическим фолбэком `0.60 * ts_norm + 0.40 * trgm_sim` при недоступности API эмбеддингов) и используется как при планировании (`/plan` -> `[Prior Session Compactions]`), так и в инструменте `coop_search_memory(include_compactions=True)`.

---

## 4. Карта декомпозиции в `.context/` (Куда переехали детали)

Все детальные схемы таблиц, контракты JSON-пейлоадов хуков, сигнатуры MCP/HTTP эндпоинтов и архитектурные инварианты спецификации v1.0 декомпозированы в живые узлы графа `.context/`:

### Модули кода (`Module`)
* [`.context/entities/modules/mod-python-core.md`](../.context/entities/modules/mod-python-core.md) — Python 3.12 ядро (`FastAPI`, модульный пакет `src/core/api/*`, `FastMCP`, `DecisionEngine` + `tiers.py` + `transcript.py`, `CompactionArchiveStore`, `HarnessCompiler`, `HookScriptDispatcher`, `ContextGraphService`, `EmbeddingClient`, `AntigravitySDKService`).
* [`.context/entities/modules/mod-bun-cli.md`](../.context/entities/modules/mod-bun-cli.md) — Кросс-рантаймовый CLI-инжектор на TypeScript (`detector`, `wizard`, `docker`, `materializer`, `hook_runner`).
* [`.context/entities/modules/mod-docker-stack.md`](../.context/entities/modules/mod-docker-stack.md) — Инфраструктурный Docker Compose стек (`Dockerfile`, `docker-compose.yml`, правила изоляции).

### Контракты и схемы (`Contract`)
* [`.context/entities/contracts/contract-storage-schema.md`](../.context/entities/contracts/contract-storage-schema.md) — DDL-схема 6 таблиц PostgreSQL 18 (`projects`, `harness_components`, `memories`, `hook_events`, `decision_providers`, `session_compactions`) и структура ключей Redis 7.
* [`.context/entities/contracts/contract-lifecycle-hooks.md`](../.context/entities/contracts/contract-lifecycle-hooks.md) — Контракты 5 фаз жизненных хуков, мульти-агентные диалекты (`HookDialect`), контур `/compact` (`Re-Hydration` + `CompactionArchiveStore`) и тройной контур принуждения `.context/`.
* [`.context/entities/contracts/contract-mcp-and-http-api.md`](../.context/entities/contracts/contract-mcp-and-http-api.md) — Спецификация 8 инструментов `FastMCP` (`/mcp`) и REST-эндпоинтов `FastAPI` (`/api/*`).

### Архитектурные решения (`Decision` / ADR)
* [`.context/entities/decisions/adr-001-clean-host-global-docker.md`](../.context/entities/decisions/adr-001-clean-host-global-docker.md) — Чистота хост-машины, легковесный CLI и вынос аналитики/БД в глобальный Docker-стек без локального PyTorch/CUDA.
* [`.context/entities/decisions/adr-002-harness2-evolution-loop.md`](../.context/entities/decisions/adr-002-harness2-evolution-loop.md) — Эволюция окружения агента по циклу `Propose → Probe → Compose` (`Harness²`) с разделением на `global` и `domain` знания.
* [`.context/entities/decisions/adr-003-isolated-antigravity-sdk-tier.md`](../.context/entities/decisions/adr-003-isolated-antigravity-sdk-tier.md) — Опциональный изолированный контур фоновых микро-агентов `google-antigravity` SDK во временных песочницах.
