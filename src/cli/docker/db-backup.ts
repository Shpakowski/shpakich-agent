/**
 * Слой 1 (Host CLI Docker Adapter — Database Backup & Restore):
 * Автоматическое создание слепков знаний PostgreSQL 18 (`pg_dump`) при остановке стека (`down`)
 * в директорию `.backups/mcp-coop-agentic-factory-<timestamp>.sql` с ротацией последних 10 файлов,
 * а также интерактивное и автоматическое восстановление при старте (`up` / `init`).
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import { runSubprocess, writeFileAtomicSync } from "../common/index.ts";
import {
  DOCKER_CONTAINER_NAMES,
  LIMITS,
  POSTGRES_BACKUP_CONFIG,
  TIMEOUTS_MS,
  WORKSPACE_RELATIVE_PATHS,
} from "../config/index.ts";
import { resolveCliPackageRoot } from "./assets.ts";
import {
  DEFAULT_WAIT_HEALTH_TIMEOUT_MS,
  DOCKER_BINARY,
  FAST_HEALTH_PROBE_TIMEOUT_MS,
  HEALTH_POLL_INTERVAL_MS,
  waitForCoreReady,
} from "./stack-status.ts";

const EMPTY_KNOWLEDGE_CHECK_SQL = [
  "SELECT (",
  "  COALESCE((SELECT COUNT(*) FROM public.projects), 0) +",
  "  COALESCE((SELECT COUNT(*) FROM public.memories), 0) +",
  "  COALESCE((SELECT COUNT(*) FROM public.project_context_nodes), 0) +",
  "  COALESCE((SELECT COUNT(*) FROM public.session_compactions), 0) +",
  "  COALESCE((SELECT COUNT(*) FROM public.decision_providers), 0) +",
  "  COALESCE((SELECT COUNT(*) FROM public.harness_components WHERE source_project_path IS NOT NULL OR usage_count > 0 OR version > 1), 0)",
  ");",
].join(" ");

const SCHEMA_EXISTS_CHECK_SQL =
  "SELECT COALESCE((SELECT COUNT(*) FROM public.harness_components), 0) + COALESCE((SELECT COUNT(*) FROM public.projects), 0);" as const;

const TERMINATE_OTHER_BACKENDS_SQL =
  `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_BACKUP_CONFIG.DB_NAME}' AND pid <> pg_backend_pid();` as const;

/**
 * Метаданные отдельного файла слепка БД в `.backups/` для отображения в TUI-меню выбора.
 */
export interface BackupSnapshotMetadata {
  readonly path: string;
  readonly filename: string;
  readonly sizeBytes: number;
  readonly formattedSize: string;
  readonly timestampLabel: string;
}

/**
 * Результат создания слепка знаний PostgreSQL при выполнении `down`.
 */
export interface BackupSnapshotResult {
  readonly created: boolean;
  readonly snapshotPath?: string | undefined;
  readonly prunedPaths: readonly string[];
  readonly skippedReason?: string | undefined;
}

/**
 * Результат проверки и восстановления слепка знаний PostgreSQL при выполнении `up` / `init`.
 */
export interface RestoreSnapshotResult {
  readonly restored: boolean;
  readonly snapshotPath?: string | undefined;
  readonly skippedReason?:
    | "no_backup_found"
    | "db_not_empty"
    | "user_skipped"
    | "postgres_unreachable"
    | "restore_failed"
    | undefined;
  readonly errorMessage?: string | undefined;
}

/**
 * Форматирует размер файла в байтах в человекочитаемую строку (`KB` / `MB`).
 */
export function formatSnapshotSize(bytes: number): string {
  if (bytes < 1024) {
    return `${String(bytes)} B`;
  }
  const kb = bytes / 1024;
  if (kb < 1024) {
    return `${kb.toFixed(1)} KB`;
  }
  const mb = kb / 1024;
  return `${mb.toFixed(2)} MB`;
}

/**
 * Извлекает читаемую дату и время `YYYY-MM-DD HH:mm:ss` из имени файла слепка.
 */
export function parseSnapshotTimestampLabel(filename: string): string {
  const prefix = `${POSTGRES_BACKUP_CONFIG.FILE_PREFIX}-`;
  const ext = POSTGRES_BACKUP_CONFIG.FILE_EXTENSION;
  if (!filename.startsWith(prefix) || !filename.endsWith(ext)) {
    return filename;
  }
  const rawStamp = filename.slice(prefix.length, filename.length - ext.length);
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})$/.exec(rawStamp);
  if (!match) {
    return rawStamp;
  }
  return `${match[1]} ${match[2]}:${match[3]}:${match[4]}`;
}

/**
 * Форматирует дату в безопасный для файловой системы и лексикографически сортируемый
 * штамп времени `YYYY-MM-DDTHH-mm-ss`.
 */
