/**
 * Слой 1 (Host CLI Injector): интерактивный TUI-визард и дизайн-система терминального вывода CLI.
 * Реализует единый визуальный стиль команд (`init`, `sync`, `harness`, `providers`, `status`, `down`, `--help`)
 * на базе `@clack/prompts` и встроенного ANSI-форматтера `node:util.styleText`. Поддерживает стрелочную навигацию
 * (`select`), чекбоксы технологических тегов (`multiselect`) и автоматический переход в безблокирующий режим при `--yes` или отсутствии TTY.
 */

import process from "node:process";
import { styleText } from "node:util";
import {
  cancel,
  intro,
  isCancel,
  log,
  multiselect,
  note,
  outro,
  select,
  spinner,
} from "@clack/prompts";
import {
  AGENT_CONFIG_HINTS,
  AGENT_DISPLAY_NAMES,
  AUTO_AGENT_OPTION,
  CONTEXT_GRAPH_INDEX_REL_PATH,
  GLOBAL_STACK_TAG,
  SUPPORTED_AGENTS,
  getAllKnownStackTags,
  type AgentId,
  type WorkspaceProfile,
} from "./detector.ts";
import type {
  CatalogComponentItem,
  ContextHealResponse,
  CoreHealthStatus,
  DockerBootstrapResult,
  ProviderStatusItem,
} from "./docker.ts";
import {
  ALL_HOOK_EVENTS,
  HARNESS_MANIFEST_REL_PATH,
  resolveEffectiveStackTags,
  type MaterializationReport,
  type ProjectHarnessManifest,
} from "./materializer.ts";
import type {
  ComponentIngestReportPayload,
  ConsolidationReportPayload,
  ContextSyncReportPayload,
} from "./workspace_sync.ts";

export interface InitWizardOptions {
  yes?: boolean;
  version?: string;
  runtime?: string;
}

const DEFAULT_CLI_VERSION = "0.1.0" as const;
const DEFAULT_RUNTIME_LABEL = "node" as const;
const DEFAULT_LOOPBACK_HOST = "127.0.0.1" as const;
const DEFAULT_CORE_HOST_PORT = 8787 as const;
const DEFAULT_POSTGRES_HOST_PORT = 54329 as const;
const DEFAULT_REDIS_HOST_PORT = 63799 as const;
const STACK_TAGS_PER_ROW = 6 as const;
const CARD_VALUE_INDENT = " ".repeat(16);

const WIZARD_CANCEL_MESSAGE = "Initialization cancelled by user." as const;
const INIT_LOCKED_OUTRO_MESSAGE =
  "✔ Configuration locked — starting factory initialization pipeline..." as const;

const EFFECTIVE_STATE_ACTIVE_AUTO = "active_auto" as const;
const EFFECTIVE_STATE_ACTIVE_FORCED = "active_forced" as const;
const EFFECTIVE_STATE_DISABLED_PROJECT = "disabled_project" as const;
const EFFECTIVE_STATE_DISABLED_GLOBAL = "disabled_global" as const;
const EFFECTIVE_STATE_UNMATCHED_STACK = "unmatched_stack" as const;

const PROVIDER_STATUS_HEALTHY = "healthy" as const;
const PROVIDER_STATUS_COOLDOWN = "cooldown" as const;
const PROVIDER_STATUS_DISABLED = "disabled" as const;

/**
 * Формирует фирменный заголовок шапки CLI с контрастным пилюльным бейджем, версией, рантаймом и названием операции.
 */
export function renderCliBanner(
  version: string = DEFAULT_CLI_VERSION,
  runtime: string = DEFAULT_RUNTIME_LABEL,
  actionTitle: string,
): string {
  const pill = styleText(["bgCyan", "black", "bold"], " MCP-COOP ");
  const brand = styleText(["bold", "white"], "Agentic Factory");
  const meta = styleText("dim", `v${version} • ${runtime}`);
  const separator = styleText("dim", "—");
  const subtitle = styleText(["bold", "cyan"], actionTitle);

  return `${pill} ${brand} ${meta} ${separator} ${subtitle}`;
}

/**
 * Формирует читаемый бейдж источника выбора ИИ-агента для карточки профиля и подсказок меню.
 */
function formatAgentSourceBadge(profile: WorkspaceProfile): string {
  switch (profile.agentSource) {
    case "cli_flag":
      return styleText("cyan", "● preselected via --agent");
    case "environment":
      return styleText(
        "cyan",
        profile.matchedAgentMarker
          ? `● env (${profile.matchedAgentMarker})`
          : "● configured via env",
      );
    case "workspace_marker":
      return styleText(
        "green",
        profile.matchedAgentMarker
          ? `● auto-detected (${profile.matchedAgentMarker})`
          : "● auto-detected",
      );
    case "default_fallback":
      return styleText("dim", "○ default factory agent");
  }
}

