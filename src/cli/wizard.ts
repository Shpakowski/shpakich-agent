/**
 * Слой 1 (Host CLI Injector): интерактивный TUI-визард и дизайн-система терминального вывода CLI.
 * Реализует единый визуальный стиль команд (`init`, `status`, `sync`, `down`, `--help`) на базе `@clack/prompts`
 * и встроенного ANSI-форматтера `node:util.styleText`. Поддерживает стрелочную навигацию (`select`),
 * чекбоксы технологических тегов (`multiselect`) и автоматический переход в безблокирующий режим при `--yes` или отсутствии TTY.
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

export interface InitWizardOptions {
  yes?: boolean;
  version?: string;
  runtime?: string;
}

const DEFAULT_CLI_VERSION = "0.1.0";
const DEFAULT_RUNTIME_LABEL = "node";
const DEFAULT_LOOPBACK_HOST = "127.0.0.1";
const DEFAULT_CORE_HOST_PORT = 8787;
const DEFAULT_POSTGRES_HOST_PORT = 54329;
const DEFAULT_REDIS_HOST_PORT = 63799;
const STACK_TAGS_PER_ROW = 6;
const CARD_VALUE_INDENT = " ".repeat(16);

const WIZARD_CANCEL_MESSAGE = "Initialization cancelled by user.";

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
    outro(
      styleText(
        "green",
        "✔ Ready — starting factory initialization pipeline (Docker stack & plugin generation stub)...",
      ),
    );
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
  outro(
    styleText(
      "green",
      "✔ Configuration locked — starting factory initialization pipeline (Docker stack & plugin generation stub)...",
    ),
  );

  return finalProfile;
}

/**
 * Отображает диагностическую панель команды `status`: профиль рабочей директории,
 * актуальность `.context/index.md` и состояние сервисов глобального Docker-стека.
 */
export function renderStatusCommand(
  profile: WorkspaceProfile,
  version: string,
  runtime: string,
): void {
  intro(renderCliBanner(version, runtime, "System & Workspace Status"));

  note(formatWorkspaceCard(profile), "Workspace & Context Graph");

  const labelCore = styleText("dim", "Core Engine   ");
  const labelPg = styleText("dim", "PostgreSQL 18 ");
  const labelRedis = styleText("dim", "Redis 7 Store ");
  const labelHooks = styleText("dim", "Context Hooks ");

  const hooksStatus = profile.hasContextGraph
    ? `${styleText("green", "● active")} ${styleText("dim", "(SessionStart, PostToolUse, Stop)")}`
    : `${styleText("yellow", "○ not installed")} ${styleText("dim", "(run init to inject)")}`;

  note(
    [
      `${labelCore}  ${styleText("cyan", "◆ standby")} ${styleText("dim", `(FastAPI + FastMCP @ ${DEFAULT_LOOPBACK_HOST}:${DEFAULT_CORE_HOST_PORT})`)}`,
      `${labelPg}  ${styleText("cyan", "◆ standby")} ${styleText("dim", `(pgvector:pg18 @ ${DEFAULT_LOOPBACK_HOST}:${DEFAULT_POSTGRES_HOST_PORT})`)}`,
      `${labelRedis}  ${styleText("cyan", "◆ standby")} ${styleText("dim", `(redis:7-alpine @ ${DEFAULT_LOOPBACK_HOST}:${DEFAULT_REDIS_HOST_PORT})`)}`,
      `${labelHooks}  ${hooksStatus}`,
    ].join("\n"),
    "Global Factory Stack (~/.mcp-coop-agentic-factory)",
  );

  outro(
    styleText(
      "dim",
      "Tip: Run `mcp-coop-agentic-factory init` to reconfigure agent or stack tags.",
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
 * Отображает панель остановки глобального Docker Compose стека при вызове команды `down`.
 */
export function renderDownCommand(version: string, runtime: string): void {
  intro(renderCliBanner(version, runtime, "Global Stack Shutdown"));

  log.step(
    styleText(
      "yellow",
      "Stopping containers in ~/.mcp-coop-agentic-factory/docker-compose.yml...",
    ),
  );

  outro(
    styleText("green", "✔ Global Docker Compose stack gracefully stopped."),
  );
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
    `${styleText(["bold", "cyan"], `$ ${cliName} <command> [options]`)}`,
    "",
    `${styleText(["bold", "white"], "Commands:")}`,
    `  ${styleText(["bold", "green"], "init  ")}    Interactive TUI wizard for Docker stack & workspace hooks`,
    `  ${styleText(["bold", "cyan"], "status")}    Inspect workspace profile, .context/ & Docker health`,
    `  ${styleText(["bold", "cyan"], "sync  ")}    Sync workspace rules & context graph with factory seed`,
    `  ${styleText(["bold", "yellow"], "down  ")}    Gracefully stop global ~/.mcp-coop-agentic-factory/`,
    `  ${styleText("dim", "hook  ")}    Run lifecycle hook (SessionStart, PostToolUse, Stop)`,
    "",
    `${styleText(["bold", "white"], "Options:")}`,
    `  ${styleText("cyan", "-a, --agent <id>")}  Target agent [default: "${AUTO_AGENT_OPTION}"]`,
    `                    ${styleText("dim", `(${supportedAgentsList})`)}`,
    `  ${styleText("cyan", "-y, --yes       ")}  Skip interactive prompts and use detected defaults`,
    `  ${styleText("cyan", "-h, --help      ")}  Show this help screen and exit`,
    `  ${styleText("cyan", "-v, --version   ")}  Show CLI version and active runtime`,
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
