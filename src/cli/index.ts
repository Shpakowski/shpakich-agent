#!/usr/bin/env node
/**
 * Слой 1 (Host CLI Injector): точка входа CLI-оркестратора `mcp-coop-agentic-factory`.
 * Отвечает за парсинг аргументов командной строки, инспекцию профиля рабочей папки (`detector.ts`),
 * управление глобальным Docker-стеком и REST API ядра (`docker.ts`), материализацию и очистку
 * файлов адаптеров (`materializer.ts`), выполнение жизненных хуков (`hook_runner.ts`) и TUI-вывод (`wizard.ts`).
 */

import path from "node:path";
import process from "node:process";
import { parseArgs } from "node:util";
import {
  AUTO_AGENT_OPTION,
  GLOBAL_STACK_TAG,
  inspectWorkspace,
  type AgentId,
  type WorkspaceProfile,
} from "./detector.ts";
import {
  checkCoreHealth,
  deleteTier2Provider,
  ensureGlobalStackUp,
  fetchHarnessCatalog,
  listTier2Providers,
  patchGlobalComponentStatus,
  patchTier2Provider,
  registerProjectWithCore,
  stopGlobalStackDown,
  upsertTier2Provider,
  type CatalogComponentItem,
} from "./docker.ts";
import {
  executeHookCommand,
  resolveCanonicalHookEvent,
} from "./hook_runner.ts";
import {
  HARNESS_MANIFEST_REL_PATH,
  buildOfflineFallbackFiles,
  buildProfileOverridesPayload,
  ensureContextGraphScaffold,
  loadProjectHarnessConfig,
  materializeFiles,
  resolveEffectiveStackTags,
  saveProjectHarnessConfig,
  type MaterializationReport,
  type ProjectHarnessManifest,
  type RenderedFilePayload,
} from "./materializer.ts";
import {
  renderCliError,
  renderDownCommand,
  renderHarnessCatalogView,
  renderHelpScreen,
  renderInitSummary,
  renderProvidersView,
  renderQuickMutationResult,
  renderStatusCommand,
  renderSyncSummary,
  runInitWizard,
} from "./wizard.ts";

export const CLI_NAME = "mcp-coop-agentic-factory" as const;
export const CLI_VERSION = "0.1.0" as const;

export const SUPPORTED_COMMANDS = [
  "init",
  "sync",
  "harness",
  "providers",
  "status",
  "down",
  "hook",
] as const;

export type CliCommand = (typeof SUPPORTED_COMMANDS)[number];

export const HARNESS_SUBCOMMANDS = [
  "list",
  "enable",
  "disable",
  "hook-enable",
  "hook-disable",
  "tag-add",
  "tag-exclude",
] as const;

export const PROVIDERS_SUBCOMMANDS = [
  "list",
  "add",
  "enable",
  "disable",
  "priority",
  "remove",
  "reset",
] as const;

const DEFAULT_PROVIDER_DIALECT = "openai_decisions" as const;
const DEFAULT_PROVIDER_URL = "https://api.openai.com/v1/decisions" as const;
const DEFAULT_PROVIDER_MODEL = "gpt-6-luna" as const;
const DEFAULT_PROVIDER_PRIORITY = 100 as const;
const OFFLINE_BUNDLE_SOURCE = "seed_fallback" as const;
const STEWARD_SKILL_SLUG = "context-graph-steward" as const;

export interface ParsedCliContext {
  command?: CliCommand;
  subargs: string[];
  agent: string;
  yes: boolean;
  help: boolean;
  version: boolean;
  all: boolean;
  global: boolean;
  typeFilter?: string;
  tagFilter?: string;
  dialect?: string;
  url?: string;
  model?: string;
  key?: string;
  priority?: string;
}

/**
 * Определяет активную среду выполнения (Bun или Node.js) для диагностики и логирования.
 */
export function detectRuntime(): string {
  const globalScope = globalThis as Record<string, unknown>;
  if (typeof globalScope.Bun !== "undefined") {
    return "bun";
  }
  return "node";
}