/**
 * Разбивает список технологических тегов на компактные строки фиксированной длины,
 * чтобы рамка карточки `@clack/prompts` сохраняла ровную геометрию при большом числе зависимостей.
 */
function formatStackTagsGrid(stackTags: readonly string[]): string {
  if (stackTags.length === 0) {
    return styleText("dim", "none");
  }

  const styledTags = stackTags.map((tag) =>
    tag === GLOBAL_STACK_TAG
      ? styleText(["bold", "magenta"], tag)
      : styleText("green", tag),
  );

  const rows: string[] = [];
  const separator = styleText("dim", " · ");

  for (let i = 0; i < styledTags.length; i += STACK_TAGS_PER_ROW) {
    const chunk = styledTags.slice(i, i + STACK_TAGS_PER_ROW);
    rows.push(chunk.join(separator));
  }

  return rows.join(`\n${CARD_VALUE_INDENT}`);
}

/**
 * Форматирует сводную карточку профиля рабочей директории с выровненными колонками,
 * цветовой индикацией состояния `.context/index.md` и датой последнего обновления графа.
 */
export function formatWorkspaceCard(profile: WorkspaceProfile): string {
  const labelProject = styleText("dim", "Project       ");
  const labelPath = styleText("dim", "Workspace     ");
  const labelAgent = styleText("dim", "Target Agent  ");
  const labelContext = styleText("dim", "Context Graph ");
  const labelStack = styleText("dim", "Stack Tags    ");

  const projectValue = styleText(["bold", "white"], profile.projectName);
  const pathValue = styleText("dim", profile.workspacePath);

  const agentName = styleText(
    ["bold", "cyan"],
    AGENT_DISPLAY_NAMES[profile.agent],
  );
  const dotSep = styleText("dim", "·");
  const agentBadge = formatAgentSourceBadge(profile);

  const updatedSuffix = profile.contextGraphUpdatedAt
    ? ` ${styleText("dim", `(updated: ${profile.contextGraphUpdatedAt})`)}`
    : "";
  const contextValue = profile.hasContextGraph
    ? `${styleText("green", "● active")} ${styleText("white", CONTEXT_GRAPH_INDEX_REL_PATH)}${updatedSuffix}`
    : `${styleText("yellow", "○ not initialized")} ${styleText("dim", `(target: ${CONTEXT_GRAPH_INDEX_REL_PATH})`)}`;

  const stackValue = formatStackTagsGrid(profile.stackTags);

  return [
    `${labelProject}  ${projectValue}`,
    `${labelPath}  ${pathValue}`,
    `${labelAgent}  ${agentName} ${dotSep} ${agentBadge}`,
    `${labelContext}  ${contextValue}`,
    `${labelStack}  ${stackValue}`,
  ].join("\n");
}

/**
 * Запускает интерактивный сценарий настройки проекта при вызове команды `init`.
 * Возвращает утверждённый пользователем профиль `WorkspaceProfile` либо `null`,
 * если пользователь прервал установку комбинацией `Ctrl+C` / `Esc`.
 */
