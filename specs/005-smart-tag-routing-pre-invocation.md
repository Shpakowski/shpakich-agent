# SPEC-005: Снайперский SEO-роутинг компонентов (`Tags + Title + Keywords`) и двухступенчатый отбор с LLM-арбитром на хуке `PreInvocation`

> **Статус:** Design Approved / Ready for Implementation (На будущее)  
> **Проект:** `mcp-coop-agentic-factory`  
> **Связанные модули:** [`seeds/harness-seed.json`](../seeds/harness-seed.json), [`seeds/prompts-config.json`](../seeds/prompts-config.json), [`src/core/db/postgres.py`](../src/core/db/postgres.py), [`src/core/harness/dynamic_rag.py`](../src/core/harness/dynamic_rag.py), [`src/core/graph/sync_engine.py`](../src/core/graph/sync_engine.py), [`src/core/api/routes_hooks.py`](../src/core/api/routes_hooks.py), [`src/core/api/models.py`](../src/core/api/models.py), [`src/cli/sync/component-scanner.ts`](../src/cli/sync/component-scanner.ts)

---

## 1. Статус базовых доработок и диагностика текущих узких мест (Root Cause Analysis)

### 1.1. Что уже реализовано в ядре (Baseline Completed)
1. **Извлечение `lastUserInput` и очистка промпта:** в [`normalize_hook_payload`](../src/core/decision/hook_dialects.py) и [`inspect_transcript_tail_detailed`](../src/core/decision/transcript.py) добавлено чтение `lastUserInput` / `last_user_input` и срезание служебного блока `<ADDITIONAL_METADATA>`.
2. **Категориальные `stack_tags` из `slug` и `execution_mode = 'semantic'` при `sync`:** в [`WorkspaceSyncEngine.ingest_components`](../src/core/graph/sync_engine.py) реализованы функции [`extract_tags_from_slug`](../src/core/graph/sync_engine.py) и [`resolve_component_tags_and_scope`](../src/core/graph/sync_engine.py) со сбросом в `scope = 'global'` и автоматической простановкой `"execution_mode": "semantic"` в `metadata`.
3. **Сессионная дедупликация в Redis:** в [`hook_pre_invocation`](../src/core/api/routes_hooks.py) добавлена защита от повторного впрыска одних и тех же правил/скиллов в рамках сессии через `LAST_PRE_INVOCATION_HASH` и `INJECTED_RAG_SLUGS`.

### 1.2. Почему текущий отбор даёт ложные срабатывания и промахи (Open Problems)
1. **Размытие смысла из-за `description` (Vector & Lexical Dilution):**
   - В [`src/core/db/postgres.py`](../src/core/db/postgres.py) генерируемая колонка `search_tsv` и векторный эмбеддинг строятся по `slug + title + description`.
   - Поле `description` нужно человеку в UI или внешнему агенту при чтении статических файлов с диска, но в нашей архитектуре решение о впрыске принимает бэкенд на `PreInvocation`. Наличие в `description` общих слов (`architecture`, `component`, `design`, `boundaries`, `pattern`, `best practices`) приводит к фоновой близости `0.40–0.62` и **ложным впрыскам** (например, срабатывание `hexagonal-architecture` на вопрос про индексацию компонентов), особенно при низком пороге `MIN_SKILL_INDEX_RELEVANCE = 0.22` в [`src/core/harness/dynamic_rag.py`](../src/core/harness/dynamic_rag.py).
2. **Отсутствие концентрированных билингвальных `keywords` и весов `setweight`:**
   - В `search_tsv` не входят `stack_tags` и триггерные ключевые слова (`metadata->'keywords'`), а все поля склеены с одинаковым весом без `setweight(..., 'A' | 'B')`.
   - Когда векторный провайдер недоступен или работает в лексическом фолбэке, русскоязычный запрос не находит англоязычный `title` без билингвальных ключевых слов.
