/**
 * Слой 1 (Host CLI TUI Presentation): диагностические экраны жизненного цикла глобального стека
 * (`status`, `up`, `down`, `ui`) и справочное руководство `--help`.
 * Использует канонические константы сетевых портов `DEFAULT_SERVICE_PORTS` из `src/cli/config` (Zero-Hardcode).
 */

import { intro, log, note, outro } from "@clack/prompts";
import { styleText } from "node:util";
import {
  CLI_VERSION,
  DEFAULT_CORE_HOST,
  DEFAULT_SERVICE_PORTS,
  GLOBAL_FACTORY_DIR_NAME,
} from "../config/index.ts";
import type {
  CoreHealthStatus,
  ProviderStatusItem,
} from "../core-api/index.ts";
import {
  AUTO_AGENT_OPTION,
  SUPPORTED_AGENTS,
  type WorkspaceProfile,
} from "../detector/index.ts";
import type {
  DockerBootstrapResult,
  StopDockerStackResult,
} from "../docker/index.ts";
import {
  DEFAULT_RUNTIME_LABEL,
  formatBadge,
  formatManifestOverridesSummary,
  formatWorkspaceCard,
  PROVIDER_HEALTH_STATES,
  renderCliBanner,
  TUI_GLYPHS,
  type TuiHarnessManifestView,
  truncateText,
} from "./theme.ts";

const DIAGNOSTICS_MAX_LENGTH = 96 as const;
const DEFAULT_COMPOSE_PATH_HINT =
  `~/${GLOBAL_FACTORY_DIR_NAME}/docker/docker-compose.yml` as const;

/**
 * Живой срез состояния сервисов ядра, пула провайдеров Tier 2 и манифеста проекта для команды `status`.
 */
export interface StatusLiveContext {
  readonly health: CoreHealthStatus;
  readonly providers: readonly ProviderStatusItem[] | null;
  readonly manifest?: TuiHarnessManifestView | undefined;
}

/**
 * Модель данных для рендеринга диагностического экрана `status`.
 */
export interface StatusCommandViewModel {
  readonly profile: WorkspaceProfile;
  readonly version?: string | undefined;
  readonly runtime?: string | undefined;
  readonly liveStatus?: StatusLiveContext | undefined;
}

/**
 * Модель данных для рендеринга экрана запуска глобального Docker-стека `up`.
 */
export interface UpCommandViewModel {
  readonly version?: string | undefined;
  readonly runtime?: string | undefined;
  readonly dockerResult: DockerBootstrapResult;
}

/**
 * Модель данных для рендеринга экрана остановки глобального Docker-стека `down`.
 */
export interface DownCommandViewModel {
  readonly version?: string | undefined;
  readonly runtime?: string | undefined;
  readonly result?: StopDockerStackResult | undefined;
}

/**
 * Модель данных для рендеринга экрана интерактивного веб-визуализатора `ui`.
 */
export interface UiCommandViewModel {
  readonly version: string;
  readonly runtime: string;
  readonly url: string;
  readonly openedBrowser: boolean;
  readonly coreReachable: boolean;
  readonly postgresConnected: boolean;
}

/**
 * Модель данных для рендеринга справочного руководства `--help`.
 */
export interface HelpScreenViewModel {
  readonly cliName: string;
  readonly version?: string | undefined;
  readonly runtime?: string | undefined;
}

function isStatusCommandViewModel(
  value: WorkspaceProfile | StatusCommandViewModel,
): value is StatusCommandViewModel {
  return (
    "profile" in value &&
    typeof value.profile === "object" &&
    value.profile !== null
  );
}

/**
 * Отображает диагностическую панель команды `status`: профиль рабочей директории,
 * актуальность `.context/index.md`, состояние сервисов глобального Docker-стека,
 * число активных провайдеров Tier 2 и оверрайды `.context/harness.json`.
 */
