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
  - src/cli/docker.ts
  - src/cli/materializer.ts
  - src/cli/hook_runner.ts
depends_on: []
implements: []
governed_by: []
---

# Dual-Runtime CLI-инжектор и оркестратор (`mod-bun-cli`)

Модуль реализует **Слой 1 (Host CLI Injector)** архитектуры `mcp-coop-agentic-factory`. Обеспечивает кросс-рантаймовый запуск (`bunx` и `npx`), автоопределение стека и интерактивный TUI-визард на базе `@clack/prompts`.

## Состав модуля
1. **`package.json` / `package-lock.json`**: манифест ESM-пакета (`"type": "module"`) с бинарной точкой входа `./dist/index.js`, скриптами разработки (`node src/cli/index.ts`), сборки (`tsc`) и зависимостью `@clack/prompts` для интерактивного терминального UI.
2. **`tsconfig.json`**: строгая конфигурация TypeScript (`ES2022`, `moduleResolution: "NodeNext"`, `strict: true`, `verbatimModuleSyntax: true`, `rewriteRelativeImportExtensions: true`) для проверки типов, прямого запуска `.ts` в Node.js/Bun и сборки в `./dist` с автоперезаписью расширений `.ts` → `.js`.
3. **`src/cli/index.ts`**: точка входа CLI с универсальным shebang `#!/usr/bin/env node`, парсером аргументов на базе стандартного `node:util` (`parseArgs`), синхронизатором артефактов `syncWorkspaceArtifacts` и маршрутизацией всех 7 команд (`init`, `sync`, `harness`, `providers`, `status`, `down`, `hook`).
4. **`src/cli/detector.ts`**: декларативный детектор технологического стека проекта (`detectStackTags`, `getAllKnownStackTags`), времени обновления графа (`getContextGraphLastUpdated`) и целевого ИИ-агента (`resolveTargetAgent`, `inspectWorkspace`, `AGENT_DISPLAY_NAMES`, `AGENT_CONFIG_HINTS`) без внешних зависимостей (`node:fs`, `node:path`, `node:process`).
5. **`src/cli/wizard.ts`**: интерактивный TUI-визард инициализации (`runInitWizard`) и единая дизайн-система терминального вывода (`renderInitSummary`, `renderSyncSummary`, `renderHarnessCatalogView`, `renderProvidersView`, `renderStatusCommand`, `renderDownCommand`, `renderHelpScreen`, `renderCliError`, `formatWorkspaceCard`) на базе `@clack/prompts` и `node:util.styleText`.
6. **`src/cli/docker.ts`**: менеджер жизненного цикла глобального Docker-стека (`~/.mcp-coop-agentic-factory/`, `syncGlobalFactoryStackFiles`, `ensureGlobalStackUp`, `stopGlobalStackDown`), проверка здоровья `/health` (`checkCoreHealth`) и типизированный HTTP-клиент к REST API ядра (`registerProjectWithCore`, `fetchHarnessCatalog`, `patchGlobalComponentStatus`, `listTier2Providers`, `upsertTier2Provider`, `patchTier2Provider`, `deleteTier2Provider`).
7. **`src/cli/materializer.ts`**: материализатор файлов адаптеров ИИ-агентов (`materializeFiles`, `mergeJsonContent`, `mergeTomlContent`), менеджер пер-проектного манифеста `.context/harness.json` (`loadProjectHarnessConfig`, `saveProjectHarnessConfig`, `resolveEffectiveStackTags`), генератор каркаса `.context/index.md` (`ensureContextGraphScaffold`) и офлайн-фолбэка (`buildOfflineFallbackFiles`).
8. **`src/cli/hook_runner.ts`**: тонкий локальный транспорт жизненных хуков (`executeHookCommand`, `readStdinJson`), не содержащий захардкоженной логики скриптов; проксирует события хуков в `POST /api/hooks/{event}`, где ядро отбирает и исполняет скрипты из таблицы `harness_components` PostgreSQL.

