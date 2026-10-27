/**
 * Слой 1 (Host CLI TUI Presentation): публичный фасад (Barrel) пакета `src/cli/tui/`.
 * Реэкспортирует дизайн-систему терминального вывода, контроллер прогресс-спиннера,
 * интерактивный визард `init` и все экраны команд CLI по правилу `Barrel per Entity`.
 */

export {
  ALL_LIFECYCLE_HOOK_EVENTS,
  type BadgeTone,
  CARD_VALUE_INDENT,
  DEFAULT_RUNTIME_LABEL,
  EFFECTIVE_COMPONENT_STATES,
  formatAgentSourceBadge,
  formatBadge,
  formatCatalogStateBadge,
  formatManifestOverridesSummary,
  formatStackTagsGrid,
  formatWorkspaceCard,
  padVisible,
  PROVIDER_HEALTH_STATES,
  renderCliBanner,
  renderCliError,
  resolveEffectiveStackTags,
  STACK_TAGS_PER_ROW,
  stripAnsiCodes,
  truncateText,
  TUI_GLYPHS,
  type TuiHarnessManifestView,
  type TuiHarnessOverridesView,
  type TuiMaterializationReportView,
} from "./theme.ts";

export {
  createPipelineProgress,
  type PipelineProgress,
  type PipelineProgressController,
} from "./progress.ts";

export {
  type InitWizardOptions,
  promptSnapshotRestoreSelection,
  runInitWizard,
  type SnapshotPromptOptions,
  type SnapshotSelectionOutcome,
  type WizardOptions,
  type WizardSelectionResult,
} from "./init-wizard.ts";

export {
  type InitSummaryViewModel,
  renderInitSummary,
  renderSyncCommand,
  renderSyncSummary,
  type SyncSummaryViewModel,
} from "./views-init-sync.ts";

export {
  type ConsolidationViewModel,
  type HarnessCatalogViewModel,
  type ProvidersViewModel,
  type QuickMutationViewModel,
  renderConsolidationView,
  renderHarnessCatalogView,
  renderProvidersView,
  renderQuickMutationResult,
} from "./views-catalog-providers.ts";

export {
  type DownCommandViewModel,
  type HelpScreenViewModel,
  renderDownCommand,
  renderHelpScreen,
  renderStatusCommand,
  renderUiCommand,
  renderUpCommand,
  type StatusCommandViewModel,
  type StatusLiveContext,
  type UiCommandViewModel,
  type UpCommandViewModel,
} from "./views-lifecycle.ts";
