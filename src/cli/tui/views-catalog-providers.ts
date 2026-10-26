/**
 * Слой 1 (Host CLI TUI Presentation): карточки и таблицы команд `harness` и `providers`.
 * Отображает каталог компонентов `Harness²` с бейджами эффективного состояния, отчёт серверной
 * дедупликации `HarnessConsolidator`, таблицу провайдеров Tier 2 с индикацией Circuit Breaker и быстрые мутации.
 */

import { intro, note, outro } from "@clack/prompts";
import { styleText } from "node:util";
import { CLI_VERSION } from "../config/index.ts";
import type {
  CatalogComponentItem,
  ConsolidationReportPayload,
  ProviderStatusItem,
} from "../core-api/index.ts";
import {
  ALL_LIFECYCLE_HOOK_EVENTS,
  DEFAULT_RUNTIME_LABEL,
  EFFECTIVE_COMPONENT_STATES,
  formatBadge,
  formatCatalogStateBadge,
  formatStackTagsGrid,
  PROVIDER_HEALTH_STATES,
  renderCliBanner,
  resolveEffectiveStackTags,
  TUI_GLYPHS,
  type TuiHarnessManifestView,
} from "./theme.ts";

/**
 * Модель данных для рендеринга каталога компонентов `harness list`.
 */
export interface HarnessCatalogViewModel {
  readonly version: string;
  readonly runtime: string;
  readonly items: readonly CatalogComponentItem[];
  readonly manifest: TuiHarnessManifestView;
  readonly showAll: boolean;
}

/**
 * Модель данных для рендеринга отчёта консолидации знаний `harness consolidate`.
 */
export interface ConsolidationViewModel {
  readonly version: string;
  readonly runtime: string;
  readonly report: ConsolidationReportPayload;
}

/**
 * Модель данных для рендеринга пула провайдеров второго контура `providers list`.
 */
export interface ProvidersViewModel {
  readonly version: string;
  readonly runtime: string;
  readonly providers: readonly ProviderStatusItem[];
}

/**
 * Модель данных для компактного подтверждения мутации (`providers add/enable/disable/...`, `harness enable/disable/...`).
 */
export interface QuickMutationViewModel {
  readonly version?: string | undefined;
  readonly runtime?: string | undefined;
  readonly actionTitle: string;
  readonly message: string;
}

/**
 * Отображает панель каталога компонентов `Harness²` (`harness list`) с визуальными бейджами
 * эффективного состояния (`● auto`, `● +forced`, `○ -muted (project)`, `○ -disabled (global)`, `· other stack`)
 * и списком активных/отключённых хуков жизненного цикла.
 */
export function renderHarnessCatalogView(
  params: HarnessCatalogViewModel,
): void {
  const { version, runtime, items, manifest, showAll } = params;
  intro(renderCliBanner(version, runtime, "Harness² Component Catalog"));

  const visibleItems = showAll
    ? items
    : items.filter(
        (item) =>
          item.effective_state !== EFFECTIVE_COMPONENT_STATES.UNMATCHED_STACK,
      );

  const rows =
    visibleItems.length > 0
      ? visibleItems.map((item) => {
          const isEmptyStub =
            item.has_content === false || item.is_empty_stub === true;
          if (isEmptyStub) {
            const stubBadge = formatBadge(
              "EMPTY STUB",
              "muted",
              TUI_GLYPHS.OFFLINE,
            );
            const slugStr = styleText("dim", item.slug.padEnd(28));
            const typeStr = styleText(
              "dim",
              `[${item.component_type}]`.padEnd(16),
            );
            const tagsStr = styleText("dim", `(${item.stack_tags.join(",")})`);
            return `${stubBadge.padEnd(28)} ${slugStr} ${typeStr} ${tagsStr}`;
          }
          const badge = formatCatalogStateBadge(item.effective_state);
          const slugStr = styleText(["bold", "white"], item.slug.padEnd(28));
          const typeStr = styleText(
            "cyan",
            `[${item.component_type}]`.padEnd(16),
          );
          const tagsStr = styleText("dim", `(${item.stack_tags.join(",")})`);
          return `${badge.padEnd(28)} ${slugStr} ${typeStr} ${tagsStr}`;
        })
      : [styleText("dim", "No matching components found for current filter.")];

  note(
    rows.join("\n"),
    `Components (${visibleItems.length}/${items.length} shown · agent: ${manifest.agent})`,
  );

  const disabledHooksSet = new Set<string>(manifest.overrides.disabled_hooks);
  const hookBadges = ALL_LIFECYCLE_HOOK_EVENTS.map((ev) =>
    disabledHooksSet.has(ev)
      ? styleText("yellow", `${TUI_GLYPHS.OFFLINE} -${ev}`)
      : styleText("green", `${TUI_GLYPHS.ONLINE} ${ev}`),
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
export function renderConsolidationView(params: ConsolidationViewModel): void {
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
        ? formatBadge("merged", "success", TUI_GLYPHS.ONLINE)
        : formatBadge("candidate", "info", TUI_GLYPHS.STANDBY);
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
        ? `${TUI_GLYPHS.CHECK} Dry-run complete: ${report.clusters_found} cluster(s) ready for consolidation.`
        : `${TUI_GLYPHS.CHECK} Consolidation complete: ${report.components_archived} duplicate component(s) archived.`,
    ),
  );
}

