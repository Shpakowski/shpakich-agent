# SPEC-001: Архитектура `mcp-coop-agentic-factory` (v1.0)

> **Статус:** Implemented (v1.0 — Полностью реализовано, включая Динамический Харнес, Двусторонний `sync`, Консолидацию Знаний `Harness²` и архивацию `/compact`, и разложено в граф `.context/`)  
> **Проект:** `mcp-coop-agentic-factory`  
> **Назначение:** Executive Summary выполненной базовой спецификации v1.0 (включая двухступенчатый Progressive Disclosure RAG, двустороннюю синхронизацию `Workspace <-> PostgreSQL` с 3-Way Hash Sync `.context/`, серверную консолидацию знаний `HarnessConsolidator` и архивацию сводок сжатия контекста `/compact`) и навигационный указатель по живым узлам инженерного графа `.context/`.

---

## 1. Суть вехи (Executive Summary)

В рамках вехи **v1.0** спроектирован и полностью реализован четырёхслойный автономный инфраструктурный слой («Фабрика агентов» `shpakich-agent` / `mcp-coop-agentic-factory`) для ИИ-ассистированной разработки. На хост-машине работает легковесный кросс-рантаймовый CLI-инжектор на **Bun / Node.js** (`bunx` / `npx`), который автоматически детектирует стек и целевого ИИ-агента (**Antigravity**, **Claude Code**, **Cursor**, **OpenCode**, **Codex**), материализует нативные адаптеры и оставляет хост абсолютно чистым от тяжёлых зависимостей и локальных ML-библиотек. Вся аналитика, долговременная память и координация изолированы внутри глобального Docker-стека (`~/.mcp-coop-agentic-factory/`), включающего асинхронное ядро **Python 3.12 (`FastAPI` + `FastMCP`)**, **PostgreSQL 18 (`pgvector` + `tsvector` + `pg_trgm`)** и **Redis 7 Alpine**.

Поверх этого фундамента развёрнут контур перехвата 5 фаз жизненного цикла агента (`SessionStart`, `PreInvocation`, `PreToolUse`, `PostToolUse`, `Stop`) под управлением модульного HTTP-шлюза (`src/core/api/*`), многоуровневого `DecisionEngine` (`src/core/decision/*`), селектора `dynamic_rag.py` и векторно-лексического диспетчера `HookScriptDispatcher`. Система реализует:
* **Двухступенчатый Progressive Disclosure (`Minimal Bootstrap on Disk + Dynamic Hook RAG in DB`)**: на диск проекта материализуется только неизменный Bootstrap-каркас (`rules/AGENTS.md` с правилами `execution_mode = 'always'` и единый скилл-оркестратор `skills/harness-orchestrator/SKILL.md`), совместимый с Prompt Caching. Все остальные доменные правила и навыки (`execution_mode = 'semantic'`) динамически отбираются под запрос пользователя в хуке `PreInvocation` (`Top-3` правила при `relevance >= 0.25`, `Top-2` скилла — полное тело `SKILL.md` при `relevance >= 0.45` или 1-строчный индекс при `0.22 <= relevance < 0.45`).
* **Двусторонний `sync` (`Workspace <-> PostgreSQL`) и 3-Way Hash Sync графа `.context/`**: сканер `scanCustomWorkspaceComponents` (`src/cli/workspace_sync.ts`) с защитой от самоинжеста загружает пользовательские скиллы и правила в `POST /api/sync/ingest` через `Content-Hash Gate` и валидацию `HARNESS_PROBE`, а `POST /api/sync/context` синхронизирует узлы `.context/` с таблицей `project_context_nodes` по тройке хешей `(local_hash, db_hash, base_hash)` и структурному AST-мержу (`markdown_merge.py`).
* **Серверную консолидацию и Lineage-дедупликацию знаний (`HarnessConsolidator`)**: кластеризация похожих скиллов и правил одного типа с пересекающимися `stack_tags` (`cosine similarity >= 0.86` или `lexical >= 0.60`) через связные компоненты `nx.Graph`, синтез канонического компонента (`synthesize_cluster_markdown`) и перевод дублей в `status = 'archived'` (`merged_from` / `merged_into`, поддержка `--dry-run`).
* **Контур Re-Hydration и долгосрочной архивации сводок `/compact` (`session_compactions`)**: при обнаружении маркера сжатия контекста (`"type": "CHECKPOINT"` / `# Resuming from a compaction` / `<summary>`) в хвосте `transcript.jsonl` хук `PreInvocation` автоматически восстанавливает карту `.context/index.md` в `systemMessage`, идемпотентно архивирует `<summary>` в таблицу `session_compactions` PostgreSQL 18 с 768-мерным эмбеддингом и фиксирует SHA-256 чекпоинт в Redis (`last_compact_hash`, `last_compact_step`).
* Локальный MCP-шлюз (`/mcp`, 8 инструментов, включая Write-Through в `project_context_nodes` при `coop_upsert_context_node` и поиск по `session_compactions` через `coop_search_memory(include_compactions=True)`) и опциональный изолированный контур микро-агентов на базе `google-antigravity` SDK (`Context Healer`, `Probe Auditor`, `E2E Agent Probe`).

