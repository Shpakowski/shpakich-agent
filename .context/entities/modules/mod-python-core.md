---
type: Entity
class: Module
id: mod-python-core
name: Базовый каркас Python-ядра, слой хранения и движок Контекстного Графа (FastAPI + FastMCP + asyncpg + Redis + NetworkX)
status: Active
stack: [python, fastapi, fastmcp, pydantic, postgresql, pgvector, asyncpg, redis, networkx, pyyaml]
files:
  - pyproject.toml
  - pyrightconfig.json
  - seeds/harness-seed.json
  - seeds/prompts-config.json
  - src/core/__init__.py
  - src/core/enums.py
  - src/core/config.py
  - src/core/main.py
  - src/core/api/__init__.py
  - src/core/api/models.py
  - src/core/api/deps.py
  - src/core/api/routes_management.py
  - src/core/api/routes_hooks.py
  - src/core/api/routes_sync.py
  - src/core/api/routes_visualizer.py
  - src/core/db/__init__.py
  - src/core/db/postgres.py
  - src/core/db/redis_client.py
  - src/core/db/compactions.py
  - src/core/graph/__init__.py
  - src/core/graph/context_graph.py
  - src/core/graph/markdown_merge.py
  - src/core/graph/sync_engine.py
  - src/core/harness/__init__.py
  - src/core/harness/seed.py
  - src/core/harness/profiles.py
  - src/core/harness/compiler.py
  - src/core/harness/dynamic_rag.py
  - src/core/harness/consolidator.py
  - src/core/harness/script_runner.py
  - src/core/decision/__init__.py
  - src/core/decision/models.py
  - src/core/decision/providers.py
  - src/core/decision/tiers.py
  - src/core/decision/engine.py
  - src/core/decision/transcript.py
  - src/core/decision/hook_dialects.py
  - src/core/services/__init__.py
  - src/core/services/embeddings.py
  - src/core/services/antigravity/__init__.py
  - src/core/services/antigravity/models.py
  - src/core/services/antigravity/sandbox.py
  - src/core/services/antigravity/runtime.py
  - src/core/services/antigravity/service.py
  - src/core/mcp/__init__.py
  - src/core/mcp/tools.py
  - src/core/mcp/server.py
  - src/core/visualizer/__init__.py
  - src/core/visualizer/models.py
  - src/core/visualizer/queries.py
  - src/core/visualizer/search.py
  - src/core/visualizer/service.py
  - src/core/visualizer/topology.py
  - src/core/ui/__init__.py
  - src/core/ui/styles.css
  - src/core/ui/template.html
  - src/core/ui/graph_canvas.js
  - src/core/ui/app.js
  - tests/unit/test_spec002_dynamic_harness.py
  - tests/unit/test_spec003_compactions.py
  - tests/unit/test_visualizer.py
depends_on: []
implements:
  - "[[contract-storage-schema]]"
  - "[[contract-lifecycle-hooks]]"
  - "[[contract-mcp-and-http-api]]"
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
  - "[[adr-003-isolated-antigravity-sdk-tier]]"
---

# Базовый каркас Python-ядра, слой хранения и движок Контекстного Графа (`mod-python-core`)

Модуль реализует фундамент **Layer 2 (Core Engine)** фабрики `mcp-coop-agentic-factory` на базе Python 3.12+, работающий внутри глобального Docker-стека (`~/.mcp-coop-agentic-factory/`). Обеспечивает типизированную конфигурацию окружения, управление жизненным циклом ресурсов (`lifespan`), нативный асинхронный пул соединений PostgreSQL 18 (`asyncpg`) с идемпотентной инициализацией схемы БД, асинхронное хранилище оперативной памяти сессий и кэширования Redis (`redis.asyncio`, паттерны Redis Iris), движок локального графа знаний `.context/` (`NetworkX DiGraph` + `Pydantic v2`) и диагностический HTTP-контур.

