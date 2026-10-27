/**
 * Слой конфигурации CLI (`src/cli/config`): централизованные таймауты сетевых и процессных операций,
 * лимиты обхода файловой системы и стоп-слова для автоматического извлечения тегов из слагов компонентов.
 */

export const TIMEOUTS_MS = {
  HEALTH_CHECK_MS: 2_500,
  STACK_HEALTH_WAIT_MS: 45_000,
  STACK_POLL_INTERVAL_MS: 1_000,
  CORE_HTTP_DEFAULT_MS: 15_000,
  CORE_REGISTRATION_MS: 10_000,
  CORE_HYDRATION_MS: 15_000,
  CORE_INGEST_MS: 15_000,
  CORE_CONTEXT_SYNC_MS: 20_000,
  CORE_CONSOLIDATE_MS: 30_000,
  CORE_HEAL_MS: 20_000,
  HOOK_HTTP_MS: 4_500,
  STDIN_READ_MS: 250,
  DOCKER_COMPOSE_MS: 180_000,
  DOCKER_INSPECT_MS: 5_000,
  PG_BACKUP_DUMP_MS: 30_000,
  PG_BACKUP_RESTORE_MS: 45_000,
  BROWSER_OPEN_MS: 5_000,
} as const;

export const LIMITS = {
  MAX_SCAN_DEPTH: 4,
  MAX_CUSTOM_COMPONENT_BYTES: 512 * 1024,
  MIN_TAG_LENGTH: 2,
  MAX_BACKUP_SNAPSHOTS: 10,
} as const;

export const SLUG_TAG_STOP_WORDS: ReadonlySet<string> = new Set([
  "and",
  "the",
  "for",
  "with",
  "from",
  "into",
  "skill",
  "rule",
  "rules",
  "skills",
  "mod",
  "adr",
  "contract",
]);