export function renderStatusCommand(viewModel: StatusCommandViewModel): void;
export function renderStatusCommand(
  profile: WorkspaceProfile,
  version: string,
  runtime: string,
  liveStatus?: StatusLiveContext,
): void;
export function renderStatusCommand(
  profileOrViewModel: WorkspaceProfile | StatusCommandViewModel,
  version?: string,
  runtime?: string,
  liveStatus?: StatusLiveContext,
): void {
  let profile: WorkspaceProfile;
  let resolvedVersion: string;
  let resolvedRuntime: string;
  let resolvedLiveStatus: StatusLiveContext | undefined;

  if (isStatusCommandViewModel(profileOrViewModel)) {
    profile = profileOrViewModel.profile;
    resolvedVersion = profileOrViewModel.version ?? CLI_VERSION;
    resolvedRuntime = profileOrViewModel.runtime ?? DEFAULT_RUNTIME_LABEL;
    resolvedLiveStatus = profileOrViewModel.liveStatus;
  } else {
    profile = profileOrViewModel;
    resolvedVersion = version ?? CLI_VERSION;
    resolvedRuntime = runtime ?? DEFAULT_RUNTIME_LABEL;
    resolvedLiveStatus = liveStatus;
  }

  intro(
    renderCliBanner(
      resolvedVersion,
      resolvedRuntime,
      "System & Workspace Status",
    ),
  );

  note(formatWorkspaceCard(profile), "Workspace & Context Graph");

  const labelCore = styleText("dim", "Core Engine   ");
  const labelPg = styleText("dim", "PostgreSQL 18 ");
  const labelRedis = styleText("dim", "Redis 7 Store ");
  const labelTier2 = styleText("dim", "Tier 2 Pool   ");
  const labelHooks = styleText("dim", "Harness² Sync ");

  const health = resolvedLiveStatus?.health;
  const coreBadge = health
    ? health.reachable
      ? formatBadge("online", "success", TUI_GLYPHS.ONLINE)
      : formatBadge(`offline (${health.status})`, "warning", TUI_GLYPHS.OFFLINE)
    : formatBadge("standby", "info", TUI_GLYPHS.STANDBY);
  const pgBadge = health
    ? health.postgres_connected
      ? formatBadge("online", "success", TUI_GLYPHS.ONLINE)
      : formatBadge("offline", "warning", TUI_GLYPHS.OFFLINE)
    : formatBadge("standby", "info", TUI_GLYPHS.STANDBY);
  const redisBadge = health
    ? health.redis_connected
      ? formatBadge("online", "success", TUI_GLYPHS.ONLINE)
      : formatBadge("offline", "warning", TUI_GLYPHS.OFFLINE)
    : formatBadge("standby", "info", TUI_GLYPHS.STANDBY);

  const providers = resolvedLiveStatus?.providers ?? null;
  const activeProvidersCount = providers
    ? providers.filter(
        (p) =>
          p.enabled && p.health_status === PROVIDER_HEALTH_STATES.HEALTHY,
      ).length
    : 0;
  const tier2Badge =
    providers !== null
      ? activeProvidersCount > 0
        ? `${formatBadge(`${activeProvidersCount} active`, "success", TUI_GLYPHS.ONLINE)} ${styleText("dim", `(${providers.length} registered)`)}`
        : `${formatBadge("0 active", "warning", TUI_GLYPHS.OFFLINE)} ${styleText("dim", "(Tier 1 deterministic fallback)")}`
      : `${formatBadge("offline", "muted", TUI_GLYPHS.OFFLINE)} ${styleText("dim", "(Tier 1 deterministic fallback)")}`;

  const manifestSummary = resolvedLiveStatus?.manifest
    ? formatManifestOverridesSummary(resolvedLiveStatus.manifest)
    : profile.hasContextGraph
      ? `${formatBadge("active", "success", TUI_GLYPHS.ONLINE)} ${styleText("dim", "(SessionStart, PreToolUse, PostToolUse, PreInvocation, Stop)")}`
      : `${formatBadge("not installed", "warning", TUI_GLYPHS.OFFLINE)} ${styleText("dim", "(run init to inject)")}`;

  note(
    [
      `${labelCore}  ${coreBadge} ${styleText("dim", `(FastAPI + FastMCP @ ${DEFAULT_CORE_HOST}:${DEFAULT_SERVICE_PORTS.CORE_API})`)}`,
      `${labelPg}  ${pgBadge} ${styleText("dim", `(pgvector:pg18 @ ${DEFAULT_CORE_HOST}:${DEFAULT_SERVICE_PORTS.POSTGRES})`)}`,
      `${labelRedis}  ${redisBadge} ${styleText("dim", `(redis:7-alpine @ ${DEFAULT_CORE_HOST}:${DEFAULT_SERVICE_PORTS.REDIS})`)}`,
      `${labelTier2}  ${tier2Badge}`,
      `${labelHooks}  ${manifestSummary}`,
    ].join("\n"),
    `Global Factory Stack (~/${GLOBAL_FACTORY_DIR_NAME})`,
  );

  outro(
    styleText(
      "dim",
      "Tip: Run `mcp-coop-agentic-factory init` or `harness list` to manage workspace rules.",
    ),
  );
}

/**
 * Отображает панель запуска глобального Docker Compose стека при вызове команды `up`.
 */
