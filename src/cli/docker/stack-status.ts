/**
 * Слой 1 (Host CLI Docker Adapter): диагностика Docker Engine, опрос контейнеров
 * через `docker compose ps` и проверка HTTP-готовности `/health` глобального аналитического ядра.
 */

import path from "node:path";

import { isJsonObject, pathExists, runSubprocess } from "../common/index.ts";
import {
  DOCKER_CONTAINER_NAMES,
  TIMEOUTS_MS,
  resolveCoreBaseUrl,
} from "../config/index.ts";
import { COMPOSE_REL_PATH, getGlobalFactoryDir } from "./assets.ts";

export const HEALTH_ENDPOINT = "/health" as const;
export const DOCKER_BINARY = "docker" as const;

export const DEFAULT_HEALTH_TIMEOUT_MS = 1_500 as const;
export const FAST_HEALTH_PROBE_TIMEOUT_MS = 1_200 as const;
export const DEFAULT_WAIT_HEALTH_TIMEOUT_MS = 10_000 as const;
export const HEALTH_POLL_INTERVAL_MS = 600 as const;

/**
 * Диагностический снимок состояния эндпоинта `GET /health` аналитического Python-ядра.
 */
export interface CoreHealthStatus {
  reachable: boolean;
  status: string;
  version?: string | undefined;
  postgres_connected: boolean;
  redis_connected: boolean;
  decisions_api_configured: boolean;
  embedding_api_configured: boolean;
  antigravity_sdk_configured?: boolean | undefined;
  mcp_endpoint?: string | undefined;
}

/**
 * Результат проверки доступности бинарника Docker CLI и запущенного Docker Engine демона.
 */
export interface DockerDaemonStatus {
  readonly available: boolean;
  readonly daemonRunning: boolean;
  readonly serverVersion?: string | undefined;
  readonly errorMessage?: string | undefined;
}

/**
 * Состояние отдельного контейнера глобального стека из вывода `docker compose ps`.
 */
export interface ContainerRuntimeInfo {
  readonly name: string;
  readonly service: string;
  readonly state: string;
  readonly status: string;
  readonly health?: string | undefined;
  readonly ports?: string | undefined;
}

/**
 * Комплексный диагностический статус глобального Docker-стека и аналитического ядра.
 */
export interface GlobalStackStatus {
  readonly stackDir: string;
  readonly composeFilePath: string;
  readonly composeFileExists: boolean;
  readonly dockerAvailable: boolean;
  readonly daemonRunning: boolean;
  readonly containers: readonly ContainerRuntimeInfo[];
  readonly runningContainersCount: number;
  readonly coreHealthy: boolean;
  readonly health: CoreHealthStatus;
  readonly errorMessage?: string | undefined;
}

/**
 * Параметры ожидания готовности эндпоинта `/health` после старта контейнеров.
 */
export interface WaitForCoreReadyOptions {
  readonly timeoutMs?: number | undefined;
  readonly pollIntervalMs?: number | undefined;
  readonly probeTimeoutMs?: number | undefined;
  readonly baseUrl?: string | undefined;
  readonly onProgress?:
    | ((phaseText: string, attempt: number, maxAttempts: number) => void)
    | undefined;
}

function sleepMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Формирует безопасный объект `CoreHealthStatus` при недоступности ядра по сети (политика Fail-Open).
 */
export function createUnreachableHealthStatus(
  status = "unreachable",
): CoreHealthStatus {
  return {
    reachable: false,
    status,
    postgres_connected: false,
    redis_connected: false,
    decisions_api_configured: false,
    embedding_api_configured: false,
  };
}

/**
 * Опрашивает диагностический эндпоинт `/health` аналитического ядра с жёстким таймаутом.
 * При любой сетевой ошибке возвращает безопасный объект с `reachable: false` (Fail-Open).
 */
