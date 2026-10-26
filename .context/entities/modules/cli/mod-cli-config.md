---
type: Entity
class: Module
id: mod-cli-config
name: CLI Zero-Hardcode Configuration & Stack Signatures (Layer 0)
status: Active
stack: [nodejs, typescript]
files:
  - src/cli/config/agents.ts
  - src/cli/config/ports-and-paths.ts
  - src/cli/config/limits-and-timeouts.ts
  - src/cli/config/stack-signatures.ts
  - src/cli/config/index.ts
depends_on: []
implements: []
governed_by:
  - "[[adr-001-clean-host-global-docker]]"
  - "[[adr-002-harness2-evolution-loop]]"
---

# CLI Zero-Hardcode Configuration & Stack Signatures (`mod-cli-config`)

Базовый модуль **Layer 0** архитектуры `[[mod-cli]]`, реализующий политику `Zero-Hardcode` (`Single Source of Truth`). Не имеет внутренних и внешних зависимостей и снабжает все вышестоящие слои CLI неизменяемыми константами (`as const`), типами объединений и декларативными сигнатурами.

## Состав подмодуля
1. **`src/cli/config/agents.ts`**: канонический реестр поддерживаемых ИИ-агентов (`SUPPORTED_AGENTS`, тип `SupportedAgent`), опция автоопределения `AUTO_AGENT_OPTION`, человекочитаемые названия `AGENT_DISPLAY_NAMES`, подсказки конфигурационных путей `AGENT_CONFIG_HINTS`, сигнатуры окружения и маркеров воркспейса, а также таблицы путей сканирования пользовательских правил и скиллов (`AGENT_CUSTOM_COMPONENT_SCAN_PATHS`).
2. **`src/cli/config/ports-and-paths.ts`**: сетевые порты и URL глобального ядра (`DEFAULT_CORE_PORT = 8787`, `DEFAULT_CORE_BASE_URL`, `DEFAULT_MCP_URL`, порты PostgreSQL `54329` и Redis `63799`), пути глобальной директории `~/.mcp-coop-agentic-factory/`, шаблон `.env` (`DEFAULT_ENV_TEMPLATE`) и POSIX-скрипт `RUN_HOOK_SH_SCRIPT`.
3. **`src/cli/config/limits-and-timeouts.ts`**: централизованные таймауты HTTP-запросов, ожидания готовности Docker-стека, чтения `stdin` в хуках, лимиты размера инспектируемых файлов (`MAX_FILE_READ_BYTES`) и константы фильтра `SYSTEM_BOOTSTRAP_SLUGS`.
4. **`src/cli/config/stack-signatures.ts`**: декларативная база правил детекции технологического стека (`PACKAGE_JSON_TAG_RULES`, `PYTHON_DEPENDENCY_TAG_RULES`, `RUST_DEPENDENCY_TAG_RULES`, `GO_MODULE_TAG_RULES`, `SPECIAL_FILE_TAG_RULES`) и правила нормализации слагов.
5. **`src/cli/config/index.ts`**: публичный фасад (`barrel`) модуля конфигурации.

## Логи контекста (AI Traces)
- 2026-10-26: Сформирован единый `Zero-Hardcode` реестр конфигурации хостового CLI (`src/cli/config/`): декларативные дескрипторы 5 ИИ-агентов (`SUPPORTED_AGENTS`, `AGENT_DISPLAY_NAMES`, `AGENT_CONFIG_HINTS`, маркеры окружения и пути сканирования кастомных правил/скиллов в `agents.ts`), сетевые порты и пути глобального рантайма `~/.mcp-coop-agentic-factory/` с шаблоном `DEFAULT_ENV_TEMPLATE` (`GEMINI_API_KEY` / `ANTIGRAVITY_API_KEY`) и универсальным POSIX-лаунчером `bin/run-hook.sh` (`ports-and-paths.ts`), таймауты HTTP/Docker/stdin и фильтр `SYSTEM_BOOTSTRAP_SLUGS` (`limits-and-timeouts.ts`), а также табличные сигнатуры автоопределения технологического стека (`stack-signatures.ts`).
- 2026-10-26: В `src/cli/config/ports-and-paths.ts` константа `OFFLINE_ORCHESTRATOR_SLUG` переведена на единый слаг `"coop-orchestrator"`.