---

## 2. Чеклист реализованных возможностей v1.0

| Слой / Контур | Реализованные компоненты и возможности | Статус |
| :--- | :--- | :--- |
| **Слой 1: Dual-Runtime CLI (`src/cli/`)** | Команды `init`, `up`, `down`, `sync` (двусторонний Ingest + 3-Way `.context/` Sync), `status`, `harness` (включая `consolidate [--dry-run]`), `providers`, `hook`; модульный `workspace_sync.ts` с Anti-Self-Ingest защитой; TUI-визард на `@clack/prompts` + `styleText`; fail-open мост хуков (`stdin/stdout`). | ✅ Выполнено |
| **Слой 2: Глобальный Docker-стек (`docker/`)** | Оркестрация 3 контейнеров (`mcp-coop-agentic-factory-core` на Python 3.12 Slim, `pgvector/pgvector:pg18`, `redis:7-alpine`); потребление RAM ядра ~50–70 МБ без PyTorch/CUDA. | ✅ Выполнено |
| **Хранилище: PostgreSQL 18 + Redis 7 (`src/core/db/`)** | 7 таблиц: `projects`, `harness_components` (с `content_hash`), `memories`, `hook_events`, `decision_providers`, `session_compactions`, `project_context_nodes` (`vector(768)` + `tsvector` + `pg_trgm`); `CompactionArchiveStore`; Redis-ключи сессий (`last_compact_hash`, `last_compact_step`), анти-зацикливания (`cmd_fails`), Iris Short-to-Long Promotion и LangCache Lite. | ✅ Выполнено |
| **Слой 3: Перехват 5 жизненных хуков (`src/core/api/` + `src/core/decision/`)** | Модульный пакет `src/core/api/` (`models.py`, `deps.py`, `routes_management.py`, `routes_hooks.py`, `routes_sync.py`) и `src/core/decision/` (`tiers.py`, `engine.py`, `transcript.py`, `hook_dialects.py`); эндпоинты `/api/hooks/{session-start,pre-invocation,pre-tool-use,post-tool-use,stop}`; двухступенчатый Progressive Disclosure (`dynamic_rag.py`), Guardrails, перехват `/compact` (`Re-Hydration` + `session_compactions`), инъекция при `/plan`, Error Recovery RAG. | ✅ Выполнено |
| **Слой 4: Контур `Harness²`, Синхронизация и Консолидация (`src/core/harness/`, `src/core/graph/`)** | Стейт-машина `Propose → Probe → Compose` (`ComponentStatus.ARCHIVED`); Minimal Bootstrap в `HarnessCompiler` (`harness-orchestrator`); `HookScriptDispatcher`; `WorkspaceSyncEngine` (`sync_engine.py`) + структурный AST-мержер `markdown_merge.py`; сервис консолидации дубликатов `HarnessConsolidator` (`consolidator.py`); валидатор `.context/` (`ContextGraphManager`) на `NetworkX` + `Pydantic`. | ✅ Выполнено |
| **Локальный шлюз `FastMCP` (`src/core/mcp/`)** | Streamable HTTP сервер (`/mcp`) с 8 инструментами: `coop_search_memory` (`include_compactions=True`), `coop_learn_memory`, `coop_propose_harness_edit`, `coop_publish_skill`, `coop_query_context_graph`, `coop_upsert_context_node` (с синхронным Write-Through в `project_context_nodes`), `coop_get_factory_status` (включая счётчики 7 таблиц), `coop_manage_decision_providers`. | ✅ Выполнено |
| **Тонкие клиенты и изолированный SDK (`src/core/services/`, `src/core/decision/`)** | Пул провайдеров `RuntimeProviderPool` + OpenAI `Decisions API` (`Predicate`, `Choice`, `Score`); тонкий HTTP-клиент `EmbeddingClient` (`EmbeddingGemma 2` / Gemini 768-dim с FTS-фолбэком); сервис микро-агентов `AntigravitySDKService` в sandbox `/tmp/mcp_coop_agy_*`. | ✅ Выполнено |

