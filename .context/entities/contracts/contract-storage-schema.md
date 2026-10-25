---
type: Entity
class: Contract
id: contract-storage-schema
name: Контракт схемы данных PostgreSQL 18 (pgvector + FTS) и оперативной памяти Redis Iris
status: Active
stack: [postgresql, pgvector, asyncpg, redis, python]
files:
  - src/core/db/postgres.py
  - src/core/db/redis_client.py
  - src/core/db/compactions.py
  - src/core/enums.py
depends_on: []
implements: []
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
---

# Контракт схемы данных PostgreSQL 18 (pgvector + FTS) и оперативной памяти Redis Iris (`contract-storage-schema`)

Формальный контракт персистентного и оперативного хранения **Layer 2 (Storage Engine)**, реализуемый модулем [[mod-python-core]] поверх инфраструктурных контейнеров [[mod-docker-stack]] (`pgvector/pgvector:pg18` и `redis:7-alpine`). Определяет реляционно-векторную DDL-схему PostgreSQL 18 (`SCHEMA_DDL` в `src/core/db/postgres.py`, `CompactionArchiveStore` в `src/core/db/compactions.py`) и иерархию ключей/TTL оперативной памяти сессий Redis 7 (`RedisSessionStore` в `src/core/db/redis_client.py`) в строгом соответствии с политикой Zero-Hardcode (`src/core/enums.py`).

---

## 1. Схема персистентного хранилища PostgreSQL 18 (`pgvector` + `tsvector` + `pg_trgm`)

При старте ASGI-приложения (`lifespan`) метод `PostgresDatabase.init_schema()` внутри единой транзакции идемпотентно активирует расширения `vector` и `pg_trgm` и разворачивает 6 базовых таблиц:

### 1.1. Таблица `projects` (Зарегистрированные рабочие пространства)
Хранит привязку локальных директорий разработчика к целевому профилю агента и тегам технологического стека.
* `id` (`UUID PRIMARY KEY DEFAULT gen_random_uuid()`) — уникальный идентификатор проекта.
* `workspace_path` (`TEXT UNIQUE NOT NULL`) — канонический абсолютный путь к корню репозитория на хосте.
* `name` (`TEXT NOT NULL`) — человекочитаемое имя проекта.
* `agent_type` (`TEXT NOT NULL`) — идентификатор целевого агента из `AgentType` (`antigravity`, `claude-code`, `cursor`, `codex`, `opencode`) без вендор-хардкода на уровне DDL.
* `stack_tags` (`TEXT[] NOT NULL DEFAULT ARRAY['global']`) — нормализованный набор тегов стека проекта.
* `profile_overrides` (`JSONB NOT NULL DEFAULT '{}'::jsonb`) — пер-проектные оверрайды `ProjectHarnessOverrides` (`enabled_components`, `disabled_components`, `disabled_hooks`, `extra_stack_tags`, `excluded_stack_tags`, `mcp_enabled`).
* `last_synced_at`, `created_at` (`TIMESTAMPTZ DEFAULT NOW()`) — временные метки синхронизации и регистрации.

### 1.2. Таблица `harness_components` (Каталог компонентов `Harness²`)
Единый реестр редактируемых компонентов окружения агента в цикле эволюции [[adr-002-harness2-evolution-loop]].
* `id` (`UUID PRIMARY KEY DEFAULT gen_random_uuid()`) — первичный ключ компонента.
* `slug` (`TEXT UNIQUE NOT NULL`) — уникальный канонический идентификатор компонента (например, `core-engineering-mentor`, `block-destructive-commands`, `enforce-context-sync`).
* `component_type` (`TEXT NOT NULL`) — тип компонента из `ComponentType` (`system_prompt`, `guardrail`, `skill`, `tool_guidance`, `script`, `subagent`, `middleware`).
* `scope` (`TEXT NOT NULL DEFAULT 'global'`) — область действия из `ScopeType`: `global` (инвариантное правило для всех проектов) или `domain` (доменно-специфичное по `stack_tags`).
* `stack_tags` (`TEXT[] NOT NULL DEFAULT ARRAY['global']`) — технологические теги, определяющие применимость компонента.
* `title`, `description`, `content` (`TEXT NOT NULL`) — заголовок, описание для семантического поиска и тело компонента (Markdown-инструкции или исходный код Python-скрипта).
* `metadata` (`JSONB NOT NULL DEFAULT '{}'::jsonb`) — типизированные параметры исполнения:
  * для `guardrail`: `{ "action": "deny" | "overwrite", "matcher": "run_command", "pattern": "...", "replacement": "..." }`;
  * для `script`: `{ "hook_events": ["session-start", ...], "execution_mode": "always" | "semantic", "priority": 100, "timeout_ms": 500 }`;
  * для `skill`: `{ "helper_scripts": { "<filename>": "<content>" } }`.
