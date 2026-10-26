/**
 * Слой 1 (Host CLI TUI Presentation): палитра ANSI-стилей, глифы, бейджи статусов и базовые компоненты
 * оформления терминального интерфейса на базе `node:util.styleText` и `@clack/prompts`.
 * Обеспечивает единообразную верстку карточек, выравнивание колонок с учётом ANSI-кодов и вывод баннера/ошибок.
 */

import { cancel, intro, log } from "@clack/prompts";
import { styleText } from "node:util";
import { CLI_VERSION } from "../config/index.ts";
import {
  AGENT_DISPLAY_NAMES,
  CONTEXT_GRAPH_INDEX_REL_PATH,
  GLOBAL_STACK_TAG,
  type WorkspaceProfile,
} from "../detector/index.ts";

export const DEFAULT_RUNTIME_LABEL = "node" as const;
export const STACK_TAGS_PER_ROW = 6 as const;
export const CARD_VALUE_INDENT: string = " ".repeat(16);

export const ALL_LIFECYCLE_HOOK_EVENTS = [
  "SessionStart",
  "PreToolUse",
  "PostToolUse",
  "PreInvocation",
  "Stop",
] as const;

export const EFFECTIVE_COMPONENT_STATES = {
  ACTIVE_AUTO: "active_auto",
  ACTIVE_FORCED: "active_forced",
  DISABLED_PROJECT: "disabled_project",
  DISABLED_GLOBAL: "disabled_global",
  UNMATCHED_STACK: "unmatched_stack",
} as const;

export const PROVIDER_HEALTH_STATES = {
  HEALTHY: "healthy",
  COOLDOWN: "cooldown",
  DISABLED: "disabled",
} as const;

export const TUI_GLYPHS = {
  ONLINE: "●",
  OFFLINE: "○",
  STANDBY: "◆",
  PARTIAL: "◐",
  CHECK: "✔",
  WARN: "⚠",
  ERROR: "✖",
  DOT: "·",
} as const;

export type BadgeTone =
  | "success"
  | "warning"
  | "error"
  | "info"
  | "accent"
  | "muted";

/**
 * Структурное представление секции локальных оверрайдов манифеста `.context/harness.json` для TUI-карточек.
 */
export interface TuiHarnessOverridesView {
  readonly enabled_components: readonly string[];
  readonly disabled_components: readonly string[];
  readonly disabled_hooks: readonly string[];
  readonly extra_stack_tags?: readonly string[] | undefined;
  readonly excluded_stack_tags?: readonly string[] | undefined;
  readonly added_tags?: readonly string[] | undefined;
  readonly excluded_tags?: readonly string[] | undefined;
}

/**
 * Структурный контракт манифеста проекта для отображения в TUI без жёсткой связности с пакетом материализатора.
 */
export interface TuiHarnessManifestView {
  readonly agent: string;
  readonly stack?: {
    readonly detected_tags: readonly string[];
    readonly extra_tags: readonly string[];
    readonly excluded_tags: readonly string[];
  } | undefined;
  readonly stack_tags?: readonly string[] | undefined;
  readonly overrides: TuiHarnessOverridesView;
}

/**
 * Структурный контракт отчёта материализации файлов для отображения статистики записанных/пропущенных/удалённых артефактов.
 */
export interface TuiMaterializationReportView {
  readonly written: readonly string[];
  readonly skipped: readonly string[];
  readonly pruned: readonly string[];
}

