/**
 * Слой 1 (Host CLI Sync): публичный фасад (Barrel) пакета `src/cli/sync/`.
 * Реэкспортирует сканер кастомных компонентов (`Anti-Self-Ingest`), 3-Way Hash синхронизатор `.context/`
 * и оркестратор материализации артефактов проекта по правилу `Barrel per Entity`.
 */

export {
  computeNormalizedContentHash,
  CUSTOM_RULE_DIRS,
  CUSTOM_SKILL_PARENT_DIRS,
  extractFrontmatterBlock,
  extractFrontmatterScalar,
  extractFrontmatterTags,
  extractTagsFromSlug,
  inferStackTagsFromSlug,
  scanCustomWorkspaceComponents,
  SYSTEM_BOOTSTRAP_SLUGS,
  type ComponentIngestItemPayload,
  type ComponentIngestReportPayload,
  type ConsolidationClusterPayload,
  type ConsolidationReportPayload,
  type WorkspaceComponentCandidatePayload,
} from "./component-scanner.ts";

export {
  applyContextSyncResults,
  collectWorkspaceContextNodes,
  type AppliedContextSyncSummary,
  type ContextNodeSyncInputPayload,
  type ContextNodeSyncItemPayload,
  type ContextSyncReportPayload,
} from "./context-sync.ts";

export {
  HARNESS_ORCHESTRATOR_SLUG,
  resolveOfflineSkillPath,
  syncWorkspaceArtifacts,
  type WorkspaceArtifactsSyncResult,
} from "./sync-orchestrator.ts";