/**
 * Отображает панель пула провайдеров второго контура (`providers list`) с приоритетами,
 * диалектами, моделями и статусом Circuit Breaker (`healthy` / `cooldown` / `disabled`).
 */
export function renderProvidersView(params: ProvidersViewModel): void {
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
        !prov.enabled || prov.health_status === PROVIDER_HEALTH_STATES.DISABLED
          ? formatBadge("disabled", "muted", TUI_GLYPHS.OFFLINE)
          : prov.health_status === PROVIDER_HEALTH_STATES.COOLDOWN
            ? formatBadge(
                `cooldown (${prov.cooldown_remaining_seconds}s)`,
                "warning",
                TUI_GLYPHS.PARTIAL,
              )
            : formatBadge("healthy", "success", TUI_GLYPHS.ONLINE);
      const prio = styleText("cyan", `[#${prov.priority}]`.padEnd(7));
      const name = styleText(["bold", "white"], prov.name.padEnd(18));
      const dialectModel = styleText(
        "dim",
        `${prov.dialect} · ${prov.model} (${prov.base_url})`,
      );
      return `${prio} ${name} ${statusBadge.padEnd(22)} ${dialectModel}`;
    });
    note(
      lines.join("\n"),
      `Runtime Provider Pool (${providers.length} configured)`,
    );
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
 * Поддерживает как объектный `QuickMutationViewModel`, так и позиционную сигнатуру `(version, runtime, actionTitle, message)`.
 */
export function renderQuickMutationResult(
  viewModel: QuickMutationViewModel,
): void;
export function renderQuickMutationResult(
  version: string,
  runtime: string,
  actionTitle: string,
  message: string,
): void;
export function renderQuickMutationResult(
  versionOrViewModel: string | QuickMutationViewModel,
  runtime?: string,
  actionTitle?: string,
  message?: string,
): void {
  if (typeof versionOrViewModel === "object") {
    const resolvedVersion = versionOrViewModel.version ?? CLI_VERSION;
    const resolvedRuntime =
      versionOrViewModel.runtime ?? DEFAULT_RUNTIME_LABEL;
    intro(
      renderCliBanner(
        resolvedVersion,
        resolvedRuntime,
        versionOrViewModel.actionTitle,
      ),
    );
    outro(
      styleText("green", `${TUI_GLYPHS.CHECK} ${versionOrViewModel.message}`),
    );
    return;
  }

  intro(
    renderCliBanner(
      versionOrViewModel,
      runtime ?? DEFAULT_RUNTIME_LABEL,
      actionTitle ?? "Mutation",
    ),
  );
  outro(styleText("green", `${TUI_GLYPHS.CHECK} ${message ?? ""}`));
}
