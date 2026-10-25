---
type: Entity
class: Contract
id: contract-mcp-and-http-api
name: Контракт локального шлюза FastMCP (/mcp) и управляющего REST API ядра
status: Active
stack: [fastapi, fastmcp, python, typescript]
files:
  - src/core/mcp/tools.py
  - src/core/mcp/server.py
  - src/core/main.py
  - src/cli/docker.ts
depends_on:
  - "[[contract-storage-schema]]"
implements: []
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
  - "[[adr-003-isolated-antigravity-sdk-tier]]"
---

# Контракт локального шлюза FastMCP (`/mcp`) и управляющего REST API ядра (`contract-mcp-and-http-api`)

Формальный интерфейс взаимодействия между нативными ИИ-агентами, хостовым CLI-инжектором [[mod-bun-cli]] (`src/cli/docker.ts`) и глобальным контейнером ядра [[mod-python-core]] (`src/core/mcp/server.py`, `src/core/mcp/tools.py`, `src/core/main.py`). Объединяет 8 инструментов протокола Model Context Protocol поверх транспорта Streamable HTTP (`http://127.0.0.1:8787/mcp`, JSON-RPC 2.0) и управляющие REST API эндпоинты `FastAPI` на порту `8787`.

---

## 1. Контракт шлюза Model Context Protocol (`FastMCP` — `http://127.0.0.1:8787/mcp`)

Класс `CoopMCPGateway` (`src/core/mcp/server.py`) монтирует ASGI-приложение `FastMCP` (`streamable_http_path="/mcp"`, `stateless_http=True`, `json_response=True`) в основной сервер `FastAPI` и делегирует вызовы фасаду `CoopMCPToolService` (`src/core/mcp/tools.py`). Нативному агенту предоставляются 8 строго типизированных инструментов:

