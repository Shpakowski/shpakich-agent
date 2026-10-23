#!/usr/bin/env node
/**
 * Слой 1 (Host CLI Injector): точка входа CLI-оркестратора `mcp-coop-agentic-factory`.
 * Отвечает за парсинг аргументов командной строки без внешних зависимостей (Zero-Dependency)
 * и маршрутизацию команд (`init`, `sync`, `status`, `down`, `hook`) между Bun (`bunx`) и Node.js (`npx`).
 */

import process from "node:process";
import { parseArgs } from "node:util";

const CLI_NAME = "mcp-coop-agentic-factory";
const CLI_VERSION = "0.1.0";

const SUPPORTED_COMMANDS = ["init", "sync", "status", "down", "hook"] as const;
type CliCommand = (typeof SUPPORTED_COMMANDS)[number];

interface ParsedCliContext {
  command?: CliCommand;
  subargs: string[];
  agent: string;
  help: boolean;
  version: boolean;
}

/**
 * Определяет активную среду выполнения (Bun или Node.js) для диагностики и логирования.
 * Использование глобального идентификатора через `globalThis` предотвращает ошибки типов
 * при строгой проверке в окружении без установленных деклараций `@types/bun`.
 */
function detectRuntime(): string {
  const globalScope = globalThis as Record<string, unknown>;
  if (typeof globalScope.Bun !== "undefined") {
    return "bun";
  }
  return "node";
}

/**
 * Выводит справочное руководство по командам и флагам CLI в стандартный поток вывода.
 */
function printHelp(): void {
  const helpText = `
${CLI_NAME} v${CLI_VERSION}
Lightweight dual-runtime (Bun & Node.js) CLI injector for MCP-COOP Agentic Factory.

Usage:
  ${CLI_NAME} <command> [options]

Commands:
  init      Initialize ~/.mcp-coop-agentic-factory/ global stack and inject workspace hooks (.context/, AGENTS.md)
  sync      Synchronize local workspace rules and context graph with the factory seed
  status    Check health status of global Docker containers and local context hooks
  down      Gracefully stop the global ~/.mcp-coop-agentic-factory/ Docker Compose stack
  hook      Execute an internal lifecycle hook handler (SessionStart, PostToolUse, Stop)

Options:
  -a, --agent <name>  Target AI coding agent (auto, antigravity, claude-code, cursor, codex, opencode) [default: "auto"]
  -h, --help          Show this help message and exit
  -v, --version       Show CLI version and runtime info
`.trim();

  process.stdout.write(`${helpText}\n`);
}

/**
 * Разбирает аргументы командной строки через встроенный `node:util.parseArgs`.
 * Выбран `parseArgs` из стандартной библиотеки Node.js, так как он нативно поддерживается
 * и в Node.js (>=20), и в Bun (>=1.1), обеспечивая нулевой размер зависимостей пакета.
 */
function parseCliArgs(rawArgs: string[]): ParsedCliContext {
  const { values, positionals } = parseArgs({
    args: rawArgs,
    options: {
      help: {
        type: "boolean",
        short: "h",
        default: false,
      },
      version: {
        type: "boolean",
        short: "v",
        default: false,
      },
      agent: {
        type: "string",
        short: "a",
        default: "auto",
      },
    },
    allowPositionals: true,
    strict: true,
  });

  const [rawCommand, ...subargs] = positionals;

  if (!rawCommand) {
    return {
      command: undefined,
      subargs: [],
      agent: values.agent ?? "auto",
      help: Boolean(values.help),
      version: Boolean(values.version),
    };
  }

  if (!SUPPORTED_COMMANDS.includes(rawCommand as CliCommand)) {
    throw new Error(
      `Unknown command "${rawCommand}". Supported commands: ${SUPPORTED_COMMANDS.join(", ")}.`,
    );
  }

  return {
    command: rawCommand as CliCommand,
    subargs,
    agent: values.agent ?? "auto",
    help: Boolean(values.help),
    version: Boolean(values.version),
  };
}

/**
 * Диспетчер команд верхнего уровня.
 * На текущем этапе (Step 1.2) реализует каркас маршрутизации; конкретные обработчики
 * каждого модуля подключаются по мере итеративной разработки следующих узлов плана.
 */
async function dispatchCommand(ctx: ParsedCliContext): Promise<void> {
  const runtime = detectRuntime();

  switch (ctx.command) {
    case "init":
      process.stdout.write(
        `[${CLI_NAME}] Initializing workspace (agent="${ctx.agent}", runtime="${runtime}")...\n`,
      );
      break;
    case "sync":
      process.stdout.write(
        `[${CLI_NAME}] Synchronizing context graph and rules (agent="${ctx.agent}", runtime="${runtime}")...\n`,
      );
      break;
    case "status":
      process.stdout.write(
        `[${CLI_NAME}] Checking factory stack and context status (runtime="${runtime}")...\n`,
      );
      break;
    case "down":
      process.stdout.write(
        `[${CLI_NAME}] Stopping global ~/.mcp-coop-agentic-factory/ Docker stack (runtime="${runtime}")...\n`,
      );
      break;
    case "hook": {
      const hookEvent = ctx.subargs[0] ?? "unknown";
      process.stdout.write(
        `[${CLI_NAME}] Executing hook event "${hookEvent}" (agent="${ctx.agent}", runtime="${runtime}")...\n`,
      );
      break;
    }
    default:
      printHelp();
      break;
  }
}

/**
 * Главная точка входа CLI: перехватывает синтаксические ошибки аргументов
 * и возвращает корректные POSIX-коды возврата (`0` — успех, `1` — ошибка вызова).
 */
async function main(): Promise<void> {
  try {
    const ctx = parseCliArgs(process.argv.slice(2));

    if (ctx.version) {
      process.stdout.write(`${CLI_NAME} v${CLI_VERSION} (${detectRuntime()})\n`);
      return;
    }

    if (ctx.help || !ctx.command) {
      printHelp();
      return;
    }

    await dispatchCommand(ctx);
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`[${CLI_NAME}] Error: ${message}\n`);
    process.stderr.write(`Run "${CLI_NAME} --help" for usage information.\n`);
    process.exitCode = 1;
  }
}

void main();

