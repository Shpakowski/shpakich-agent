---
type: Entity
class: Module
id: mod-cli-hooks
name: CLI Lifecycle Hook Transport, Stdin Reader & Dialect Fallback (Layer 3)
status: Active
stack: [nodejs, typescript]
files:
  - src/cli/hooks/stdin-reader.ts
  - src/cli/hooks/dialect-translator.ts
  - src/cli/hooks/hook-executor.ts
  - src/cli/hooks/index.ts
depends_on:
  - "[[mod-cli-config]]"
  - "[[mod-cli-common]]"
  - "[[mod-cli-detector]]"
  - "[[mod-cli-core-api]]"
implements:
  - "[[contract-lifecycle-hooks]]"
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
---

# CLI Lifecycle Hook Transport, Stdin Reader & Dialect Fallback (`mod-cli-hooks`)

Доменный модуль **Layer 3** архитектуры `[[mod-cli]]`, реализующий тонкий Fail-Open мост перехвата жизненных хуков ИИ-агентов по контракту `[[contract-lifecycle-hooks]]`.

## Состав подмодуля
1. **`src/cli/hooks/stdin-reader.ts`**: неблокирующий асинхронный читатель JSON-контекста из `process.stdin` с защитным таймаутом (`readStdinJson`) и резолвер рабочей директории/идентификатора диалога из payload хука.
2. **`src/cli/hooks/dialect-translator.ts`**: генератор безопасных Fail-Open ответов (`buildFailOpenHookResponse`) для всех поддерживаемых протокольных диалектов (`ANTIGRAVITY_PROTOJSON`, `CLAUDE_CODEX_V1`, `CURSOR_V1`) на случай недоступности Docker-ядра.
3. **`src/cli/hooks/hook-executor.ts`**: исполнитель команды `hook <event>` (`executeHookCommand`), обогащающий `session-start` содержимым `.context/index.md`, проксирующий вызов через `postHookEventToCore` (`[[mod-cli-core-api]]`), атомарно применяющий `_syncFiles` и выводящий чистый JSON-ответ в `stdout`.
4. **`src/cli/hooks/index.ts`**: публичный фасад (`barrel`) модуля хуков.

## Логи контекста (AI Traces)
- 2026-10-26: Реализован тонкий локальный транспорт жизненных хуков к `HookScriptDispatcher` в Docker (`src/cli/hooks/`): неблокирующее чтение JSON-контекста из `stdin` с таймаутом (`readStdinJson` в `stdin-reader.ts`), генерация Fail-Open ответов для 3 протокольных диалектов (`ANTIGRAVITY_PROTOJSON`, `CLAUDE_CODEX_V1`, `CURSOR_V1` в `dialect-translator.ts`) с локальной инъекцией `.context/index.md` при оффлайне ядра, а также проксирование 5 событий (`session-start`, `pre-tool-use`, `post-tool-use`, `pre-invocation`, `stop`) в `POST /api/hooks/{event}` с атомарным применением `_syncFiles` (`executeHookCommand` в `hook-executor.ts`).
