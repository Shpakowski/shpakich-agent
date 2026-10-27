/**
 * Слой 1 (Host CLI Docker Adapter): публичный фасад (Barrel) пакета `src/cli/docker/`.
 * Экспортирует контракты и утилиты синхронизации ассетов глобального стека (`~/.mcp-coop-agentic-factory/`),
 * генерации `bin/run-hook.sh`, диагностики Docker Engine / `/health` и оркестрации `docker compose`.
 */

export {
  COMPOSE_REL_PATH,
  CORE_SRC_REL_PATH,
  CORE_URL_ENV_KEY,
  DEFAULT_CORE_BASE_URL,
  DOCKERFILE_REL_PATH,
  ENV_FILE_NAME,
  FACTORY_HOME_ENV_KEY,
  GLOBAL_FACTORY_DIR_NAME,
  type GlobalStackAssetsResult,
  HOOK_LAUNCHER_REL_PATH,
  buildHookLauncherScript,
  ensureGlobalDockerAssets,
  getGlobalFactoryDir,
  getGlobalHookRunnerPath,
  resolveCliPackageRoot,
  resolveCoreBaseUrl,
  resolveRepoRootFromCli,
  syncGlobalFactoryStackFiles,
  syncGlobalStackAssets,
} from "./assets.ts";

export {
  type ContainerRuntimeInfo,
  type CoreHealthStatus,
  DEFAULT_HEALTH_TIMEOUT_MS,
  DEFAULT_WAIT_HEALTH_TIMEOUT_MS,
  DOCKER_BINARY,
  type DockerDaemonStatus,
  FAST_HEALTH_PROBE_TIMEOUT_MS,
  type GlobalStackStatus,
  HEALTH_ENDPOINT,
  HEALTH_POLL_INTERVAL_MS,
  type WaitForCoreReadyOptions,
  checkCoreHealth,
  checkDockerDaemonAvailable,
  createUnreachableHealthStatus,
  getGlobalStackStatus,
  inspectGlobalDockerStack,
  isDockerAvailable,
  listStackContainers,
  parseDockerComposePsJson,
  waitForCoreHealth,
  waitForCoreReady,
} from "./stack-status.ts";

export {
  type DockerBootstrapResult,
  type DockerStackResult,
  type EnsureGlobalStackOptions,
  type StopDockerStackResult,
  ensureGlobalDockerStack,
  ensureGlobalStackUp,
  extractDockerProgressPhase,
  formatDockerErrorSummary,
  stopGlobalDockerStack,
  stopGlobalStackDown,
} from "./compose-runner.ts";

export {
  type BackupSnapshotMetadata,
  type BackupSnapshotResult,
  type RestoreSnapshotResult,
  buildBackupFilename,
  createPostgresBackupSnapshot,
  findLatestBackupSnapshot,
  formatBackupTimestamp,
  formatSnapshotSize,
  isPostgresKnowledgeEmpty,
  listAvailableBackupSnapshots,
  listBackupSnapshots,
  parseSnapshotTimestampLabel,
  pruneOldBackupSnapshots,
  resolveWorkspaceBackupsDir,
  restoreLatestPostgresBackupIfEmpty,
  restorePostgresSnapshotFile,
} from "./db-backup.ts";