| № | Имя MCP-инструмента | Входные аргументы (сигнатура) | Назначение и бизнес-логика | Выходной JSON-контракт |
| :- | :--- | :--- | :--- | :--- |
| 1 | **`coop_search_memory`** | `query: str`, `stack_tags: list[str] \| None = None`, `limit: int = 5` (диапазон `1..20`) | Выполняет 3-way гибридный поиск по таблице `memories` ([[contract-storage-schema]]): векторная близость `pgvector` (`EmbeddingGemma 2`) + полнотекстовый `ts_rank_cd` + триграммы `pg_trgm`. | `{"query": str, "count": int, "results": [{"id", "title", "error_symptom", "solution", "scope", "stack_tags", "similarity", ...}]}` |
| 2 | **`coop_learn_memory`** | `title: str`, `error_symptom: str`, `solution: str`, `stack_tags: list[str] \| None = None`, `scope: str = "global"`, `source_project_path: str \| None = None` | Сохраняет новое решение ошибки или архитектурный паттерн в таблицу `memories` с автоматической генерацией 768-мерного вектора через `EmbeddingClient`. | `{"id": str \| None, "title": str, "scope": str, "stack_tags": list[str], "embedding_indexed": bool, "persisted": bool}` |
| 3 | **`coop_propose_harness_edit`** | `component_type: str`, `slug: str`, `title: str`, `description: str`, `content: str`, `scope: str = "global"`, `stack_tags: list[str] \| None = None`, `metadata: dict[str, Any] \| None = None`, `source_project_path: str \| None = None` | Реализует цикл эволюции [[adr-002-harness2-evolution-loop]] (`Propose -> Probe -> Compose`): оценивает кандидата через `DecisionEngine.evaluate(domain=HARNESS_PROBE)` (Tier 1 + Tier 2 + опциональный `AntigravityProbeDecisionTier` из [[adr-003-isolated-antigravity-sdk-tier]]), при вердикте `allow` выполняет `UPSERT` в `harness_components` со статусом `composed` и сбрасывает кэш `cache:rules:*` в Redis. | `{"id": str \| None, "slug": str, "component_type": str, "scope": str, "stack_tags": list[str], "status": "composed" \| "rejected", "score": float, "action": str, "decided_by_tier": str, "reason": str \| None, "version": int, "persisted": bool}` |
| 4 | **`coop_publish_skill`** | `slug: str`, `description: str`, `instructions_md: str`, `stack_tags: list[str] \| None = None`, `helper_scripts: dict[str, str] \| None = None`, `source_project_path: str \| None = None` | Специализированная обёртка над `coop_propose_harness_edit` для публикации переиспользуемого навыка (`ComponentType.SKILL`, `SKILL.md` + `metadata.helper_scripts`) с авто-выводом `scope` (`domain` при наличии стековых тегов, иначе `global`). | Возвращает полный контракт ответа `coop_propose_harness_edit` для созданного/обновлённого скила. |
| 5 | **`coop_query_context_graph`** | `workspace_path: str`, `node_id: str = ""`, `depth: int = 2` (диапазон `0..5`) | Собирает ориентированный граф `nx.DiGraph` из `.context/entities/**/*.md`, проверяет инварианты целостности и возвращает двунаправленную окрестность узла `node_id` радиуса `depth` (`SubgraphQueryResult`). | `{"root_node_id": str \| None, "depth": int, "nodes": list[ContextNode], "edges": list[GraphEdge], "validation": GraphValidationReport}` |
| 6 | **`coop_upsert_context_node`** | `workspace_path: str`, `entity_class: str` (`Module` \| `Contract` \| `Decision`), `node_id: str`, `name: str`, `files: list[str]`, `body_md: str`, `trace_note: str`, `stack: list[str] \| None = None`, `depends_on: list[str] \| None = None`, `implements: list[str] \| None = None`, `governed_by: list[str] \| None = None`, `status: str = "Active"`, `conversation_id: str = ""` | Атомарно создаёт или обновляет `.md`-узел в `.context/entities/`, дописывает датированную строку в `## Логи контекста (AI Traces)`, освежает `updated_at` в `.context/index.md` и выставляет флаг `context_updated = "1"` в Redis для сессии `conversation_id`. | `{"node": ContextNode, "validation": {"is_valid": bool, "node_count": int, "edge_count": int, "missing_files": dict, "broken_links": dict, "parse_errors": dict}}` |
| 7 | **`coop_get_factory_status`** | `workspace_path: str = ""` | Возвращает комплексную диагностику фабрики: подключение к PostgreSQL и Redis, количественные метрики таблиц, статус `EmbeddingClient`, `AntigravitySDKService`, пула провайдеров Tier 2 и отчёт валидации `.context/`. | `{"service": str, "version": str, "postgres_connected": bool, "redis_connected": bool, "embedding_client": dict, "antigravity_sdk": dict, "decision_providers": list[dict], "database_counts": dict[str, int], "context_graph": dict \| None}` |
| 8 | **`coop_manage_decision_providers`** | `action: str = "list"` (`"list"` \| `"update"`), `name: str = ""`, `priority: int \| None = None`, `enabled: bool \| None = None`, `reset_circuit: bool = False` | Позволяет агенту просматривать состояние провайдеров второго контура (`list`) или менять их приоритет, активность и сбрасывать Circuit Breaker (`update`) без экспонирования секретных API-ключей в чат. | При `list`: `{"action": "list", "count": int, "providers": list[ProviderStatusView]}`; при `update`: `{"action": "update", "updated": bool, "provider": ProviderStatusView}`. |

---

## 2. Контракт управляющего REST API (`FastAPI` — `http://127.0.0.1:8787`)

Хостовый CLI-инжектор [[mod-bun-cli]] (`init`, `up`, `sync`, `status`, `harness`, `providers`) взаимодействует с контейнером ядра [[mod-docker-stack]] через следующие HTTP-эндпоинты (`src/core/main.py`):

### 2.1. Диагностика и регистрация проектов
* **`GET /health`**
  * **Назначение:** Проверка готовности ядра при старте Docker Compose и выполнении команд CLI (`checkCoreHealth` в `src/cli/docker.ts`).
  * **Ответ (`200 OK`):** `{"status": "ok", "service": str, "version": str, "postgres_connected": bool, "redis_connected": bool, "decisions_api_configured": bool, "embedding_api_configured": bool, "antigravity_sdk_configured": bool, "embedding_client": dict, "antigravity_sdk": dict, "mcp_endpoint": "/mcp"}`.
* **`POST /api/projects/register`**
  * **Тело запроса (`ProjectRegistrationRequest`):** `{"workspace_path": str, "project_name": str, "agent_type": AgentType, "stack_tags": list[str], "profile_overrides": dict[str, Any] | null}`.
  * **Действие:** Сохраняет проект в таблицу `projects` (`UPSERT_PROJECT_SQL`) и компилирует целевой артефакт адаптера через `HarnessCompiler.compile_for_project()`.
  * **Ответ (`200 OK`):** `{"status": "ok", "project_id": str | null, "persisted_in_db": bool, "artifact": RenderedHarnessArtifact}` (содержит массив `files: [{path, content, strategy, executable}]` для материализации на диск).

