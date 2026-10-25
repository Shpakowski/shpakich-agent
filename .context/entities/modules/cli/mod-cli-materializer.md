---
type: Entity
class: Module
id: mod-cli-materializer
name: CLI Adapter File Materializer, Config Mergers & Offline Templates (Layer 3)
status: Active
stack: [nodejs, typescript]
files:
  - src/cli/materializer/manifest-store.ts
  - src/cli/materializer/mergers.ts
  - src/cli/materializer/file-writer.ts
  - src/cli/materializer/context-scaffold.ts
  - src/cli/materializer/offline-templates.ts
  - src/cli/materializer/index.ts
depends_on:
  - "[[mod-cli-config]]"
  - "[[mod-cli-common]]"
  - "[[mod-cli-detector]]"
  - "[[mod-cli-docker]]"
implements: []
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
---

# CLI Adapter File Materializer, Config Mergers & Offline Templates (`mod-cli-materializer`)

Доменный модуль **Layer 3** архитектуры `[[mod-cli]]`, отвечающий за материализацию скомпилированных артефактов ИИ-агентов на диск рабочего пространства, неразрушающее слияние конфигураций (`JSON`/`TOML`), генерацию каркаса `.context/index.md` и оффлайн-фолбэк.

## Состав подмодуля
1. **`src/cli/materializer/manifest-store.ts`**: управление профилем проекта в памяти и гидратация манифеста (`loadProjectHarnessConfig`, `saveProjectHarnessConfig`, `hydrateManifestFromCore`, `resolveEffectiveStackTags`).
2. **`src/cli/materializer/mergers.ts`**: идемпотентные алгоритмы глубокого слияния JSON-конфигов (`mergeJsonContent` для `hooks.json` и `mcp_config.json`) и секционного слияния TOML (`mergeTomlContent` для `.codex/config.toml`).
3. **`src/cli/materializer/file-writer.ts`**: движок записи файлов адаптеров (`materializeFiles`) с поддержкой стратегий `overwrite`, `merge_json`, `merge_toml`, `create_if_missing`, установкой прав `0o755` и очисткой устаревших управляемых файлов.
4. **`src/cli/materializer/context-scaffold.ts`**: идемпотентный генератор базовой структуры `.context/` и стартовой карты `.context/index.md` (`ensureContextGraphScaffold`).
5. **`src/cli/materializer/offline-templates.ts`**: генератор автономного набора файлов адаптеров (`buildOfflineFallbackFiles`) с инвариантными правилами и bootstrap-скиллом `harness-orchestrator` при недоступном Docker-ядре.
6. **`src/cli/materializer/index.ts`**: публичный фасад (`barrel`) материализатора.

## Логи контекста (AI Traces)
- 2026-10-26: Реализован конвейер материализации адаптеров для всех 5 ИИ-агентов (`src/cli/materializer/`): гидратация профиля проекта (`stack_tags`, `agent_type`, `profile_overrides`) из таблицы `projects` PostgreSQL через Docker-ядро и ведение `.harness-manifest.json` с `managed_files` и `context_base_hashes` (`loadProjectHarnessConfig`, `saveProjectHarnessConfig`, `resolveEffectiveStackTags` в `manifest-store.ts`), идемпотентное слияние JSON/TOML конфигов (`mergeJsonContent`, `mergeTomlContent` в `mergers.ts`), запись файлов с очисткой устаревших артефактов (`materializeFiles` в `file-writer.ts`), генерация каркаса `.context/index.md` (`ensureContextGraphScaffold` в `context-scaffold.ts`) и оффлайн-фолбэк с минимальным bootstrap-скиллом `harness-orchestrator` (`buildOfflineFallbackFiles` в `offline-templates.ts`).
