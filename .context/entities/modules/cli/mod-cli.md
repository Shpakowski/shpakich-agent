---
type: Entity
class: Module
id: mod-cli
name: Host CLI Orchestrator & Modular Architecture
status: Active
stack: [nodejs, typescript]
files:
  - src/cli/index.ts
  - package.json
  - tsconfig.json
depends_on:
  - "[[mod-cli-commands]]"
  - "[[mod-cli-tui]]"
  - "[[mod-cli-sync]]"
  - "[[mod-cli-materializer]]"
  - "[[mod-cli-hooks]]"
  - "[[mod-cli-docker]]"
  - "[[mod-cli-core-api]]"
  - "[[mod-cli-detector]]"
  - "[[mod-cli-common]]"
  - "[[mod-cli-config]]"
implements:
  - "[[contract-lifecycle-hooks]]"
  - "[[contract-mcp-and-http-api]]"
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
  - "[[adr-003-isolated-antigravity-sdk-tier]]"
---

# Host CLI Orchestrator & Modular Architecture (`mod-cli`)

Корневой узел-агрегатор **Слоя 1 (Host CLI Injector)** архитектуры `mcp-coop-agentic-factory`. Обеспечивает рантайм-агностичный запуск (`npx` на Node.js 22+ Type Stripping и `bunx` на Bun) и координирует модульную иерархию из 10 специализированных доменных пакетов (`Layer 0..4`) по стандарту `Barrel per Entity`.

## 1. Многослойная топология пакетов (`Layer 0..4`) и правило `Barrel per Entity`

Для исключения монолитных God-файлов и циклических зависимостей хостовый CLI разделён на 5 направленных архитектурных слоёв (каждый доменный пакет экспортирует публичный контракт исключительно через свой `index.ts`):
- **Layer 0 — Zero-Hardcode Configuration (`[[mod-cli-config]]`)**: единый источник истины для идентификаторов агентов, портов, путей глобального рантайма, лимитов, таймаутов и сигнатур технологического стека.
- **Layer 1 — Shared Primitives & Workspace Detector (`[[mod-cli-common]]`, `[[mod-cli-detector]]`)**: базовые доменные контракты, атомарный файловый I/O, SHA-256 хеширование, парсинг YAML frontmatter, безопасный спавн процессов и детектор стека/агента рабочего пространства.
- **Layer 2 — Infrastructure & Core HTTP Adapters (`[[mod-cli-docker]]`, `[[mod-cli-core-api]]`)**: управление жизненным циклом глобального Docker Compose стека (`~/.mcp-coop-agentic-factory/`) и типизированный Fail-Open HTTP-клиент к REST API ядра `[[mod-python-core]]`.
- **Layer 3 — Domain Engines & TUI Presentation (`[[mod-cli-materializer]]`, `[[mod-cli-sync]]`, `[[mod-cli-hooks]]`, `[[mod-cli-tui]]`)**: материализация адаптеров и манифеста проекта, двусторонняя синхронизация пользовательских компонентов и 3-Way Hash Sync графа `.context/`, тонкий прокси жизненных хуков и терминальная дизайн-система на `@clack/prompts` + `node:util.styleText`.
- **Layer 4 — Command Controllers & Entrypoint (`[[mod-cli-commands]]`, `src/cli/index.ts`)**: парсер аргументов CLI, контроллеры 9 команд (`init`, `sync`, `harness`, `providers`, `status`, `ui`, `up`, `down`, `hook`) и тонкая точка входа `main()`.

## 2. Корневые артефакты модуля
1. **`src/cli/index.ts`**: тонкая точка входа (`#!/usr/bin/env node`), перехватывающая фатальные ошибки, делегирующая разбор `process.argv` в `parseCliArgs` (`[[mod-cli-commands]]`) и маршрутизирующая выполнение в соответствующий контроллер команды.
2. **`package.json`**: манифест ESM-пакета (`"type": "module"`) с бинарной точкой входа `./dist/index.js`, скриптами разработки и сборки (`tsc`) и единственной рантайм-зависимостью `@clack/prompts`.
3. **`tsconfig.json`**: строгая конфигурация TypeScript (`ES2022`, `moduleResolution: "NodeNext"`, `strict: true`, `verbatimModuleSyntax: true`, `rewriteRelativeImportExtensions: true`) для типобезопасной компиляции и нативного исполнения `.ts` файлов.

## Логи контекста (AI Traces)
- 2026-10-26: Реализован рантайм-агностичный оркестратор и точка входа хостового CLI (`src/cli/index.ts`, `package.json`, `tsconfig.json`) с универсальным shebang `#!/usr/bin/env node` для прямого исполнения через `npx` (Node.js Type Stripping) и `bunx`, строгой конфигурацией `rewriteRelativeImportExtensions: true`, публичным Barrel API над 10 доменными подпакетами (`Layer 0..4`) и маршрутизацией вызовов `runCli` к контроллерам 9 команд (`init`, `sync`, `harness`, `providers`, `status`, `ui`, `up`, `down`, `hook`).
- 2026-10-26: В `package.json` актуализирован скрипт `typecheck` (`ruff check src/core tests/unit` без удалённой директории `.agents/hooks`) и пересобран `dist/`.
