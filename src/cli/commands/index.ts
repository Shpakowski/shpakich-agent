/**
 * Слой 1 (Host CLI Commands): публичный фасад (Barrel) пакета `src/cli/commands/`.
 * Реэкспортирует парсер аргументов, обработчики вертикальных срезов команд
 * и единый диспетчер `dispatchCliCommand` по правилу `Barrel per Entity`.
 */

import process from "node:process";
import { renderHelpScreen } from "../tui/index.ts";
import {
  CLI_NAME,
  CLI_VERSION,
  type CliCommand,
  detectRuntime,
  HARNESS_SUBCOMMANDS,
  type HarnessSubcommand,
  type ParsedCliContext,
  PROVIDERS_SUBCOMMANDS,
  type ProvidersSubcommand,
  parseCliArgs,
  SUPPORTED_COMMANDS,
  UI_ENDPOINT,
} from "./arg-parser.ts";
import { handleHarnessCommand } from "./cmd-harness.ts";
import { handleHookSubcommand, resolveCanonicalHookEvent } from "./cmd-hook.ts";
import { handleInitCommand } from "./cmd-init.ts";
import {
  handleDownCommand,
  handleStatusCommand,
  handleUiCommand,
  handleUpCommand,
  launchSystemBrowser,
} from "./cmd-lifecycle.ts";
import {
  DEFAULT_PROVIDER_DIALECT,
  DEFAULT_PROVIDER_MODEL,
  DEFAULT_PROVIDER_PRIORITY,
  DEFAULT_PROVIDER_URL,
  handleProvidersCommand,
} from "./cmd-providers.ts";
import { handleSyncCommand } from "./cmd-sync.ts";

export {
  CLI_NAME,
  CLI_VERSION,
  type CliCommand,
  DEFAULT_PROVIDER_DIALECT,
  DEFAULT_PROVIDER_MODEL,
  DEFAULT_PROVIDER_PRIORITY,
  DEFAULT_PROVIDER_URL,
  detectRuntime,
  HARNESS_SUBCOMMANDS,
  type HarnessSubcommand,
  handleDownCommand,
  handleHarnessCommand,
  handleHookSubcommand,
  handleInitCommand,
  handleProvidersCommand,
  handleStatusCommand,
  handleSyncCommand,
  handleUiCommand,
  handleUpCommand,
  launchSystemBrowser,
  type ParsedCliContext,
  PROVIDERS_SUBCOMMANDS,
  type ProvidersSubcommand,
  parseCliArgs,
  resolveCanonicalHookEvent,
  SUPPORTED_COMMANDS,
  UI_ENDPOINT,
};

/**
 * Единый диспетчер CLI-команд верхнего уровня.
 * Маршрутизирует разобранный контекст `ParsedCliContext` в соответствующий
 * вертикальный обработчик команды и возвращает POSIX-код завершения (`0` или `1`).
 */
export async function dispatchCliCommand(
  ctx: ParsedCliContext,
  runtime: string = detectRuntime(),
): Promise<number> {
  switch (ctx.command) {
    case "init":
      await handleInitCommand(ctx, runtime);
      return typeof process.exitCode === "number" ? process.exitCode : 0;
    case "sync":
      await handleSyncCommand(ctx, runtime);
      return 0;
    case "harness":
      await handleHarnessCommand(ctx, runtime);
      return 0;
    case "providers":
      await handleProvidersCommand(ctx, runtime);
      return 0;
    case "status":
      await handleStatusCommand(ctx, runtime);
      return 0;
    case "ui":
      await handleUiCommand(ctx, runtime);
      return 0;
    case "up":
      await handleUpCommand(ctx, runtime);
      return 0;
    case "down":
      await handleDownCommand(ctx, runtime);
      return 0;
    case "hook":
      return await handleHookSubcommand(ctx);
    default:
      renderHelpScreen(CLI_NAME, CLI_VERSION, runtime);
      return 0;
  }
}

/**
 * Обратно-совместимая обёртка над `dispatchCliCommand`, определяющая среду выполнения автоматически.
 */
export async function dispatchCommand(ctx: ParsedCliContext): Promise<void> {
  await dispatchCliCommand(ctx, detectRuntime());
}