* `embedding` (`vector(768)`) — опциональный L2-нормализованный 768-мерный вектор от `EmbeddingGemma 2` для семантического отбора скриптов и навыков.
* `search_tsv` (`tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(slug, '') || ' ' || coalesce(title, '') || ' ' || coalesce(description, ''))) STORED`) — автоматически вычисляемый полнотекстовый индекс.
* `status` (`TEXT NOT NULL DEFAULT 'composed'`) — фаза жизненного цикла из `ComponentStatus` (`candidate`, `probing`, `composed`, `rejected`, `disabled`).
* `score` (`REAL NOT NULL DEFAULT 1.0`), `usage_count` (`INT NOT NULL DEFAULT 0`), `version` (`INT NOT NULL DEFAULT 1`) — метрики качества, частоты применения и ревизии.
* `source_project_path` (`TEXT`), `updated_at`, `created_at` (`TIMESTAMPTZ DEFAULT NOW()`).

### 1.3. Таблица `memories` (Долгосрочная сквозная память опыта)
Хранит решённые нетривиальные ошибки и архитектурные паттерны для Error Recovery RAG и планирования.
* `id` (`UUID PRIMARY KEY DEFAULT gen_random_uuid()`).
* `title` (`TEXT NOT NULL`) — краткое резюме проблемы или паттерна.
* `error_symptom` (`TEXT NOT NULL`) — захваченный симптом ошибки (лог исключения, код возврата или анти-паттерн).
* `solution` (`TEXT NOT NULL`) — проверенное инженерное решение или рабочий обходной путь.
* `scope` (`TEXT NOT NULL DEFAULT 'global'`), `stack_tags` (`TEXT[] NOT NULL DEFAULT ARRAY['global']`).
* `embedding` (`vector(768)`) — 768-мерный вектор `EmbeddingGemma 2` (или `NULL` в оффлайн-режиме).
* `search_tsv` (`tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(error_symptom, '') || ' ' || coalesce(solution, ''))) STORED`).
* `helpful_count` (`INT NOT NULL DEFAULT 0`), `source_project_path` (`TEXT`), `created_at` (`TIMESTAMPTZ DEFAULT NOW()`).

### 1.4. Таблица `hook_events` (Журнал аудита жизненных хуков)
Неизменяемый лог перехватов и принятых вердиктов (`contract-lifecycle-hooks`).
* `id` (`BIGSERIAL PRIMARY KEY`).
* `conversation_id` (`TEXT NOT NULL`) — идентификатор диалоговой сессии агента.
* `workspace_path` (`TEXT`) — путь к рабочей директории проекта.
* `event_type` (`TEXT NOT NULL`) — тип события из `HookEventType` (`session-start`, `pre-tool-use`, `post-tool-use`, `pre-invocation`, `stop`).
* `tool_name` (`TEXT`) — имя вызванного инструмента (для `pre-tool-use` / `post-tool-use`).
* `payload`, `decision` (`JSONB NOT NULL DEFAULT '{}'::jsonb`) — сырой входной запрос и возвращённый JSON-ответ.
* `created_at` (`TIMESTAMPTZ DEFAULT NOW()`).

