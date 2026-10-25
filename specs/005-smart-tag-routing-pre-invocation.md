# SPEC-005: Умный роутинг правил и скиллов по тегам на хуке `PreInvocation`

> **Статус:** Investigation / Draft (На будущее)  
> **Проект:** `mcp-coop-agentic-factory`  
> **Связанные модули:** [`src/core/decision/hook_dialects.py`](../src/core/decision/hook_dialects.py), [`src/core/api/routes_hooks.py`](../src/core/api/routes_hooks.py), [`src/core/api/models.py`](../src/core/api/models.py), [`src/core/harness/dynamic_rag.py`](../src/core/harness/dynamic_rag.py), [`src/core/graph/sync_engine.py`](../src/core/graph/sync_engine.py)

---

## 1. Результаты расследования текущих узких мест (Root Cause Analysis)

1. **Пропуск `lastUserInput` в диалекте Antigravity:**
   - В [`normalize_hook_payload`](../src/core/decision/hook_dialects.py) извлечение текста запроса ищет только `raw_payload.get("prompt")` и `raw_payload.get("user_prompt")`.
   - Antigravity передаёт текст запроса в поле `lastUserInput`. Из-за этого `hook_in.prompt` остаётся пустым, и хук вынужден каждый раз ходить на диск и парсить `transcript.jsonl`.
2. **Хардкод ключевых слов планирования (`PLAN_KEYWORDS`):**
   - В [`src/core/api/models.py`](../src/core/api/models.py) зашит статический кортеж `PLAN_KEYWORDS` (`plan`, `план`, `architect`, `спроектируй` и т.д.). Любая нестандартная формулировка архитектурной задачи мимо этого списка не включает инъекцию архитектурного контекста в [`hook_pre_invocation`](../src/core/api/routes_hooks.py).
3. **Языковой барьер при лексическом RAG-отборе (`tsvector` + `pg_trgm`):**
   - В [`SELECT_DYNAMIC_COMPONENTS_LEXICAL_SQL`](../src/core/harness/dynamic_rag.py) запрос пользователя сравнивается напрямую с англоязычными `slug`, `title`, `description` и `search_tsv`.
   - Если пользователь пишет запрос на русском языке (например: *«сделай чистую или гексагональную архитектуру на тайпскрипте»*), лексическое пересечение с английскими тегами `["typescript", "hexagonal", "clean", "architecture"]` даёт околонулевой `relevance`, и нужные правила/скиллы не подтягиваются.
4. **Фильтрация по `execution_mode = 'semantic'`:**
   - SQL-запросы в [`dynamic_rag.py`](../src/core/harness/dynamic_rag.py) содержат условие `COALESCE(metadata->>'execution_mode', 'always') = 'semantic'`.
   - Кастомные правила и скиллы, загруженные через `sync` ([`WorkspaceSyncEngine.ingest_workspace_components`](../src/core/graph/sync_engine.py)), не получают `execution_mode: "semantic"` в `metadata`, из-за чего дефолтятся в `'always'` и игнорируются динамическим RAG-отбором.

---

## 2. Целевая архитектура умного роутинга (`Smart Tag & Intent Router`)

### 2.1. Категориальная система тегов из `slug`
Все правила (`.agents/rules/<slug>.md`) и скиллы (`.agents/skills/<slug>/SKILL.md`) именуются строго через дефис из чистых технических и архитектурных категорий без мусорных слов (`modern`, `strict`, `standards`, `patterns`):
- Однословные базовые правила: `typescript` (`["typescript"]`), `javascript` (`["javascript"]`).
- Составные категориальные правила и скиллы: `clean-architecture` (`["clean", "architecture"]`), `typescript-architecture` (`["typescript", "architecture"]`), `javascript-ecmascript` (`["javascript", "ecmascript"]`), `hexagonal-architecture` (`["hexagonal", "architecture"]`).

### 2.2. Быстрый LLM-классификатор интента и тегов на входе `PreInvocation`
Вместо захардкоженных `PLAN_KEYWORDS` и слепого `tsvector`-матчинга русскоязычного промпта по английским слагам:
1. На хуке `PreInvocation` собирается список всех уникальных активных тегов (`DISTINCT UNNEST(stack_tags)`) из таблицы `harness_components`.
2. Быстрая модель из `RuntimeProviderPool` (с жёстким таймаутом и детерминированным фолбэком при отсутствии сети/ключей) получает:
   - текст запроса пользователя (`active_prompt` на любом языке);
   - список доступных тегов каталога.
3. Классификатор возвращает компактный JSON:
   ```json
   {
     "matched_tags": ["typescript", "hexagonal", "architecture"],
     "is_planning": true
   }
   ```

### 2.3. Взвешенное ранжирование по пересечению тегов (`Weighted Tag Overlap`)
После получения `matched_tags` SQL-запрос к `harness_components` ранжирует правила и скиллы по числу и доле совпавших тегов:
- **Точное многословное попадание (высший вес):** если промпт классифицирован в `["typescript", "architecture"]`, компонент `typescript-architecture` имеет пересечение `2` из `2` тегов.
- **Базовое однословное попадание (средний вес):** правило `typescript` имеет пересечение `1` из `1` тега и идёт следом как базовый стандарт языка.
- **Отсечение шума:** компоненты с нулевым пересечением (`javascript`, `javascript-ecmascript`) получают вес `0` и не попадают в контекст.

---

## 3. План изменений при реализации (`Checklist`)

1. **[`src/core/decision/hook_dialects.py`](../src/core/decision/hook_dialects.py):** добавить чтение `raw_payload.get("lastUserInput")` в `normalize_hook_payload`.
2. **[`src/core/graph/sync_engine.py`](../src/core/graph/sync_engine.py):** при инжесте правил и скиллов через `ingest_workspace_components` выставлять в `metadata` ключ `"execution_mode": "semantic"`.
3. **[`src/core/harness/dynamic_rag.py`](../src/core/harness/dynamic_rag.py):**
   - добавить получение списка уникальных тегов каталога;
   - реализовать `classify_prompt_tags_and_intent` через быстрый провайдер с локальным мультиязычным фолбэком;
   - обновить SQL-ранжирование с учётом веса пересечения `stack_tags` (`cardinality(ARRAY(SELECT UNNEST(stack_tags) INTERSECT SELECT UNNEST($matched_tags::text[])))`).
4. **[`src/core/api/routes_hooks.py`](../src/core/api/routes_hooks.py) и [`src/core/api/models.py`](../src/core/api/models.py):** заменить проверку `PLAN_KEYWORDS` на флаг `is_planning` из роутера.
