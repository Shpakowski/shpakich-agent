#!/usr/bin/env node
/**
 * Слой 1 (Host CLI Injector): точка входа CLI-оркестратора `mcp-coop-agentic-factory`.
 * Отвечает за парсинг аргументов командной строки, инспекцию профиля рабочей папки (`detector.ts`),
 * запуск интерактивного TUI-визарда и стилизованных панелей (`wizard.ts`) и маршрутизацию команд
 * (`init`, `sync`, `status`, `down`, `hook`) между Bun (`bunx`) и Node.js (`npx`).
 */

import process from "node:process";
import { parseArgs } from "node:util";
import { AUTO_AGENT_OPTION, inspectWorkspace } from "./detector.ts";
import {
  renderCliError,
  renderDownCommand,
  renderHelpScreen,
  renderStatusCommand,
  renderSyncCommand,
  runInitWizard,
} from "./wizard.ts";

const CLI_NAME = "mcp-coop-agentic-factory";
const CLI_VERSION = "0.1.0";

const SUPPORTED_COMMANDS = ["init", "sync", "status", "down", "hook"] as const;
type CliCommand = (typeof SUPPORTED_COMMANDS)[number];

interface ParsedCliContext {
  command?: CliCommand;
  subargs: string[];
  agent: string;
  yes: boolean;
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
 * Разбирает аргументы командной строки через встроенный `node:util.parseArgs`.
 * Выбран `parseArgs` из стандартной библиотеки Node.js, так как он нативно поддерживается
 * и в Node.js (>=20), и в Bun (>=1.1).
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
      yes: {
        type: "boolean",
        short: "y",
        default: false,
      },
      agent: {
        type: "string",
        short: "a",
        default: AUTO_AGENT_OPTION,
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
      agent: values.agent ?? AUTO_AGENT_OPTION,
      yes: Boolean(values.yes),
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
    agent: values.agent ?? AUTO_AGENT_OPTION,
    yes: Boolean(values.yes),
    help: Boolean(values.help),
    version: Boolean(values.version),
  };
}

/**
 * Диспетчер команд верхнего уровня.
 * Пользовательские команды (`init`, `status`, `sync`, `down`) рендерятся через единую TUI дизайн-систему
 * `@clack/prompts`, тогда как техническая команда `hook` сохраняет чистый поток вывода для JSON-контракта IDE.
 */
async function dispatchCommand(ctx: ParsedCliContext): Promise<void> {
  const runtime = detectRuntime();

  switch (ctx.command) {
    case "init": {
      const profile = inspectWorkspace(process.cwd(), ctx.agent);
      const confirmedProfile = await runInitWizard(profile, {
        yes: ctx.yes,
        version: CLI_VERSION,
        runtime,
      });
      if (!confirmedProfile) {
        process.exitCode = 1;
        return;
      }
      break;
    }
    case "sync": {
      const profile = inspectWorkspace(process.cwd(), ctx.agent);
      renderSyncCommand(profile, CLI_VERSION, runtime);
      break;
    }
    case "status": {
      const profile = inspectWorkspace(process.cwd(), ctx.agent);
      renderStatusCommand(profile, CLI_VERSION, runtime);
      break;
    }
    case "down":
      renderDownCommand(CLI_VERSION, runtime);
      break;
    case "hook": {
      const hookEvent = ctx.subargs[0] ?? "unknown";
      process.stdout.write(
        `[${CLI_NAME}] Executing hook event "${hookEvent}" (agent="${ctx.agent}", runtime="${runtime}")...\n`,
      );
      break;
    }
    default:
      renderHelpScreen(CLI_NAME, CLI_VERSION, runtime);
      break;
  }
}

/**
 * Главная точка входа CLI: перехватывает синтаксические ошибки аргументов
 * и возвращает корректные POSIX-коды возврата (`0` — успех, `1` — ошибка вызова).
 */
async function main(): Promise<void> {
  const runtime = detectRuntime();
  try {
    const ctx = parseCliArgs(process.argv.slice(2));

    if (ctx.version) {
      process.stdout.write(`${CLI_NAME} v${CLI_VERSION} (${runtime})\n`);
      return;
    }

    if (ctx.help || !ctx.command) {
      renderHelpScreen(CLI_NAME, CLI_VERSION, runtime);
      return;
    }

    await dispatchCommand(ctx);
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    renderCliError(message, CLI_NAME, CLI_VERSION, runtime);
    process.exitCode = 1;
  }
}

void main();
