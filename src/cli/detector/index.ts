/**
 * Слой 1 (Host CLI Detector): публичный фасад (Barrel) пакета `src/cli/detector/`.
 * Реэкспортирует сканер технологического стека, инспектор графа `.context/`, резолвер целевого
 * ИИ-агента и метаданные агентов для сохранения единого контракта `Barrel per Entity`.
 */

export {
  AGENT_CONFIG_HINTS,
  AGENT_DISPLAY_NAMES,
  AGENT_TYPES,
  isValidAgentType,
  SUPPORTED_AGENTS_LABEL,
  type AgentType,
} from "../config/index.ts";

export {
  detectStackTags,
  FILE_STACK_RULES,
  getAllKnownStackTags,
  GLOBAL_STACK_TAG,
  PACKAGE_JSON_DEPENDENCY_RULES,
  TEXT_MANIFEST_TOKEN_RULES,
  type FileStackRule,
  type TextManifestTokenRule,
} from "./stack-scanner.ts";

export {
  CONTEXT_GRAPH_INDEX_REL_PATH,
  getContextGraphLastUpdated,
  hasWorkspaceContextGraph,
  inspectContextGraph,
  resolveContextIndexPath,
  type ContextGraphInspection,
} from "./context-inspector.ts";

export {
  AGENT_ENV_KEYS,
  AGENT_WORKSPACE_MARKERS,
  AUTO_AGENT_OPTION,
  DEFAULT_AGENT,
  inspectWorkspace,
  isSupportedAgent,
  resolveTargetAgent,
  SUPPORTED_AGENTS,
  type AgentId,
  type AgentOption,
  type AgentResolutionResult,
  type AgentResolutionSource,
  type AgentWorkspaceMarkerRule,
  type ResolvedAgentInfo,
  type WorkspaceInspection,
  type WorkspaceProfile,
} from "./agent-resolver.ts";
