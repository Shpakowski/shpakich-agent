/**
 * Слой 1 (Host CLI TUI Presentation): интерактивный визард команды `init` на базе `@clack/prompts`.
 * Проводит пользователя через выбор целевого ИИ-агента (`select`) и тегов технологического стека (`multiselect`),
 * автоматически переключаясь в неблокирующий режим подтверждения при флаге `--yes` или отсутствии TTY.
 */

import {
  cancel,
  intro,
  isCancel,
  log,
  multiselect,
  note,
  select,
} from "@clack/prompts";
import process from "node:process";
import { styleText } from "node:util";
import { CLI_VERSION } from "../config/index.ts";
import {
  AGENT_CONFIG_HINTS,
  AGENT_DISPLAY_NAMES,
  GLOBAL_STACK_TAG,
  SUPPORTED_AGENTS,
  getAllKnownStackTags,
  type AgentId,
  type WorkspaceProfile,
} from "../detector/index.ts";
import {
  DEFAULT_RUNTIME_LABEL,
  formatAgentSourceBadge,
  formatWorkspaceCard,
  renderCliBanner,
  TUI_GLYPHS,
} from "./theme.ts";

const WIZARD_CANCEL_MESSAGE = "Initialization cancelled by user." as const;
const INIT_LOCKED_OUTRO_MESSAGE =
  `${TUI_GLYPHS.CHECK} Configuration locked — starting factory initialization pipeline...` as const;

/**
 * Опции запуска интерактивного визарда инициализации проекта.
 */
export interface WizardOptions {
  readonly yes?: boolean | undefined;
  readonly version?: string | undefined;
  readonly runtime?: string | undefined;
}

export type InitWizardOptions = WizardOptions;

/**
 * Результат прохождения визарда: утверждённый профиль `WorkspaceProfile` либо `null` при отмене пользователем (`Ctrl+C` / `Esc`).
 */
export type WizardSelectionResult = WorkspaceProfile | null;

/**
 * Запускает интерактивный сценарий настройки проекта при вызове команды `init`.
 * Возвращает утверждённый пользователем профиль `WorkspaceProfile` либо `null`,
 * если пользователь прервал установку комбинацией `Ctrl+C` / `Esc`.
 */
export async function runInitWizard(
  initialProfile: WorkspaceProfile,
  options: WizardOptions = {},
): Promise<WizardSelectionResult> {
  const version = options.version ?? CLI_VERSION;
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
          ? styleText("green", `${TUI_GLYPHS.ONLINE} detected in workspace`)
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
