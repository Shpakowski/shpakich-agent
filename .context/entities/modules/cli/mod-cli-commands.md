---
type: Entity
class: Module
id: mod-cli-commands
name: CLI Argument Parser & Command Controllers (Layer 4)
status: Active
stack: [nodejs, typescript]
files:
  - src/cli/commands/arg-parser.ts
  - src/cli/commands/cmd-init.ts
  - src/cli/commands/cmd-sync.ts
  - src/cli/commands/cmd-harness.ts
  - src/cli/commands/cmd-providers.ts
  - src/cli/commands/cmd-lifecycle.ts
  - src/cli/commands/cmd-hook.ts
  - src/cli/commands/index.ts
depends_on:
  - "[[mod-cli-config]]"
  - "[[mod-cli-common]]"
  - "[[mod-cli-detector]]"
  - "[[mod-cli-docker]]"
  - "[[mod-cli-core-api]]"
  - "[[mod-cli-materializer]]"
  - "[[mod-cli-sync]]"
  - "[[mod-cli-hooks]]"
  - "[[mod-cli-tui]]"
implements:
  - "[[contract-lifecycle-hooks]]"
  - "[[contract-mcp-and-http-api]]"
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
---

# CLI Argument Parser & Command Controllers (`mod-cli-commands`)

Оркестрационный модуль **Layer 4** архитектуры `[[mod-cli]]`, связывающий разбор аргументов командной строки с доменными сервисами (`Layer 1..3`) и TUI-представлениями.

## Состав подмодуля
1. **`src/cli/commands/arg-parser.ts`**: строгий парсер CLI-аргументов и флагов на базе `node:util.parseArgs` (`parseCliArgs`, `CLI_COMMANDS`, `ParsedCliInvocation`).
2. **`src/cli/commands/cmd-init.ts`**: контроллер команды `init` (`handleInitCommand`), запускающий интерактивный визард, глобальный Docker-стек, первичную синхронизацию и рендеринг отчёта.
3. **`src/cli/commands/cmd-sync.ts`**: контроллер команды `sync` (`handleSyncCommand`) с защитой от перезаписи адаптеров дефолтным оффлайн-фолбэком при остановленном Docker-ядре.
4. **`src/cli/commands/cmd-harness.ts`**: контроллер команды `harness` (`handleHarnessCommand`) для подкоманд `list`, `enable`, `disable`, `hook-enable`, `hook-disable`, `tag-add`, `tag-exclude` и `consolidate [--dry-run]`.
5. **`src/cli/commands/cmd-providers.ts`**: контроллер команды `providers` (`handleProvidersCommand`) для подкоманд `list`, `add`, `enable`, `disable`, `priority`, `remove`, `reset`.
6. **`src/cli/commands/cmd-lifecycle.ts`**: контроллеры инфраструктурных команд `status` (`handleStatusCommand`), `ui` (`handleUiCommand`), `up` (`handleUpCommand`) и `down` (`handleDownCommand`).
7. **`src/cli/commands/cmd-hook.ts`**: контроллер команды `hook <event>` (`handleHookCommand`), делегирующий выполнение в `executeHookCommand` (`[[mod-cli-hooks]]`).
8. **`src/cli/commands/index.ts`**: публичный фасад (`barrel`) командного слоя.

## Логи контекста (AI Traces)
- 2026-10-26: Реализованы парсер CLI-аргументов на `node:util.parseArgs` (`parseCliArgs` в `arg-parser.ts`) и контроллеры всех 9 команд (`src/cli/commands/`): `handleInitCommand` (`cmd-init.ts`), `handleSyncCommand` с защитой от перезаписи адаптеров дефолтным оффлайн-фолбэком при выключенном Docker-ядре (`cmd-sync.ts`), `handleHarnessCommand` для подкоманд `list`, `enable`, `disable`, `hook-enable`, `hook-disable`, `tag-add`, `tag-exclude`, `consolidate [--dry-run]` (`cmd-harness.ts`), `handleProvidersCommand` для `list`, `add`, `enable`, `disable`, `priority`, `remove`, `reset` (`cmd-providers.ts`), инфраструктурные контроллеры `status`, `ui [--no-open]`, `up`, `down` (`cmd-lifecycle.ts`) и `handleHookCommand` (`cmd-hook.ts`).
- 2026-10-26: Пайплайн `handleInitCommand` (`src/cli/commands/cmd-init.ts`) переведён на архитектуру DB-First: сначала поднимается глобальный Docker-стек (`ensureGlobalStackUp`), затем из PostgreSQL запрашивается живой каталог компонентов и сигнатур детектора (`fetchHarnessComponents`), после чего выполняется `inspectWorkspace` и запускается `runInitWizard`.
- 2026-10-27: В `handleInitCommand` (`src/cli/commands/cmd-init.ts`) и `handleUpCommand` (`src/cli/commands/cmd-lifecycle.ts`) интегрирован первый шаг выбора и восстановления слепка БД из `.backups/` (`promptSnapshotRestoreSelection` + `restorePostgresSnapshotFile`) с защитой непустой БД в режиме `--yes`.
- 2026-10-27: В `src/cli/commands/cmd-providers.ts` добавлены пресеты `DIALECT_DEFAULTS` (с дефолтной моделью `models/gemini-embedding-2` для `gemini_native`) для регистрации провайдеров эмбеддингов (`gemini_native`, `openai_compatible`) и решений (`openai_decisions`, `jev_clef`, `openai_chat_schema`) одной командой `providers add <name> --dialect <d> --key <k>`.


