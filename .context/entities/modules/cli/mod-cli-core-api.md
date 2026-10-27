---
type: Entity
class: Module
id: mod-cli-core-api
name: CLI Typed HTTP Client for Core Engine REST API (Layer 2)
status: Active
stack: [nodejs, typescript]
files:
  - src/cli/core-api/http-client.ts
  - src/cli/core-api/endpoints-projects.ts
  - src/cli/core-api/endpoints-harness.ts
  - src/cli/core-api/endpoints-sync.ts
  - src/cli/core-api/endpoints-providers.ts
  - src/cli/core-api/endpoints-hooks.ts
  - src/cli/core-api/index.ts
depends_on:
  - "[[mod-cli-config]]"
  - "[[mod-cli-common]]"
implements:
  - "[[contract-lifecycle-hooks]]"
  - "[[contract-mcp-and-http-api]]"
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
  - "[[adr-003-isolated-antigravity-sdk-tier]]"
---

# CLI Typed HTTP Client for Core Engine REST API (`mod-cli-core-api`)

Адаптерный модуль **Layer 2** архитектуры `[[mod-cli]]`, инкапсулирующий всё сетевое взаимодействие с REST API глобального Python-ядра (`[[mod-python-core]]`) по протоколам `[[contract-mcp-and-http-api]]` и `[[contract-lifecycle-hooks]]`.

## Состав подмодуля
1. **`src/cli/core-api/http-client.ts`**: базовый Fail-Open HTTP-транспорт поверх `fetch` и `AbortSignal.timeout` (`requestCoreJson`, `checkCoreHealth`, `resolveCoreBaseUrl`).
2. **`src/cli/core-api/endpoints-projects.ts`**: регистрация проекта и компиляция адаптеров `POST /api/projects/register` (`registerProjectWithCore`), а также фоновое самовосстановление графа `POST /api/context/heal` (`healProjectContext`).
3. **`src/cli/core-api/endpoints-harness.ts`**: чтение каталога компонентов `GET /api/harness/components` (`fetchHarnessCatalog`), глобальное переключение статуса `PATCH /api/harness/components/{slug}` (`patchGlobalComponentStatus`) и запуск кластерной консолидации `POST /api/harness/consolidate` (`consolidateHarnessComponents`).
4. **`src/cli/core-api/endpoints-sync.ts`**: двусторонний инжест пользовательских компонентов `POST /api/sync/ingest` (`ingestWorkspaceComponents`) и 3-Way Hash сверка узлов `.context/` `POST /api/sync/context` (`syncWorkspaceContextNodes`).
5. **`src/cli/core-api/endpoints-providers.ts`**: CRUD-операции над пулом провайдеров второго контура `/api/providers` (`listTier2Providers`, `upsertTier2Provider`, `patchTier2Provider`, `deleteTier2Provider`).
6. **`src/cli/core-api/endpoints-hooks.ts`**: отправка нормализованных событий жизненных хуков в `POST /api/hooks/{event}` (`postHookEventToCore`).
7. **`src/cli/core-api/index.ts`**: публичный фасад (`barrel`) HTTP-клиента ядра.

## Логи контекста (AI Traces)
- 2026-10-26: Реализован типизированный Fail-Open HTTP-клиент к REST API ядра (`src/cli/core-api/`): базовый транспорт на `fetch` + `AbortSignal.timeout` и диагностика `/health` с полем `antigravity_sdk_configured` (`checkCoreHealth` в `http-client.ts`), регистрация проекта `POST /api/projects/register` и самоисцеление графа `POST /api/context/heal` (`registerProjectWithCore`, `healProjectContext` в `endpoints-projects.ts`), управление каталогом `GET/PATCH /api/harness/components` и кластерная консолидация `POST /api/harness/consolidate` (`fetchHarnessCatalog`, `patchGlobalComponentStatus`, `consolidateHarnessComponents` в `endpoints-harness.ts`), двусторонняя синхронизация `POST /api/sync/ingest` и `POST /api/sync/context` (`ingestWorkspaceComponents`, `syncWorkspaceContextNodes` в `endpoints-sync.ts`), CRUD пула провайдеров Tier 2 `/api/providers` (`endpoints-providers.ts`) и проксирование событий хуков `POST /api/hooks/{event}` (`postHookEventToCore` в `endpoints-hooks.ts`).
- 2026-10-26: В `src/cli/core-api/endpoints-harness.ts` расширен контракт ответа `GET /api/harness/components` (`fetchHarnessComponents`): добавлены метаданные `has_content`, `is_empty_stub`, `category`, `detectors` и список профилей агентов `agents`.
- 2026-10-27: В `src/cli/core-api/endpoints-providers.ts` расширен интерфейс `ProviderStatusItem` (`cooldown_seconds_remaining`, `api_key_configured`, `circuit_reason`) для синхронизации с пулом провайдеров Tier 2 и Embeddings.


