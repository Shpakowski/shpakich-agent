/**
 * Публичный фасад (Barrel) пакета конфигурации CLI (`src/cli/config`).
 * Инкапсулирует и реэкспортирует константы агентов, сетевых портов, путей, лимитов
 * и сигнатур стека для всех остальных пакетов CLI по правилу `Barrel per Entity`.
 */

export {
  AGENT_CONFIG_HINTS,
  AGENT_DIALECTS,
  AGENT_DISPLAY_NAMES,
  AGENT_METADATA,
  AGENT_TYPES,
  HOOK_EVENT_TYPES,
  SUPPORTED_AGENTS_LABEL,
  isValidAgentType,
  normalizeHookEventName,
  type AgentDescriptor,
  type AgentDialect,
  type AgentType,
  type HookEventType,
} from "./agents.ts";

export {
  CLI_VERSION,
  DEFAULT_CORE_BASE_URL,
  DEFAULT_CORE_HOST,
  DEFAULT_SERVICE_PORTS,
  DOCKER_CONTAINER_NAMES,
  ENV_VARS,
  GLOBAL_FACTORY_DIR_NAME,
  GLOBAL_FACTORY_RELATIVE_PATHS,
  HARNESS_CONSOLIDATION_THRESHOLD_HOURS,
  MANIFEST_SCHEMA_VERSION,
  OFFLINE_ORCHESTRATOR_SLUG,
  POSTGRES_BACKUP_CONFIG,
  WORKSPACE_RELATIVE_PATHS,
  resolveCoreBaseUrl,
  resolveGlobalFactoryDir,
  resolveMcpEndpointUrl,
  resolveUiDashboardUrl,
} from "./ports-and-paths.ts";

export {
  LIMITS,
  SLUG_TAG_STOP_WORDS,
  TIMEOUTS_MS,
} from "./limits-and-timeouts.ts";

export {
  buildStackSignatureRulesFromComponents,
  FILE_MARKER_TAG_MAP,
  HARNESS_SEED_FILE_NAME,
  IGNORED_SCAN_DIRECTORIES,
  loadHarnessSeedCatalog,
  loadPromptsConfigJson,
  NODE_DEP_TAG_MAP,
  PROMPTS_CONFIG_FILE_NAME,
  PYTHON_DEP_TAG_MAP,
  STACK_SIGNATURE_RULES,
  UNIVERSAL_STACK_TAGS,
  type CatalogComponentDetectorSource,
  type HarnessSeedCatalog,
  type SeedAgentEntry,
  type SeedComponentDetectors,
  type SeedComponentEntry,
  type StackSignatureRule,
} from "./stack-signatures.ts";
