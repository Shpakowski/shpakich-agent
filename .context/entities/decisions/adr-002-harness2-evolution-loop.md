---
type: Entity
class: Decision
id: adr-002-harness2-evolution-loop
name: "ADR-002: Эволюция окружения агента по методологии Harness² (Propose -> Probe -> Compose) и декаплированная шина хуков"
status: Active
stack: [python, postgresql, pgvector, redis]
files:
  - src/core/harness/seed.py
  - src/core/harness/compiler.py
  - src/core/harness/script_runner.py
  - src/core/decision/engine.py
depends_on:
  - "[[adr-001-clean-host-global-docker]]"
implements: []
governed_by: []
---

# ADR-002: Эволюция окружения агента по методологии Harness² (`Propose -> Probe -> Compose`) и декаплированная шина хуков (`adr-002-harness2-evolution-loop`)

> **Статус:** Active  
> **Связанные узлы:** `[[adr-001-clean-host-global-docker]]`, `[[mod-python-core]]`, `[[mod-bun-cli]]`, `[[contract-storage-schema]]`, `[[contract-lifecycle-hooks]]`, `[[contract-mcp-and-http-api]]`

---

## 1. Контекст и постановка проблемы

Нативные ИИ-агенты обладают статическими системными промптами и начинают каждую новую сессию «с чистого листа», повторяя одни и те же ошибки сборки, нарушая проектные конвенции или зацикливаясь на сломанных командах. При этом:
1. Дообучение весов (Fine-tuning) базовых LLM в локальном контуре невозможно и нерентабельно.
2. Ручное редактирование файлов правил (`AGENTS.md`, `.cursorrules`, `CLAUDE.md`) в каждом репозитории не масштабируется: общие инженерные инварианты перемешиваются со спецификой конкретного фреймворка и не переносятся в другие проекты.
3. Хранение исполняемых проверочных скриптов хуков в виде разрозненных файлов внутри каждого репозитория усложняет их обновление и приводит к рассинхронизации логики перехвата между проектами.

---

## 2. Принятое архитектурное решение

В развитие архитектуры глобального ядра (`[[adr-001-clean-host-global-docker]]`) мы внедряем замкнутый контур самообучения окружения агента на базе методологии **Google Research `Harness²`** внутри `[[mod-python-core]]`:

### 2.1. Моделирование харнеса как 8 типизированных компонентов с разделением Scope
Окружение ИИ-агента декомпозируется на 8 редактируемых компонентов (`System Prompt`, `Guardrails`, `Memory`, `Skills`, `Tool Guidance`, `Scripts`, `Subagents`, `Middleware Plugin`), хранящихся в таблицах `harness_components` и `memories` PostgreSQL (`[[contract-storage-schema]]`).
Каждая единица знаний маркируется областью видимости (`ScopeType`):
* **`invariant` (`scope = 'global'`)**: фундаментальные инженерные правила, политика безопасности и протокол работы с `.context/`, применяемые ко всем проектам без исключения.
* **`domain-specific` (`scope = 'domain'`, `stack_tags = [...]`)**: правила, скиллы и перезаписи команд (например, `rewrite-npm-to-bun`), активируемые компилятором `HarnessCompiler` (`src/core/harness/compiler.py`) только при пересечении с тегами стека целевого проекта и учёте пер-проектных оверрайдов `ProjectHarnessOverrides`.

