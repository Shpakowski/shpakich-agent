/**
 * Слой 1 (Host CLI TUI Presentation): интерактивный визард команды `init` на базе `@clack/prompts`.
 * Проводит пользователя через выбор целевого ИИ-агента (`select`) и тегов технологического стека (`multiselect`),
 * визуально различая активные скиллы в БД (`hasContent: true`) и пустые технологические заглушки (`styleText("dim", ...)`).
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
import type { HarnessComponentItem } from "../core-api/endpoints-harness.ts";
import {
  AGENT_CONFIG_HINTS,
  AGENT_DISPLAY_NAMES,
  type AgentId,
  getAllKnownStackTags,
  getCatalogTagStatusMap,
  GLOBAL_STACK_TAG,
  SUPPORTED_AGENTS,
  type WorkspaceProfile,
} from "../detector/index.ts";
import type { BackupSnapshotMetadata } from "../docker/index.ts";
import {
  DEFAULT_RUNTIME_LABEL,
  formatAgentSourceBadge,
  formatWorkspaceCard,
  renderCliBanner,
  TUI_GLYPHS,
} from "./theme.ts";

const WIZARD_CANCEL_MESSAGE = "Initialization cancelled by user." as const;
const SKIP_SNAPSHOT_RESTORE_VALUE = "__skip_snapshot_restore__" as const;
const INIT_LOCKED_OUTRO_MESSAGE =
  `${TUI_GLYPHS.CHECK} Configuration locked — starting factory initialization pipeline...` as const;

/**
 * Параметры интерактивного меню выбора слепка БД из `.backups/` на первом шаге `init` / `up`.
 */
export interface SnapshotPromptOptions {
  readonly snapshots: readonly BackupSnapshotMetadata[];
  readonly isDbEmpty: boolean;
  readonly yes?: boolean | undefined;
}

/**
 * Результат выбора слепка БД пользователем.
 */
export type SnapshotSelectionOutcome =
  | { readonly action: "restore"; readonly snapshotPath: string }
  | {
      readonly action: "skip";
      readonly reason: "db_not_empty" | "user_skipped";
    }
  | { readonly action: "cancelled" };

/**
 * Опции запуска интерактивного визарда инициализации проекта.
 */
export interface WizardOptions {
  readonly yes?: boolean | undefined;
  readonly version?: string | undefined;
  readonly runtime?: string | undefined;
  readonly skipBanner?: boolean | undefined;
  readonly catalogComponents?: readonly HarnessComponentItem[] | undefined;
}

export type InitWizardOptions = WizardOptions;

/**
 * Результат прохождения визарда: утверждённый профиль `WorkspaceProfile` либо `null` при отмене пользователем (`Ctrl+C` / `Esc`).
 */
export type WizardSelectionResult = WorkspaceProfile | null;

/**
 * Отображает интерактивное меню выбора слепка знаний из `.backups/` (первый шаг перед выбором агента
 * в `init` и при запуске `up`, если в `.backups/` найдены слепки).
 * Если база пустая — по умолчанию предвыбран самый свежий слепок; если в базе уже есть данные —
 * по умолчанию предвыбрано сохранение текущей БД без перезаписи.
 */