---

## 3. Ключевые архитектурные контуры ядра

### 3.1. Двухступенчатый Progressive Disclosure и Динамический Харнес (`src/core/harness/dynamic_rag.py`)
1. **Minimal Bootstrap на диске (`execution_mode = 'always'`):**
   - В папку проекта материализуются только базовые инвариантные правила (`rules/AGENTS.md`) и единый скилл-оркестратор (`skills/harness-orchestrator/SKILL.md`), что исключает разрастание файлов в репозитории и сохраняет кэш префикса системного промпта (Prompt Caching).
2. **Семантический отбор на `PreInvocation` (`execution_mode = 'semantic'`):**
   - В [`dynamic_rag.py`](../src/core/harness/dynamic_rag.py) запрос пользователя ранжируется по гибридной формуле `0.65 * vec_sim + 0.20 * ts_rank_cd + 0.15 * word_similarity` (с лексическим и оффлайн-фолбэками):
     - **Доменные правила (`system_prompt`, `tool_guidance`):** отбираются `Top-3` при `relevance >= 0.25` (`[Dynamic Harness² Rules]`).
     - **Прикладные навыки (`skill`):** отбираются `Top-2` — при `relevance >= 0.45` инжектируется полное тело `SKILL.md` (`[Active Skill Blueprint: <slug>]`), а при `0.22 <= relevance < 0.45` — компактный 1-строчный индекс (`[Available Skill Hint]`).

### 3.2. Двусторонний `sync`, 3-Way Hash Sync `.context/` и Консолидация Знаний (`src/core/graph/sync_engine.py`, `src/core/harness/consolidator.py`)
1. **Ingest пользовательских скиллов и правил (`POST /api/sync/ingest`):**
   - Сканер `scanCustomWorkspaceComponents` (`src/cli/workspace_sync.ts`) обходит `.agents/{skills,rules}`, `.claude/{skills,rules}` и `.cursor/{skills,rules}`, исключая системную папку плагина и `manifest.managed_files`.
   - `WorkspaceSyncEngine.ingest_components()` проверяет `content_hash` (пропуская неизменившиеся записи), валидирует новинки через `DecisionDomain.HARNESS_PROBE`, векторизует и сохраняет со статусом `composed` (`execution_mode = 'semantic'`).
2. **3-Way Hash Sync и структурный AST-мерж `.context/` (`POST /api/sync/context`):**
   - Сверка тройки хешей `(local_hash, db_hash, base_hash)` в таблице `project_context_nodes` защищает от ложных перезаписей из-за сброса `mtime` при `git checkout` / `git pull`.
   - При параллельных изменениях `merge_context_node_markdown()` (`src/core/graph/markdown_merge.py`) объединяет списки связей в YAML-шапке (`stack`, `files`, `depends_on`, `implements`, `governed_by`) без дублей и хронологически сливает записи `- YYYY-MM-DD: ...` в секции `## Логи контекста (AI Traces)`.
3. **Серверная консолидация знаний (`POST /api/harness/consolidate`):**
   - `HarnessConsolidator` (`src/core/harness/consolidator.py`) строит граф близости `nx.Graph` среди `composed` компонентов одного типа с пересекающимися `stack_tags` (`cosine similarity >= 0.86` или `lexical >= 0.60`), выделяет связные кластеры, синтезирует канонический компонент и переводит дубликаты в `ComponentStatus.ARCHIVED` с lineage-трассировкой `merged_from` / `merged_into`.

### 3.3. Архивация сжатий контекста (Post-Compact Summary Archival, `src/core/db/compactions.py`)
1. **Неблокирующий парсинг `<summary>` из `transcript.jsonl` (`src/core/decision/transcript.py`):**
   - Читаются последние `64 КБ` (`TRANSCRIPT_TAIL_MAX_BYTES = 65_536`) файла транскрипта с формированием иммутабельного `CompactionCheckpoint` и 16-символьного SHA-256 дайджеста (`compute_checkpoint_hash`).
