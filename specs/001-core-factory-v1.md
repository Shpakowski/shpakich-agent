# SPEC-001: Базовая архитектура `mcp-coop-agentic-factory` (v1.0)

> **Статус:** Implemented (v1.0 — Полностью реализовано и разложено в граф .context/)  
> **Проект:** `mcp-coop-agentic-factory` (`shpakich-agent`)  
> **Назначение:** Executive Summary выполненной базовой спецификации v1.0 и навигационный указатель по живым узлам инженерного графа `.context/`.

---

## 1. Суть вехи (Executive Summary)

В рамках вехи **v1.0** спроектирован и полностью реализован четырёхслойный автономный инфраструктурный слой («Фабрика агентов») для ИИ-ассистированной разработки. На хост-машине работает легковесный кросс-рантаймовый CLI-инжектор на **Bun / Node.js** (`bunx` / `npx`), который автоматически детектирует стек и целевого ИИ-агента (**Antigravity**, **Claude Code**, **Cursor**, **Windsurf**, **Codex**), материализует нативные адаптеры и оставляет хост абсолютно чистым от тяжёлых зависимостей и локальных ML-библиотек. Вся аналитика, долговременная память и координация изолированы внутри глобального Docker-стека (`~/.mcp-coop-agentic-factory/`), включающего асинхронное ядро **Python 3.12 (`FastAPI` + `FastMCP`)**, **PostgreSQL 18 (`pgvector` + `tsvector` + `pg_trgm`)** и **Redis 7 Alpine**.

Поверх этого фундамента развёрнут контур перехвата 5 фаз жизненного цикла агента (`SessionStart`, `PreInvocation`, `PreToolUse`, `PostToolUse`, `Stop`) под управлением многоуровневого `DecisionEngine` и векторно-лексического диспетчера `HookScriptDispatcher`. Система реализует замкнутый цикл самообучения **`Harness²` (`Propose → Probe → Compose`)** с разделением знаний на инвариантные (`global`) и доменно-специфичные (`domain` по тегам стека), поддерживает пер-проектные оверрайды компонентов (`ProjectHarnessOverrides`), предоставляет 8 инструментов через локальный MCP-шлюз (`/mcp`) и включает опциональный изолированный контур микро-агентов на базе `google-antigravity` SDK (`Context Healer`, `Probe Auditor`, `E2E Agent Probe`).

---

## 2. Чеклист реализованных возможностей v1.0

| Слой / Контур | Реализованные компоненты и возможности | Статус |
| :--- | :--- | :--- |
| **Слой 1: Dual-Runtime CLI (`src/cli/`)** | Команды `init`, `up`, `down`, `sync`, `status`, `harness`, `providers`, `hook`; авто-детектор стека и 5 ИИ-агентов; генератор плагинов; TUI-визард на `@clack/prompts` + `styleText`; fail-open мост хуков (`stdin/stdout`). | ✅ Выполнено |
| **Слой 2: Глобальный Docker-стек (`docker/`)** | Оркестрация 3 контейнеров (`mcp-coop-agentic-factory-core` на Python 3.12 Slim, `pgvector/pgvector:pg18`, `redis:7-alpine`); потребление RAM ядра ~50–70 МБ без PyTorch/CUDA. | ✅ Выполнено |
| **Хранилище: PostgreSQL 18 + Redis 7 (`src/core/db/`)** | Таблицы `projects`, `harness_components`, `project_harness_overrides`, `memories` (`vector(768)` + `tsvector` + `pg_trgm`), `hook_events`, `runtime_providers`; Redis-ключи сессий, анти-зацикливания (`cmd_fails`), Iris Short-to-Long Promotion и LangCache Lite. | ✅ Выполнено |
| **Слой 3: Перехват 5 жизненных хуков (`FastAPI` + `DecisionEngine`)** | Эндпоинты `/api/hooks/{session-start,pre-invocation,pre-tool-use,post-tool-use,stop}`; детерминированные Guardrails (`block_command`, `rewrite_command`, контроль `implementation_plan.md`), анти-зацикливание, перехват `/compact` (Re-Hydration) и `/plan`, Error Recovery RAG. | ✅ Выполнено |
| **Слой 4: Контур `Harness²` и Контекстный Граф (`src/core/harness/`, `src/core/graph/`)** | Стейт-машина `Propose → Probe → Compose` для компонентов `system_prompt`, `guardrail`, `skill`, `tool_guidance`, `script`, `subagent`; компилятор `HarnessCompiler`; диспетчер `HookScriptDispatcher`; парсер и валидатор `.context/` на `NetworkX` + `Pydantic`. | ✅ Выполнено |
| **Локальный шлюз `FastMCP` (`src/core/mcp/`)** | Streamable HTTP сервер (`/mcp`) с 8 инструментами: `coop_search_memory`, `coop_learn_memory`, `coop_propose_harness_edit`, `coop_publish_skill`, `coop_query_context_graph`, `coop_upsert_context_node`, `coop_get_factory_status`, `coop_manage_decision_providers`. | ✅ Выполнено |
| **Тонкие клиенты и изолированный SDK (`src/core/services/`, `src/core/decision/`)** | Пул провайдеров `RuntimeProviderPool` + OpenAI `Decisions API` (`Predicate`, `Choice`, `Score`); тонкий HTTP-клиент `EmbeddingClient` (`EmbeddingGemma 2` / Gemini 768-dim с FTS-фолбэком); сервис микро-агентов `AntigravitySDKService` в sandbox `/tmp/mcp_coop_agy_*`. | ✅ Выполнено |