export function formatBackupTimestamp(date: Date = new Date()): string {
  const pad = (n: number): string => String(n).padStart(2, "0");
  const year = date.getFullYear();
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());
  const seconds = pad(date.getSeconds());
  return `${String(year)}-${month}-${day}T${hours}-${minutes}-${seconds}`;
}

/**
 * Строит каноническое имя файла слепка фабрики: `mcp-coop-agentic-factory-YYYY-MM-DDTHH-mm-ss.sql`.
 */
export function buildBackupFilename(date: Date = new Date()): string {
  return `${POSTGRES_BACKUP_CONFIG.FILE_PREFIX}-${formatBackupTimestamp(date)}${POSTGRES_BACKUP_CONFIG.FILE_EXTENSION}`;
}

/**
 * Вычисляет абсолютный путь к директории `.backups/` внутри указанного рабочего пространства.
 */
export function resolveWorkspaceBackupsDir(
  workspaceDir: string = process.cwd(),
): string {
  return path.join(
    path.resolve(workspaceDir),
    WORKSPACE_RELATIVE_PATHS.BACKUPS_DIR,
  );
}

/**
 * Возвращает список абсолютных путей к файлам слепков `mcp-coop-agentic-factory-*.sql`
 * в директории `backupsDir`, отсортированный от самого свежего к старым.
 */
export function listBackupSnapshots(backupsDir: string): string[] {
  if (!fs.existsSync(backupsDir)) {
    return [];
  }

  const prefix = `${POSTGRES_BACKUP_CONFIG.FILE_PREFIX}-`;
  const ext = POSTGRES_BACKUP_CONFIG.FILE_EXTENSION;

  try {
    const entries = fs.readdirSync(backupsDir, { withFileTypes: true });
    const matchedNames = entries
      .filter(
        (entry) =>
          entry.isFile() &&
          entry.name.startsWith(prefix) &&
          entry.name.endsWith(ext),
      )
      .map((entry) => entry.name)
      .sort((a, b) => b.localeCompare(a));

    return matchedNames.map((name) => path.join(backupsDir, name));
  } catch {
    return [];
  }
}

/**
 * Возвращает упорядоченный (от новейшего к старым) массив метаданных доступных слепков
 * в `.backups/` рабочей директории (или корня пакета CLI, если в текущем каталоге `.backups/` пуст).
 */
export function listAvailableBackupSnapshots(
  workspaceDir: string = process.cwd(),
): BackupSnapshotMetadata[] {
  const candidateDirs = [
    resolveWorkspaceBackupsDir(workspaceDir),
    resolveWorkspaceBackupsDir(resolveCliPackageRoot()),
  ];

  const seenDirs = new Set<string>();
  for (const dir of candidateDirs) {
    const resolvedDir = path.resolve(dir);
    if (seenDirs.has(resolvedDir)) {
      continue;
    }
    seenDirs.add(resolvedDir);
    const paths = listBackupSnapshots(resolvedDir);
    if (paths.length > 0) {
      return paths.map((snapshotPath) => {
        const filename = path.basename(snapshotPath);
        let sizeBytes = 0;
        try {
          sizeBytes = fs.statSync(snapshotPath).size;
        } catch {
          sizeBytes = 0;
        }
        return {
          path: snapshotPath,
          filename,
          sizeBytes,
          formattedSize: formatSnapshotSize(sizeBytes),
          timestampLabel: parseSnapshotTimestampLabel(filename),
        };
      });
    }
  }

  return [];
}

/**
 * Ротирует слепки в директории `backupsDir`, оставляя не более `maxSnapshots` (по умолчанию 10)
 * самых свежих файлов и удаляя более старые. Возвращает массив удалённых путей.
 */
export function pruneOldBackupSnapshots(
  backupsDir: string,
  maxSnapshots: number = LIMITS.MAX_BACKUP_SNAPSHOTS,
): string[] {
  const allSnapshots = listBackupSnapshots(backupsDir);
  if (allSnapshots.length <= maxSnapshots) {
    return [];
  }

  const toDelete = allSnapshots.slice(maxSnapshots);
  const deleted: string[] = [];

  for (const oldPath of toDelete) {
    try {
      fs.rmSync(oldPath, { force: true });
      deleted.push(oldPath);
    } catch {
      // Игнорируем ошибку удаления отдельного устаревшего файла при ротации.
    }
  }

  return deleted;
}

/**
 * Находит самый свежий файл слепка `.backups/mcp-coop-agentic-factory-*.sql`, проверяя
 * сначала текущую рабочую директорию `workspaceDir`, а затем корень репозитория CLI.
 */
export function findLatestBackupSnapshot(
  workspaceDir: string = process.cwd(),
): string | null {
  const available = listAvailableBackupSnapshots(workspaceDir);
  return available[0]?.path ?? null;
}