export function renderUpCommand(viewModel: UpCommandViewModel): void;
export function renderUpCommand(
  version: string,
  runtime: string,
  dockerResult: DockerBootstrapResult,
): void;
export function renderUpCommand(
  versionOrViewModel: string | UpCommandViewModel,
  runtime?: string,
  dockerResult?: DockerBootstrapResult,
): void {
  const resolvedResult =
    typeof versionOrViewModel === "object"
      ? versionOrViewModel.dockerResult
      : dockerResult!;

  const stackBadge = resolvedResult.coreHealthy
    ? `${formatBadge("online", "success", TUI_GLYPHS.ONLINE)} ${styleText("dim", `(${resolvedResult.stackDir})`)}`
    : formatBadge("failed to start", "error", TUI_GLYPHS.ERROR);
  const pgBadge = resolvedResult.health.postgres_connected
    ? formatBadge("online", "success", TUI_GLYPHS.ONLINE)
    : formatBadge("unreachable", "warning", TUI_GLYPHS.OFFLINE);
  const redisBadge = resolvedResult.health.redis_connected
    ? formatBadge("online", "success", TUI_GLYPHS.ONLINE)
    : formatBadge("unreachable", "warning", TUI_GLYPHS.OFFLINE);

  const lines = [
    `${styleText("dim", "Docker Stack  ")}  ${stackBadge}`,
    `${styleText("dim", "PostgreSQL 18 ")}  ${pgBadge}`,
    `${styleText("dim", "Redis 7 Store ")}  ${redisBadge}`,
  ];
  if (!resolvedResult.coreHealthy && resolvedResult.errorMessage) {
    lines.push(
      `${styleText("dim", "Diagnostics   ")}  ${styleText("yellow", truncateText(resolvedResult.errorMessage, DIAGNOSTICS_MAX_LENGTH))}`,
    );
  }

  note(lines.join("\n"), "Global Factory Stack Startup");

  if (resolvedResult.coreHealthy) {
    outro(
      styleText(
        "green",
        `${TUI_GLYPHS.CHECK} Global Docker Compose stack is up and healthy. Run \`mcp-coop-agentic-factory status\` or \`sync\`.`,
      ),
    );
  } else {
    outro(
      styleText(
        "yellow",
        `${TUI_GLYPHS.WARN} Global Docker Compose stack could not reach healthy state. Ensure Docker Desktop is running.`,
      ),
    );
  }
}

/**
 * Отображает панель остановки глобального Docker Compose стека при вызове команды `down`.
 */
export function renderDownCommand(viewModel: DownCommandViewModel): void;
export function renderDownCommand(
  version: string,
  runtime: string,
  result?: StopDockerStackResult,
): void;
export function renderDownCommand(
  versionOrViewModel: string | DownCommandViewModel,
  runtime?: string,
  result?: StopDockerStackResult,
): void {
  const isViewModel = typeof versionOrViewModel === "object";
  const resolvedVersion = isViewModel
    ? (versionOrViewModel.version ?? CLI_VERSION)
    : versionOrViewModel;
  const resolvedRuntime = isViewModel
    ? (versionOrViewModel.runtime ?? DEFAULT_RUNTIME_LABEL)
    : (runtime ?? DEFAULT_RUNTIME_LABEL);
  const resolvedResult = isViewModel ? versionOrViewModel.result : result;

  intro(
    renderCliBanner(resolvedVersion, resolvedRuntime, "Global Stack Shutdown"),
  );

  const targetPath =
    resolvedResult?.composeFilePath ?? DEFAULT_COMPOSE_PATH_HINT;
  log.step(styleText("yellow", `Stopping containers in ${targetPath}...`));

  if (resolvedResult && !resolvedResult.stopped) {
    log.warn(
      styleText(
        "yellow",
        `Stack stop reported warning: ${resolvedResult.errorMessage ?? "Docker Compose stack was not running"}`,
      ),
    );
    outro(
      styleText(
        "yellow",
        `${TUI_GLYPHS.OFFLINE} Global stack was already stopped or unavailable.`,
      ),
    );
    return;
  }

  outro(
    styleText(
      "green",
      `${TUI_GLYPHS.CHECK} Global Docker Compose stack gracefully stopped.`,
    ),
  );
}

/**
 * Отображает панель команды `ui` для интерактивного RAG & Harness² веб-визуализатора.
 * Выводит URL веб-интерфейса, статус доступности Core API и PostgreSQL 18,
 * а также статус автоматического запуска браузера.
 */
