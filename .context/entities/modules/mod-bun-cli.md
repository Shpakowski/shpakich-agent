---
type: Entity
class: Module
id: mod-bun-cli
name: Dual-Runtime CLI-инжектор и оркестратор (Bun & Node.js)
status: Active
stack: [bun, nodejs, typescript]
files:
  - package.json
  - tsconfig.json
  - src/cli/index.ts
depends_on: []
implements: []
governed_by: []
---

# Dual-Runtime CLI-инжектор и оркестратор (`mod-bun-cli`)

Модуль реализует **Слой 1 (Host CLI Injector)** архитектуры `mcp-coop-agentic-factory`. Обеспечивает кросс-рантаймовый запуск (`bunx` и `npx`) без единой внешней runtime-зависимости (`Zero-Dependency`).

## Состав модуля
1. **`package.json`**: манифест ESM-пакета (`"type": "module"`) с бинарной точкой входа `./dist/index.js`, скриптами разработки на Bun (`bun run src/cli/index.ts`) и сборки под целевой таргет Node.js (`bun build --target node`).
2. **`tsconfig.json`**: строгая конфигурация TypeScript (`ES2022`, `moduleResolution: "bundler"`, `strict: true`, `verbatimModuleSyntax: true`) для проверки типов исходников в `src/cli/**/*.ts`.
3. **`src/cli/index.ts`**: точка входа CLI с универсальным shebang `#!/usr/bin/env node`, парсером аргументов на базе стандартного `node:util` (`parseArgs`) и маршрутизацией базовых команд (`init`, `sync`, `status`, `down`, `hook`) и флагов (`--help`, `--version`, `--agent`).

## Логи контекста (AI Traces)
- 2026-10-24: Создан каркас dual-runtime CLI (`package.json`, `tsconfig.json`, `src/cli/index.ts`) с нулевыми внешними зависимостями на базе `node:util.parseArgs` и `node:process`.

