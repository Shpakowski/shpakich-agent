---
type: Entity
class: Module
id: mod-cli-tui
name: CLI Terminal UI Design System, Progress Spinners & Views (Layer 3)
status: Active
stack: [nodejs, typescript]
files:
  - src/cli/tui/theme.ts
  - src/cli/tui/progress.ts
  - src/cli/tui/init-wizard.ts
  - src/cli/tui/views-init-sync.ts
  - src/cli/tui/views-catalog-providers.ts
  - src/cli/tui/views-lifecycle.ts
  - src/cli/tui/index.ts
depends_on:
  - "[[mod-cli-config]]"
  - "[[mod-cli-detector]]"
  - "[[mod-cli-docker]]"
  - "[[mod-cli-core-api]]"
implements: []
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
---

# CLI Terminal UI Design System, Progress Spinners & Views (`mod-cli-tui`)

Презентационный модуль **Layer 3** архитектуры `[[mod-cli]]`, реализующий интерактивный визард и единую терминальную дизайн-систему на базе `@clack/prompts` и `node:util.styleText`.

## Состав подмодуля
1. **`src/cli/tui/theme.ts`**: цветовая палитра, бейджи статусов, инвайт-баннер и карточка рабочего пространства (`formatWorkspaceCard`).
2. **`src/cli/tui/progress.ts`**: обёртка над интерактивным спиннером `@clack/prompts` (`createPipelineProgress`, интерфейс `PipelineProgress`) с безопасной деградацией в неинтерактивных (`CI` / `non-TTY`) терминалах.
3. **`src/cli/tui/init-wizard.ts`**: интерактивный визард `runInitWizard` для выбора целевого ИИ-агента (`select`) и технологических тегов стека (`multiselect`) с поддержкой режима `--yes`.
4. **`src/cli/tui/views-init-sync.ts`**: экраны итоговых отчётов `init` (`renderInitSummary`) и `sync` (`renderSyncSummary`, включая статистику инжеста компонентов, 3-Way Hash Sync `.context/` и `Context Heal`).
5. **`src/cli/tui/views-catalog-providers.ts`**: табличные и карточные представления каталога компонентов `Harness²` (`renderHarnessCatalogView`, `renderConsolidationReport`) и пула провайдеров второго контура (`renderProvidersView`).
6. **`src/cli/tui/views-lifecycle.ts`**: экраны команд `status` (`renderStatusCommand`), `ui` (`renderUiCommand`), `up` (`renderUpCommand`), `down` (`renderDownCommand`), справки (`renderHelpScreen`) и ошибок (`renderCliError`).
7. **`src/cli/tui/index.ts`**: публичный фасад (`barrel`) TUI-слоя.

## Логи контекста (AI Traces)
- 2026-10-26: Реализована единая терминальная дизайн-система на базе `@clack/prompts` и `node:util.styleText` (`src/cli/tui/`): карточка воркспейса с меткой обновления `.context/index.md` (`formatWorkspaceCard` в `theme.ts`), живой спиннер прогресса пайплайна (`createPipelineProgress` в `progress.ts`), интерактивный визард инициализации со стрелочным выбором ИИ-агента (`select`), чекбоксами стековых тегов (`multiselect`) и поддержкой `--yes` (`runInitWizard` в `init-wizard.ts`), отчёты `renderInitSummary` и `renderSyncSummary` со статистикой инжеста пользовательских компонентов (`created / updated / skipped`), 3-Way Hash Sync графа `.context/` и `Context Heal` (`views-init-sync.ts`), представления каталога `Harness²`, консолидации и пула провайдеров Tier 2 (`views-catalog-providers.ts`), а также экраны `renderStatusCommand`, `renderUiCommand`, `renderUpCommand`, `renderDownCommand`, `renderHelpScreen` и `renderCliError` (`views-lifecycle.ts`).