## Состав модуля
1. **`pyproject.toml`**: декларация пакета по стандарту PEP 621 (`hatchling`, `requires-python = ">=3.12"`). Включает исключительно легковесные сетевые и графовые зависимости (`fastapi`, `uvicorn[standard]`, `mcp`, `asyncpg`, `redis`, `networkx`, `httpx`, `pydantic`, `pydantic-settings`, `pyyaml`) без тяжёлых ML-библиотек (`PyTorch` / `CUDA`).
2. **`src/core/__init__.py`**: корневой пакет ядра с фиксацией семантической версии `__version__ = "0.1.0"`.
3. **`src/core/enums.py`**: единый реестр доменных перечислений на базе `StrEnum` (`AgentType`, `ComponentType`, `ScopeType`, `ComponentStatus`, `HookEventType`, `RedisNamespace`, `RedisKeySegment`, `SessionMetaField`, `RedisFlag`, `ContextEntityClass`, `ContextEntityStatus`, `ContextRelationType`, `ContextEntityFolder`, `GuardrailAction`, `FileWriteStrategy`, `HookDialect`, `McpConfigFormat`, `RulesFormat`, `VisualizerNodeKind`, `VisualizerEdgeKind`, `VisualizerLayoutMode`), обеспечивающий валидацию типов, ключей Redis, сущностей графа `.context/`, целевых форматов компилятора адаптеров и визуализатора топологии на уровне Python/Pydantic по принципу DRY & Zero-Hardcode.
4. **`src/core/config.py`**: класс `CoreSettings` (`BaseSettings` с префиксом `MCP_COOP_`) и константы верхнего уровня (`DEFAULT_REDIS_SESSION_TTL_SECONDS`, `DEFAULT_REDIS_CACHE_TTL_SECONDS`, `MIN_REDIS_TTL_SECONDS`, `DEFAULT_MAX_CONSECUTIVE_CMD_FAILURES`), разделяющий базовую инфраструктуру (`postgres_dsn`, `redis_url`, `redis_session_ttl_seconds`, `redis_cache_ttl_seconds`, `max_consecutive_cmd_failures`, `host`, `port`, `default_agent`) и опциональные ключи тонких внешних HTTP-интеграций (`openai_api_key`, `embedding_api_key`) с вычисляемыми флагами фолбэка.
5. **`src/core/db/__init__.py`, `src/core/db/postgres.py` и `src/core/db/compactions.py`**: нативный асинхронный адаптер `PostgresDatabase` поверх `asyncpg.Pool`, функция нормализации DSN (`normalize_asyncpg_dsn`), идемпотентная DDL-схема `SCHEMA_DDL` (расширения `vector` и `pg_trgm`, 7 таблиц `projects`, `harness_components` с `content_hash`, `memories`, `hook_events`, `decision_providers`, `session_compactions`, `project_context_nodes`), а также репозиторий `CompactionArchiveStore` (`CompactionArchiveRecord`, `archive_compaction`, `search_compactions`) для долгосрочной архивации и 3-way гибридного поиска сводок `<summary>` после `/compact`.
6. **`src/core/db/redis_client.py`**: асинхронный клиент `RedisSessionStore` поверх `redis.asyncio`, модели `SessionLastError` и `MemoryPromotionCandidate`, методы дедупликации чекпоинтов сжатия (`get_last_compaction_checkpoint`, `record_compaction_checkpoint`), а также чистые функции хеширования `compute_command_hash` и `compute_stack_hash`. Реализует три паттерна **Redis Iris**:
   - **Short-Term Working Memory** (`session:{id}:meta`, `last_error`, `cmd_fails`): атомарный трекинг состояния диалога, дедупликация `/compact` (`last_compact_hash`, `last_compact_step`) и детектор зацикливания команд через `pipeline(transaction=True)`.
   - **Short-to-Long Memory Promotion** (`session:{id}:promotion`): формирование карточки `MemoryPromotionCandidate` при успешном устранении ошибки для последующего переноса в долгосрочную таблицу `memories` PostgreSQL.
   - **LangCache Lite** (`cache:decision:*`, `cache:rules:*`): субмиллисекундное кэширование решений хуков `PreToolUse` и бандлов правил по инвариантному SHA-256 хешу стека.
