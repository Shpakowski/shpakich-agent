#!/usr/bin/env node
/**
 * Слой 1 (Host CLI Injector — Composition Root): тонкий оркестратор `mcp-coop-agentic-factory`.
 * Связывает парсер аргументов и вертикальные срезы команд (`./commands/index.ts`) с TUI-выводом
 * (`./tui/index.ts`), обрабатывает ошибки верхнего уровня и реэкспортирует публичные контракты пакетов CLI.
 */

import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import {
  CLI_NAME,
  CLI_VERSION,
  detectRuntime,
  dispatchCliCommand,
  parseCliArgs,
} from "./commands/index.ts";
import { renderCliError, renderHelpScreen } from "./tui/index.ts";

export * from "./commands/index.ts";
export * from "./common/index.ts";
export * from "./config/index.ts";
export * from "./core-api/index.ts";
export * from "./detector/index.ts";
export * from "./docker/index.ts";
export * from "./hooks/index.ts";
export * from "./materializer/index.ts";
export * from "./sync/index.ts";
export * from "./tui/index.ts";

export {
  computeNormalizedContentHash,
  extractFrontmatterScalar,
  extractTagsFromSlug,
} from "./common/index.ts";
export {
  AGENT_CONFIG_HINTS,
  AGENT_DISPLAY_NAMES,
  AGENT_TYPES,
  type AgentType,
  CLI_VERSION,
  DEFAULT_CORE_BASE_URL,
  GLOBAL_FACTORY_DIR_NAME,
  isValidAgentType,
  resolveCoreBaseUrl,
  SUPPORTED_AGENTS_LABEL,
} from "./config/index.ts";
export {
  type ComponentIngestItemPayload,
  type ComponentIngestReportPayload,
  type ConsolidationClusterPayload,
  type ConsolidationReportPayload,
  type ContextNodeSyncInputPayload,
  type ContextNodeSyncItemPayload,
  type ContextSyncReportPayload,
  type CoreHealthStatus,
  checkCoreHealth,
  HEALTH_ENDPOINT,
  type WorkspaceComponentCandidatePayload,
} from "./core-api/index.ts";
export {
  type HookEventName,
  isHookEventName,
  resolveCanonicalHookEvent,
} from "./hooks/index.ts";
export { resolveEffectiveStackTags } from "./materializer/index.ts";

/**
 * Исполняет полный цикл вызова CLI-инжектора для переданных аргументов командной строки
 * и возвращает POSIX-код завершения (`0` — успех, `1` — ошибка).
 */
export async function runCli(
  rawArgs: string[] = process.argv.slice(2),
): Promise<number> {
  const runtime = detectRuntime();
  try {
    const ctx = parseCliArgs(rawArgs);

    if (ctx.version) {
      process.stdout.write(`${CLI_NAME} v${CLI_VERSION} (${runtime})\n`);
      return 0;
    }

    if (ctx.help || !ctx.command) {
      renderHelpScreen(CLI_NAME, CLI_VERSION, runtime);
      return 0;
    }

    const exitCode = await dispatchCliCommand(ctx, runtime);
    if (exitCode !== 0) {
      process.exitCode = exitCode;
    }
    return exitCode;
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    renderCliError(message, CLI_NAME, CLI_VERSION, runtime);
    process.exitCode = 1;
    return 1;
  }
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
const currentModulePath = path.resolve(fileURLToPath(import.meta.url));
const isMainModule =
  invokedPath === currentModulePath ||
  invokedPath.endsWith(`/${CLI_NAME}`) ||
  invokedPath.endsWith(`\\${CLI_NAME}`);

if (isMainModule) {
  void runCli();
}