/**
 * Синхронно проверяет, запущен ли контейнер PostgreSQL и содержит ли он инициализированную схему с данными,
 * затем снимает SQL-дамп всех таблиц знаний (исключая сырые логи `hook_events`), атомарно сохраняет его
 * в `.backups/mcp-coop-agentic-factory-<timestamp>.sql` и ротирует старые слепки (хранит последние 10).
 */
export function createPostgresBackupSnapshot(options?: {
  readonly workspaceDir?: string | undefined;
  readonly maxSnapshots?: number | undefined;
}): BackupSnapshotResult {
  const workspaceDir = options?.workspaceDir ?? process.cwd();
  const maxSnapshots = options?.maxSnapshots ?? LIMITS.MAX_BACKUP_SNAPSHOTS;
  const pgContainer = DOCKER_CONTAINER_NAMES.POSTGRES;
  const dbUser = POSTGRES_BACKUP_CONFIG.DB_USER;
  const dbName = POSTGRES_BACKUP_CONFIG.DB_NAME;

  const readyProbe = spawnSync(
    DOCKER_BINARY,
    ["exec", pgContainer, "pg_isready", "-U", dbUser, "-d", dbName],
    {
      encoding: "utf-8",
      timeout: TIMEOUTS_MS.DOCKER_INSPECT_MS,
    },
  );

  if (readyProbe.error || readyProbe.status !== 0) {
    return {
      created: false,
      prunedPaths: [],
      skippedReason: "PostgreSQL container is not running",
    };
  }

  const countProbe = spawnSync(
    DOCKER_BINARY,
    [
      "exec",
      pgContainer,
      "psql",
      "-U",
      dbUser,
      "-d",
      dbName,
      "-tAc",
      SCHEMA_EXISTS_CHECK_SQL,
    ],
    {
      encoding: "utf-8",
      timeout: TIMEOUTS_MS.DOCKER_INSPECT_MS,
    },
  );

  const rowCount = Number.parseInt((countProbe.stdout ?? "").trim(), 10);
  if (
    countProbe.error ||
    countProbe.status !== 0 ||
    Number.isNaN(rowCount) ||
    rowCount <= 0
  ) {
    return {
      created: false,
      prunedPaths: [],
      skippedReason: "Database schema is empty or uninitialized",
    };
  }

  const dumpProc = spawnSync(
    DOCKER_BINARY,
    [
      "exec",
      pgContainer,
      "pg_dump",
      "-U",
      dbUser,
      "-d",
      dbName,
      "--clean",
      "--if-exists",
      "--no-owner",
      "--no-privileges",
      `--exclude-table-data=public.${POSTGRES_BACKUP_CONFIG.EXCLUDED_TABLE_DATA}`,
      `--exclude-table-data=${POSTGRES_BACKUP_CONFIG.EXCLUDED_TABLE_DATA}`,
    ],
    {
      encoding: "utf-8",
      timeout: TIMEOUTS_MS.PG_BACKUP_DUMP_MS,
      maxBuffer: 64 * 1024 * 1024,
    },
  );

  const sqlOutput = dumpProc.stdout ?? "";
  if (dumpProc.error || dumpProc.status !== 0 || sqlOutput.trim().length === 0) {
    return {
      created: false,
      prunedPaths: [],
      skippedReason:
        dumpProc.error?.message ||
        dumpProc.stderr?.trim() ||
        "pg_dump returned empty output",
    };
  }

  const backupsDir = resolveWorkspaceBackupsDir(workspaceDir);
  const snapshotPath = path.join(backupsDir, buildBackupFilename());

  try {
    writeFileAtomicSync(snapshotPath, sqlOutput);
    const prunedPaths = pruneOldBackupSnapshots(backupsDir, maxSnapshots);
    return {
      created: true,
      snapshotPath,
      prunedPaths,
    };
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return {
      created: false,
      prunedPaths: [],
      skippedReason: msg,
    };
  }
}

/**
 * Проверяет, пуста ли база знаний PostgreSQL (возвращает `true`, если в БД нет ни одного
 * зарегистрированного проекта, записи `memories`, узла `project_context_nodes`, сводки
 * `session_compactions`, провайдера `decision_providers` и пользовательских правок в `harness_components`).
 */
export async function isPostgresKnowledgeEmpty(): Promise<boolean | null> {
  const result = await runSubprocess(
    DOCKER_BINARY,
    [
      "exec",
      DOCKER_CONTAINER_NAMES.POSTGRES,
      "psql",
      "-U",
      POSTGRES_BACKUP_CONFIG.DB_USER,
      "-d",
      POSTGRES_BACKUP_CONFIG.DB_NAME,
      "-tAc",
      EMPTY_KNOWLEDGE_CHECK_SQL,
    ],
    { timeoutMs: TIMEOUTS_MS.DOCKER_INSPECT_MS },
  );

  if (result.exitCode !== 0) {
    return null;
  }

  const count = Number.parseInt(result.stdout.trim(), 10);
  if (Number.isNaN(count)) {
    return null;
  }

  return count === 0;
}