export function renderUiCommand(params: UiCommandViewModel): void {
  intro(
    renderCliBanner(
      params.version,
      params.runtime,
      "RAG & Harness² Graph Visualizer",
    ),
  );

  const coreBadge = params.coreReachable
    ? formatBadge("online", "success", TUI_GLYPHS.ONLINE)
    : formatBadge("offline (start with `up`)", "warning", TUI_GLYPHS.OFFLINE);
  const pgBadge = params.postgresConnected
    ? formatBadge("connected", "success", TUI_GLYPHS.ONLINE)
    : formatBadge(
        "offline fallback (.context + Seed)",
        "warning",
        TUI_GLYPHS.OFFLINE,
      );
  const browserBadge = params.openedBrowser
    ? formatBadge("launched automatically", "success", TUI_GLYPHS.ONLINE)
    : formatBadge("skipped (--no-open or headless)", "muted", TUI_GLYPHS.OFFLINE);

  const lines = [
    `${styleText("dim", "Dashboard URL ")}  ${styleText(["bold", "cyan"], params.url)}`,
    `${styleText("dim", "Core Engine   ")}  ${coreBadge}`,
    `${styleText("dim", "PostgreSQL 18 ")}  ${pgBadge}`,
    `${styleText("dim", "Browser       ")}  ${browserBadge}`,
  ];

  note(lines.join("\n"), "Visualizer Web UI");

  outro(
    styleText(
      params.coreReachable ? "green" : "yellow",
      params.coreReachable
        ? `${TUI_GLYPHS.CHECK} Graph Visualizer ready at ${params.url}`
        : `${TUI_GLYPHS.WARN} Core API is offline. Run "mcp-coop-agentic-factory up" and open ${params.url}`,
    ),
  );
}

/**
 * Отображает стилизованное справочное руководство CLI (`--help`).
 */
export function renderHelpScreen(viewModel: HelpScreenViewModel): void;
export function renderHelpScreen(
  cliName: string,
  version: string,
  runtime: string,
): void;
export function renderHelpScreen(
  cliNameOrViewModel: string | HelpScreenViewModel,
  version?: string,
  runtime?: string,
): void {
  const isViewModel = typeof cliNameOrViewModel === "object";
  const resolvedCliName = isViewModel
    ? cliNameOrViewModel.cliName
    : cliNameOrViewModel;
  const resolvedVersion = isViewModel
    ? (cliNameOrViewModel.version ?? CLI_VERSION)
    : (version ?? CLI_VERSION);
  const resolvedRuntime = isViewModel
    ? (cliNameOrViewModel.runtime ?? DEFAULT_RUNTIME_LABEL)
    : (runtime ?? DEFAULT_RUNTIME_LABEL);

  intro(renderCliBanner(resolvedVersion, resolvedRuntime, "CLI Reference"));

  const supportedAgentsList = [AUTO_AGENT_OPTION, ...SUPPORTED_AGENTS].join(
    ", ",
  );

  const usageBlock = [
    `${styleText(["bold", "cyan"], `$ ${resolvedCliName} <command> [subcommand] [options]`)}`,
    "",
    `${styleText(["bold", "white"], "Commands:")}`,
    `  ${styleText(["bold", "green"], "init     ")}  Interactive TUI wizard for Docker stack, project DB registration & agent hooks`,
    `  ${styleText(["bold", "cyan"], "sync     ")}  Recompile & sync workspace rules/skills/hooks from Core PostgreSQL DB`,
    `  ${styleText(["bold", "cyan"], "harness  ")}  Manage rules, skills, hooks & tags (list, enable, disable, hook-enable,`,
    `             ${styleText("dim", "hook-disable, tag-add, tag-exclude, consolidate)")}`,
    `  ${styleText(["bold", "cyan"], "providers")}  Manage Tier 2 Decision API providers (list, add, enable, disable,`,
    `             ${styleText("dim", "priority, remove, reset)")}`,
    `  ${styleText(["bold", "cyan"], "status   ")}  Inspect workspace profile, PostgreSQL 18 Core, Docker & Tier 2 health`,
    `  ${styleText(["bold", "cyan"], "ui       ")}  Open interactive RAG & Harness² Graph Visualizer in default browser`,
    `  ${styleText(["bold", "green"], "up       ")}  Start global ~/${GLOBAL_FACTORY_DIR_NAME}/ Docker Compose stack`,
    `  ${styleText(["bold", "yellow"], "down     ")}  Gracefully stop global ~/${GLOBAL_FACTORY_DIR_NAME}/ stack`,
    `  ${styleText("dim", "hook     ")}  Lifecycle hook bridge (session-start, pre-tool-use, post-tool-use, pre-invocation, stop)`,
    "",
    `${styleText(["bold", "white"], "Options:")}`,
    `  ${styleText("cyan", "-a, --agent <id>   ")}  Target agent [default: "${AUTO_AGENT_OPTION}"] (${supportedAgentsList})`,
    `  ${styleText("cyan", "-y, --yes          ")}  Skip interactive prompts and use detected defaults`,
    `  ${styleText("cyan", "-g, --global       ")}  Toggle component globally in PostgreSQL (for harness enable/disable)`,
    `  ${styleText("cyan", "--all              ")}  Include unmatched stack components in harness list`,
    `  ${styleText("cyan", "--dry-run          ")}  Preview semantic duplicate clusters in harness consolidate without DB mutation`,
    `  ${styleText("cyan", "--no-open          ")}  Print Visualizer URL without launching browser (for ui command)`,
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
      `Quick start: run \`${resolvedCliName} init\` inside any project directory.`,
    ),
  );
}
