/**
 * Слой 1 (Host CLI Materializer): публичный фасад (Barrel) пакета `src/cli/materializer/`.
 * Реэкспортирует хранилище манифеста `ProjectHarnessManifest`, стратегии слияния JSON/TOML/Markdown,
 * движок атомарной материализации файлов, генератор каркаса `.context/` и автономные шаблоны адаптеров.
 */

export {
  ALL_HOOK_EVENTS,
  buildProfileOverridesPayload,
  computeContentSha256,
  createDefaultHarnessManifest,
  createDefaultManifest,
  FACTORY_HOOK_SIGNATURE,
  FILE_WRITE_STRATEGIES,
  type FileWriteStrategy,
  HARNESS_MANIFEST_REL_PATH,
  HARNESS_MANIFEST_SCHEMA_VERSION,
  HARNESS_MANIFEST_VERSION,
  type HarnessOverrides,
  type HookEventName,
  hydrateManifestFromCore,
  isHookEventName,
  loadProjectHarnessConfig,
  type MaterializationReport,
  type MaterializeOptions,
  type ProfileOverridesPayload,
  type ProjectHarnessManifest,
  type RenderedFilePayload,
  resolveDisabledSkillPrefix,
  resolveEffectiveStackTags,
  resolveRuleSlugFromRelPath,
  resolveSkillSlugFromRelPath,
  saveProjectHarnessConfig,
  type WriteStrategy,
} from "./manifest-store.ts";

export {
  applyWriteStrategy,
  deepMergeObjects,
  filterDisabledHooksFromPayload,
  MANAGED_MD_END,
  MANAGED_MD_START,
  MANAGED_TOML_END,
  MANAGED_TOML_START,
  mergeJsonContent,
  mergeSectionContent,
  mergeTomlContent,
} from "./mergers.ts";

export {
  cleanupEmptySkillParent,
  materializeFiles,
  pruneDisabledWorkspaceArtifacts,
} from "./file-writer.ts";

export {
  buildContextArchitectureMarkdown,
  buildContextIndexMarkdown,
  buildContextSpecMarkdown,
  CONTEXT_DIR_REL_PATH,
  CONTEXT_ENTITY_DIRS,
  CONTEXT_TEMPLATES_VERSION,
  ensureContextGraphScaffold,
} from "./context-scaffold.ts";

export {
  buildOfflineBootstrapSkillMarkdown,
  buildOfflineCodexConfigToml,
  buildOfflineFallbackFiles,
  buildOfflineHooksJson,
  buildOfflineMcpJson,
  buildOfflineOpencodeConfigJson,
  buildOfflineRulesMarkdown,
  CODEX_MCP_SERVER_KEY,
  DEFAULT_HOOK_TIMEOUT_SECONDS,
  DEFAULT_MCP_SERVER_KEY,
  DEFAULT_OFFLINE_MCP_URL,
} from "./offline-templates.ts";