// Регулярное выражение для удаления управляющих ANSI-последовательностей при расчёте видимой ширины колонок
const ANSI_ESCAPE_REGEX = /\x1B\[[0-9;]*m/g;

/**
 * Очищает строку от ANSI-кодов форматирования для вычисления реальной ширины текста в терминале.
 */
export function stripAnsiCodes(text: string): string {
  return text.replace(ANSI_ESCAPE_REGEX, "");
}

/**
 * Дополняет строку пробелами справа до заданной видимой ширины `targetWidth`,
 * компенсируя невидимые управляющие символы ANSI-раскраски.
 */
export function padVisible(text: string, targetWidth: number): string {
  const visibleLength = stripAnsiCodes(text).length;
  if (visibleLength >= targetWidth) {
    return text;
  }
  return `${text}${" ".repeat(targetWidth - visibleLength)}`;
}

/**
 * Приводит многострочный текст к одной строке и усекает его до `maxLength` символов
 * для сохранения геометрии карточек `@clack/prompts`.
 */
export function truncateText(text: string, maxLength: number): string {
  const singleLine = text.replace(/\r?\n/g, " ").trim();
  if (singleLine.length <= maxLength) {
    return singleLine;
  }
  return singleLine.slice(0, maxLength);
}

/**
 * Формирует цветной статусный бейдж с опциональным глифом индикации состояния.
 */
export function formatBadge(
  label: string,
  tone: BadgeTone = "info",
  glyph?: string,
): string {
  const content = glyph ? `${glyph} ${label}` : label;
  switch (tone) {
    case "success":
      return styleText("green", content);
    case "warning":
      return styleText("yellow", content);
    case "error":
      return styleText("red", content);
    case "info":
      return styleText("cyan", content);
    case "accent":
      return styleText(["bold", "cyan"], content);
    case "muted":
      return styleText("dim", content);
  }
}

/**
 * Формирует фирменный заголовок шапки CLI с контрастным пилюльным бейджем, версией, рантаймом и названием операции.
 */
export function renderCliBanner(
  version: string = CLI_VERSION,
  runtime: string = DEFAULT_RUNTIME_LABEL,
  actionTitle: string = "CLI",
): string {
  const pill = styleText(["bgCyan", "black", "bold"], " MCP-COOP ");
  const brand = styleText(["bold", "white"], "Agentic Factory");
  const meta = styleText("dim", `v${version} • ${runtime}`);
  const separator = styleText("dim", "—");
  const subtitle = styleText(["bold", "cyan"], actionTitle);

  return `${pill} ${brand} ${meta} ${separator} ${subtitle}`;
}

/**
 * Отображает форматированное сообщение об ошибке CLI с подсказкой вызова `--help`.
 */
export function renderCliError(
  message: string,
  cliName: string,
  version: string = CLI_VERSION,
  runtime: string = DEFAULT_RUNTIME_LABEL,
): void {
  intro(renderCliBanner(version, runtime, "Command Error"));
  log.error(styleText("red", message));
  cancel(styleText("dim", `Run "${cliName} --help" for usage information.`));
}

/**
 * Формирует читаемый бейдж источника выбора ИИ-агента для карточки профиля и подсказок меню.
 */
export function formatAgentSourceBadge(profile: WorkspaceProfile): string {
  switch (profile.agentSource) {
    case "cli_flag":
      return formatBadge("preselected via --agent", "info", TUI_GLYPHS.ONLINE);
    case "environment":
      return formatBadge(
        profile.matchedAgentMarker
          ? `env (${profile.matchedAgentMarker})`
          : "configured via env",
        "info",
        TUI_GLYPHS.ONLINE,
      );
    case "workspace_marker":
      return formatBadge(
        profile.matchedAgentMarker
          ? `auto-detected (${profile.matchedAgentMarker})`
          : "auto-detected",
        "success",
        TUI_GLYPHS.ONLINE,
      );
    case "default_fallback":
      return formatBadge("default factory agent", "muted", TUI_GLYPHS.OFFLINE);
  }
}

/**
 * Разбивает список технологических тегов на компактные строки фиксированной длины,
 * чтобы рамка карточки `@clack/prompts` сохраняла ровную геометрию при большом числе зависимостей.
 */
export function formatStackTagsGrid(stackTags: readonly string[]): string {
  if (stackTags.length === 0) {
    return styleText("dim", "none");
  }

  const styledTags = stackTags.map((tag) =>
    tag === GLOBAL_STACK_TAG
      ? styleText(["bold", "magenta"], tag)
      : styleText("green", tag),
  );

  const rows: string[] = [];
  const separator = styleText("dim", ` ${TUI_GLYPHS.DOT} `);

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
  const dotSep = styleText("dim", TUI_GLYPHS.DOT);
  const agentBadge = formatAgentSourceBadge(profile);

  const updatedSuffix = profile.contextGraphUpdatedAt
    ? ` ${styleText("dim", `(updated: ${profile.contextGraphUpdatedAt})`)}`
    : "";
  const contextValue = profile.hasContextGraph
    ? `${formatBadge("active", "success", TUI_GLYPHS.ONLINE)} ${styleText("white", CONTEXT_GRAPH_INDEX_REL_PATH)}${updatedSuffix}`
    : `${formatBadge("not initialized", "warning", TUI_GLYPHS.OFFLINE)} ${styleText("dim", `(target: ${CONTEXT_GRAPH_INDEX_REL_PATH})`)}`;

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
 * Вычисляет результирующий набор активных тегов стека с учётом пользовательских добавлений и исключений в манифесте.
 */
export function resolveEffectiveStackTags(
  manifest: TuiHarnessManifestView,
): string[] {
  const excludedSet = new Set<string>([
    ...(manifest.stack?.excluded_tags ?? []),
    ...(manifest.overrides.excluded_stack_tags ?? []),
    ...(manifest.overrides.excluded_tags ?? []),
  ]);
  const combinedSet = new Set<string>([
    GLOBAL_STACK_TAG,
    ...(manifest.stack?.detected_tags ?? []),
    ...(manifest.stack?.extra_tags ?? []),
    ...(manifest.stack_tags ?? []),
    ...(manifest.overrides.extra_stack_tags ?? []),
    ...(manifest.overrides.added_tags ?? []),
  ]);

  const domainTags = Array.from(combinedSet)
    .filter((tag) => tag !== GLOBAL_STACK_TAG && !excludedSet.has(tag))
    .sort((a, b) => a.localeCompare(b));

  return [GLOBAL_STACK_TAG, ...domainTags];
}

/**
 * Форматирует строку сводки локальных оверрайдов `.context/harness.json` (принудительно включённые/отключённые компоненты и хуки).
 */
export function formatManifestOverridesSummary(
  manifest: TuiHarnessManifestView,
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

/**
 * Формирует бейдж эффективного состояния компонента `Harness` для табличного вывода `harness list`.
 */
export function formatCatalogStateBadge(state: string): string {
  switch (state) {
    case EFFECTIVE_COMPONENT_STATES.ACTIVE_AUTO:
      return formatBadge("auto", "success", TUI_GLYPHS.ONLINE);
    case EFFECTIVE_COMPONENT_STATES.ACTIVE_FORCED:
      return formatBadge("+forced", "accent", TUI_GLYPHS.ONLINE);
    case EFFECTIVE_COMPONENT_STATES.DISABLED_PROJECT:
      return formatBadge("-muted (project)", "warning", TUI_GLYPHS.OFFLINE);
    case EFFECTIVE_COMPONENT_STATES.DISABLED_GLOBAL:
      return formatBadge("-disabled (global)", "error", TUI_GLYPHS.OFFLINE);
    case EFFECTIVE_COMPONENT_STATES.UNMATCHED_STACK:
    default:
      return formatBadge("other stack", "muted", TUI_GLYPHS.DOT);
  }
}
