/**
 * Слой 1 (Host CLI Core API Adapter): публичный фасад (Barrel) пакета `src/cli/core-api/`.
 * Объединяет и реэкспортирует низкоуровневый HTTP-клиент `requestCoreJson`, проверку `/health`
 * и все типизированные методы взаимодействия с REST API Python-ядра по правилу `Barrel per Entity`.
 */

export {
  checkCoreHealth,
  type CoreApiError,
  type CoreHealthStatus,
  type CoreHttpMethod,
  type CoreRequestOptions,
  HEALTH_ENDPOINT,
  requestCoreJson,
} from "./http-client.ts";

export {
  CONTEXT_HEAL_ENDPOINT,
  type ContextHealRequestPayload,
  type ContextHealResponse,
  healProjectContext,
  REGISTER_PROJECT_ENDPOINT,
  type RegisterProjectPayload,
  registerProjectWithCore,
} from "./endpoints-projects.ts";

export {
  type CatalogComponentItem,
  type ConsolidateHarnessRequestPayload,
  type ConsolidateHarnessResult,
  consolidateHarnessComponents,
  type ConsolidationClusterPayload,
  type ConsolidationReportPayload,
  fetchHarnessCatalog,
  HARNESS_COMPONENTS_ENDPOINT,
  HARNESS_CONSOLIDATE_ENDPOINT,
  type HarnessCatalogItem,
  type HarnessCatalogResponse,
  patchGlobalComponentStatus,
} from "./endpoints-harness.ts";

export {
  type ComponentIngestItemPayload,
  type ComponentIngestReportPayload,
  type ContextNodeSyncInputPayload,
  type ContextNodeSyncItemPayload,
  type ContextSyncReportPayload,
  ingestWorkspaceComponents,
  SYNC_CONTEXT_ENDPOINT,
  SYNC_INGEST_ENDPOINT,
  syncWorkspaceContextNodes,
  type WorkspaceComponentCandidatePayload,
} from "./endpoints-sync.ts";

export {
  deleteTier2Provider,
  listTier2Providers,
  type PatchProviderPayload,
  patchTier2Provider,
  PROVIDERS_ENDPOINT,
  type ProviderStatusItem,
  type Tier2ProviderRecord,
  type UpsertProviderPayload,
  upsertTier2Provider,
} from "./endpoints-providers.ts";

export {
  AGENT_TYPE_HEADER,
  type CoreHookResponsePayload,
  type CoreHookSyncFilePayload,
  dispatchHookToCore,
  HOOKS_BASE_ENDPOINT,
} from "./endpoints-hooks.ts";