export async function runInitWizard(
  initialProfile: WorkspaceProfile,
  options: InitWizardOptions = {},
): Promise<WorkspaceProfile | null> {
  const version = options.version ?? DEFAULT_CLI_VERSION;
  const runtime = options.runtime ?? DEFAULT_RUNTIME_LABEL;
  const isNonInteractive = Boolean(options.yes) || !process.stdin.isTTY;

  intro(renderCliBanner(version, runtime, "Workspace Initialization"));

  if (isNonInteractive) {
    note(
      formatWorkspaceCard(initialProfile),
      "Auto-Confirmed Workspace Profile (--yes / non-TTY)",
    );
    log.step(styleText("green", INIT_LOCKED_OUTRO_MESSAGE));
    return initialProfile;
  }

  note(formatWorkspaceCard(initialProfile), "Detected Workspace Profile");

  const orderedAgents: AgentId[] = [
    initialProfile.agent,
    ...SUPPORTED_AGENTS.filter((agent) => agent !== initialProfile.agent),
  ];

  const selectedAgent = await select<AgentId>({
    message:
      "Select target AI coding agent (Use ↑/↓ arrows, Enter to confirm):",
    initialValue: initialProfile.agent,
    options: orderedAgents.map((agentId) => {
      const isPreselected = agentId === initialProfile.agent;
      const configTarget = AGENT_CONFIG_HINTS[agentId];
      const hintText = isPreselected
        ? `${formatAgentSourceBadge(initialProfile)} ${styleText("dim", `→ ${configTarget}`)}`
        : styleText("dim", `generates ${configTarget}`);

      return {
        value: agentId,
        label: `${styleText("bold", AGENT_DISPLAY_NAMES[agentId])} ${styleText("dim", `(${agentId})`)}`,
        hint: hintText,
      };
    }),
  });

  if (isCancel(selectedAgent)) {
    cancel(WIZARD_CANCEL_MESSAGE);
    return null;
  }

  const detectedDomainTags = initialProfile.stackTags.filter(
    (tag) => tag !== GLOBAL_STACK_TAG,
  );
  const detectedSet = new Set(detectedDomainTags);
  const additionalKnownTags = getAllKnownStackTags().filter(
    (tag) => !detectedSet.has(tag),
  );
  const orderedStackCatalog = [...detectedDomainTags, ...additionalKnownTags];

  const selectedDomainTags = await multiselect<string>({
    message:
      "Confirm project stack tags (Press Space to toggle, Enter to confirm):",
    initialValues: detectedDomainTags,
    required: false,
    options: orderedStackCatalog.map((tag) => {
      const isDetected = detectedSet.has(tag);
      return {
        value: tag,
        label: isDetected ? styleText(["bold", "green"], tag) : tag,
        hint: isDetected
          ? styleText("green", "● detected in workspace")
          : styleText("dim", "optional stack tag"),
      };
    }),
  });

  if (isCancel(selectedDomainTags)) {
    cancel(WIZARD_CANCEL_MESSAGE);
    return null;
  }

  const normalizedDomainTags = Array.from(new Set(selectedDomainTags)).sort(
    (a, b) => a.localeCompare(b),
  );

  const isSameAgent = selectedAgent === initialProfile.agent;
  const finalProfile: WorkspaceProfile = {
    workspacePath: initialProfile.workspacePath,
    projectName: initialProfile.projectName,
    agent: selectedAgent,
    agentSource: isSameAgent ? initialProfile.agentSource : "cli_flag",
    matchedAgentMarker: isSameAgent
      ? initialProfile.matchedAgentMarker
      : undefined,
    stackTags: [GLOBAL_STACK_TAG, ...normalizedDomainTags],
    hasContextGraph: initialProfile.hasContextGraph,
    contextGraphUpdatedAt: initialProfile.contextGraphUpdatedAt,
  };

  note(formatWorkspaceCard(finalProfile), "Confirmed Configuration");
  log.step(styleText("green", INIT_LOCKED_OUTRO_MESSAGE));

  return finalProfile;
}

function formatManifestOverridesSummary(
  manifest: ProjectHarnessManifest,
): string {
  const enabledCount = manifest.overrides.enabled_components.length;
  const disabledCount = manifest.overrides.disabled_components.length;
  const disabledHooksCount = manifest.overrides.disabled_hooks.length;

  const enabledPart =
    enabledCount > 0
      ? styleText("green", `+${enabledCount} forced`)
      : styleText("dim", "+0 forced");
  const disabledPart =
    disabledCount > 0
      ? styleText("yellow", `-${disabledCount} muted`)
      : styleText("dim", "-0 muted");
  const hooksPart =
    disabledHooksCount > 0
      ? styleText("yellow", `-${disabledHooksCount} hooks`)
      : styleText("green", "all 5 hooks active");

  return `${enabledPart} · ${disabledPart} · ${hooksPart}`;
}

export interface PipelineProgressController {
  start: (initialMessage: string) => void;
  update: (phaseMessage: string) => void;
  stop: (finalMessage: string) => void;
}

/**
 * Создаёт контроллер живого прогресс-спиннера `@clack/prompts` для длительных операций
 * (`docker compose up -d --build`, ожидание `/health`, компиляция бандла).
 */
export function createPipelineProgress(): PipelineProgressController {
  const isTty = Boolean(process.stdout.isTTY);
  const s = isTty ? spinner() : null;
  let started = false;

  return {
    start(initialMessage: string): void {
      if (s) {
        s.start(styleText("cyan", initialMessage));
        started = true;
      } else {
        log.step(styleText("cyan", initialMessage));
      }
    },
    update(phaseMessage: string): void {
      if (s && started) {
        s.message(styleText("cyan", phaseMessage));
      }
    },
    stop(finalMessage: string): void {
      if (s && started) {
        s.stop(finalMessage);
        started = false;
      } else {
        log.step(finalMessage);
      }
    },
  };
}

/**
 * Отображает итоговую карточку после выполнения конвейера инициализации (`init`):
 * статус Docker-стека, количество записанных/пропущенных/удалённых файлов и путь к `.context/harness.json`.
 */