## Логи контекста (AI Traces)
- 2026-10-24: Создан каркас dual-runtime CLI (`package.json`, `tsconfig.json`, `src/cli/index.ts`) с нулевыми внешними зависимостями на базе `node:util.parseArgs` и `node:process`.
- 2026-10-24: Реализован модуль `src/cli/detector.ts` (Итерация 3.1) с табличным автоопределением тегов стека (`stackTags`) и каскадным резолвингом целевого ИИ-агента (`cli_flag` → `environment` → `workspace_marker` → `default_fallback`).
- 2026-10-24: Выполнена Итерация 3.2 — `inspectWorkspace`, `SUPPORTED_AGENTS` и `AUTO_AGENT_OPTION` интегрированы в `src/cli/index.ts` для команд `init`, `sync`, `status` и динамической справки `--help`.
- 2026-10-24: Реализован интерактивный TUI-визард `src/cli/wizard.ts` на базе `@clack/prompts` со стрелочным выбором ИИ-агента (`select`), чекбоксами технологических тегов (`multiselect`, `getAllKnownStackTags`) и поддержкой флага `-y, --yes`.
- 2026-10-24: Добавлено отображение времени последнего обновления `.context/index.md` (`contextGraphUpdatedAt`) и внедрена единая цветовая TUI дизайн-система (`@clack/prompts` + `node:util.styleText`) для всех команд CLI (`init`, `status`, `sync`, `down`, `--help`, ошибки).
- 2026-10-24: Реализован модуль `src/cli/docker.ts` (Итерация 8.2, часть B) для синхронизации файлов глобального Docker-стека в `~/.mcp-coop-agentic-factory/`, Fail-Open запуска `docker compose up -d` и взаимодействия с REST API ядра.
- 2026-10-24: Реализован модуль `src/cli/materializer.ts` (Итерация 8.2, часть A) для управления `.context/harness.json`, идемпотентного слияния JSON/TOML-конфигов, очистки устаревших управляемых файлов и офлайн-генерации адаптеров.
- 2026-10-24: Реализован модуль `src/cli/hook_runner.ts` (Итерация 8.3, часть A) с неблокирующим чтением `stdin`, проксированием хуков в ядро, синхронизацией `_syncFiles` и локальным фолбэком Контуров 2 и 3 для всех 5 диалектов ИИ-агентов.
- 2026-10-24: Завершена Итерация 8.3 (часть B) — в `src/cli/wizard.ts` и `src/cli/index.ts` подключены полные конвейеры команд `init`, `sync`, `harness` (`list`, `enable`, `disable`, `hook-enable`, `hook-disable`, `tag-add`, `tag-exclude`), `providers` (`list`, `add`, `enable`, `disable`, `priority`, `remove`, `reset`), `status`, `down` и `hook`.
- 2026-10-24: Выполнена Задача 8.4 — в `src/cli/docker.ts` и `src/cli/wizard.ts` внедрён асинхронный стриминг прогресса `docker compose up -d --build` с живым спиннером `@clack/prompts` (`createPipelineProgress`), добавлено копирование `README.md` и `.dockerignore` в контекст сборки и создан универсальный POSIX-лаунчер `~/.mcp-coop-agentic-factory/bin/run-hook.sh` с автоопределением `bun` / `node`.
- 2026-10-24: Устранён дублирующий файл `.context/harness.json` — все команды (`status`, `sync`, `harness`) теперь читают и гидрируют профиль проекта (`stack_tags`, `agent_type`, `profile_overrides`) напрямую из таблицы `projects` в PostgreSQL через Docker-ядро (`hydrateManifestFromCore`).
- 2026-10-24: Добавлена команда `up` (`mcp-coop-agentic-factory up`, `renderUpCommand`) для быстрого запуска глобального Docker-стека после `down` без повторного визарда `init`; команды `sync` и `harness` защищены от перезаписи файлов проекта дефолтным оффлайн-фолбэком при выключенном Docker-ядре.
- 2026-10-24: Выполнена полная декомпозиция `src/cli/hook_runner.ts` (удалены захардкоженные функции Контура 3 и временные файлы `/tmp/mcp-coop-session-*`), модуль переведён в режим чистого HTTP-прокси к `HookScriptDispatcher` в Docker.

