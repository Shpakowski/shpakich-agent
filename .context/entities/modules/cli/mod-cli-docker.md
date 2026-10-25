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
3. **`src/cli/docker/compose-runner.ts`**: оркестратор запуска (`ensureGlobalStackUp` с асинхронным стримингом этапов сборки `docker compose up -d --build` и поллингом `/health`) и остановки (`stopGlobalStackDown` с сохранением named volumes PostgreSQL и Redis).
4. **`src/cli/docker/index.ts`**: публичный фасад (`barrel`) Docker-менеджера.

## Логи контекста (AI Traces)
- 2026-10-26: Реализован менеджер жизненного цикла глобального Docker-стека в `~/.mcp-coop-agentic-factory/` (`src/cli/docker/`): синхронизация контекста сборки (`docker/Dockerfile`, `docker/docker-compose.yml`, `pyproject.toml`, `README.md`, `.dockerignore`, `src/core`), генерация `.env` и POSIX-лаунчера `~/.mcp-coop-agentic-factory/bin/run-hook.sh` с автоопределением `bun` / `node` (`syncGlobalFactoryStackFiles` в `assets.ts`), диагностика Docker Engine и контейнеров (`checkDockerDaemonAvailable`, `inspectGlobalDockerContainers` в `stack-status.ts`), а также Fail-Open запуск `docker compose up -d --build` с асинхронным стримингом прогресса и поллингом `/health` (`ensureGlobalStackUp`, `stopGlobalStackDown` в `compose-runner.ts`).