---

## 3. Карта декомпозиции в `.context/` (Куда переехали детали)

Все детальные схемы таблиц, контракты JSON-пейлоадов хуков, сигнатуры MCP/HTTP эндпоинтов и архитектурные инварианты из исходной спецификации v1.0 декомпозированы в живые узлы графа `.context/`:

### Модули кода (`Module`)
* [`.context/entities/modules/mod-python-core.md`](../.context/entities/modules/mod-python-core.md) — Python 3.12 ядро (`FastAPI`, `FastMCP`, `DecisionEngine`, `HarnessCompiler`, `HookScriptDispatcher`, `ContextGraphService`, `EmbeddingClient`, `AntigravitySDKService`).
* [`.context/entities/modules/mod-bun-cli.md`](../.context/entities/modules/mod-bun-cli.md) — Кросс-рантаймовый CLI-инжектор на TypeScript (`detector`, `wizard`, `docker`, `materializer`, `hook_runner`).
* [`.context/entities/modules/mod-docker-stack.md`](../.context/entities/modules/mod-docker-stack.md) — Инфраструктурный Docker Compose стек (`Dockerfile`, `docker-compose.yml`, правила изоляции).

### Контракты и схемы (`Contract`)
* [`.context/entities/contracts/contract-storage-schema.md`](../.context/entities/contracts/contract-storage-schema.md) — DDL-схема таблиц PostgreSQL 18 (`pgvector`, `tsvector`, `pg_trgm`) и структура ключей Redis 7.
* [`.context/entities/contracts/contract-lifecycle-hooks.md`](../.context/entities/contracts/contract-lifecycle-hooks.md) — Контракты 5 фаз жизненных хуков, мульти-агентные диалекты (`HookDialect`) и тройной контур принуждения `.context/`.
* [`.context/entities/contracts/contract-mcp-and-http-api.md`](../.context/entities/contracts/contract-mcp-and-http-api.md) — Спецификация 8 инструментов `FastMCP` (`/mcp`) и REST-эндпоинтов `FastAPI` (`/api/*`).

### Архитектурные решения (`Decision` / ADR)
* [`.context/entities/decisions/adr-001-clean-host-global-docker.md`](../.context/entities/decisions/adr-001-clean-host-global-docker.md) — Чистота хост-машины, легковесный CLI и вынос аналитики/БД в глобальный Docker-стек без локального PyTorch/CUDA.
* [`.context/entities/decisions/adr-002-harness2-evolution-loop.md`](../.context/entities/decisions/adr-002-harness2-evolution-loop.md) — Эволюция окружения агента по циклу `Propose → Probe → Compose` (`Harness²`) с разделением на `global` и `domain` знания.
* [`.context/entities/decisions/adr-003-isolated-antigravity-sdk-tier.md`](../.context/entities/decisions/adr-003-isolated-antigravity-sdk-tier.md) — Опциональный изолированный контур фоновых микро-агентов `google-antigravity` SDK во временных песочницах.

---

## 4. Связанные будущие спецификации (`Draft`)

Задачи следующих итераций развития платформы вынесены в отдельные номерные спецификации в директории `specs/`:

* [`./002-dynamic-harness-v0.2.md`](./002-dynamic-harness-v0.2.md) — **SPEC-002:** Минимальный статический Bootstrap на диске, 100% динамическая RAG-инъекция скиллов/рулов в `PreInvocation`, двусторонний `sync` (`Workspace <-> PostgreSQL`) и серверная консолидация знаний по векторам `pgvector`.
* [`./003-compact-archive.md`](./003-compact-archive.md) — **SPEC-003:** Долгосрочная архивация `<summary>` чекпоинтов после выполнения `/compact` в базу знаний PostgreSQL.
* [`./004-e2e-test-suite.md`](./004-e2e-test-suite.md) — **SPEC-004:** Автоматизированный тестовый контур (`pytest` Unit / Integration + E2E Verification Suite через `google-antigravity` SDK).