3. **Отсутствие верификации сомнительных кандидатов и хардкод `PLAN_KEYWORDS`:**
   - Векторная близость в пограничной зоне (`0.40–0.64`) не distingue между реальной задачей на разработку и косвенным упоминанием термина в диалоге.
   - Детекция режима планирования в [`hook_pre_invocation`](../src/core/api/routes_hooks.py) по-прежнему привязана к статическому списку подстрок [`PLAN_KEYWORDS`](../src/core/api/models.py).

---

## 2. Целевая архитектура: SEO-паспорт компонента (`Tags + Title + Keywords`)

Мы полностью разделяем **отображение для человека** (`description`), **полезную нагрузку для впрыска** (`content`) и **поисковый SEO-индекс** (`stack_tags + title + keywords`).

### 2.1. Три поля поискового SEO-паспорта
Ни `content`, ни `description` **не участвуют** в расчёте `search_tsv` и векторного `embedding`. Поиск работает строго по трём концентрированным полям:
1. **`stack_tags` (Уровень A — Главные теги стека и домена):**
   - Чистые технические категории из `slug` или явного списка: `["typescript", "architecture"]`, `["python", "fastapi"]`, `["postgresql", "pgvector"]`.
2. **`title` (Уровень A/B — Короткое точное имя компонента):**
   - Сухое название домена из 3–6 слов без «воды» (например: `"Hexagonal Ports and Adapters Architecture"`).
3. **`metadata.keywords` (Уровень B — Билингвальное облако триггеров RU + EN):**
   - Массив из 10–20 высокоточных доменных терминов, синонимов, имён библиотек и русских/английских эквивалентов **строго без общеупотребимых стоп-слов** (`code`, `system`, `component`, `design`, `pattern`, `guide`).
   - Пример для `hexagonal-architecture`:
     ```json
     [
       "hexagonal", "ports and adapters", "clean architecture", "onion architecture",
       "domain layer", "application layer", "use cases", "dependency inversion", "driven adapter",
       "гексагональная", "чистая архитектура", "луковая архитектура", "порты и адаптеры", "слои домена"
     ]
     ```

### 2.2. Схема индексации в PostgreSQL и `EmbeddingClient`
1. **Взвешенный `search_tsv` в [`src/core/db/postgres.py`](../src/core/db/postgres.py):**
   - Формируется через `setweight` исключительно по SEO-паспорту:
     - **Вес `'A'` (`1.0`):** `slug` + элементы `stack_tags`.
     - **Вес `'B'` (`0.8`):** `title` + строка `metadata->>'keywords_text'` (или разворот JSON-массива `metadata->'keywords'`).
   - Триграммный индекс `GIN (gin_trgm_ops)` строится по сконкатенированной SEO-строке (`slug || ' ' || title || ' ' || keywords_text`), а не по `description`.
2. **Векторный документ для `EmbeddingClient` (`format_component_seo_document`):**
   - В [`src/core/harness/seed.py`](../src/core/harness/seed.py) и [`src/core/graph/sync_engine.py`](../src/core/graph/sync_engine.py) эмбеддинг компонента вычисляется строго по шаблону:
     ```text
     {title} | tags: {', '.join(stack_tags)} | keywords: {', '.join(keywords)}
     ```
   - Это устраняет `Vector Dilution` и даёт максимальную кучность косинусной метрики на целевых запросах.

---

## 3. Двухступенчатая воронка отбора на хуке `PreInvocation`

На каждом вызове [`hook_pre_invocation`](../src/core/api/routes_hooks.py) при новом непустом промпте пользователя отбор проходит в 2 ступени.

### 3.1. Ступень 1: Быстрый SEO + Vector отбор в PostgreSQL с правилом якоря (`Lexical Anchor Gate`)
В [`select_dynamic_components_for_prompt`](../src/core/harness/dynamic_rag.py) SQL-запрос вычисляет для каждого `semantic`-компонента три сигнала:
1. **`tag_overlap_ratio`:** доля `stack_tags` компонента, явно упомянутых в токенах запроса или совпавших с активным стеком.
2. **`lexical_seo_score`:** `ts_rank_cd(search_tsv, ...)` (по весам `A` и `B`) + `word_similarity` промпта к строке `slug + title + keywords`.
3. **`vector_seo_score`:** косинусная близость `1.0 - (embedding <=> $query_vec)` к SEO-эмбеддингу компонента.