### 2.2. Стейт-машина эволюции `Propose -> Probe -> Compose` и мульти-тировый `DecisionEngine`
Вся мутация правил и оценка действий агента проходят через многоуровневый движок `DecisionEngine` (`src/core/decision/engine.py`) и интерфейс `[[contract-mcp-and-http-api]]`:
1. **Propose (Предложение):** при устранении ошибки в сессии (паттерн Redis Iris `Short-to-Long Memory Promotion`) или вызове MCP-инструмента `coop_propose_harness_edit` создаётся карточка компонента.
2. **Probe (Многоуровневая проверка и скоринг):** кандидат проходит каскадную проверку:
   * **Tier 1 (`LocalDeterministicTier`)**: суб-миллисекундные детерминированные инварианты (проверка размера, отсутствие пустых директив, валидация regex-паттернов `Guardrail`, анти-зацикливание команд и контроль `implementation_plan.md`).
   * **Tier 2 (`AntigravityProbeDecisionTier` и `RuntimeProviderPool`)**: глубокий архитектурный аудит через изолированного Read-Only микро-агента или пул внешних провайдеров (`OPENAI_DECISIONS`, `JEV_CLEF`, `OPENAI_CHAT_SCHEMA`) с защитой через Redis Circuit Breaker.
3. **Compose (Включение в базовый харнес):** валидированные компоненты получают статус `ComponentStatus.COMPOSED`, инвалидируют кэш `LangCache Lite` (`cache:rules:{stack_hash}`) и автоматически доставляются в проекты через `HarnessCompiler` при `SessionStart` или команде `sync`.

### 2.3. Декаплированная шина жизненных хуков и `HookScriptDispatcher`
Вместо жёсткого хардкода скриптов в файловой системе проектов все исполняемые скрипты жизненных хуков (`inject-context-map`, `track-file-changes`, `enforce-context-sync` и пользовательские расширения) перенесены в таблицу `harness_components` (`ComponentType.SCRIPT`) под управление `HookScriptDispatcher` (`src/core/harness/script_runner.py`) и протокола `[[contract-lifecycle-hooks]]`:
* Хостовый `src/cli/hook_runner.ts` (`[[mod-bun-cli]]`) работает как тонкий универсальный HTTP-прокси.
* При поступлении события хука `HookScriptDispatcher` применяет **двухступенчатую воронку отбора**:
  1. Обязательные инвариантные скрипты с режимом `activation = "always"` для текущей фазы хука и стека проекта.
  2. Семантический **Top-K отбор** (`pgvector` косинусная близость или полнотекстовый `tsvector`) для динамического подключения специализированных скриптов под конкретный контекст ошибки или вызываемого инструмента.
* Состояние сессии (`code_modified`, `context_updated`, `resolved_error`) хранится централизованно в метаданных сессии Redis (`session:{id}:meta`).

---

## 3. Отвергнутые альтернативы и компромиссы (Trade-offs)

1. **Прямая перезапись `AGENTS.md` самим агентом без фазы `Probe` и БД:**
   * *Причина отказа:* приводит к деградации промпта («prompt drift»), раздуванию файла сверх лимита контекстного окна и невозможности переиспользовать выученное правило в соседних репозиториях. В `HarnessCompiler` введён строгий бюджет `MAX_RULES_MARKDOWN_BYTES = 24_000` с ранжированием по `score` и `usage_count`.
2. **Исполнение bash/python-скриптов хуков непосредственно на хосте из папки `.agents/hooks/`:**
   * *Причина отказа:* дублирует файлы в каждом репозитории, создаёт зависимость от версии локального интерпретатора Python на хосте и не позволяет динамически подбирать скрипты через векторный поиск `pgvector`.
3. **Компромисс принятого решения:**
   * Вызов хуков по HTTP к локальному Docker-контейнеру добавляет сетевой hop на loopback-интерфейсе (`127.0.0.1:8787`), однако благодаря кэшированию `LangCache Lite` в Redis и параллельному пулу `asyncpg` латентность Tier 1 удерживается в пределах нескольких миллисекунд.

---

## Логи контекста (AI Traces)
- 2026-10-25: Зафиксировано архитектурное решение `adr-002-harness2-evolution-loop`, описывающее модель 8 компонентов `Harness²`, цикл `Propose -> Probe -> Compose`, каскадный `DecisionEngine` и перенос скриптов хуков в БД под управление `HookScriptDispatcher`.
