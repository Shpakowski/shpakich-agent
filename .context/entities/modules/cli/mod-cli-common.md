---
type: Entity
class: Module
id: mod-cli-common
name: CLI Shared Types, Atomic I/O, Hashing & Process Utilities (Layer 1)
status: Active
stack: [nodejs, typescript]
files:
  - src/cli/common/types.ts
  - src/cli/common/fs-atomic.ts
  - src/cli/common/hashing.ts
  - src/cli/common/frontmatter.ts
  - src/cli/common/process.ts
  - src/cli/common/index.ts
depends_on:
  - "[[mod-cli-config]]"
implements: []
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
---

# CLI Shared Types, Atomic I/O, Hashing & Process Utilities (`mod-cli-common`)

Фундаментальный модуль **Layer 1** архитектуры `[[mod-cli]]`, объединяющий сквозные TypeScript-интерфейсы и чистые инфраструктурные утилиты файловой системы, криптографии, разбора Markdown frontmatter и запуска дочерних процессов.

## Состав подмодуля
1. **`src/cli/common/types.ts`**: общие доменные контракты и DTO, разделяемые между всеми слоями CLI (`RenderedFileSpec`, `RenderedHarnessArtifact`, `ProjectHarnessOverrides`, `ProjectHarnessManifest`, `CoreHealthStatus`, `HarnessComponentSummary`, `Tier2ProviderView`, `WorkspaceComponentCandidate`, `ContextNodeSyncInput`, `ContextSyncReport` и др.).
2. **`src/cli/common/fs-atomic.ts`**: утилиты безопасного чтения файлов с ограничением размера (`readTextFileSafe`) и атомарной записи через временный файл `.tmp-*` с последующим `renameSync` (`writeFileAtomicSync`) для предотвращения повреждения конфигов при конкурентных вызовах хуков.
3. **`src/cli/common/hashing.ts`**: вычисление канонических SHA-256 дайджестов (`computeNormalizedContentHash`) с нормализацией переводов строк `CRLF -> LF` и краевых пробелов (используется в `Content-Hash Gate` и 3-Way Hash Sync).
4. **`src/cli/common/frontmatter.ts`**: легковесный парсер YAML frontmatter (`parseMarkdownFrontmatter`, `extractStackTagsFromFrontmatter`, `extractTagsFromSlug`) без тяжёлых внешних YAML-зависимостей.
5. **`src/cli/common/process.ts`**: кросс-платформенные обёртки запуска подпроцессов (`runSubprocessSync`, `openUrlInBrowser` для macOS, Linux и Windows).
6. **`src/cli/common/index.ts`**: публичный фасад (`barrel`) общих примитивов.

## Логи контекста (AI Traces)
- 2026-10-26: Реализованы сквозные доменные контракты и инфраструктурные примитивы хостового CLI (`src/cli/common/`): атомарная запись файлов через временный `.tmp-*` с `renameSync` (`writeFileAtomicSync`) и защитой от выхода за пределы рабочей директории (`resolveSafeWorkspacePath` в `fs-atomic.ts`), генерация канонических SHA-256 дайджестов с нормализацией `CRLF -> LF` (`computeNormalizedContentHash` в `hashing.ts`), легковесный парсинг YAML frontmatter и детерминированное извлечение `stack_tags` из имени файла (`extractTagsFromSlug` в `frontmatter.ts`), а также запуск подпроцессов и кросс-платформенное открытие URL визуализатора в браузере (`openUrlInBrowser` для macOS/Linux/Windows в `process.ts`).
