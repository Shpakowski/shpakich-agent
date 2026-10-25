---
type: Entity
class: Decision
id: adr-003-isolated-antigravity-sdk-tier
name: "ADR-003: Модульная изоляция google-antigravity SDK во втором контуре микро-агентов и песочнице /tmp"
status: Active
stack: [python, fastapi, pydantic, docker]
files:
  - src/core/services/antigravity/models.py
  - src/core/services/antigravity/sandbox.py
  - src/core/services/antigravity/runtime.py
  - src/core/services/antigravity/service.py
depends_on:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
implements: []
governed_by: []
---

# ADR-003: Модульная изоляция `google-antigravity` SDK во втором контуре микро-агентов и песочнице `/tmp` (`adr-003-isolated-antigravity-sdk-tier`)

> **Статус:** Active  
> **Связанные узлы:** `[[adr-001-clean-host-global-docker]]`, `[[adr-002-harness2-evolution-loop]]`, `[[mod-python-core]]`, `[[mod-docker-stack]]`, `[[mod-bun-cli]]`, `[[contract-mcp-and-http-api]]`

---

## 1. Контекст и постановка проблемы

Для глубокого аудита предлагаемых правил на фазе `Probe` (`[[adr-002-harness2-evolution-loop]]`), фонового самовосстановления графа `.context/` (`Background Context Healer`) и запуска E2E-верификации ядру фабрики требуется возможность программно спавнить изолированных микро-агентов через `google-antigravity` Python SDK (`ag.Agent`).

Однако прямое встраивание SDK в базовое ядро создаёт серьёзные инженерные риски:
1. **Нарушение мульти-агентной нейтральности:** ядро фабрики обслуживает 5 различных ИИ-агентов (`antigravity`, `claude-code`, `cursor`, `codex`, `opencode`). Вызов `google-antigravity` SDK для проектов, работающих под управлением `Claude Code` или `Cursor`, приводил бы к нежелательным внешним расходам и смешиванию провайдеров.
2. **Угрозы безопасности файловой системы и Prompt Injection:** микро-агент, анализирующий ненадёжный код или сторонний дифф, при прямом доступе к рабочей директории может повредить исходники проекта, выйти за пределы песочницы через симлинки (`Symlink / Path Traversal`) или выполнить инъекцию инструкций из анализируемого текста.
3. **Утечка подпроцессов в Docker:** SDK запускает дочерние бинарные подпроцессы CLI, которые при таймаутах или обрывах соединения могут превращаться в процессы-зомби (`defunct`) внутри контейнера `[[mod-docker-stack]]`.

---

## 2. Принятое архитектурное решение

Мы изолируем интеграцию `google-antigravity` SDK в виде опционального контура второго уровня внутри `[[mod-python-core]]` с соблюдением жёстких границ ответственности и безопасности:

### 2.1. Модульная декомпозиция пакета по принципу SRP (Отказ от God-модуля)
Вместо монолитного сервисного файла контур разбит на 5 специализированных модулей в `src/core/services/antigravity/`:
* **`models.py`**: неизменяемые Pydantic v2 контракты (`ProbeAuditVerdict`, `ContextHealPlan`, `ContextHealNodeMutation`, `ContextHealReport`, `E2EProbeReport`, `AntigravityDiagnosticStatus`) со строгой валидацией `extra="forbid"`.
* **`sandbox.py`**: низкоуровневая работа с временными песочницами `/tmp/mcp_coop_agy_*`, защита от симлинков, обёртывание ненадёжного контента в криптографический XML-конверт (`wrap_untrusted_content`) и фильтрация секретов окружения (`build_sanitized_env`).
* **`runtime.py`**: ленивый резолвер символов SDK (`load_sdk_symbols`), контекстный менеджер перехвата переменных окружения и управление таймаутами / жизненным циклом подпроцесса агента.
* **`service.py`**: высокоуровневый фасад `AntigravitySDKService`, реализующий три целевых сценария (`audit_harness_candidate`, `heal_context_graph`, `run_e2e_agent_probe`), интеграцию с Redis Circuit Breaker и взаимодействие с `[[contract-mcp-and-http-api]]`.
* **`__init__.py`**: чистый публичный фасад пакета.

