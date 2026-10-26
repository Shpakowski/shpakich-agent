---
type: Entity
class: Module
id: mod-cli-detector
name: CLI Workspace Stack & Agent Detector (Layer 1)
status: Active
stack: [nodejs, typescript]
files:
  - src/cli/detector/stack-scanner.ts
  - src/cli/detector/context-inspector.ts
  - src/cli/detector/agent-resolver.ts
  - src/cli/detector/index.ts
depends_on:
  - "[[mod-cli-config]]"
  - "[[mod-cli-common]]"
implements: []
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
---

# CLI Workspace Stack & Agent Detector (`mod-cli-detector`)

Модуль **Layer 1** архитектуры `[[mod-cli]]`, отвечающий за неразрушающее инспектирование локального рабочего пространства разработчика: автоопределение технологического стека, чтение метаданных `.context/index.md` и каскадный резолвинг целевого ИИ-агента.

## Состав подмодуля
1. **`src/cli/detector/stack-scanner.ts`**: детерминированный сканер манифестов проекта (`package.json`, `pyproject.toml`, `requirements*.txt`, `Cargo.toml`, `go.mod`, `Dockerfile`, `docker-compose.yml`, `*.sol`) по правилам из `[[mod-cli-config]]` (`detectStackTags`, `getAllKnownStackTags`).
2. **`src/cli/detector/context-inspector.ts`**: инспектор состояния локального графа знаний `.context/index.md` (`getContextGraphLastUpdated`), извлекающий метку `updated_at` из YAML frontmatter.
3. **`src/cli/detector/agent-resolver.ts`**: каскадный резолвер целевого ИИ-агента (`resolveTargetAgent`: приоритет `cli_flag` → `environment` → `workspace_marker` → `default_fallback`) и агрегирующая функция `inspectWorkspace`.
4. **`src/cli/detector/index.ts`**: публичный фасад (`barrel`) детектора рабочего пространства.

## Логи контекста (AI Traces)
- 2026-10-26: Реализован детерминированный инспектор рабочего пространства (`src/cli/detector/`): табличное автоопределение тегов технологического стека (`detectStackTags`, `getAllKnownStackTags` в `stack-scanner.ts` по манифестам `package.json`, `pyproject.toml`, `requirements*.txt`, `Cargo.toml`, `go.mod`, `Dockerfile`, `docker-compose.yml`, `*.sol`), извлечение метки последнего обновления графа `.context/index.md` (`getContextGraphLastUpdated` в `context-inspector.ts`) и каскадный резолвинг целевого ИИ-агента (`resolveTargetAgent`, `inspectWorkspace` в `agent-resolver.ts` по цепочке `cli_flag` → `environment` → `workspace_marker` → `default_fallback`).
- 2026-10-26: В `src/cli/detector/stack-scanner.ts` и `src/cli/detector/agent-resolver.ts` добавлена поддержка динамических правил детекции стека из БД (`customRules: StackSignatureRules` в `detectStackTags` и `inspectWorkspace`), сканирование манифестов JVM (`pom.xml`, `build.gradle*`) и карта статусов компонентов каталога `getCatalogTagStatusMap(catalogComponents)`.

