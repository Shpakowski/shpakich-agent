---
type: Entity
class: Module
id: mod-cli-sync
name: CLI Bidirectional Component Scanner & 3-Way Context Graph Sync (Layer 3)
status: Active
stack: [nodejs, typescript]
files:
  - src/cli/sync/component-scanner.ts
  - src/cli/sync/context-sync.ts
  - src/cli/sync/sync-orchestrator.ts
  - src/cli/sync/index.ts
depends_on:
  - "[[mod-cli-config]]"
  - "[[mod-cli-common]]"
  - "[[mod-cli-detector]]"
  - "[[mod-cli-docker]]"
  - "[[mod-cli-core-api]]"
  - "[[mod-cli-materializer]]"
implements:
  - "[[contract-mcp-and-http-api]]"
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
---

# CLI Bidirectional Component Scanner & 3-Way Context Graph Sync (`mod-cli-sync`)

Доменный модуль **Layer 3** архитектуры `[[mod-cli]]`, реализующий клиентский контур двусторонней синхронизации знаний и сквозной конвейер материализации артефактов проекта.

## Состав подмодуля
1. **`src/cli/sync/component-scanner.ts`**: сканер пользовательских скиллов (`SKILL.md` + `scripts/*`) и правил (`*.md`, `*.mdc`) в директориях агентов (`scanCustomWorkspaceComponents`) с защитой `Anti-Self-Ingest` (`SYSTEM_BOOTSTRAP_SLUGS` и `manifest.managed_files`) и детерминированным извлечением тегов из `slug`.
2. **`src/cli/sync/context-sync.ts`**: сборщик локальных узлов `.context/` (`collectWorkspaceContextNodes`) и атомарный исполнитель серверных вердиктов 3-Way Hash Sync (`applyContextSyncResults` для статусов `pulled_from_db` и `merged`).
3. **`src/cli/sync/sync-orchestrator.ts`**: единый координатор синхронизации рабочего пространства (`syncWorkspaceArtifacts`, тип `WorkspaceSyncOutcome`), объединяющий инжест пользовательских компонентов, компиляцию/материализацию адаптеров, 3-Way Hash Sync `.context/` и вызов `healProjectContext` для `antigravity`.
4. **`src/cli/sync/index.ts`**: публичный фасад (`barrel`) модуля синхронизации.

## Логи контекста (AI Traces)
- 2026-10-26: Реализован хостовый контур двусторонней синхронизации (`src/cli/sync/`): сканирование пользовательских скиллов и правил (`scanCustomWorkspaceComponents` в `component-scanner.ts` с фильтром `Anti-Self-Ingest` по `SYSTEM_BOOTSTRAP_SLUGS` / `manifest.managed_files` и извлечением `stack_tags` из `slug`), клиентский движок 3-Way Hash Sync графа `.context/` с рекурсивным обходом поддиректорий `.context/entities/**` и атомарной записью при `pulled_from_db` / `merged` (`collectWorkspaceContextNodes`, `applyContextSyncResults` в `context-sync.ts`), а также сквозной оркестратор `syncWorkspaceArtifacts` (`sync-orchestrator.ts`) с вызовом `healProjectContext` для агента `antigravity`.