export function renderInitSummary(params: {
  profile: WorkspaceProfile;
  dockerResult: DockerBootstrapResult;
  report: MaterializationReport;
  manifestPath?: string;
  persistedInDb: boolean;
}): void {
  const { dockerResult, report, persistedInDb } = params;
  const stackBadge = dockerResult.coreHealthy
    ? `${styleText("green", "● online")} ${styleText("dim", `(${dockerResult.stackDir})`)}`
    : `${styleText("yellow", "○ offline (Fail-Open seed mode)")}`;
  const dbBadge = persistedInDb
    ? styleText("green", "● registered in PostgreSQL 18 (projects table)")
    : styleText("yellow", "○ offline fallback (Core unreachable)");
  const launcherBadge = styleText(
    "cyan",
    `${dockerResult.stackDir}/bin/run-hook.sh`,
  );
  const filesSummary = [
    styleText("green", `${report.written.length} written`),
    styleText("dim", `${report.skipped.length} unchanged`),
    report.pruned.length > 0
      ? styleText("yellow", `${report.pruned.length} pruned`)
      : styleText("dim", "0 pruned"),
  ].join(styleText("dim", " · "));

  const summaryLines: string[] = [
    `${styleText("dim", "Docker Stack  ")}  ${stackBadge}`,
    `${styleText("dim", "Project Store ")}  ${dbBadge}`,
    `${styleText("dim", "Hook Launcher ")}  ${launcherBadge}`,
    `${styleText("dim", "Materialized  ")}  ${filesSummary}`,
  ];

  if (!dockerResult.coreHealthy && dockerResult.errorMessage) {
    const cleanReason = dockerResult.errorMessage
      .replace(/\r?\n/g, " ")
      .slice(0, 96);
    summaryLines.push(
      `${styleText("dim", "Diagnostics   ")}  ${styleText("yellow", cleanReason)}`,
      `${styleText("dim", "Logs Command  ")}  ${styleText("dim", `docker compose -f "${dockerResult.composeFilePath}" logs`)}`,
    );
  }

  note(summaryLines.join("\n"), "Initialization Pipeline Summary");

  outro(
    styleText(
      "green",
      "✔ Workspace initialized. Run `mcp-coop-agentic-factory harness list` to inspect rules & skills.",
    ),
  );
}

/**
 * Отображает сводную панель после перекомпиляции и синхронизации артефактов проекта (`sync`).
 */
export function renderSyncSummary(params: {
  profile: WorkspaceProfile;
  manifest: ProjectHarnessManifest;
  report: MaterializationReport;
  source: string;
  healReport?: ContextHealResponse | null;
  ingestReport?: ComponentIngestReportPayload | null;
  contextSyncReport?: ContextSyncReportPayload | null;
}): void {
  const {
    profile,
    manifest,
    report,
    source,
    healReport,
    ingestReport,
    contextSyncReport,
  } = params;
  const effectiveTags = resolveEffectiveStackTags(manifest);
  const filesSummary = [
    styleText("green", `${report.written.length} written`),
    styleText("dim", `${report.skipped.length} unchanged`),
    report.pruned.length > 0
      ? styleText("yellow", `${report.pruned.length} pruned`)
      : styleText("dim", "0 pruned"),
  ].join(styleText("dim", " · "));

  const lines: string[] = [
    `${styleText("dim", "Target Agent  ")}  ${styleText(["bold", "cyan"], AGENT_DISPLAY_NAMES[profile.agent])}`,
    `${styleText("dim", "Bundle Source ")}  ${styleText("white", source)}`,
    `${styleText("dim", "Overrides     ")}  ${formatManifestOverridesSummary(manifest)}`,
    `${styleText("dim", "Effective Tags")}  ${formatStackTagsGrid(effectiveTags)}`,
    `${styleText("dim", "Materialized  ")}  ${filesSummary}`,
  ];

  if (ingestReport) {
    const ingestSummary = [
      styleText("green", `${ingestReport.created} created`),
      styleText("cyan", `${ingestReport.updated} updated`),
      styleText("dim", `${ingestReport.unchanged} unchanged`),
      ingestReport.rejected > 0
        ? styleText("yellow", `${ingestReport.rejected} rejected`)
        : styleText("dim", "0 rejected"),
    ].join(styleText("dim", " · "));
    lines.push(`${styleText("dim", "Custom Ingest ")}  ${ingestSummary}`);
  }

  if (contextSyncReport) {
    const ctxSummary = [
      styleText("green", `${contextSyncReport.pushed_to_db} pushed`),
      styleText("cyan", `${contextSyncReport.pulled_from_db} pulled`),
      contextSyncReport.merged > 0
        ? styleText("magenta", `${contextSyncReport.merged} merged`)
        : styleText("dim", "0 merged"),
      styleText("dim", `${contextSyncReport.unchanged} unchanged`),
    ].join(styleText("dim", " · "));
    lines.push(`${styleText("dim", "3-Way Context ")}  ${ctxSummary}`);
  }

  if (healReport) {
    const healBadge = healReport.executed
      ? styleText(
          "green",
          `● applied ${healReport.applied_nodes.length} node(s) (${healReport.summary || "validated"})`,
        )
      : styleText(
          "dim",
          `◆ standby (${healReport.skipped_reason || "SDK key not configured"})`,
        );
    lines.push(`${styleText("dim", "Context Heal  ")}  ${healBadge}`);
  }

  note(
    lines.join("\n"),
    "Synchronized Workspace (PostgreSQL 18 Core)",
  );

  outro(
    styleText(
      "green",
      "✔ Workspace rules, skills, and hooks synchronized with Harness² compiler.",
    ),
  );
}

