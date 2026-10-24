---
type: Entity
class: Module
id: mod-bun-cli
name: Dual-Runtime CLI-инжектор и оркестратор (Bun & Node.js)
status: Active
stack: [bun, nodejs, typescript]
files:
  - package.json
  - package-lock.json
  - tsconfig.json
  - src/cli/index.ts
  - src/cli/detector.ts
  - src/cli/wizard.ts
depends_on: []
implements: []
governed_by: []
---

# Dual-Runtime CLI-инжектор и оркестратор (`mod-bun-cli`)

Модуль реализует **Слой 1 (Host CLI Injector)** архитектуры `mcp-coop-agentic-factory`. Обеспечивает кросс-рантаймовый запуск (`bunx` и `npx`), автоопределение стека и интерактивный TUI-визард на базе `@clack/prompts`.

## Состав модуля
1. **`package.json` / `package-lock.json`**: манифест ESM-пакета (`"type": "module"`) с бинарной точкой входа `./dist/index.js`, скриптами разработки (`node src/cli/index.ts`), сборки (`tsc`) и зависимостью `@clack/prompts` для интерактивного терминального UI.
2. **`tsconfig.json`**: строгая конфигурация TypeScript (`ES2022`, `moduleResolution: "NodeNext"`, `strict: true`, `verbatimModuleSyntax: true`, `rewriteRelativeImportExtensions: true`) для проверки типов, прямого запуска `.ts` в Node.js/Bun и сборки в `./dist` с автоперезаписью расширений `.ts` → `.js`.
3. **`src/cli/index.ts`**: точка входа CLI с универсальным shebang `#!/usr/bin/env node`, парсером аргументов на базе стандартного `node:util` (`parseArgs`) и маршрутизацией пользовательских команд (`init`, `sync`, `status`, `down`, `--help`) через TUI дизайн-систему `wizard.ts`, с сохранением чистого потока вывода для технической команды `hook`.
4. **`src/cli/detector.ts`**: декларативный детектор технологического стека проекта (`detectStackTags`, `getAllKnownStackTags`), времени обновления графа (`getContextGraphLastUpdated`) и целевого ИИ-агента (`resolveTargetAgent`, `inspectWorkspace`, `AGENT_DISPLAY_NAMES`, `AGENT_CONFIG_HINTS`) без внешних зависимостей (`node:fs`, `node:path`, `node:process`).
5. **`src/cli/wizard.ts`**: интерактивный TUI-визард инициализации (`runInitWizard`) и единая дизайн-система терминального вывода (`renderStatusCommand` с портами `8787 / 54329 / 63799`, `renderSyncCommand`, `renderDownCommand`, `renderHelpScreen`, `renderCliError`, `formatWorkspaceCard`) на базе `@clack/prompts` и `node:util.styleText`.

## Логи контекста (AI Traces)
- 2026-10-24: Создан каркас dual-runtime CLI (`package.json`, `tsconfig.json`, `src/cli/index.ts`) с нулевыми внешними зависимостями на базе `node:util.parseArgs` и `node:process`.
- 2026-10-24: Реализован модуль `src/cli/detector.ts` (Итерация 3.1) с табличным автоопределением тегов стека (`stackTags`) и каскадным резолвингом целевого ИИ-агента (`cli_flag` → `environment` → `workspace_marker` → `default_fallback`).
- 2026-10-24: Выполнена Итерация 3.2 — `inspectWorkspace`, `SUPPORTED_AGENTS` и `AUTO_AGENT_OPTION` интегрированы в `src/cli/index.ts` для команд `init`, `sync`, `status` и динамической справки `--help`.
- 2026-10-24: Реализован интерактивный TUI-визард `src/cli/wizard.ts` на базе `@clack/prompts` со стрелочным выбором ИИ-агента (`select`), чекбоксами технологических тегов (`multiselect`, `getAllKnownStackTags`) и поддержкой флага `-y, --yes`.
- 2026-10-24: Добавлено отображение времени последнего обновления `.context/index.md` (`contextGraphUpdatedAt`) и внедрена единая цветовая TUI дизайн-система (`@clack/prompts` + `node:util.styleText`) для всех команд CLI (`init`, `status`, `sync`, `down`, `--help`, ошибки).