**Правило якоря (`Lexical Anchor Gate`) и 3 зоны уверенности:**
Чтобы голый вектор никогда не протаскивал случайные компоненты на общих фразах, вводится фильтрация по зонам:
- 🟢 **Зелёная зона (`High Confidence`, `relevance >= 0.65` при наличии лексического якоря `lexical_seo_score >= 0.12` или `vector_seo_score >= 0.76`):**
  - Стопроцентное попадание в тему задачи (например, прямое упоминание кейворда/тега + высокий вектор). Кандидат **утверждается сразу**.
- 🟡 **Жёлтая зона (`Ambiguous / Candidate Zone`, `0.40 <= relevance < 0.65`):**
  - Косвенное совпадение (например, вектор `0.61–0.70`, но без прямого попадания в `slug`/`stack_tags`, либо частичное триграммное совпадение).
  - Кандидаты из Жёлтой зоны **не впрыскиваются напрямую**, а передаются на **Ступень 2 (LLM-арбитр)**.
- 🔴 **Красная зона (`Noise`, `relevance < 0.40`):**
  - Мгновенно отбрасывается. Также `MIN_SKILL_INDEX_RELEVANCE` поднимается с `0.22` до порога Жёлтой/Зелёной зоны, чтобы исключить «мусорные» впрыски скиллов.

### 3.2. Ступень 2: Быстрый LLM-арбитр и детектор интента (`Tier 2 Candidate Judge & Intent Router`)
Если в `RuntimeProviderPool` ([`src/core/decision/providers.py`](../src/core/decision/providers.py)) активен быстрый Tier 2 провайдер, он вызывается с коротким таймаутом (`400–600 мс`) для финальной верификации кандидатов и определения архитектурного интента:
1. **Вход арбитра (ультра-компактный промпт ~150–250 токенов):**
   - Запрос пользователя `active_prompt`.
   - Список Top-K (до 5) кандидатов со Ступени 1: только `slug`, `component_type`, `title` и `stack_tags` (без передачи огромных `content`).
2. **Выход арбитра (строгий JSON):**
   ```json
   {
     "approved_slugs": ["hexagonal-architecture"],
     "is_planning": true
   }
   ```
3. **Логика принятия решения:**
   - Из кандидатов 🟡 **Жёлтой зоны** (и при необходимости 🟢 Зелёной зоны) в финальный `ephemeralMessage` попадают **только те `slug`, которые подтвердил арбитр** (`approved_slugs`), проверяя, действительно ли пользователь просит применить этот стандарт/скилл, а не просто вскользь упомянул похожее слово.
   - Флаг `is_planning` включает инъекцию архитектурного контекста (`_build_plan_best_practices_hint`), заменяя жёсткую привязку к `PLAN_KEYWORDS`.
4. **Детерминированный Fail-Open / Оффлайн-фолбэк:**
   - Если Tier 2 LLM-провайдер не настроен, отключён или превысил таймаут:
     - Впрыскиваются **только** кандидаты из 🟢 **Зелёной зоны** (где есть жёсткий лексический/теговый якорь).
     - Кандидаты из 🟡 **Жёлтой зоны** отклоняются (принцип `Zero Noise Fallback`).
     - Для `is_planning` используется резервная проверка по словарю `PLAN_KEYWORDS`.

---

## 4. Авто-обогащение `keywords` при синхронизации кастомных компонентов (`mcp-coop sync`)

Чтобы пользовательские правила (`.agents/rules/<slug>.md`) и скиллы (`.agents/skills/<slug>/SKILL.md`) работали в SEO-роутинге так же точно, как системные сиды:
1. **Поддержка `keywords` во Frontmatter ([`src/cli/sync/component-scanner.ts`](../src/cli/sync/component-scanner.ts) и [`src/core/graph/sync_engine.py`](../src/core/graph/sync_engine.py)):**
   - Сканер CLI читает поле `keywords` (или `triggers`) из YAML frontmatter файла и передаёт его в `metadata.keywords`.