### 1.5. Таблица `decision_providers` (Рантайм-пул провайдеров Tier 2)
Персистентный реестр внешних LLM-арбитров второго контура (`RuntimeProviderPool`).
* `id` (`UUID PRIMARY KEY DEFAULT gen_random_uuid()`).
* `name` (`TEXT UNIQUE NOT NULL`) — уникальный идентификатор провайдера (например, `openai-default`).
* `dialect` (`TEXT NOT NULL`) — протокольный диалект из `ProviderDialect` (`openai_decisions`, `jev_clef`, `openai_chat_schema`).
* `base_url`, `model` (`TEXT NOT NULL`), `api_key` (`TEXT NOT NULL DEFAULT ''`).
* `priority` (`INT NOT NULL DEFAULT 100`) — порядок обхода в каскаде (меньше = выше приоритет).
* `timeout_ms` (`INT NOT NULL DEFAULT 600`), `enabled` (`BOOLEAN NOT NULL DEFAULT TRUE`), `metadata` (`JSONB NOT NULL DEFAULT '{}'::jsonb`).
* `updated_at`, `created_at` (`TIMESTAMPTZ DEFAULT NOW()`).

### 1.6. Таблица `session_compactions` (Долгосрочный архив сводок сжатия контекста `/compact`, SPEC-003)
Хранит извлечённые чекпоинты `<summary>` после выполнения `/compact` для кросс-сессионного восстановления контекста и планирования (`CompactionArchiveStore`).
* `id` (`UUID PRIMARY KEY DEFAULT gen_random_uuid()`).
* `conversation_id` (`TEXT NOT NULL`), `workspace_path` (`TEXT NOT NULL DEFAULT ''`), `agent_type` (`TEXT NOT NULL`).
* `step_idx` (`INT NOT NULL DEFAULT 0`) — номер шага диалога на момент сжатия контекста.
* `checkpoint_hash` (`TEXT NOT NULL`) — 16-символьный детерминированный SHA-256 дайджест нормализованного текста сводки; в паре с `conversation_id` образует ограничение `UNIQUE (conversation_id, checkpoint_hash)` для идемпотентной записи (`ON CONFLICT DO NOTHING`).
* `summary_text` (`TEXT NOT NULL`) — очищенное тело сводки `<summary>...</summary>`.
* `stack_tags` (`TEXT[] NOT NULL DEFAULT ARRAY['global']`), `metadata` (`JSONB NOT NULL DEFAULT '{}'::jsonb`).
* `embedding` (`vector(768)`) — опциональный 768-мерный вектор `EmbeddingGemma 2`.
* `search_tsv` (`tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(workspace_path, '') || ' ' || coalesce(summary_text, ''))) STORED`).
* `created_at` (`TIMESTAMPTZ DEFAULT NOW()`).

### 1.7. Гибридные индексы (`HNSW` + `GIN tsvector` + `GIN pg_trgm`)
Для суб-миллисекундного 3-way гибридного поиска (косинусная близость `pgvector` + полнотекстовый `ts_rank_cd` + триграммная схожесть `word_similarity` / `similarity`) поддерживаются 10 индексов:
* **На таблице `harness_components`:**
  * `idx_components_tsv` — `USING GIN (search_tsv)`;
  * `idx_components_desc_trgm` — `USING GIN (description gin_trgm_ops)`;
  * `idx_components_embedding_hnsw` — частичный HNSW-индекс `USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL`.
* **На таблице `memories`:**
  * `idx_memories_tsv` — `USING GIN (search_tsv)`;
  * `idx_memories_symptom_trgm` — `USING GIN (error_symptom gin_trgm_ops)`;
  * `idx_memories_embedding_hnsw` — частичный HNSW-индекс `USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL`.
* **На таблице `session_compactions`:**
  * `idx_compactions_workspace` — `(workspace_path, created_at DESC)`;
  * `idx_compactions_tsv` — `USING GIN (search_tsv)`;
  * `idx_compactions_summary_trgm` — `USING GIN (summary_text gin_trgm_ops)`;
  * `idx_compactions_embedding_hnsw` — частичный HNSW-индекс `USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL`.

---

## 2. Контракты оперативной памяти Redis 7 (Паттерны Redis Iris)

Клиент `RedisSessionStore` (`src/core/db/redis_client.py`) организует ключи через константу-разделитель `:` и строгие перечисления `RedisNamespace` (`session`, `cache`) и `RedisKeySegment`. Все операции модификации связанных ключей выполняются атомарно за 1 сетевой RTT через `pipeline(transaction=True)`.