7. **`src/core/graph/__init__.py`, `src/core/graph/context_graph.py`, `src/core/graph/markdown_merge.py` и `src/core/graph/sync_engine.py`**: движок `ContextGraphManager` (и алиас `ContextGraphService`) на базе `NetworkX` (`nx.DiGraph`) и `Pydantic v2`, структурный AST-мержер `merge_context_node_markdown` (объединение YAML-списков `stack`/`files`/`depends_on`/`implements`/`governed_by` и хронологическая дедупликация `AI Traces`) и оркестратор `WorkspaceSyncEngine` (`ingest_components` с `Content-Hash Gate` и `HARNESS_PROBE`, `sync_context_nodes` по алгоритму 3-Way Hash Sync `(local_hash, db_hash, base_hash)` и `upsert_node_write_through` для сквозного зеркалирования узлов в БД).
8. **`src/core/harness/seed.py`**: неизменяемая модель `HarnessComponentSeed`, эталонный реестр стартовых компонентов `DEFAULT_HARNESS_SEEDS` (`core-engineering-mentor` — универсальные правила инженерного мышления, `block-destructive-commands` и `rewrite-npm-to-bun` — защитные барьеры `PreToolUse`, единый бутстрап-скилл `coop-orchestrator` с `execution_mode='always'` и 3 хук-скрипта), функция `compute_component_content_hash`, чистые функции фильтрации `filter_seed_components(stack_tags)` и `resolve_seed_components_with_overrides(stack_tags, enabled_components, disabled_components)` для оффлайн-фолбэка и идемпотентный загрузчик `seed_default_components(db)` (`ON CONFLICT (slug) DO UPDATE` с очисткой устаревших системных слагов `LEGACY_SYSTEM_SLUGS`).
9. **`src/core/harness/profiles.py`**: декларативные целевые профили `AgentTargetProfile` (`PluginManifestSpec`, `RulesTargetSpec`, `SkillsTargetSpec`, `McpTargetSpec`, `HooksTargetSpec`), реестр `AGENT_PROFILES` для всех 5 агентов (`antigravity`, `claude-code`, `cursor`, `codex`, `opencode`) и функции `get_agent_profile(agent_type, overrides)` + `list_agent_profiles()`.
10. **`src/core/harness/compiler.py`, `src/core/harness/dynamic_rag.py`, `src/core/harness/consolidator.py` и `src/core/harness/__init__.py`**: двухфазный компилятор `HarnessCompiler` (материализует в статический бандл только инвариантные компоненты с `execution_mode='always'`, сохраняя все `guardrails` в `bundle.guardrails`), движок двухступенчатого Progressive Disclosure `select_dynamic_components_for_prompt` (`DynamicPromptRAGResult` в `dynamic_rag.py`: `relevance >= 0.45` — полный blueprint скилла, `0.22 <= relevance < 0.45` — однострочный индекс-намёк, `relevance >= 0.25` — Top-3 доменных правил) и сервис серверной консолидации и Lineage-дедупликации `HarnessConsolidator` (`consolidator.py`, кластеризация на `nx.Graph` + `synthesize_cluster_markdown`).
11. **`src/core/main.py` и модульный пакет `src/core/api/` (`models.py`, `deps.py`, `routes_management.py`, `routes_hooks.py`, `routes_sync.py`, `routes_visualizer.py`, `__init__.py`)**: тонкая точка входа `create_app()` и `lifespan(app: FastAPI)` с маршрутизаторами `management_router`, `hooks_router` (включая Progressive Disclosure RAG в `PreInvocation`), `sync_router` (`POST /api/sync/ingest`, `POST /api/sync/context`, `POST /api/harness/consolidate`) и `visualizer_router` (`GET /ui`, `GET /api/visualizer/graph`, `GET /api/visualizer/search`).
12. **`src/core/decision/__init__.py`, `src/core/decision/models.py`, `src/core/decision/providers.py`, `src/core/decision/tiers.py`, `src/core/decision/engine.py`, `src/core/decision/transcript.py` и `src/core/decision/hook_dialects.py`**: полный пакет двухкоординатного `DecisionEngine` (тиры `LocalDeterministicTier`, `SystemOneDecisionTier`, `AntigravityProbeDecisionTier` и `DomainEvaluationSpec` в `tiers.py`, оркестратор и гибридная память в `engine.py`), парсер хвоста JSONL-транскриптов и `<summary>` чекпоинтов в `transcript.py` (`CompactionCheckpoint`, `TranscriptTailInspection`, `inspect_transcript_tail_detailed`, `extract_compaction_summary`, `compute_checkpoint_hash`) и адаптеры диалектов хуков в `hook_dialects.py`.
13. **`src/core/services/__init__.py`, `src/core/services/embeddings.py` и пакет `src/core/services/antigravity/` (`models.py`, `sandbox.py`, `runtime.py`, `service.py`, `__init__.py`)**: тонкий мульти-диалектный HTTP-клиент векторных эмбеддингов `EmbeddingClient` (`GEMINI_NATIVE` `:embedContent` и `OPENAI_COMPATIBLE` `/embeddings` для `EmbeddingGemma 2`), функции `normalize_l2`, `format_pgvector`, `format_embeddinggemma_prompt`, а также декомпозированный адаптер микро-агентов второго уровня `AntigravitySDKService` (`Read-Only Probe Auditor`, `Background Context Healer`, `E2E Agent Probe` со строгой проверкой `is_enabled_for_agent` для `AgentType.ANTIGRAVITY`, защитой песочницы `/tmp` от симлинков, XML-конвертом от Prompt Injection и Circuit Breaker).
14. **`src/core/mcp/__init__.py`, `src/core/mcp/tools.py` и `src/core/mcp/server.py`**: локальный MCP-шлюз `CoopMCPGateway` (`FastMCP Streamable HTTP /mcp` + JSON-RPC 2.0) и сервис `CoopMCPToolService` для 8 аннотированных инструментов (`coop_search_memory` с поддержкой `include_compactions=True`, `coop_learn_memory`, `coop_propose_harness_edit`, `coop_publish_skill`, `coop_query_context_graph`, `coop_upsert_context_node` с Write-Through в `project_context_nodes`, `coop_get_factory_status` со счётчиками `session_compactions` и `project_context_nodes`, `coop_manage_decision_providers`).
15. **Пакет визуализатора топологии и знаний `src/core/visualizer/` (`models.py`, `queries.py`, `topology.py`, `search.py`, `service.py`, `__init__.py`) и веб-оболочка `src/core/ui/` (`styles.css`, `template.html`, `graph_canvas.js`, `app.js`, `__init__.py` -> `render_visualizer_html`)**: полнофункциональный интерактивный визуализатор графа RAG и топологии `Harness²`. Включает Pydantic-модели снимков Cytoscape.js (`VisualizerGraphSnapshot`, `CytoscapeElement`, `VisualizerSearchHit`), оптимизированные SQL-запросы (`queries.py`), асинхронный сборщик топологии `VisualizerTopologyBuilder` с поддержкой автономного оффлайн-режима (`db=None`) и генерацией семантических рёбер по косинусной близости, гибридный векторно-полнотекстовый поиск `VisualizerSearchService` с автоматическим лексическим фолбэком, фасад `VisualizerGraphService` и легковесный SPA-интерфейс в тёмной теме без внешних CDN/NPM зависимостей.
16. **`tests/unit/test_spec002_dynamic_harness.py` и `tests/unit/test_spec003_compactions.py`**: комплексные наборы модульных и интеграционных тестов динамического харнеса (Progressive Disclosure, двусторонний инжест, 3-Way Hash Sync, консолидация) и архивации сводок сжатия контекста `/compact`.
17. **`tests/unit/test_visualizer.py`**: исчерпывающий набор тестов визуализатора графа RAG и топологии `Harness²`, покрывающий автономный режим `VisualizerTopologyBuilder(db=None)`, сборку снимка при активной БД с дельтой изменений и семантическими рёбрами, гибридный векторно-полнотекстовый поиск `VisualizerSearchService` с лексическим фолбэком и HTTP-эндпоинты FastAPI (`/ui`, `/api/visualizer/graph`, `/api/visualizer/search`).