/**
 * Отображает диагностическую панель команды `status`: профиль рабочей директории,
 * актуальность `.context/index.md`, состояние сервисов глобального Docker-стека,
 * число активных провайдеров Tier 2 и оверрайды `.context/harness.json`.
 */
export function renderStatusCommand(
  profile: WorkspaceProfile,
  version: string,
  runtime: string,
  liveStatus?: {
    health: CoreHealthStatus;
    providers: ProviderStatusItem[] | null;
    manifest: ProjectHarnessManifest;
  },
): void {
  intro(renderCliBanner(version, runtime, "System & Workspace Status"));

  note(formatWorkspaceCard(profile), "Workspace & Context Graph");

  const labelCore = styleText("dim", "Core Engine   ");
  const labelPg = styleText("dim", "PostgreSQL 18 ");
  const labelRedis = styleText("dim", "Redis 7 Store ");
  const labelTier2 = styleText("dim", "Tier 2 Pool   ");
  const labelHooks = styleText("dim", "Harness² Sync ");

  const health = liveStatus?.health;
  const coreBadge = health
    ? health.reachable
      ? styleText("green", "● online")
      : styleText("yellow", `○ offline (${health.status})`)
    : styleText("cyan", "◆ standby");
  const pgBadge = health
    ? health.postgres_connected
      ? styleText("green", "● online")
      : styleText("yellow", "○ offline")
    : styleText("cyan", "◆ standby");
  const redisBadge = health
    ? health.redis_connected
      ? styleText("green", "● online")
      : styleText("yellow", "○ offline")
    : styleText("cyan", "◆ standby");

  const providers = liveStatus?.providers ?? null;
  const activeProvidersCount = providers
    ? providers.filter((p) => p.enabled && p.health_status === PROVIDER_STATUS_HEALTHY).length
    : 0;
  const tier2Badge =
    providers !== null
      ? activeProvidersCount > 0
        ? `${styleText("green", `● ${activeProvidersCount} active`)} ${styleText("dim", `(${providers.length} registered)`)}`
        : `${styleText("yellow", "○ 0 active")} ${styleText("dim", "(Tier 1 deterministic fallback)")}`
      : `${styleText("dim", "○ offline")} ${styleText("dim", "(Tier 1 deterministic fallback)")}`;

  const manifestSummary = liveStatus?.manifest
    ? formatManifestOverridesSummary(liveStatus.manifest)
    : profile.hasContextGraph
      ? `${styleText("green", "● active")} ${styleText("dim", "(SessionStart, PreToolUse, PostToolUse, PreInvocation, Stop)")}`
      : `${styleText("yellow", "○ not installed")} ${styleText("dim", "(run init to inject)")}`;

  note(
    [
      `${labelCore}  ${coreBadge} ${styleText("dim", `(FastAPI + FastMCP @ ${DEFAULT_LOOPBACK_HOST}:${DEFAULT_CORE_HOST_PORT})`)}`,
      `${labelPg}  ${pgBadge} ${styleText("dim", `(pgvector:pg18 @ ${DEFAULT_LOOPBACK_HOST}:${DEFAULT_POSTGRES_HOST_PORT})`)}`,
      `${labelRedis}  ${redisBadge} ${styleText("dim", `(redis:7-alpine @ ${DEFAULT_LOOPBACK_HOST}:${DEFAULT_REDIS_HOST_PORT})`)}`,
      `${labelTier2}  ${tier2Badge}`,
      `${labelHooks}  ${manifestSummary}`,
    ].join("\n"),
    "Global Factory Stack (~/.mcp-coop-agentic-factory)",
  );

  outro(
    styleText(
      "dim",
      "Tip: Run `mcp-coop-agentic-factory init` or `harness list` to manage workspace rules.",
    ),
  );
}

/**
 * Отображает панель синхронизации команды `sync` для обновления правил и графа `.context/`.
 */
