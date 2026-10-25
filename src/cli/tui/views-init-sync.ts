/**
 * Слой 1 (Host CLI TUI Presentation): итоговые карточки конвейеров инициализации (`init`)
 * и двусторонней синхронизации рабочего пространства (`sync`).
 * Отображает сводку записанных/пропущенных/удалённых файлов, статус регистрации в PostgreSQL 18,
 * метрики `Custom Ingest`, `3-Way Context Sync` и авто-исцеления графа `.context/`.
 */

import { intro, log, note, outro } from "@clack/prompts";
import { styleText } from "node:util";
import type {
  ComponentIngestReportPayload,
  ContextHealResponse,
  ContextSyncReportPayload,
} from "../core-api/index.ts";
import {
  AGENT_DISPLAY_NAMES,
  CONTEXT_GRAPH_INDEX_REL_PATH,
  type WorkspaceProfile,
} from "../detector/index.ts";
import type { DockerBootstrapResult } from "../docker/index.ts";
import {
  formatBadge,
  formatManifestOverridesSummary,
  formatStackTagsGrid,
  formatWorkspaceCard,
  renderCliBanner,
  resolveEffectiveStackTags,
  TUI_GLYPHS,
  type TuiHarnessManifestView,
  type TuiMaterializationReportView,
  truncateText,
} from "./theme.ts";

const DIAGNOSTICS_MAX_LENGTH = 96 as const;

/**
 * Модель данных для рендеринга итоговой карточки команды `init`.
 */
export interface InitSummaryViewModel {
  readonly profile: WorkspaceProfile;
  readonly dockerResult: DockerBootstrapResult;
  readonly report: TuiMaterializationReportView;
  readonly manifestPath?: string | undefined;
  readonly persistedInDb: boolean;
}

/**
 * Модель данных для рендеринга итоговой карточки двусторонней синхронизации `sync`.
 */
export interface SyncSummaryViewModel {
  readonly profile: WorkspaceProfile;
  readonly manifest: TuiHarnessManifestView;
  readonly report: TuiMaterializationReportView;
  readonly source: string;
  readonly healReport?: ContextHealResponse | null | undefined;
  readonly ingestReport?: ComponentIngestReportPayload | null | undefined;
  readonly contextSyncReport?: ContextSyncReportPayload | null | undefined;
}

function formatMaterializedFilesSummary(
  report: TuiMaterializationReportView,
): string {
  return [
    styleText("green", `${report.written.length} written`),
    styleText("dim", `${report.skipped.length} unchanged`),
    report.pruned.length > 0
      ? styleText("yellow", `${report.pruned.length} pruned`)
      : styleText("dim", "0 pruned"),
  ].join(styleText("dim", ` ${TUI_GLYPHS.DOT} `));
}

/**
 * Отображает итоговую карточку после выполнения конвейера инициализации (`init`):
 * статус Docker-стека, количество записанных/пропущенных/удалённых файлов и статус регистрации в БД.
 */
export function renderInitSummary(params: InitSummaryViewModel): void {
  const { dockerResult, report, persistedInDb } = params;
  const stackBadge = dockerResult.coreHealthy
    ? `${formatBadge("online", "success", TUI_GLYPHS.ONLINE)} ${styleText("dim", `(${dockerResult.stackDir})`)}`
    : formatBadge("offline (Fail-Open seed mode)", "warning", TUI_GLYPHS.OFFLINE);
  const dbBadge = persistedInDb
    ? formatBadge(
        "registered in PostgreSQL 18 (projects table)",
        "success",
        TUI_GLYPHS.ONLINE,
      )
    : formatBadge(
        "offline fallback (Core unreachable)",
        "warning",
        TUI_GLYPHS.OFFLINE,
      );
  const launcherBadge = styleText(
    "cyan",
    `${dockerResult.stackDir}/bin/run-hook.sh`,
  );
  const filesSummary = formatMaterializedFilesSummary(report);

  const summaryLines: string[] = [
    `${styleText("dim", "Docker Stack  ")}  ${stackBadge}`,
    `${styleText("dim", "Project Store ")}  ${dbBadge}`,
    `${styleText("dim", "Hook Launcher ")}  ${launcherBadge}`,
    `${styleText("dim", "Materialized  ")}  ${filesSummary}`,
  ];

  if (!dockerResult.coreHealthy && dockerResult.errorMessage) {
    const cleanReason = truncateText(
      dockerResult.errorMessage,
      DIAGNOSTICS_MAX_LENGTH,
    );
    summaryLines.push(
      `${styleText("dim", "Diagnostics   ")}  ${styleText("yellow", cleanReason)}`,
      `${styleText("dim", "Logs Command  ")}  ${styleText("dim", `docker compose -f "${dockerResult.composeFilePath}" logs`)}`,
    );
  }

  note(summaryLines.join("\n"), "Initialization Pipeline Summary");

  outro(
    styleText(
      "green",
      `${TUI_GLYPHS.CHECK} Workspace initialized. Run \`mcp-coop-agentic-factory harness list\` to inspect rules & skills.`,
    ),
  );
}

/**
 * Отображает сводную панель после перекомпиляции и синхронизации артефактов проекта (`sync`).
 */
export function renderSyncSummary(params: SyncSummaryViewModel): void {
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
  const filesSummary = formatMaterializedFilesSummary(report);

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
    ].join(styleText("dim", ` ${TUI_GLYPHS.DOT} `));
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
    ].join(styleText("dim", ` ${TUI_GLYPHS.DOT} `));
    lines.push(`${styleText("dim", "3-Way Context ")}  ${ctxSummary}`);
  }

  if (healReport) {
    const healBadge = healReport.executed
      ? formatBadge(
          `applied ${healReport.applied_nodes.length} node(s) (${healReport.summary || "validated"})`,
          "success",
          TUI_GLYPHS.ONLINE,
        )
      : formatBadge(
          `standby (${healReport.skipped_reason || "SDK key not configured"})`,
          "muted",
          TUI_GLYPHS.STANDBY,
        );
    lines.push(`${styleText("dim", "Context Heal  ")}  ${healBadge}`);
  }

  note(lines.join("\n"), "Synchronized Workspace (PostgreSQL 18 Core)");

  outro(
    styleText(
      "green",
      `${TUI_GLYPHS.CHECK} Workspace rules, skills, and hooks synchronized with Harness² compiler.`,
    ),
  );
}

/**
 * Отображает базовую панель синхронизации команды `sync` для обновления правил и графа `.context/`.
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
      `${TUI_GLYPHS.CHECK} Workspace rules and context graph synchronized with factory seed.`,
    ),
  );
}