### 2.2. Управление глобальным каталогом компонентов `Harness²`
* **`GET /api/harness/components?workspace_path=...&stack_tags=...`**
  * **Назначение:** Возвращает полный список компонентов `Harness²` с вычисленным эффективным состоянием (`ComponentEffectiveState`: `active_auto`, `active_forced`, `inactive_stack_mismatch`, `disabled_by_project`, `disabled_globally`) для TUI-менеджера `mcp-coop-agentic-factory harness`.
  * **Ответ (`200 OK`):** `{"status": "ok", "count": int, "stack_tags": list[str], "profile_overrides": dict, "components": list[dict]}`.
* **`PATCH /api/harness/components/{slug}`**
  * **Тело запроса (`ComponentPatchRequest`):** `{"status": ComponentStatus | null, "enabled": bool | null}`.
  * **Действие:** Переключает глобальный статус компонента в таблице `harness_components` (`composed` / `disabled`), инкрементирует `version` и инвалидирует кэш `cache:rules:*` в Redis.
  * **Ответ (`200 OK`):** `{"status": "ok", "component": {"slug", "component_type", "scope", "stack_tags", "title", "status", "score", "version"}}` (коды ошибок: `400` при пустом теле, `404` если `slug` не найден, `503` если PostgreSQL оффлайн).

### 2.3. Горячее управление пулом провайдеров Tier 2 (`RuntimeProviderPool`)
* **`GET /api/providers`** — возвращает отсортированный по приоритету список `{"status": "ok", "providers": list[ProviderStatusView]}` (с замаскированными ключами `api_key_masked` и статусом Circuit Breaker).
* **`POST /api/providers`** — принимает `DecisionProviderConfig` (`name`, `dialect`, `base_url`, `model`, `api_key`, `priority`, `timeout_ms`, `enabled`, `metadata`), регистрирует или обновляет провайдера в рантайме и БД (`{"status": "ok", "provider": ProviderStatusView}`).
* **`PATCH /api/providers/{name}`** — принимает `ProviderPatchRequest` (`priority`, `enabled`, `api_key`, `model`, `base_url`, `timeout_ms`, `reset_circuit`), частично обновляет провайдера и при `reset_circuit=True` очищает ключ `cache:provider_cb:{name}` в Redis (`404`, если провайдер не найден).
* **`DELETE /api/providers/{name}`** — удаляет провайдера из рантайм-пула и таблицы `decision_providers` (`{"status": "ok", "removed": name}`).

### 2.4. Прямая оценка решений и фоновое самоисцеление графа `.context/`
* **`POST /api/decisions/evaluate`**
  * **Тело запроса (`DecisionRequest`):** `{"domain": DecisionDomain, "conversation_id": str, "workspace_path": str, "agent_type": AgentType, "stack_tags": list[str], "payload": dict[str, Any]}`.
  * **Ответ (`200 OK`, `DecisionResolution`):** `{"domain": str, "action": VerdictAction, "decided_by_tier": str, "provider_name": str | null, "confidence": float, "score": float | null, "reason": str | null, "overwrite_args": dict | null, "warnings": list[str], ...}`.
* **`POST /api/context/heal`**
  * **Тело запроса (`ContextHealRequest`):** `{"workspace_path": str, "changed_files": list[str], "diff_summary": str, "agent_type": AgentType | null}`.
  * **Действие:** Запускает двухфазное самоисцеление графа `.context/` через `AntigravitySDKService.heal_context_graph()` ([[adr-003-isolated-antigravity-sdk-tier]]): детерминированная сверка `ContextGraphManager.build_and_validate()` + спавн изолированного микро-агента `Background Context Healer` при обнаружении рассинхронизации.
  * **Ответ (`200 OK`, `ContextHealReport`):** `{"status": str, "healed": bool, "initial_validation": dict, "final_validation": dict, "agent_summary": str, ...}`.

### 2.5. Шлюз жизненных хуков (`/api/hooks/*`)
Пять эндпоинтов перехвата (`POST /api/hooks/session-start`, `POST /api/hooks/pre-tool-use`, `POST /api/hooks/post-tool-use`, `POST /api/hooks/pre-invocation`, `POST /api/hooks/stop`) детально специфицированы в контракте [[contract-lifecycle-hooks]].

## Логи контекста (AI Traces)
- 2026-10-25: Сформирован контракт `contract-mcp-and-http-api`, специфицирующий 8 инструментов локального шлюза `FastMCP` (`Streamable HTTP /mcp`) и управляющие REST API эндпоинты `FastAPI` (`/health`, `/api/projects/register`, `/api/harness/components`, `/api/providers`, `/api/decisions/evaluate`, `/api/context/heal`).