/**
 * Восстанавливает указанный SQL-слепок `snapshotPath` в контейнер PostgreSQL 18:
 * временно останавливает `core` для снятия блокировок, накатывает дамп через `psql`,
 * очищает оперативный кэш Redis, запускает `core` обратно и дожидается готовности `/health`.
 */
export async function restorePostgresSnapshotFile(
  snapshotPath: string,
  options?: {
    readonly onProgress?: ((phaseText: string) => void) | undefined;
  },
): Promise<RestoreSnapshotResult> {
  options?.onProgress?.(
    `Restoring PostgreSQL knowledge snapshot ${path.basename(snapshotPath)}...`,
  );

  let sqlContent: string;
  try {
    sqlContent = fs.readFileSync(snapshotPath, "utf-8");
  } catch (err: unknown) {
    return {
      restored: false,
      snapshotPath,
      skippedReason: "restore_failed",
      errorMessage: err instanceof Error ? err.message : String(err),
    };
  }

  await runSubprocess(
    DOCKER_BINARY,
    ["stop", DOCKER_CONTAINER_NAMES.CORE],
    { timeoutMs: TIMEOUTS_MS.DOCKER_INSPECT_MS },
  );

  await runSubprocess(
    DOCKER_BINARY,
    [
      "exec",
      DOCKER_CONTAINER_NAMES.POSTGRES,
      "psql",
      "-U",
      POSTGRES_BACKUP_CONFIG.DB_USER,
      "-d",
      POSTGRES_BACKUP_CONFIG.DB_NAME,
      "-tAc",
      TERMINATE_OTHER_BACKENDS_SQL,
    ],
    { timeoutMs: TIMEOUTS_MS.DOCKER_INSPECT_MS },
  );

  const restoreRes = await runSubprocess(
    DOCKER_BINARY,
    [
      "exec",
      "-i",
      DOCKER_CONTAINER_NAMES.POSTGRES,
      "psql",
      "-v",
      "ON_ERROR_STOP=1",
      "-U",
      POSTGRES_BACKUP_CONFIG.DB_USER,
      "-d",
      POSTGRES_BACKUP_CONFIG.DB_NAME,
    ],
    {
      stdinData: sqlContent,
      timeoutMs: TIMEOUTS_MS.PG_BACKUP_RESTORE_MS,
    },
  );

  await runSubprocess(
    DOCKER_BINARY,
    ["exec", DOCKER_CONTAINER_NAMES.REDIS, "redis-cli", "FLUSHDB"],
    { timeoutMs: TIMEOUTS_MS.DOCKER_INSPECT_MS },
  );

  await runSubprocess(
    DOCKER_BINARY,
    ["start", DOCKER_CONTAINER_NAMES.CORE],
    { timeoutMs: TIMEOUTS_MS.DOCKER_INSPECT_MS },
  );

  await waitForCoreReady({
    timeoutMs: DEFAULT_WAIT_HEALTH_TIMEOUT_MS,
    pollIntervalMs: HEALTH_POLL_INTERVAL_MS,
    probeTimeoutMs: FAST_HEALTH_PROBE_TIMEOUT_MS,
  });

  if (restoreRes.exitCode !== 0) {
    return {
      restored: false,
      snapshotPath,
      skippedReason: "restore_failed",
      errorMessage:
        restoreRes.stderr.trim() ||
        restoreRes.stdout.trim() ||
        "psql exited with non-zero code during snapshot restore",
    };
  }

  return {
    restored: true,
    snapshotPath,
  };
}

/**
 * Проверяет наличие слепка в `.backups/` при неинтерактивном запуске стека и автоматически
 * подливает самый свежий слепок строго в том случае, если база данных PostgreSQL пустая.
 */
export async function restoreLatestPostgresBackupIfEmpty(options?: {
  readonly workspaceDir?: string | undefined;
  readonly onProgress?: ((phaseText: string) => void) | undefined;
}): Promise<RestoreSnapshotResult> {
  const snapshotPath = findLatestBackupSnapshot(options?.workspaceDir);
  if (!snapshotPath) {
    return {
      restored: false,
      skippedReason: "no_backup_found",
    };
  }

  const isEmpty = await isPostgresKnowledgeEmpty();
  if (isEmpty === null) {
    return {
      restored: false,
      snapshotPath,
      skippedReason: "postgres_unreachable",
    };
  }

  if (!isEmpty) {
    return {
      restored: false,
      snapshotPath,
      skippedReason: "db_not_empty",
    };
  }

  return restorePostgresSnapshotFile(snapshotPath, {
    onProgress: options?.onProgress,
  });
}