/**
 * Разбирает аргументы командной строки через встроенный `node:util.parseArgs`.
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
    typeFilter: typeof values.type === "string" ? values.type : undefined,
    tagFilter: typeof values.tag === "string" ? values.tag : undefined,
    dialect: typeof values.dialect === "string" ? values.dialect : undefined,
    url: typeof values.url === "string" ? values.url : undefined,
    model: typeof values.model === "string" ? values.model : undefined,
    key: typeof values.key === "string" ? values.key : undefined,
    priority: typeof values.priority === "string" ? values.priority : undefined,
  };
}

function resolveOfflineSkillPath(agent: AgentId, slug: string): string {
  if (agent === "antigravity") {
    return `.agents/plugins/${CLI_NAME}/skills/${slug}/SKILL.md`;
  }
  if (agent === "claude-code") {
    return `.claude/skills/${slug}/SKILL.md`;
  }
  return `.agents/skills/${slug}/SKILL.md`;
}

function buildOfflineCatalogItems(
  manifest: ProjectHarnessManifest,
): CatalogComponentItem[] {
  const effectiveTags = new Set(resolveEffectiveStackTags(manifest));
  const enabledSet = new Set(manifest.overrides.enabled_components);
  const disabledSet = new Set(manifest.overrides.disabled_components);
  const seeds: ReadonlyArray<{
    slug: string;
    component_type: string;
    scope: string;
    stack_tags: string[];
    title: string;
    description: string;
  }> = [
    {
      slug: "core-engineering-mentor",
      component_type: "system_prompt",
      scope: "global",
      stack_tags: [GLOBAL_STACK_TAG],
      title: "Core Engineering Mentor & Production Standards",
      description: "Learning-First workflow and KISS/DRY/SOLID/YAGNI standards.",
    },
    {
      slug: "block-destructive-commands",
      component_type: "guardrail",
      scope: "global",
      stack_tags: [GLOBAL_STACK_TAG],
      title: "Block Destructive Shell & Git Commands",
      description: "Blocks rm -rf /, force-push to main/master, and git reset --hard.",
    },
    {
      slug: "rewrite-npm-to-bun",
      component_type: "guardrail",
      scope: "domain",
      stack_tags: ["bun"],
      title: "Rewrite npm Package Commands to Bun",
      description: "Rewrites npm install/add/run/test to bun in Bun workspaces.",
    },
    {
      slug: "context-graph-discipline",
      component_type: "tool_guidance",
      scope: "global",
      stack_tags: [GLOBAL_STACK_TAG],
      title: "Context Graph (.context/) Exploration & Sync Discipline",
      description: "Guidance for .context/index.md and atomic turn synchronization.",
    },
    {
      slug: STEWARD_SKILL_SLUG,
      component_type: "skill",
      scope: "global",
      stack_tags: [GLOBAL_STACK_TAG],
      title: "Context Graph Steward",
      description: "Read, validate, and synchronize .context/ Graph-as-Code nodes.",
    },
  ];

  return seeds.map((item) => {
    const hasTag =
      item.scope === "global" ||
      item.stack_tags.some((t) => effectiveTags.has(t));
    const effective_state = disabledSet.has(item.slug)
      ? "disabled_project"
      : enabledSet.has(item.slug)
        ? "active_forced"
        : hasTag
          ? "active_auto"
          : "unmatched_stack";
    return {
      ...item,
      status: "composed",
      score: 1.0,
      version: 1,
      effective_state,
    };
  });
}

/**
 * Синхронизирует артефакты рабочей директории: создаёт каркас `.context/`, запрашивает скомпилированный
 * бандл у ядра (`registerProjectWithCore`) или генерирует автономный фолбэк, материализует файлы
 * с очисткой отключённых скиллов/хуков и сохраняет обновлённый `.context/harness.json`.
 */