export function renderSyncCommand(
  profile: WorkspaceProfile,
  version: string,
  runtime: string,
): void {
  intro(renderCliBanner(version, runtime, "Context & Rules Sync"));

  note(formatWorkspaceCard(profile), "Synchronization Target");

  log.step(
    styleText(
      "cyan",
      `Synchronizing ${CONTEXT_GRAPH_INDEX_REL_PATH}, AGENTS.md invariants, and ${AGENT_DISPLAY_NAMES[profile.agent]} hooks...`,
    ),
  );

  outro(
    styleText(
      "green",
      "✔ Workspace rules and context graph synchronized with factory seed.",
    ),
  );
}

/**
 * Отображает панель запуска глобального Docker Compose стека при вызове команды `up`.
 */
export function renderUpCommand(
  version: string,
  runtime: string,
  dockerResult: DockerBootstrapResult,
): void {
  const stackBadge = dockerResult.coreHealthy
    ? `${styleText("green", "● online")} ${styleText("dim", `(${dockerResult.stackDir})`)}`
    : `${styleText("red", "✖ failed to start")}`;
  const pgBadge = dockerResult.health.postgres_connected
    ? styleText("green", "● online")
    : styleText("yellow", "○ unreachable");
  const redisBadge = dockerResult.health.redis_connected
    ? styleText("green", "● online")
    : styleText("yellow", "○ unreachable");

  const lines = [
    `${styleText("dim", "Docker Stack  ")}  ${stackBadge}`,
    `${styleText("dim", "PostgreSQL 18 ")}  ${pgBadge}`,
    `${styleText("dim", "Redis 7 Store ")}  ${redisBadge}`,
  ];
  if (!dockerResult.coreHealthy && dockerResult.errorMessage) {
    lines.push(
      `${styleText("dim", "Diagnostics   ")}  ${styleText("yellow", dockerResult.errorMessage.slice(0, 96))}`,
    );
  }

  note(lines.join("\n"), "Global Factory Stack Startup");

  if (dockerResult.coreHealthy) {
    outro(
      styleText(
        "green",
        "✔ Global Docker Compose stack is up and healthy. Run `mcp-coop-agentic-factory status` or `sync`.",
      ),
    );
  } else {
    outro(
      styleText(
        "yellow",
        "⚠ Global Docker Compose stack could not reach healthy state. Ensure Docker Desktop is running.",
      ),
    );
  }
}

/**
 * Отображает панель остановки глобального Docker Compose стека при вызове команды `down`.
 */
export function renderDownCommand(
  version: string,
  runtime: string,
  result?: {
    stopped: boolean;
    composeFilePath: string;
    errorMessage?: string;
  },
): void {
  intro(renderCliBanner(version, runtime, "Global Stack Shutdown"));

  const targetPath =
    result?.composeFilePath ?? "~/.mcp-coop-agentic-factory/docker/docker-compose.yml";
  log.step(styleText("yellow", `Stopping containers in ${targetPath}...`));

  if (result && !result.stopped) {
    log.warn(
      styleText(
        "yellow",
        `Stack stop reported warning: ${result.errorMessage ?? "Docker Compose stack was not running"}`,
      ),
    );
    outro(styleText("yellow", "○ Global stack was already stopped or unavailable."));
    return;
  }

  outro(
    styleText("green", "✔ Global Docker Compose stack gracefully stopped."),
  );
}

function formatCatalogStateBadge(state: string): string {
  switch (state) {
    case EFFECTIVE_STATE_ACTIVE_AUTO:
      return styleText("green", "● auto");
    case EFFECTIVE_STATE_ACTIVE_FORCED:
      return styleText(["bold", "cyan"], "● +forced");
    case EFFECTIVE_STATE_DISABLED_PROJECT:
      return styleText("yellow", "○ -muted (project)");
    case EFFECTIVE_STATE_DISABLED_GLOBAL:
      return styleText("red", "○ -disabled (global)");
    case EFFECTIVE_STATE_UNMATCHED_STACK:
    default:
      return styleText("dim", "· other stack");
  }
}

/**
 * Отображает панель каталога компонентов `Harness²` (`harness list`) с визуальными бейджами
 * эффективного состояния (`● auto`, `● +forced`, `○ -muted (project)`, `○ -disabled (global)`, `· other stack`)
 * и списком активных/отключённых хуков жизненного цикла.
 */