export async function checkCoreHealth(
  timeoutMs: number = DEFAULT_HEALTH_TIMEOUT_MS,
  baseUrl: string = resolveCoreBaseUrl(),
): Promise<CoreHealthStatus> {
  const cleanUrl = `${baseUrl.replace(/\/+$/, "")}${HEALTH_ENDPOINT}`;
  try {
    const response = await fetch(cleanUrl, {
      method: "GET",
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) {
      return createUnreachableHealthStatus(`http_${String(response.status)}`);
    }
    const data: unknown = await response.json();
    if (!isJsonObject(data)) {
      return createUnreachableHealthStatus("invalid_payload");
    }
    return {
      reachable: true,
      status: typeof data.status === "string" ? data.status : "ok",
      version: typeof data.version === "string" ? data.version : undefined,
      postgres_connected: Boolean(data.postgres_connected),
      redis_connected: Boolean(data.redis_connected),
      decisions_api_configured: Boolean(data.decisions_api_configured),
      embedding_api_configured: Boolean(data.embedding_api_configured),
      antigravity_sdk_configured:
        typeof data.antigravity_sdk_configured === "boolean"
          ? data.antigravity_sdk_configured
          : undefined,
      mcp_endpoint:
        typeof data.mcp_endpoint === "string" ? data.mcp_endpoint : undefined,
    };
  } catch {
    return createUnreachableHealthStatus();
  }
}

/**
 * Циклически опрашивает `/health` аналитического ядра до получения `reachable: true`
 * или истечения `timeoutMs`, уведомляя вызывающий код о номере попытки через `onProgress`.
 */
export async function waitForCoreReady(
  options?: WaitForCoreReadyOptions,
): Promise<CoreHealthStatus> {
  const waitTimeoutMs = options?.timeoutMs ?? DEFAULT_WAIT_HEALTH_TIMEOUT_MS;
  const pollIntervalMs = options?.pollIntervalMs ?? HEALTH_POLL_INTERVAL_MS;
  const probeTimeoutMs = options?.probeTimeoutMs ?? FAST_HEALTH_PROBE_TIMEOUT_MS;
  const baseUrl = options?.baseUrl ?? resolveCoreBaseUrl();
  const maxAttempts = Math.max(1, Math.ceil(waitTimeoutMs / pollIntervalMs));
  const deadline = Date.now() + waitTimeoutMs;

  let attempt = 1;
  options?.onProgress?.(
    `[3/4] Waiting for FastAPI + FastMCP Core readiness (/health) — attempt ${String(attempt)}/${String(maxAttempts)}...`,
    attempt,
    maxAttempts,
  );
  let latestHealth = await checkCoreHealth(probeTimeoutMs, baseUrl);

  while (!latestHealth.reachable && Date.now() < deadline) {
    await sleepMs(pollIntervalMs);
    attempt += 1;
    const boundedAttempt = Math.min(attempt, maxAttempts);
    options?.onProgress?.(
      `[3/4] Waiting for FastAPI + FastMCP Core readiness (/health) — attempt ${String(boundedAttempt)}/${String(maxAttempts)}...`,
      boundedAttempt,
      maxAttempts,
    );
    latestHealth = await checkCoreHealth(probeTimeoutMs, baseUrl);
  }

  return latestHealth;
}

/**
 * Синоним `waitForCoreReady` для совместимости потребительских модулей.
 */
export const waitForCoreHealth = waitForCoreReady;

/**
 * Проверяет наличие Docker CLI и запущенного Docker Engine демона через `docker info`.
 */
export async function checkDockerDaemonAvailable(
  timeoutMs: number = TIMEOUTS_MS.DOCKER_INSPECT_MS,
): Promise<DockerDaemonStatus> {
  const result = await runSubprocess(
    DOCKER_BINARY,
    ["info", "--format", "{{.ServerVersion}}"],
    { timeoutMs },
  );

  if (result.exitCode === 0) {
    const version = result.stdout.trim();
    return {
      available: true,
      daemonRunning: true,
      ...(version.length > 0 ? { serverVersion: version } : {}),
    };
  }

  const combinedErr = (result.stderr || result.stdout).trim();
  const cliMissing = /ENOENT|not found|not recognized/i.test(combinedErr);
  return {
    available: !cliMissing,
    daemonRunning: false,
    errorMessage:
      combinedErr.length > 0
        ? combinedErr
        : "Docker daemon is not running or unreachable",
  };
}

/**
 * Возвращает `true`, если Docker CLI установлен и демон Docker Engine отвечает на запросы.
 */
export async function isDockerAvailable(
  timeoutMs: number = TIMEOUTS_MS.DOCKER_INSPECT_MS,
): Promise<boolean> {
  const status = await checkDockerDaemonAvailable(timeoutMs);
  return status.available && status.daemonRunning;
}

function mapRawContainerRecord(
  record: Record<string, unknown>,
): ContainerRuntimeInfo | null {
  const name =
    typeof record.Name === "string"
      ? record.Name
      : typeof record.Names === "string"
        ? record.Names
        : "";
  const service =
    typeof record.Service === "string" ? record.Service : name;

  if (name.length === 0 && service.length === 0) {
    return null;
  }

  const state =
    typeof record.State === "string"
      ? record.State.toLowerCase()
      : "unknown";
  const status =
    typeof record.Status === "string" ? record.Status : state;
  const health =
    typeof record.Health === "string" && record.Health.length > 0
      ? record.Health
      : undefined;
  const ports =
    typeof record.Ports === "string" && record.Ports.length > 0
      ? record.Ports
      : undefined;

  return {
    name: name || service,
    service: service || name,
    state,
    status,
    ...(health !== undefined ? { health } : {}),
    ...(ports !== undefined ? { ports } : {}),
  };
}

/**
 * Разбирает JSON-вывод команды `docker compose ps --format json` (поддерживает как единый JSON-массив,
 * так и построчный NDJSON-формат разных версий Docker Compose v2).
 */
export function parseDockerComposePsJson(
  rawStdout: string,
): ContainerRuntimeInfo[] {
  const trimmed = rawStdout.trim();
  if (trimmed.length === 0) {
    return [];
  }

  const containers: ContainerRuntimeInfo[] = [];

  if (trimmed.startsWith("[")) {
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (Array.isArray(parsed)) {
        for (const item of parsed) {
          if (isJsonObject(item)) {
            const mapped = mapRawContainerRecord(item);
            if (mapped) {
              containers.push(mapped);
            }
          }
        }
        return containers;
      }
    } catch {
      // Переходим к построчному NDJSON-разбору при нестандартном выводе.
    }
  }

  for (const line of trimmed.split(/\r?\n/)) {
    const cleanLine = line.trim();
    if (!cleanLine.startsWith("{")) {
      continue;
    }
    try {
      const parsed: unknown = JSON.parse(cleanLine);
      if (isJsonObject(parsed)) {
        const mapped = mapRawContainerRecord(parsed);
        if (mapped) {
          containers.push(mapped);
        }
      }
    } catch {
      // Пропускаем невалидные диагностические строки вывода.
    }
  }

  return containers;
}