export async function syncWorkspaceArtifacts(
  profile: WorkspaceProfile,
  manifest: ProjectHarnessManifest,
): Promise<{
  report: MaterializationReport;
  persistedInDb: boolean;
  source: string;
}> {
  const effectiveTags = resolveEffectiveStackTags(manifest);
  ensureContextGraphScaffold(
    profile.workspacePath,
    profile.projectName,
    effectiveTags,
  );

  const coreResponse = await registerProjectWithCore({
    workspace_path: profile.workspacePath,
    project_name: profile.projectName,
    agent_type: manifest.agent,
    stack_tags: effectiveTags,
    profile_overrides: buildProfileOverridesPayload(manifest),
  });

  const artifactObj =
    coreResponse && typeof coreResponse.artifact === "object"
      ? (coreResponse.artifact as Record<string, unknown>)
      : null;

  let files: RenderedFilePayload[];
  let source: string = OFFLINE_BUNDLE_SOURCE;
  const persistedInDb = Boolean(coreResponse?.persisted_in_db);

  if (artifactObj && Array.isArray(artifactObj.files)) {
    files = artifactObj.files as RenderedFilePayload[];
    source =
      typeof artifactObj.source === "string"
        ? artifactObj.source
        : "core_compiler";
  } else {
    files = [...buildOfflineFallbackFiles(manifest.agent, effectiveTags)];
    if (!manifest.overrides.disabled_components.includes(STEWARD_SKILL_SLUG)) {
      files.push({
        relative_path: resolveOfflineSkillPath(
          manifest.agent,
          STEWARD_SKILL_SLUG,
        ),
        content: [
          "---",
          `name: ${STEWARD_SKILL_SLUG}`,
          "description: >-",
          "  Read, validate, and synchronize .context/ Graph-as-Code knowledge nodes.",
          "---",
          "",
          "# Context Graph Steward (`context-graph-steward`)",
          "",
          "Keep `.context/index.md` and `.context/entities/` synchronized with repository files.",
          "",
        ].join("\n"),
        write_strategy: "overwrite",
      });
    }
  }

  const report = materializeFiles(profile.workspacePath, files, {
    disabledHooks: manifest.overrides.disabled_hooks,
    pruneFromManifest: manifest,
  });

  saveProjectHarnessConfig(profile.workspacePath, manifest);
  return { report, persistedInDb, source };
}