export function renderHarnessCatalogView(params: {
  version: string;
  runtime: string;
  items: CatalogComponentItem[];
  manifest: ProjectHarnessManifest;
  showAll: boolean;
}): void {
  const { version, runtime, items, manifest, showAll } = params;
  intro(renderCliBanner(version, runtime, "Harness² Component Catalog"));

  const visibleItems = showAll
    ? items
    : items.filter((item) => item.effective_state !== EFFECTIVE_STATE_UNMATCHED_STACK);

  const rows =
    visibleItems.length > 0
      ? visibleItems.map((item) => {
          const badge = formatCatalogStateBadge(item.effective_state);
          const slugStr = styleText(["bold", "white"], item.slug.padEnd(28));
          const typeStr = styleText("cyan", `[${item.component_type}]`.padEnd(16));
          const tagsStr = styleText("dim", `(${item.stack_tags.join(",")})`);
          return `${badge.padEnd(28)} ${slugStr} ${typeStr} ${tagsStr}`;
        })
      : [styleText("dim", "No matching components found for current filter.")];

  note(
    rows.join("\n"),
    `Components (${visibleItems.length}/${items.length} shown · agent: ${manifest.agent})`,
  );

  const disabledHooksSet = new Set<string>(manifest.overrides.disabled_hooks);
  const hookBadges = ALL_HOOK_EVENTS.map((ev) =>
    disabledHooksSet.has(ev)
      ? styleText("yellow", `○ -${ev}`)
      : styleText("green", `● ${ev}`),
  ).join(styleText("dim", "  "));

  note(
    [
      `${styleText("dim", "Lifecycle Hooks")}  ${hookBadges}`,
      `${styleText("dim", "Effective Stack")}  ${formatStackTagsGrid(resolveEffectiveStackTags(manifest))}`,
    ].join("\n"),
    "Project Harness Profile (PostgreSQL 18 Core)",
  );

  outro(
    styleText(
      "dim",
      "Use `harness enable|disable <slug>` or `harness consolidate [--dry-run]` to manage knowledge.",
    ),
  );
}

/**
 * Отображает панель результатов серверной консолидации знаний `HarnessConsolidator` (`harness consolidate [--dry-run]`).
 */
export function renderConsolidationView(params: {
  version: string;
  runtime: string;
  report: ConsolidationReportPayload;
}): void {
  const { version, runtime, report } = params;
  const title = report.dry_run
    ? "Harness² Consolidation (Dry Run)"
    : "Harness² Consolidation";
  intro(renderCliBanner(version, runtime, title));

  if (report.clusters.length === 0) {
    note(
      styleText(
        "dim",
        "No semantic or lexical duplicate clusters detected above similarity threshold.",
      ),
      `Consolidation Report (0 clusters · dry_run=${String(report.dry_run)})`,
    );
  } else {
    const rows = report.clusters.map((cluster) => {
      const badge = cluster.applied
        ? styleText("green", "● merged")
        : styleText("cyan", "◆ candidate");
      const canon = styleText(["bold", "white"], cluster.canonical_slug);
      const dups = styleText("yellow", cluster.duplicate_slugs.join(", "));
      const sim = styleText("dim", `(sim=${cluster.max_similarity.toFixed(2)})`);
      return `${badge} ${canon} <= [${dups}] ${sim}`;
    });
    note(
      rows.join("\n"),
      `Clusters (${report.clusters_found} found · ${report.components_archived} archived)`,
    );
  }

  outro(
    styleText(
      "green",
      report.dry_run
        ? `✔ Dry-run complete: ${report.clusters_found} cluster(s) ready for consolidation.`
        : `✔ Consolidation complete: ${report.components_archived} duplicate component(s) archived.`,
    ),
  );
}

/**
 * Отображает панель пула провайдеров второго контура (`providers list`) с приоритетами,
 * диалектами, моделями и статусом Circuit Breaker (`healthy` / `cooldown` / `disabled`).
 */
export function renderProvidersView(params: {
  version: string;
  runtime: string;
  providers: ProviderStatusItem[];
}): void {
  const { version, runtime, providers } = params;
  intro(renderCliBanner(version, runtime, "Tier 2 Decision Providers"));

  if (providers.length === 0) {
    note(
      styleText(
        "dim",
        "No Tier 2 providers registered. DecisionEngine runs in Tier 1 deterministic fallback mode.",
      ),
      "Runtime Provider Pool (0 providers)",
    );
  } else {
    const lines = providers.map((prov) => {
      const statusBadge =
        !prov.enabled || prov.health_status === PROVIDER_STATUS_DISABLED
          ? styleText("dim", "○ disabled")
          : prov.health_status === PROVIDER_STATUS_COOLDOWN
            ? styleText(
                "yellow",
                `◐ cooldown (${prov.cooldown_remaining_seconds}s)`,
              )
            : styleText("green", "● healthy");
      const prio = styleText("cyan", `[#${prov.priority}]`.padEnd(7));
      const name = styleText(["bold", "white"], prov.name.padEnd(18));
      const dialectModel = styleText(
        "dim",
        `${prov.dialect} · ${prov.model} (${prov.base_url})`,
      );
      return `${prio} ${name} ${statusBadge.padEnd(22)} ${dialectModel}`;
    });
    note(lines.join("\n"), `Runtime Provider Pool (${providers.length} configured)`);
  }

  outro(
    styleText(
      "dim",
      "Use `providers add <name> --dialect ... --url ... --model ... --key ...` to register a provider.",
    ),
  );
}
/**
 * Отображает компактное подтверждение операции модификации (`providers add/enable/disable/priority/remove/reset`, `harness --global`).
 */
