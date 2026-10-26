# SPEC-004: Тестовый контур (E2E Verification Suite & Integration Tests)

> **Статус:** Draft (В очереди на реализацию)  
> **Проект:** `mcp-coop-agentic-factory`  
> **Назначение:** Архитектура автоматизированного тестирования жизненных хуков (`hooks.json`), MCP-инструментов (`FastMCP`) и headless-сессий `google-antigravity` SDK через `pytest`.

---

## 1. Назначение и границы контура

Тестовый слой вынесен в отдельный этап, чтобы не блокировать разработку основного рантайма фабрики (`Core Engine + Bun CLI + Harness`). Контур отвечает за программную проверку трёх уровней системы:

1. **Unit & Contract Layer (`tests/unit/`):**
   - Детерминированная проверка `DecisionEngine` (Tier 1 Guardrails, `block_command`, `rewrite_command`, анти-зацикливание, валидация `implementation_plan.md`).
   - Проверка парсера и валидатора графа `.context/` (`ContextGraphService`, детекция битых `[[wiki-links]]` и фантомных файлов).
   - Проверка диалектов хуков (`HookDialect.ANTIGRAVITY_PROTOJSON`, `CLAUDE_CODEX_V1`, `CURSOR_V1`).
2. **Core API & MCP Integration Layer (`tests/integration/`):**
   - Тестирование эндпоинтов `FastAPI` (`/health`, `/api/projects/register`, `/api/hooks/*`, `/api/context/heal`) через `httpx.AsyncClient`.
   - Тестирование 8 инструментов `FastMCP` (`/mcp`).
3. **E2E Agent Probe Layer (`tests/e2e/`):**
   - Программный запуск изолированного `Agent(config)` из пакета `google-antigravity` через метод [`AntigravitySDKService.run_e2e_agent_probe`](../src/core/services/antigravity/service.py) во временной песочнице `/tmp/mcp_coop_agy_*`.
   - Проверка реального срабатывания `PreToolUse` (`deny` опасных команд и `overwrite` `npm -> bun`), `SessionStart`, `PreInvocation` и `Stop` на живом движке агента.

---

## 2. Критерии приёмки (`SPEC-004`)

1. Команда `pytest` проходит в полностью автономном режиме (без внешних API-ключей) для `unit` и `integration` слоёв.
2. E2E-тесты с реальным вызовом `google-antigravity` SDK автоматически помечаются маркером `@pytest.mark.e2e` и запускаются при наличии настроенной авторизации (`ANTIGRAVITY_API_KEY` или `ANTIGRAVITY_VERTEX=true`).