2. **Авто-экстракция при инжесте (`extract_seo_keywords_on_ingest`):**
   - Если пользователь не прописал `keywords` вручную в `.md`-файле, [`WorkspaceSyncEngine.ingest_components`](../src/core/graph/sync_engine.py) автоматически формирует `metadata.keywords` из:
     - токенов `slug`, `title` и `stack_tags`;
     - технических терминов из заголовков `##` / `###` и инлайн-кода `` `...` `` внутри Markdown;
     - *(опционально при активном Tier 2 провайдере во время `sync`)* однократной генерации 10–15 билингвальных RU/EN ключевых слов при прохождении `DecisionDomain.HARNESS_PROBE`.

---

## 5. Пошаговый план реализации (`Implementation Checklist`)

1. **Обогащение сидов ([`seeds/harness-seed.json`](../seeds/harness-seed.json) и [`seeds/prompts-config.json`](../seeds/prompts-config.json)):**
   - Добавить плотные билингвальные массивы `metadata.keywords` (RU + EN без общих стоп-слов) для всех семантических правил и скиллов в `seeds/harness-seed.json`.
   - Добавить системный промпт `pre_invocation_arbiter` в `seeds/prompts-config.json` для быстрого LLM-арбитра (`approved_slugs` + `is_planning`).
2. **Обновление SEO-индекса в PostgreSQL ([`src/core/db/postgres.py`](../src/core/db/postgres.py)) и генерации эмбеддингов ([`src/core/harness/seed.py`](../src/core/harness/seed.py), [`src/core/graph/sync_engine.py`](../src/core/graph/sync_engine.py)):**
   - Вынести функцию `format_component_seo_text(slug, title, stack_tags, keywords)` как единый источник текста для `EmbeddingClient` и `search_tsv`.
   - Перевести `search_tsv` в `harness_components` на взвешенный `setweight` (`'A'` для `slug + stack_tags`, `'B'` для `title + keywords`), полностью исключив `description` и `content` из поискового индекса.
3. **Двухступенчатый отбор и LLM-арбитр в [`src/core/harness/dynamic_rag.py`](../src/core/harness/dynamic_rag.py):**
   - Обновить SQL-запросы (`SELECT_DYNAMIC_COMPONENTS_VECTOR_SQL` и `SELECT_DYNAMIC_COMPONENTS_LEXICAL_SQL`) и оффлайн-ранжировщик `_compute_seed_relevance` под формулу `tag_overlap + lexical_seo_score + vector_seo_score` с правилом якоря (`Lexical Anchor Gate`).
   - Поднять пороги отсечения (Зелёная зона `>= 0.65`, Жёлтая зона `0.40..0.65`, Красная зона `< 0.40`).
   - Реализовать `evaluate_candidates_with_arbiter(prompt, candidates, provider_pool)` для фильтрации Жёлтой зоны и детекции `is_planning`.
4. **Интеграция в [`src/core/api/routes_hooks.py`](../src/core/api/routes_hooks.py) и CLI-сканер ([`src/cli/sync/component-scanner.ts`](../src/cli/sync/component-scanner.ts)):**
   - Подключить возвращаемый из роутера флаг `is_planning` (с фолбэком на `PLAN_KEYWORDS` при оффлайне) в `hook_pre_invocation`.
   - Добавить парсинг `keywords` во frontmatter при `mcp-coop sync` и авто-экстракцию ключевых слов в `WorkspaceSyncEngine`.
5. **Тестирование (`tests/unit/test_spec002_dynamic_harness.py`):**
   - Покрыть юнит-тестами отсутствие ложных срабатываний на общих запросах (Zero False Positives), билингвальный матчинг RU-запросов по `metadata.keywords`, фильтрацию Жёлтой зоны через LLM-арбитр и оффлайн-фолбэк.