2. **Дедупликация в Redis (`src/core/db/redis_client.py`) и хранение в PostgreSQL 18 (`session_compactions`):**
   - Поля `last_compact_hash` и `last_compact_step` в `session:{conversation_id}:meta` предотвращают повторную архивацию на следующих ходах.
   - Поиск `CompactionArchiveStore.search_compactions()` ранжирует сводки по `0.55 * vec_sim + 0.25 * ts_norm + 0.20 * trgm_sim` и используется при планировании (`/plan` -> `[Prior Session Compactions]`) и в `coop_search_memory(include_compactions=True)`.

---

## 4. Карта декомпозиции в `.context/` (Куда переехали детали)

Все детальные схемы таблиц, контракты JSON-пейлоадов хуков, сигнатуры MCP/HTTP эндпоинтов и архитектурные инварианты спецификации v1.0 декомпозированы в живые узлы графа `.context/`:

### Модули кода (`Module`)
* [`.context/entities/modules/mod-python-core.md`](../.context/entities/modules/mod-python-core.md) — Python 3.12 ядро (`FastAPI`, модульный пакет `src/core/api/*`, `FastMCP`, `DecisionEngine` + `tiers.py` + `transcript.py`, `CompactionArchiveStore`, `HarnessCompiler` + `dynamic_rag.py` + `consolidator.py`, `HookScriptDispatcher`, `ContextGraphManager` + `markdown_merge.py` + `sync_engine.py`, `EmbeddingClient`, `AntigravitySDKService`).
* [`.context/entities/modules/mod-bun-cli.md`](../.context/entities/modules/mod-bun-cli.md) — Кросс-рантаймовый CLI-инжектор на TypeScript (`detector`, `wizard`, `docker`, `materializer`, `workspace_sync`, `hook_runner`).
* [`.context/entities/modules/mod-docker-stack.md`](../.context/entities/modules/mod-docker-stack.md) — Инфраструктурный Docker Compose стек (`Dockerfile`, `docker-compose.yml`, правила изоляции).

### Контракты и схемы (`Contract`)
* [`.context/entities/contracts/contract-storage-schema.md`](../.context/entities/contracts/contract-storage-schema.md) — DDL-схема 7 таблиц PostgreSQL 18 (`projects`, `harness_components`, `memories`, `hook_events`, `decision_providers`, `session_compactions`, `project_context_nodes`) и структура ключей Redis 7.
* [`.context/entities/contracts/contract-lifecycle-hooks.md`](../.context/entities/contracts/contract-lifecycle-hooks.md) — Контракты 5 фаз жизненных хуков, мульти-агентные диалекты (`HookDialect`), двухступенчатый Progressive Disclosure (`dynamic_rag.py`), контур `/compact` (`Re-Hydration` + `CompactionArchiveStore`) и тройной контур принуждения `.context/`.
* [`.context/entities/contracts/contract-mcp-and-http-api.md`](../.context/entities/contracts/contract-mcp-and-http-api.md) — Спецификация 8 инструментов `FastMCP` (`/mcp`) и REST-эндпоинтов `FastAPI` (`/api/*`, включая `/api/sync/ingest`, `/api/sync/context`, `/api/harness/consolidate`).

### Архитектурные решения (`Decision` / ADR)
* [`.context/entities/decisions/adr-001-clean-host-global-docker.md`](../.context/entities/decisions/adr-001-clean-host-global-docker.md) — Чистота хост-машины, легковесный CLI и вынос аналитики/БД в глобальный Docker-стек без локального PyTorch/CUDA.
* [`.context/entities/decisions/adr-002-harness2-evolution-loop.md`](../.context/entities/decisions/adr-002-harness2-evolution-loop.md) — Эволюция окружения агента по циклу `Propose → Probe → Compose` (`Harness²`) с разделением на `global` и `domain` знания.
* [`.context/entities/decisions/adr-003-isolated-antigravity-sdk-tier.md`](../.context/entities/decisions/adr-003-isolated-antigravity-sdk-tier.md) — Опциональный изолированный контур фоновых микро-агентов `google-antigravity` SDK во временных песочницах.