async function handleHarnessCommand(
  ctx: ParsedCliContext,
  runtime: string,
): Promise<void> {
  const subcmd = (ctx.subargs[0] ?? "list").trim().toLowerCase();
  const targetArg = (ctx.subargs[1] ?? "").trim().toLowerCase();
  const profile = inspectWorkspace(process.cwd(), ctx.agent);
  const manifest = loadProjectHarnessConfig(
    profile.workspacePath,
    profile.agent,
    profile.stackTags,
  );

  if (subcmd === "list") {
    const effectiveTags = resolveEffectiveStackTags(manifest);
    const catalogResp = await fetchHarnessCatalog({
      workspacePath: profile.workspacePath,
      stackTags: effectiveTags,
    });
    let items = catalogResp?.components ?? buildOfflineCatalogItems(manifest);
    if (ctx.typeFilter) {
      const wantedType = ctx.typeFilter.trim().toLowerCase();
      items = items.filter((i) => i.component_type.toLowerCase() === wantedType);
    }
    if (ctx.tagFilter) {
      const wantedTag = ctx.tagFilter.trim().toLowerCase();
      items = items.filter((i) =>
        i.stack_tags.some((t) => t.toLowerCase() === wantedTag),
      );
    }
    renderHarnessCatalogView({
      version: CLI_VERSION,
      runtime,
      items,
      manifest,
      showAll: ctx.all,
    });
    return;
  }

  if (!targetArg) {
    throw new Error(`Missing target argument for "harness ${subcmd}".`);
  }

  if ((subcmd === "enable" || subcmd === "disable") && ctx.global) {
    const enableFlag = subcmd === "enable";
    const patched = await patchGlobalComponentStatus(targetArg, enableFlag);
    if (!patched) {
      throw new Error(
        `Failed to update global component "${targetArg}" (Core offline or slug not found).`,
      );
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Global Harness² Update",
      `Global component "${targetArg}" ${enableFlag ? "enabled (composed)" : "disabled"} in PostgreSQL.`,
    );
    return;
  }
  if (subcmd === "enable") {
    manifest.overrides.disabled_components =
      manifest.overrides.disabled_components.filter((c) => c !== targetArg);
    if (!manifest.overrides.enabled_components.includes(targetArg)) {
      manifest.overrides.enabled_components.push(targetArg);
    }
  } else if (subcmd === "disable") {
    manifest.overrides.enabled_components =
      manifest.overrides.enabled_components.filter((c) => c !== targetArg);
    if (!manifest.overrides.disabled_components.includes(targetArg)) {
      manifest.overrides.disabled_components.push(targetArg);
    }
  } else if (subcmd === "hook-enable" || subcmd === "hook-disable") {
    const hookEvent = resolveCanonicalHookEvent(targetArg);
    if (!hookEvent) {
      throw new Error(`Unsupported hook event "${targetArg}".`);
    }
    manifest.overrides.disabled_hooks =
      manifest.overrides.disabled_hooks.filter((h) => h !== hookEvent);
    if (subcmd === "hook-disable") {
      manifest.overrides.disabled_hooks.push(hookEvent);
    }
  } else if (subcmd === "tag-add") {
    manifest.stack.excluded_tags = manifest.stack.excluded_tags.filter(
      (t) => t !== targetArg,
    );
    if (!manifest.stack.extra_tags.includes(targetArg)) {
      manifest.stack.extra_tags.push(targetArg);
    }
  } else if (subcmd === "tag-exclude") {
    if (targetArg === GLOBAL_STACK_TAG) {
      throw new Error(`Cannot exclude the mandatory "${GLOBAL_STACK_TAG}" tag.`);
    }
    manifest.stack.extra_tags = manifest.stack.extra_tags.filter(
      (t) => t !== targetArg,
    );
    if (!manifest.stack.excluded_tags.includes(targetArg)) {
      manifest.stack.excluded_tags.push(targetArg);
    }
  } else {
    throw new Error(
      `Unknown harness subcommand "${subcmd}". Supported: ${HARNESS_SUBCOMMANDS.join(", ")}.`,
    );
  }

  const { report, source } = await syncWorkspaceArtifacts(profile, manifest);
  renderSyncSummary({ profile, manifest, report, source });
}