/**
 * Запрашивает список контейнеров глобального стека через `docker compose -f <composeFilePath> ps --format json`.
 */
export async function listStackContainers(
  composeFilePath?: string,
  timeoutMs: number = TIMEOUTS_MS.DOCKER_INSPECT_MS,
): Promise<ContainerRuntimeInfo[]> {
  const resolvedComposePath =
    composeFilePath ?? path.join(getGlobalFactoryDir(), COMPOSE_REL_PATH);
  if (!(await pathExists(resolvedComposePath))) {
    return [];
  }

  const stackDir = path.dirname(path.dirname(resolvedComposePath));
  const result = await runSubprocess(
    DOCKER_BINARY,
    ["compose", "-f", resolvedComposePath, "ps", "--format", "json"],
    { cwd: stackDir, timeoutMs },
  );

  if (result.exitCode !== 0) {
    return [];
  }

  return parseDockerComposePsJson(result.stdout);
}

/**
 * Собирает комплексный статус глобального Docker-стека: проверяет наличие `docker-compose.yml`,
 * состояние Docker Engine, список запущенных контейнеров (`mcp-coop-core`, `postgres`, `redis`) и `/health`.
 */
export async function getGlobalStackStatus(options?: {
  readonly baseUrl?: string | undefined;
  readonly timeoutMs?: number | undefined;
  readonly homeDirOverride?: string | undefined;
}): Promise<GlobalStackStatus> {
  const stackDir = getGlobalFactoryDir(options?.homeDirOverride);
  const composeFilePath = path.join(stackDir, COMPOSE_REL_PATH);
  const timeoutMs = options?.timeoutMs ?? DEFAULT_HEALTH_TIMEOUT_MS;
  const baseUrl = options?.baseUrl ?? resolveCoreBaseUrl();

  const [composeFileExists, daemonStatus, health] = await Promise.all([
    pathExists(composeFilePath),
    checkDockerDaemonAvailable(TIMEOUTS_MS.DOCKER_INSPECT_MS),
    checkCoreHealth(timeoutMs, baseUrl),
  ]);

  const containers =
    composeFileExists && daemonStatus.daemonRunning
      ? await listStackContainers(composeFilePath, TIMEOUTS_MS.DOCKER_INSPECT_MS)
      : [];

  const knownNames = new Set<string>(Object.values(DOCKER_CONTAINER_NAMES));
  const runningContainersCount = containers.filter(
    (c) =>
      c.state === "running" ||
      knownNames.has(c.name) && /up|running/i.test(c.status),
  ).length;

  return {
    stackDir,
    composeFilePath,
    composeFileExists,
    dockerAvailable: daemonStatus.available,
    daemonRunning: daemonStatus.daemonRunning,
    containers,
    runningContainersCount,
    coreHealthy: health.reachable && health.status === "ok",
    health,
    ...(daemonStatus.errorMessage !== undefined
      ? { errorMessage: daemonStatus.errorMessage }
      : {}),
  };
}

/**
 * Синоним `getGlobalStackStatus` для инспекции состояния глобального Docker-стека.
 */
export const inspectGlobalDockerStack = getGlobalStackStatus;