## Логи контекста (AI Traces)
- 2026-10-24: Создан базовый скелет Python 3.12 Core (`pyproject.toml`, `src/core/__init__.py`, `src/core/config.py`, `src/core/main.py`) в строгом соответствии с PEP 621, паттерном FastAPI `lifespan` и языковым стандартом `AGENTS.md` (код на английском, архитектурные комментарии и докстринги на русском).
- 2026-10-24: Реализован слой персистентного хранения PostgreSQL (`src/core/db/__init__.py`, `src/core/db/postgres.py`) с идемпотентной накаткой схемы `SCHEMA_DDL` (`vector`, `pg_trgm`, 4 таблицы и GIN-индексы) и интеграцией `PostgresDatabase` в `lifespan` и `/health` (`src/core/main.py`).
- 2026-10-24: Выделен единый модуль перечислений `src/core/enums.py` (`StrEnum`), добавлено поле `default_agent` в `CoreSettings` и убран вендор-хардкод `DEFAULT 'antigravity'` из SQL-схеемы `projects` в пользу валидации на уровне приложения (DRY & KISS).
- 2026-10-24: Реализована Итерация 2.1 (Оперативная память сессий Redis + паттерны Redis Iris): расширены `src/core/enums.py` и `src/core/config.py`, создан `src/core/db/redis_client.py` (`RedisSessionStore`, `SessionLastError`, `MemoryPromotionCandidate`, `compute_command_hash`, `compute_stack_hash`).
- 2026-10-24: Завершена Итерация 2.2: `RedisSessionStore` и связанные модели экспортированы в `src/core/db/__init__.py` и интегрированы в `lifespan` (`app.state.redis`) и диагностический эндпоинт `GET /health` (`redis_connected`) в `src/core/main.py`, синхронизирован Раздел 4.2 `SPEC.md`.
- 2026-10-24: Реализованы Итерации 4.1 и 4.2 (Онтология `.context/schema.md` и Движок Контекстного Графа `src/core/graph/context_graph.py` на базе `NetworkX` + `Pydantic v2`): добавлены перечисления графа в `src/core/enums.py` и реализован `ContextGraphManager` (`build_and_validate`, `query_subgraph`, `upsert_node`), пройдены проверки `pyright` и `ruff`.
- 2026-10-24: Реализована Итерация 5.1 (Доменные перечисления адаптеров в `src/core/enums.py` и идемпотентный Seed базовых компонентов `Harness²` в `src/core/harness/seed.py`), пройдены проверки `pyright` и `ruff`.
- 2026-10-24: Реализована Итерация 5.2 (Декларативные целевые профили 5 агентов в `src/core/harness/profiles.py`: `AgentTargetProfile`, `AGENT_PROFILES`, `get_agent_profile` с глубоким слиянием `overrides`), пройдены проверки `pyright` и `ruff`.
- 2026-10-24: Завершена Итерация 5.3 (Компилятор `HarnessCompiler` в `src/core/harness/compiler.py`, пакетный экспорт `src/core/harness/__init__.py`, авто-сидирование в `lifespan` и эндпоинт `POST /api/projects/register` в `src/core/main.py`), пройдены проверки `pyright`, `ruff`, валидация графа `.context/` и интеграционный тест компиляции по всем 5 агентам.
- 2026-10-24: Реализована Итерация 6.1 (Доменные перечисления `DecisionEngine`, таблица `decision_providers` в PostgreSQL, Circuit Breaker `ProviderCircuitState` в Redis, модели `src/core/decision/models.py` и рантайм-пул провайдеров `RuntimeProviderPool` в `src/core/decision/providers.py`), пройдены проверки `pyright` и `ruff`.
- 2026-10-24: Реализована Итерация 6.2 (Мульти-тировый `DecisionEngine` в `src/core/decision/engine.py`: протокол `DecisionTier`, `DomainEvaluationSpec` для 5 доменов, `LocalDeterministicTier`, `SystemOneDecisionTier` и гибридный поиск опыта `search_memories`), пройдены проверки `pyright` и `ruff`.
- 2026-10-24: Завершена Итерация 6.3 (`src/core/decision/hook_dialects.py`, `src/core/decision/__init__.py`, эндпоинты `/api/providers`, `/api/decisions/evaluate` и `/api/hooks/*` в `src/core/main.py` с поддержкой `injectSteps`, `overwrite`, `_syncFiles` и ре-гидратации `.context/index.md` после `/compact`), пройдены проверки `pyright`, `ruff`, валидация графа `.context/` и сквозной интеграционный тест каскада провайдеров и хуков (`ALL_ITERATION_6_TESTS_PASSED`).
- 2026-10-24: Реализована Итерация 7.1 (Тонкий мульти-диалектный клиент `EmbeddingClient` в `src/core/services/embeddings.py`, HNSW-индекс `idx_memories_embedding_hnsw` в `src/core/db/postgres.py`, 3-way гибридный поиск `search_memories` и индексация `store_memory` в `src/core/decision/engine.py`), пройдены проверки `pyright` и `ruff`.
- 2026-10-24: Завершена Итерация 7.2 (Локальный MCP-шлюз `CoopMCPGateway` и сервис 8 инструментов `CoopMCPToolService` в `src/core/mcp/`, справедливое ранжирование оффлайн-записей в `search_memories`, интеграция `EmbeddingClient` и `/mcp` в `src/core/main.py`), пройдены проверки `pyright`, `ruff`, валидация графа `.context/` и сквозной тест всех 8 инструментов (`ALL_8_MCP_TOOLS_AND_HTTP_GATEWAY_PASSED`).
- 2026-10-24: Реализована Итерация 8.1 (Пер-проектные оверрайды `ProjectHarnessOverrides` в `src/core/harness/compiler.py`, колонка `profile_overrides` в `projects`, учёт отключённых барьеров в `LocalDeterministicTier._evaluate_tool_safety` и REST API каталога компонентов `GET/PATCH /api/harness/components` в `src/core/main.py`), пройдены проверки `pyright` и `ruff`.
- 2026-10-24: Обновлён шаблон вызова хуков `DEFAULT_HOOK_CLI_COMMAND_TEMPLATE` в `src/core/harness/compiler.py` на универсальный рантайм-лончер `~/.mcp-coop-agentic-factory/bin/run-hook.sh` и синхронизирована гидратация конфигурации проекта из таблицы `projects` PostgreSQL.
- 2026-10-24: Завершена реализация декаплированной шины жизненных хуков и динамического диспетчера скриптов `HookScriptDispatcher` (`src/core/harness/script_runner.py`): в таблицу `harness_components` добавлены `embedding vector(768)` и `search_tsv`, 3 базовых скрипта (`inject-context-map`, `track-file-changes`, `enforce-context-sync`) зарегистрированы как компоненты `ComponentType.SCRIPT` в PostgreSQL, состояние сессии перенесено в Redis (`update_session_meta`), а отбор скриптов на хуках выполняется через двухступенчатую воронку (`always` + семантический Top-K `pgvector` / `tsvector`).
- 2026-10-25: Выполнена миграция локального `.venv` на `Python 3.12.15`, адаптирована типизация `pipe.hset(..., mapping=...)` в `src/core/db/redis_client.py` через строгие типы `dict[FieldT, EncodableT]` из `redis.typing` (без `Any`) под `redis 8.1.0` и удалён устаревший путь `.agents/hooks` из `pyrightconfig.json` (`0 errors` в `pyright` и `ruff`).
- 2026-10-25: Выполнен Шаг 1.1 интеграции `google-antigravity` SDK — добавлены перечисления `AntigravityAuthMode` и `AntigravityAgentRole` в `src/core/enums.py`, настройки `antigravity_*` и свойства `antigravity_auth_mode` / `antigravity_sdk_configured` в `src/core/config.py`, а также опциональная группа зависимостей `antigravity` в `pyproject.toml`.
- 2026-10-25: Завершена Итерация 1 интеграции `google-antigravity` SDK — вместо монолитного файла создан модульный пакет `src/core/services/antigravity/` (`models.py`, `sandbox.py`, `runtime.py`, `service.py`, `__init__.py`) и ре-экспортирован в `src/core/services/__init__.py` (`0 errors` в `ruff` и `pyright`).
- 2026-10-25: Завершена сквозная интеграция `AntigravitySDKService` в ядро (`SPEC.md` §6.3) — добавлен тир `AntigravityProbeDecisionTier` в `src/core/decision/engine.py`, эндпоинт `POST /api/context/heal` и поля диагностики SDK в `GET /health` (`src/core/main.py`), а также диагностика `antigravity_sdk` в `coop_get_factory_status` (`src/core/mcp/tools.py`); пройден сквозной E2E-тест `FULL_E2E_SDK_INTEGRATION_PASSED`.
- 2026-10-25: В `hook_pre_invocation` (`src/core/main.py`) добавлена динамическая выборка архитектурных паттернов из `harness_components` и `memories` при запросах `/plan` (`_build_plan_best_practices_hint`), а в `src/core/decision/hook_dialects.py` исключено поле `ephemeralMessage` из ответа `PreToolUse` для `ANTIGRAVITY_PROTOJSON`.
- 2026-10-25: Связаны направленные рёбра `implements` (`contract-storage-schema`, `contract-lifecycle-hooks`, `contract-mcp-and-http-api`) и `governed_by` (`adr-001-clean-host-global-docker`, `adr-002-harness2-evolution-loop`, `adr-003-isolated-antigravity-sdk-tier`) после полной декомпозиции `SPEC.md` v1.0 в граф `.context/`.
- 2026-10-25: Выполнена реализация долгосрочной архивации `<summary>` после `/compact` (`src/core/db/compactions.py` и `src/core/decision/transcript.py`), модульная декомпозиция `src/core/decision/engine.py` (`tiers.py`) и `src/core/main.py` в пакет `src/core/api/` (`models.py`, `deps.py`, `routes_management.py`, `routes_hooks.py`, `__init__.py`), расширены `coop_search_memory` (`include_compactions`) и `coop_get_factory_status` (`session_compactions`), добавлен набор автотестов `tests/unit/test_spec003_compactions.py`.
- 2026-10-25: Реализованы все 4 контура `Dynamic Harness²`: минимальный статический Bootstrap (`harness-orchestrator` с `execution_mode='always'`) и двухступенчатый Progressive Disclosure в `src/core/harness/dynamic_rag.py`, двусторонний инжест и 3-Way Hash Sync узлов `.context/` с AST-мержером в `src/core/graph/markdown_merge.py` и `src/core/graph/sync_engine.py`, кластеризация и Lineage-дедупликация `HarnessConsolidator` в `src/core/harness/consolidator.py`, маршрутизатор `src/core/api/routes_sync.py` и модульные тесты `tests/unit/test_spec002_dynamic_harness.py`.
- 2026-10-25: Реализована Фаза 7 визуализатора знаний и топологии Harness²: создан маршрутизатор `src/core/api/routes_visualizer.py` (`GET /ui`, `GET /api/visualizer/graph`, `GET /api/visualizer/search`), добавлен провайдер `_get_visualizer_service` в `src/core/api/deps.py`, выполнена регистрация `visualizer_router` и инициализация `VisualizerGraphService` в `lifespan` / `create_app()` (`src/core/main.py`), 0 ошибок в `ruff` и `pyright`, пройден `smoke test`.
- 2026-10-25: Реализована Фаза 9 (Комплексные юнит- и интеграционные тесты `tests/unit/test_visualizer.py`): верифицированы 4 ключевых контура визуализатора (автономный режим сборщика топологии, снимок с дельтой изменений и семантическими рёбрами, гибридный и лексический поиск, эндпоинты FastAPI /ui, /api/visualizer/graph, /api/visualizer/search), 19/19 тестов успешно пройдены, 0 ошибок в `ruff` и `pyright`.
- 2026-10-25: Завершена Фаза 10 (Синхронизация Graph-as-Code и валидация целостности графа `.context/`): зарегистрированы все файлы визуализатора и веб-UI (`src/core/visualizer/*`, `src/core/ui/*`, `src/core/api/routes_visualizer.py`, `tests/unit/test_visualizer.py`), обновлены `mod-python-core.md`, `contract-mcp-and-http-api.md` и `index.md`, верифицирована валидность контекстного графа (10 узлов, 35 рёбер) и пройден полный тестовый сьют (19/19 тестов).
- 2026-10-25: Исправлена фильтрация видимости составных групп (`group_container`) в `src/core/ui/graph_canvas.js` (`style("display") !== "none"` вместо `:visible` внутри `cy.batch`), улучшена визуальная дифференциация узлов `.context/` (`GraphIndex`, `Module`, `Contract`, `Decision`) и компонентов `Harness²`, добавлены полные сводки для `project` и `stack_tag` и очищена иерархия связей `context:index` в `src/core/visualizer/topology.py`.
- 2026-10-26: В `src/core/graph/sync_engine.py` добавлены `extract_tags_from_slug` и `resolve_component_tags_and_scope` для сохранения чистых семантических тегов из `slug` без принудительного подмешивания `"global"` и с установкой `scope = 'global'` для всех синхронизируемых компонентов; расширены тесты в `tests/unit/test_spec002_dynamic_harness.py`.
- 2026-10-26: В `ContextGraphManager.upsert_node` (`src/core/graph/context_graph.py`) добавлен рекурсивный поиск существующего файла узла через `target_dir.rglob(f"{clean_node_id}.md")` для in-place обновления узлов, сгруппированных по подпапкам подсистем (например, `.context/entities/modules/cli/mod-cli-*.md`).
- 2026-10-26: В `src/core/harness/profiles.py` и `src/core/harness/compiler.py` унифицировано описание манифеста плагина Antigravity (`DEFAULT_PLUGIN_DESCRIPTION` без упоминания внутренних/внешних брендов) и исключена безусловная генерация пустого поля `suggestedPrompts` в `plugin.json`.
- 2026-10-26: В `src/core/harness/seed.py` и `src/core/harness/compiler.py` правила плагина (`rules/AGENTS.md`) сведены к единому универсальному проектно-агностичному кодексу `core-engineering-mentor` без техно-специфичных заголовков, а скиллы объединены в один бутстрап-скилл `coop-orchestrator` (`execution_mode='always'`, универсальная оркестрация задач, гигиена контекста, делегирование субагентам и верификация); в `seed_default_components` включён `ON CONFLICT (slug) DO UPDATE` и очистка устаревших системных слагов.
- 2026-10-26: В `lifespan` (`src/core/main.py`) добавлен автоматический сброс кэша бандлов правил в Redis (`await redis_store.invalidate_rules_cache()`) при старте ядра после `seed_default_components`, чтобы перезапуск контейнера с обновлённым `seed.py` не возвращал устаревший кэш из AOF-тома Redis.
- 2026-10-26: Реализован единый источник знаний в `seeds/harness-seed.json` и `seeds/prompts-config.json`: все 40 компонентов `Harness²` (универсальный статический Bootstrap, динамические семантические скиллы/правила и 27 пустых технологических стабов с `detectors`), профили 5 агентов и системные промпты загружаются через `load_harness_seed_payload()` и `load_prompts_config()` в `src/core/harness/seed.py`, `src/core/harness/profiles.py`, `src/core/harness/compiler.py`, `src/core/harness/dynamic_rag.py`, `src/core/api/routes_hooks.py`, `src/core/api/routes_management.py`, `src/core/decision/tiers.py` и `src/core/services/antigravity/service.py`.