async function handleProvidersCommand(
  ctx: ParsedCliContext,
  runtime: string,
): Promise<void> {
  const subcmd = (ctx.subargs[0] ?? "list").trim().toLowerCase();
  const providerName = (ctx.subargs[1] ?? "").trim();

  if (subcmd === "list") {
    const providers = (await listTier2Providers()) ?? [];
    renderProvidersView({ version: CLI_VERSION, runtime, providers });
    return;
  }

  if (!providerName) {
    throw new Error(`Missing provider name for "providers ${subcmd}".`);
  }

  if (subcmd === "add") {
    const created = await upsertTier2Provider({
      name: providerName,
      dialect: ctx.dialect ?? DEFAULT_PROVIDER_DIALECT,
      base_url: ctx.url ?? DEFAULT_PROVIDER_URL,
      model: ctx.model ?? DEFAULT_PROVIDER_MODEL,
      api_key: ctx.key ?? "",
      priority: Number(ctx.priority ?? DEFAULT_PROVIDER_PRIORITY),
      enabled: true,
    });
    if (!created) {
      throw new Error(`Failed to register Tier 2 provider "${providerName}".`);
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Tier 2 Provider Registered",
      `Provider "${providerName}" added/updated in RuntimeProviderPool.`,
    );
    return;
  }

  if (subcmd === "enable" || subcmd === "disable") {
    const enabled = subcmd === "enable";
    const res = await patchTier2Provider(providerName, { enabled });
    if (!res) {
      throw new Error(`Failed to ${subcmd} provider "${providerName}".`);
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Tier 2 Provider Updated",
      `Provider "${providerName}" ${enabled ? "enabled" : "disabled"}.`,
    );
    return;
  }

  if (subcmd === "priority") {
    const rawPrio = ctx.subargs[2] ?? ctx.priority;
    if (!rawPrio || Number.isNaN(Number(rawPrio))) {
      throw new Error("Numeric priority is required for `providers priority <name> <prio>`.");
    }
    const res = await patchTier2Provider(providerName, {
      priority: Number(rawPrio),
    });
    if (!res) {
      throw new Error(`Failed to update priority for "${providerName}".`);
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Tier 2 Priority Updated",
      `Provider "${providerName}" priority set to ${Number(rawPrio)}.`,
    );
    return;
  }

  if (subcmd === "reset") {
    const res = await patchTier2Provider(providerName, { reset_circuit: true });
    if (!res) {
      throw new Error(`Failed to reset Circuit Breaker for "${providerName}".`);
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Circuit Breaker Reset",
      `Provider "${providerName}" cooldown cleared.`,
    );
    return;
  }

  if (subcmd === "remove") {
    const ok = await deleteTier2Provider(providerName);
    if (!ok) {
      throw new Error(`Failed to remove provider "${providerName}".`);
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Tier 2 Provider Removed",
      `Provider "${providerName}" deleted from pool.`,
    );
    return;
  }

  throw new Error(
    `Unknown providers subcommand "${subcmd}". Supported: ${PROVIDERS_SUBCOMMANDS.join(", ")}.`,
  );
}
/**
 * Диспетчер команд верхнего уровня.
 * Пользовательские команды (`init`, `sync`, `harness`, `providers`, `status`, `down`) рендерятся
 * через единую TUI дизайн-систему `@clack/prompts`, тогда как техническая команда `hook` сохраняет
 * чистый поток `stdout` для JSON-протокола жизненных хуков IDE.
 */
export async function dispatchCommand(ctx: ParsedCliContext): Promise<void> {
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
      const manifest = loadProjectHarnessConfig(
        confirmedProfile.workspacePath,
        confirmedProfile.agent,
        confirmedProfile.stackTags,
      );
      manifest.agent = confirmedProfile.agent;
      manifest.stack.detected_tags = [...confirmedProfile.stackTags];

      const dockerResult = await ensureGlobalStackUp();
      const { report, persistedInDb } = await syncWorkspaceArtifacts(
        confirmedProfile,
        manifest,
      );
      renderInitSummary({
        profile: confirmedProfile,
        dockerResult,
        report,
        manifestPath: path.join(
          confirmedProfile.workspacePath,
          HARNESS_MANIFEST_REL_PATH,
        ),
        persistedInDb,
      });
      break;
    }
    case "sync": {
      const profile = inspectWorkspace(process.cwd(), ctx.agent);
      const manifest = loadProjectHarnessConfig(
        profile.workspacePath,
        profile.agent,
        profile.stackTags,
      );
      if (ctx.agent !== AUTO_AGENT_OPTION) {
        manifest.agent = profile.agent;
      }
      const { report, source } = await syncWorkspaceArtifacts(
        profile,
        manifest,
      );
      renderSyncSummary({ profile, manifest, report, source });
      break;
    }
    case "harness":
      await handleHarnessCommand(ctx, runtime);
      break;
    case "providers":
      await handleProvidersCommand(ctx, runtime);
      break;
    case "status": {
      const profile = inspectWorkspace(process.cwd(), ctx.agent);
      const manifest = loadProjectHarnessConfig(
        profile.workspacePath,
        profile.agent,
        profile.stackTags,
      );
      const [health, providers] = await Promise.all([
        checkCoreHealth(),
        listTier2Providers(),
      ]);
      renderStatusCommand(profile, CLI_VERSION, runtime, {
        health,
        providers,
        manifest,
      });
      break;
    }
    case "down": {
      const stopResult = stopGlobalStackDown();
      renderDownCommand(CLI_VERSION, runtime, stopResult);
      break;
    }
    case "hook": {
      await executeHookCommand(ctx.subargs[0] ?? "", ctx.agent);
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