### 2.2. Строгий гейтинг `is_enabled_for_agent` только для `AgentType.ANTIGRAVITY`
Метод `AntigravitySDKService.is_enabled_for_agent(agent_type)` реализует жёсткий архитектурный барьер:
* Контур активируется **только** тогда, когда в `CoreSettings` включён флаг `antigravity_sdk_configured == True` (задан API-ключ или OAuth-токен и установлен пакет SDK) **И** целевой проект зарегистрирован с типом агента `AgentType.ANTIGRAVITY`.
* Для всех остальных агентов (`claude-code`, `cursor`, `codex`, `opencode`) любые вызовы `AntigravityProbeDecisionTier` и `POST /api/context/heal` мгновенно завершаются детерминированным `no-op` (`None` / `skipped`), передавая управление стандартному пулу `RuntimeProviderPool` или Tier 1.

### 2.3. Многоуровневая защита песочницы `/tmp` и контейнера
1. **Изоляция в `/tmp/mcp_coop_agy_*` и защита от Path Traversal (`sandbox.py`):**
   * Микро-агенты никогда не получают прямого права записи в исходный репозиторий проекта. Временный воркспейс создаётся через `tempfile.mkdtemp(prefix="mcp_coop_agy_")` с лимитом копирования `MAX_SANDBOX_TOTAL_BYTES = 8 МБ`.
   * При копировании дерева файлов все символьные ссылки (`Path.is_symlink()`) принудительно игнорируются, а целевые пути проверяются через `Path.resolve().relative_to(root_resolved)`.
2. **Двухфазный коммит `Plan -> Validate -> Apply` для `Background Context Healer`:**
   * Микро-агент в песочнице работает исключительно в режиме `Read-Only` и возвращает структурированный план мутаций `ContextHealPlan` (`Pydantic v2`).
   * Запись изменений в реальный граф `.context/` выполняется детерминированным движком `ContextGraphManager.upsert_node`, который повторно проверяет существование физических файлов (`No Phantom Nodes`) и отсутствие битых ссылок (`No Broken Links`).
3. **Защита от Prompt Injection через одноразовый XML-конверт:**
   * Любой анализируемый контент (дифф кода, текст кандидата в правила, описание задачи) оборачивается в тег `<untrusted_data_{nonce}>...</untrusted_data_{nonce}>` со случайным криптографическим `secrets.token_hex(8)` и явной системной инструкцией игнорировать любые команды внутри этого блока.
4. **Защита от процессов-зомби в Docker (`init: true`):**
   * В `docker/docker-compose.yml` (`[[mod-docker-stack]]`) для контейнера `mcp-coop-agentic-factory-core` включён параметр `init: true` (`tini`), гарантирующий корректный сбор (`reaping`) дочерних процессов CLI при таймаутах.

---

## 3. Отвергнутые альтернативы и компромиссы (Trade-offs)

1. **Предоставление микро-агенту `Context Healer` прямого инструмента `edit_file` в папке проекта:**
   * *Причина отказа:* даже при ограничении системного промпта папка проекта остаётся уязвимой к галлюцинациям модели или перезаписи исходного кода вне `.context/`. Схема `Read-Only Agent -> Pydantic Plan -> ContextGraphManager.upsert_node` гарантирует 100% соблюдение онтологии `.context/schema.md`.
2. **Обязательная установка `google-antigravity` в базовые зависимости `pyproject.toml`:**
   * *Причина отказа:* нарушает принцип легковесности базового ядра (`[[adr-001-clean-host-global-docker]]`). Пакет вынесен в опциональную группу `[project.optional-dependencies].antigravity`, а импорт выполняется лениво через `importlib.import_module` в `runtime.py`.
3. **Компромисс принятого решения:**
   * Копирование среза проектных файлов во временную песочницу `/tmp/mcp_coop_agy_*` добавляет небольшой дисковый I/O в памяти контейнера, который полностью нивелируется фильтрацией служебных директорий (`node_modules`, `.git`, `.venv`, `dist`) и жёстким бюджетом в 8 МБ.

---

## Логи контекста (AI Traces)
- 2026-10-25: Зафиксировано архитектурное решение `adr-003-isolated-antigravity-sdk-tier`, формализующее модульную декомпозицию `src/core/services/antigravity/`, строгий гейтинг для `AgentType.ANTIGRAVITY`, двухфазный протокол `Plan -> Validate -> Apply` и защиту песочницы `/tmp/mcp_coop_agy_*`.
