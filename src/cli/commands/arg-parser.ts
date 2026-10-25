/**
 * Слой 1 (Host CLI Commands — Argument Parser): разбор позиционных команд и флагов CLI.
 * Определяет поддерживаемые команды, подкоманды `harness` и `providers`, детектор среды
 * выполнения (`bun` / `node`) и типизированный парсер аргументов поверх `node:util.parseArgs`.
 */

import { parseArgs } from "node:util";
import { CLI_VERSION } from "../config/index.ts";
import { AUTO_AGENT_OPTION } from "../detector/index.ts";

export { CLI_VERSION };

export const CLI_NAME = "mcp-coop-agentic-factory" as const;

export const SUPPORTED_COMMANDS = [
  "init",
  "sync",
  "harness",
  "providers",
  "status",
  "ui",
  "up",
  "down",
  "hook",
] as const;

export type CliCommand = (typeof SUPPORTED_COMMANDS)[number];

export const UI_ENDPOINT = "/ui" as const;

export const HARNESS_SUBCOMMANDS = [
  "list",
  "enable",
  "disable",
  "hook-enable",
  "hook-disable",
  "tag-add",
  "tag-exclude",
  "consolidate",
] as const;

export type HarnessSubcommand = (typeof HARNESS_SUBCOMMANDS)[number];

export const PROVIDERS_SUBCOMMANDS = [
  "list",
  "add",
  "enable",
  "disable",
  "priority",
  "remove",
  "reset",
] as const;

export type ProvidersSubcommand = (typeof PROVIDERS_SUBCOMMANDS)[number];

export interface ParsedCliContext {
  command?: CliCommand;
  subargs: string[];
  agent: string;
  yes: boolean;
  help: boolean;
  version: boolean;
  all: boolean;
  global: boolean;
  dryRun: boolean;
  noOpen: boolean;
  typeFilter?: string;
  tagFilter?: string;
  dialect?: string;
  url?: string;
  model?: string;
  key?: string;
  priority?: string;
}

/**
 * Определяет активную среду выполнения (`bun` или `node`) для диагностики и баннеров TUI.
 */
export function detectRuntime(): string {
  const globalScope = globalThis as Record<string, unknown>;
  if (typeof globalScope.Bun !== "undefined") {
    return "bun";
  }
  return "node";
}

/**
 * Разбирает сырые аргументы командной строки через встроенный `node:util.parseArgs`
 * и валидирует имя корневой команды против `SUPPORTED_COMMANDS`.
 */
export function parseCliArgs(rawArgs: string[]): ParsedCliContext {
  const { values, positionals } = parseArgs({
    args: rawArgs,
    options: {
      help: { type: "boolean", short: "h", default: false },
      version: { type: "boolean", short: "v", default: false },
      yes: { type: "boolean", short: "y", default: false },
      agent: { type: "string", short: "a", default: AUTO_AGENT_OPTION },
      all: { type: "boolean", default: false },
      global: { type: "boolean", short: "g", default: false },
      "dry-run": { type: "boolean", default: false },
      "no-open": { type: "boolean", default: false },
      type: { type: "string", short: "t" },
      tag: { type: "string" },
      dialect: { type: "string" },
      url: { type: "string" },
      model: { type: "string" },
      key: { type: "string" },
      priority: { type: "string" },
    },
    allowPositionals: true,
    strict: true,
  });

  const [rawCommand, ...subargs] = positionals;
  if (rawCommand && !SUPPORTED_COMMANDS.includes(rawCommand as CliCommand)) {
    throw new Error(
      `Unknown command "${rawCommand}". Supported commands: ${SUPPORTED_COMMANDS.join(", ")}.`,
    );
  }

  return {
    command: rawCommand ? (rawCommand as CliCommand) : undefined,
    subargs,
    agent: values.agent ?? AUTO_AGENT_OPTION,
    yes: Boolean(values.yes),
    help: Boolean(values.help),
    version: Boolean(values.version),
    all: Boolean(values.all),
    global: Boolean(values.global),
    dryRun: Boolean(values["dry-run"]),
    noOpen: Boolean(values["no-open"]),
    typeFilter: typeof values.type === "string" ? values.type : undefined,
    tagFilter: typeof values.tag === "string" ? values.tag : undefined,
    dialect: typeof values.dialect === "string" ? values.dialect : undefined,
    url: typeof values.url === "string" ? values.url : undefined,
    model: typeof values.model === "string" ? values.model : undefined,
    key: typeof values.key === "string" ? values.key : undefined,
    priority: typeof values.priority === "string" ? values.priority : undefined,
  };
}
