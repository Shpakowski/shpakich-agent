---
type: Entity
class: Module
id: mod-cli-docker
name: CLI Global Docker Stack Manager (Layer 2)
status: Active
stack: [docker, nodejs, typescript]
files:
  - src/cli/docker/assets.ts
  - src/cli/docker/stack-status.ts
  - src/cli/docker/db-backup.ts
  - src/cli/docker/compose-runner.ts
  - src/cli/docker/index.ts
depends_on:
  - "[[mod-cli-config]]"
  - "[[mod-cli-common]]"
implements: []
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
---

# CLI Global Docker Stack Manager (`mod-cli-docker`)

Инфраструктурный модуль **Layer 2** архитектуры `[[mod-cli]]`, управляющий синхронизацией файлов и жизненным циклом глобального Docker Compose стека в домашней директории `~/.mcp-coop-agentic-factory/` (`[[mod-docker-stack]]`).

## Состав подмодуля
1. **`src/cli/docker/assets.ts`**: синхронизатор файлов глобального рантайма (`syncGlobalFactoryStackFiles`, `resolveGlobalFactoryDir`, `resolvePackageRoot`), копирующий `docker/Dockerfile`, `docker/docker-compose.yml`, `pyproject.toml`, `README.md`, `.dockerignore`, исходники `src/core` и создающий идемпотентный `.env` и универсальный лаунчер хуков `bin/run-hook.sh`.
2. **`src/cli/docker/stack-status.ts`**: инспектор доступности Docker CLI и демона (`checkDockerDaemonAvailable`), а также статуса контейнеров глобального стека (`inspectGlobalDockerContainers`).
3. **`src/cli/docker/db-backup.ts`**: менеджер слепков знаний PostgreSQL 18 (`createPostgresBackupSnapshot` через `pg_dump --exclude-table-data=hook_events` в `.backups/mcp-coop-agentic-factory-<timestamp>.sql` с ротацией последних 10 файлов `pruneOldBackupSnapshots`, инспектор пустоты БД `isPostgresKnowledgeEmpty`, список доступных слепков `listAvailableBackupSnapshots` и восстановление `restorePostgresSnapshotFile` / `restoreLatestPostgresBackupIfEmpty` со сбросом кэша Redis).
4. **`src/cli/docker/compose-runner.ts`**: оркестратор запуска (`ensureGlobalStackUp` с асинхронным стримингом этапов сборки `docker compose up -d --build` и поллингом `/health`) и остановки (`stopGlobalStackDown` с автоматическим созданием слепка знаний PostgreSQL перед остановкой контейнеров).
5. **`src/cli/docker/index.ts`**: публичный фасад (`barrel`) Docker-менеджера.

## Логи контекста (AI Traces)
- 2026-10-26: Реализован менеджер жизненного цикла глобального Docker-стека в `~/.mcp-coop-agentic-factory/` (`src/cli/docker/`): синхронизация контекста сборки (`docker/Dockerfile`, `docker/docker-compose.yml`, `pyproject.toml`, `README.md`, `.dockerignore`, `src/core`), генерация `.env` и POSIX-лаунчера `~/.mcp-coop-agentic-factory/bin/run-hook.sh` с автоопределением `bun` / `node` (`syncGlobalFactoryStackFiles` в `assets.ts`), диагностика Docker Engine и контейнеров (`checkDockerDaemonAvailable`, `inspectGlobalDockerContainers` в `stack-status.ts`), а также Fail-Open запуск `docker compose up -d --build` с асинхронным стримингом прогресса и поллингом `/health` (`ensureGlobalStackUp`, `stopGlobalStackDown` в `compose-runner.ts`).
- 2026-10-26: В `src/cli/docker/assets.ts` добавлена синхронизация директории `seeds/` (`seeds/harness-seed.json` и `seeds/prompts-config.json`) в `~/.mcp-coop-agentic-factory/seeds/` перед сборкой и запуском глобального Docker-стека.
- 2026-10-27: Добавлен модуль `src/cli/docker/db-backup.ts` для автоматического создания слепков знаний PostgreSQL 18 в `.backups/mcp-coop-agentic-factory-<timestamp>.sql` при `down` (с хранением последних 10 слепков и исключением таблицы `hook_events`) и восстановления выбранного/последнего слепка при `up` и `init`.