export function renderQuickMutationResult(
  version: string,
  runtime: string,
  actionTitle: string,
  message: string,
): void {
  intro(renderCliBanner(version, runtime, actionTitle));
  outro(styleText("green", `✔ ${message}`));
}

/**
 * Отображает стилизованное справочное руководство CLI (`--help`).
 */
export function renderHelpScreen(
  cliName: string,
  version: string,
  runtime: string,
): void {
  intro(renderCliBanner(version, runtime, "CLI Reference"));

  const supportedAgentsList = [AUTO_AGENT_OPTION, ...SUPPORTED_AGENTS].join(
    ", ",
  );

  const usageBlock = [
    `${styleText(["bold", "cyan"], `$ ${cliName} <command> [subcommand] [options]`)}`,
    "",
    `${styleText(["bold", "white"], "Commands:")}`,
    `  ${styleText(["bold", "green"], "init     ")}  Interactive TUI wizard for Docker stack, project DB registration & agent hooks`,
    `  ${styleText(["bold", "cyan"], "sync     ")}  Recompile & sync workspace rules/skills/hooks from Core PostgreSQL DB`,
    `  ${styleText(["bold", "cyan"], "harness  ")}  Manage rules, skills, hooks & tags (list, enable, disable, hook-enable,`,
    `             ${styleText("dim", "hook-disable, tag-add, tag-exclude, consolidate)")}`,
    `  ${styleText(["bold", "cyan"], "providers")}  Manage Tier 2 Decision API providers (list, add, enable, disable,`,
    `             ${styleText("dim", "priority, remove, reset)")}`,
    `  ${styleText(["bold", "cyan"], "status   ")}  Inspect workspace profile, PostgreSQL 18 Core, Docker & Tier 2 health`,
    `  ${styleText(["bold", "green"], "up       ")}  Start global ~/.mcp-coop-agentic-factory/ Docker Compose stack`,
    `  ${styleText(["bold", "yellow"], "down     ")}  Gracefully stop global ~/.mcp-coop-agentic-factory/ stack`,
    `  ${styleText("dim", "hook     ")}  Lifecycle hook bridge (session-start, pre-tool-use, post-tool-use, pre-invocation, stop)`,
    "",
    `${styleText(["bold", "white"], "Options:")}`,
    `  ${styleText("cyan", "-a, --agent <id>   ")}  Target agent [default: "${AUTO_AGENT_OPTION}"] (${supportedAgentsList})`,
    `  ${styleText("cyan", "-y, --yes          ")}  Skip interactive prompts and use detected defaults`,
    `  ${styleText("cyan", "-g, --global       ")}  Toggle component globally in PostgreSQL (for harness enable/disable)`,
    `  ${styleText("cyan", "--all              ")}  Include unmatched stack components in harness list`,
    `  ${styleText("cyan", "--dry-run          ")}  Preview semantic duplicate clusters in harness consolidate without DB mutation`,
    `  ${styleText("cyan", "-t, --type <type>  ")}  Filter component type in harness list (system_prompt, guardrail, tool_guidance, skill)`,
    `  ${styleText("cyan", "--tag <tag>        ")}  Filter stack tag in harness list`,
    `  ${styleText("cyan", "--dialect <id>     ")}  Provider dialect for providers add [default: "openai_decisions"]`,
    `  ${styleText("cyan", "--url <url>        ")}  Base URL for providers add`,
    `  ${styleText("cyan", "--model <name>     ")}  Model name for providers add`,
    `  ${styleText("cyan", "--key <api_key>    ")}  API key for providers add`,
    `  ${styleText("cyan", "--priority <num>   ")}  Priority number for providers add / priority`,
    `  ${styleText("cyan", "-h, --help         ")}  Show this help screen and exit`,
    `  ${styleText("cyan", "-v, --version      ")}  Show CLI version and active runtime`,
  ].join("\n");

  note(usageBlock, "Usage & Commands");

  outro(
    styleText(
      "dim",
      `Quick start: run \`${cliName} init\` inside any project directory.`,
    ),
  );
}

/**
 * Отображает форматированное сообщение об ошибке CLI с подсказкой вызова `--help`.
 */
export function renderCliError(
  message: string,
  cliName: string,
  version: string = DEFAULT_CLI_VERSION,
  runtime: string = DEFAULT_RUNTIME_LABEL,
): void {
  intro(renderCliBanner(version, runtime, "Command Error"));
  log.error(styleText("red", message));
  cancel(styleText("dim", `Run "${cliName} --help" for usage information.`));
}