export async function promptSnapshotRestoreSelection(
  options: SnapshotPromptOptions,
): Promise<SnapshotSelectionOutcome> {
  const { snapshots, isDbEmpty } = options;
  if (snapshots.length === 0) {
    return { action: "skip", reason: "user_skipped" };
  }

  const isNonInteractive = Boolean(options.yes) || !process.stdin.isTTY;
  if (isNonInteractive) {
    if (isDbEmpty && snapshots[0]) {
      return { action: "restore", snapshotPath: snapshots[0].path };
    }
    return { action: "skip", reason: "db_not_empty" };
  }

  const selectOptions = isDbEmpty
    ? [
        ...snapshots.map((snap, idx) => ({
          value: snap.path,
          label:
            idx === 0
              ? `${styleText(["bold", "green"], snap.filename)} ${styleText("cyan", "(latest)")}`
              : styleText("bold", snap.filename),
          hint:
            idx === 0
              ? styleText(
                  "green",
                  `${TUI_GLYPHS.ONLINE} Recommended · DB is empty · ${snap.timestampLabel} · ${snap.formattedSize}`,
                )
              : styleText("dim", `${snap.timestampLabel} · ${snap.formattedSize}`),
        })),
        {
          value: SKIP_SNAPSHOT_RESTORE_VALUE,
          label: styleText(
            "dim",
            "Skip snapshot restore (start with fresh default seed)",
          ),
          hint: styleText("dim", "do not import .backups/ snapshot"),
        },
      ]
    : [
        {
          value: SKIP_SNAPSHOT_RESTORE_VALUE,
          label: styleText(
            ["bold", "green"],
            "Keep current live database (do not overwrite)",
          ),
          hint: styleText(
            "green",
            `${TUI_GLYPHS.ONLINE} Recommended · PostgreSQL already contains live data`,
          ),
        },
        ...snapshots.map((snap, idx) => ({
          value: snap.path,
          label:
            idx === 0
              ? `${styleText("bold", snap.filename)} ${styleText("cyan", "(latest)")}`
              : styleText("bold", snap.filename),
          hint: styleText(
            "yellow",
            `restore & replace DB with snapshot from ${snap.timestampLabel} (${snap.formattedSize})`,
          ),
        })),
      ];

  const initialValue =
    isDbEmpty && snapshots[0]
      ? snapshots[0].path
      : SKIP_SNAPSHOT_RESTORE_VALUE;

  const selected = await select<string>({
    message: isDbEmpty
      ? `PostgreSQL knowledge DB is empty — select snapshot from .backups/ to restore (${String(snapshots.length)} available):`
      : `Found ${String(snapshots.length)} knowledge snapshot(s) in .backups/ — select database state:`,
    initialValue,
    options: selectOptions,
  });

  if (isCancel(selected)) {
    cancel(WIZARD_CANCEL_MESSAGE);
    return { action: "cancelled" };
  }

  if (selected === SKIP_SNAPSHOT_RESTORE_VALUE) {
    return {
      action: "skip",
      reason: isDbEmpty ? "user_skipped" : "db_not_empty",
    };
  }

  return { action: "restore", snapshotPath: selected };
}

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

  if (!options.skipBanner) {
    intro(renderCliBanner(version, runtime, "Workspace Initialization"));
  }

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

  const tagStatusMap = getCatalogTagStatusMap(options.catalogComponents);
  const detectedDomainTags = initialProfile.stackTags.filter(
    (tag) => tag !== GLOBAL_STACK_TAG,
  );
  const detectedSet = new Set(detectedDomainTags);

  const allCatalogTagsSet = new Set<string>([
    ...getAllKnownStackTags(),
    ...tagStatusMap.keys(),
  ]);
  const additionalKnownTags = Array.from(allCatalogTagsSet)
    .filter((tag) => !detectedSet.has(tag) && tag !== GLOBAL_STACK_TAG)
    .sort((a, b) => a.localeCompare(b));
  const orderedStackCatalog = [...detectedDomainTags, ...additionalKnownTags];

  const selectedDomainTags = await multiselect<string>({
    message:
      "Confirm project stack tags (Press Space to toggle, Enter to confirm):",
    initialValues: detectedDomainTags,
    required: false,
    options: orderedStackCatalog.map((tag) => {
      const isDetected = detectedSet.has(tag);
      const tagInfo = tagStatusMap.get(tag);
      const hasContent = tagInfo?.hasContent ?? false;

      if (hasContent) {
        return {
          value: tag,
          label: isDetected
            ? styleText(["bold", "green"], tag)
            : styleText("bold", tag),
          hint: isDetected
            ? styleText(
                "green",
                `${TUI_GLYPHS.ONLINE} detected in workspace · active skill in DB`,
              )
            : styleText("cyan", `active skill in DB (${tagInfo?.title ?? tag})`),
        };
      }

      return {
        value: tag,
        label: styleText("dim", `${tag} (empty stub)`),
        hint: isDetected
          ? styleText(
              "dim",
              `${TUI_GLYPHS.OFFLINE} detected in workspace · inactive stub in DB`,
            )
          : styleText("dim", "inactive stub in DB (tag only)"),
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