| Паттерн / Контур | Шаблон ключа Redis | Тип Redis / Модель | TTL по умолчанию | Назначение и контракт полей |
| :--- | :--- | :--- | :--- | :--- |
| **1. Short-Term Working Memory** | `session:{conversation_id}:meta` | `Hash` (`dict[str, str]`) | `86400` сек (24 ч, `redis_session_ttl_seconds`) | Оперативные флаги сессии (`SessionMetaField`): `workspace_path`, `resolved_error` (`"0"` \| `"1"`), `reflection_prompted` (`"0"` \| `"1"`), `code_modified` (`"0"` \| `"1"`), `context_updated` (`"0"` \| `"1"`), `last_compact_hash` (16-hex SHA-256 последнего сархивированного `/compact`), `last_compact_step` (индекс шага чекпоинта). |
| **1. Short-Term Working Memory** | `session:{conversation_id}:last_error` | `String` (JSON `SessionLastError`) | `86400` сек (24 ч) | Слепок последнего сбоя инструмента: `{ tool_name, command, error_text, step_idx }`. Читается на `PreInvocation` для Error Recovery RAG. |
| **1. Short-Term Working Memory** | `session:{conversation_id}:cmd_fails` | `Hash` (`{cmd_hash: count}`) | `86400` сек (24 ч) | Счётчик последовательных падений пары `(tool_name, command)` по 16-символьному SHA-256 дайджесту `compute_command_hash`. Блокирует зацикливание при достижении `max_consecutive_cmd_failures` (`2`). |
| **2. Short-to-Long Promotion** | `session:{conversation_id}:promotion` | `String` (JSON `MemoryPromotionCandidate`) | `86400` сек (24 ч) | Карточка успешно преодолённого сбоя `{ tool_name, failed_command, error_text, recovery_command, step_idx }`. Создаётся в `mark_error_resolved()` при успешном шаге после ошибки и служит триггером рефлексии на хуке `Stop`. |
| **3. LangCache Lite** | `cache:decision:{cmd_hash}` | `String` (JSON) | `3600` сек (1 ч, `redis_cache_ttl_seconds`) | Кэш детерминированных и Tier 2 вердиктов `DecisionEngine` для пары `(tool_name, command)`. |
| **3. LangCache Lite** | `cache:rules:{stack_hash}` | `String` (JSON `CanonicalHarnessBundle`) | `3600` сек (1 ч) | Кэш скомпилированного бандла компонентов `Harness²` по инвариантному к порядку и регистру SHA-256 дайджесту `compute_stack_hash(stack_tags)`. Принудительно сбрасывается через `invalidate_rules_cache()` при мутациях каталога. |
| **4. Tier 2 Circuit Breaker** | `cache:provider_cb:{provider_name}` | `String` (JSON `ProviderCircuitState`) | Динамический `cooldown_seconds` (`60`–`300` сек) | Состояние разомкнутого прерывателя провайдера Tier 2 (`status`: `rate_limited` \| `quota_exhausted` \| `upstream_error`, `reason`, `cooldown_seconds`). Позволяет мгновенно переключаться на следующий провайдер в пуле или локальный Tier 1. |

---

## 3. Архитектурные связи

* Реализуется в модуле [[mod-python-core]] (`src/core/db/postgres.py`, `src/core/db/redis_client.py`, `src/core/db/compactions.py`, `src/core/enums.py`) и разворачивается в изолированной инфраструктуре [[mod-docker-stack]].
* Служит фундаментом данных для протокола перехвата жизненных хуков [[contract-lifecycle-hooks]] и для инструментов/эндпоинтов [[contract-mcp-and-http-api]].
* Подчиняется архитектурным инвариантам [[adr-001-clean-host-global-docker]] и [[adr-002-harness2-evolution-loop]].

## Логи контекста (AI Traces)
- 2026-10-25: Сформирован формальный контракт схемы хранения `contract-storage-schema` (5 таблиц PostgreSQL 18 с HNSW/GIN-индексами `vector(768)` + `tsvector` + `pg_trgm`, 3 паттерна Redis Iris и Circuit Breaker провайдеров Tier 2).
- 2026-10-25: Добавлена 6-я таблица `session_compactions` с 4 гибридными индексами (`idx_compactions_workspace`, `idx_compactions_tsv`, `idx_compactions_summary_trgm`, `idx_compactions_embedding_hnsw`) и поля дедупликации чекпоинтов сжатия `last_compact_hash`, `last_compact_step` в `session:{id}:meta` (`SPEC-003`).
