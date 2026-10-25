/**
 * Слой 1 (Host CLI Docker Adapter): оркестрация жизненного цикла глобального Docker Compose стека.
 * Управляет запуском (`ensureGlobalDockerStack` / `ensureGlobalStackUp` с прогрессом BuildKit и поллингом `/health`)
 * и остановкой (`stopGlobalDockerStack` / `stopGlobalStackDown`) контейнеров в `~/.mcp-coop-agentic-factory/`.
 */

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { pathExists, runSubprocess } from "../common/index.ts";
import { TIMEOUTS_MS } from "../config/index.ts";
import {
  COMPOSE_REL_PATH,
  getGlobalFactoryDir,
  syncGlobalStackAssets,
} from "./assets.ts";
import {
  type CoreHealthStatus,
  DEFAULT_WAIT_HEALTH_TIMEOUT_MS,
  DOCKER_BINARY,
  FAST_HEALTH_PROBE_TIMEOUT_MS,
  HEALTH_POLL_INTERVAL_MS,
  checkCoreHealth,
  waitForCoreReady,
} from "./stack-status.ts";

const DOCKER_COMPOSE_TIMEOUT_MS = TIMEOUTS_MS.DOCKER_COMPOSE_MS;
const DOCKER_PROGRESS_TICK_MS = 1_000 as const;
const MAX_ERROR_SUMMARY_LENGTH = 140 as const;

/**
 * Результат инициализации и запуска глобального Docker Compose стека.
 */
export interface DockerBootstrapResult {
  stackDir: string;
  composeFilePath: string;
  startedContainers: boolean;
  coreHealthy: boolean;
  health: CoreHealthStatus;
  errorMessage?: string | undefined;
}

/**
 * Синоним `DockerBootstrapResult` для единого именования контракта запуска стека.
 */
export type DockerStackResult = DockerBootstrapResult;

/**
 * Результат остановки контейнеров глобального стека (`docker compose down`).
 */
export interface StopDockerStackResult {
  stopped: boolean;
  composeFilePath: string;
  errorMessage?: string | undefined;
}

/**
 * Опции запуска глобального Docker-стека.
 */
export interface EnsureGlobalStackOptions {
  skipDockerBuild?: boolean | undefined;
  waitTimeoutMs?: number | undefined;
  onProgress?: ((phaseText: string) => void) | undefined;
}

/**
 * Извлекает короткую читаемую фазу прогресса из строки вывода `docker compose` / BuildKit
 * для живого обновления спиннера `@clack/prompts`.
 */
export function extractDockerProgressPhase(rawLine: string): string | null {
  const cleaned = rawLine.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "").trim();
  if (!cleaned || cleaned.startsWith("---") || /^\d+\s*\|/.test(cleaned)) {
    return null;
  }

  const stepMatch = /^#\d+\s+\[([^\]]+)\]\s*(.*)$/.exec(cleaned);
  if (stepMatch) {
    const stage = stepMatch[1]?.trim() ?? "";
    const detail = stepMatch[2]?.trim() ?? "";
    const combined = detail ? `${stage}: ${detail}` : stage;
    return combined.length > 72 ? `${combined.slice(0, 69)}...` : combined;
  }

  if (
    /^(Pulling|Building|Creating|Starting|Waiting|Container|Network|Volume|Image)\b/i.test(
      cleaned,
    )
  ) {
    return cleaned.length > 72 ? `${cleaned.slice(0, 69)}...` : cleaned;
  }

  return null;
}

/**
 * Очищает многострочный стектрейс ошибки Docker BuildKit / Compose до компактной однострочной сводки,
 * чтобы сообщение об ошибке никогда не разрывало рамку карточки `@clack/prompts`.
 */
export function formatDockerErrorSummary(rawError: string): string {
  const lines = rawError
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(
      (line) =>
        line.length > 0 &&
        !line.startsWith("---") &&
        !line.startsWith("View build details:") &&
        !/^\d+\s*\|/.test(line) &&
        !/^>>>\s*\d+\s*\|/.test(line),
    );

  const priorityLine =
    lines.find((line) =>
      /failed to solve:|error:|cannot connect|daemon/i.test(line),
    ) ??
    lines[lines.length - 1] ??
    "Docker daemon is unavailable or compose command failed";

  const compact = priorityLine.replace(/\s+/g, " ").trim();
  if (compact.length <= MAX_ERROR_SUMMARY_LENGTH) {
    return compact;
  }
  return `${compact.slice(0, MAX_ERROR_SUMMARY_LENGTH - 3)}...`;
}

async function runDockerComposeUpAsync(
  composeArgs: readonly string[],
  stackDir: string,
  onProgress?: (phaseText: string) => void,
): Promise<{ ok: boolean; errorSummary?: string | undefined }> {
  const { promise, resolve } = Promise.withResolvers<{
    ok: boolean;
    errorSummary?: string | undefined;
  }>();

  const startedAt = Date.now();
  let currentPhase = "Starting Docker Compose stack...";
  const outputTail: string[] = [];
  let settled = false;

  const emitProgress = (): void => {
    if (!onProgress) {
      return;
    }
    const elapsedSec = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
    onProgress(`[2/4] Docker Compose: ${currentPhase} (${String(elapsedSec)}s)`);
  };

  emitProgress();
  const tickTimer = setInterval(emitProgress, DOCKER_PROGRESS_TICK_MS);

  let child: ReturnType<typeof spawn>;
  try {
    child = spawn(DOCKER_BINARY, [...composeArgs], {
      cwd: stackDir,
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (err: unknown) {
    clearInterval(tickTimer);
    const msg = err instanceof Error ? err.message : String(err);
    resolve({ ok: false, errorSummary: formatDockerErrorSummary(msg) });
    return promise;
  }

  const timeoutTimer = setTimeout(() => {
    if (!settled) {
      settled = true;
      clearInterval(tickTimer);
      child.kill("SIGTERM");
      resolve({
        ok: false,
        errorSummary: `Docker Compose timed out after ${String(Math.round(DOCKER_COMPOSE_TIMEOUT_MS / 1000))}s`,
      });
    }
  }, DOCKER_COMPOSE_TIMEOUT_MS);

  const handleChunk = (chunk: Buffer | string): void => {
    const text = Buffer.isBuffer(chunk) ? chunk.toString("utf-8") : String(chunk);
    for (const rawLine of text.split(/\r?\n/)) {
      const trimmed = rawLine.trim();
      if (!trimmed) {
        continue;
      }
      outputTail.push(trimmed);
      if (outputTail.length > 40) {
        outputTail.shift();
      }
      const phase = extractDockerProgressPhase(trimmed);
      if (phase) {
        currentPhase = phase;
        emitProgress();
      }
    }
  };

  child.stdout?.on("data", handleChunk);
  child.stderr?.on("data", handleChunk);

  child.once("error", (err: Error) => {
    if (settled) {
      return;
    }
    settled = true;
    clearInterval(tickTimer);
    clearTimeout(timeoutTimer);
    resolve({ ok: false, errorSummary: formatDockerErrorSummary(err.message) });
  });

  child.once("close", (code: number | null) => {
    if (settled) {
      return;
    }
    settled = true;
    clearInterval(tickTimer);
    clearTimeout(timeoutTimer);
    if (code === 0) {
      resolve({ ok: true });
    } else {
      resolve({
        ok: false,
        errorSummary: formatDockerErrorSummary(outputTail.join("\n")),
      });
    }
  });

  return promise;
}

/**
 * Гарантирует готовность глобального Docker-стека: синхронизирует файлы в `~/.mcp-coop-agentic-factory/`,
 * проверяет текущее здоровье ядра и при необходимости асинхронно запускает `docker compose up -d --build`
 * с живой трансляцией статуса в колбэк `onProgress`.
 */
export async function ensureGlobalStackUp(
  options?: EnsureGlobalStackOptions,
): Promise<DockerBootstrapResult> {
  options?.onProgress?.(
    "[1/4] Syncing runtime & Docker stack files to ~/.mcp-coop-agentic-factory/...",
  );
  const { stackDir, composeFilePath } = await syncGlobalStackAssets();

  const initialHealth = await checkCoreHealth(FAST_HEALTH_PROBE_TIMEOUT_MS);
  if (initialHealth.reachable && initialHealth.status === "ok") {
    options?.onProgress?.("[3/4] Core API is already online and healthy.");
    return {
      stackDir,
      composeFilePath,
      startedContainers: false,
      coreHealthy: true,
      health: initialHealth,
    };
  }

  if (!(await pathExists(composeFilePath))) {
    return {
      stackDir,
      composeFilePath,
      startedContainers: false,
      coreHealthy: false,
      health: initialHealth,
      errorMessage: `Docker Compose manifest not found at ${composeFilePath}`,
    };
  }

  const composeArgs = ["compose", "-f", composeFilePath, "up", "-d"];
  if (!options?.skipDockerBuild) {
    composeArgs.push("--build");
  }

  const composeRun = await runDockerComposeUpAsync(
    composeArgs,
    stackDir,
    options?.onProgress,
  );

  if (!composeRun.ok) {
    return {
      stackDir,
      composeFilePath,
      startedContainers: false,
      coreHealthy: false,
      health: initialHealth,
      errorMessage: composeRun.errorSummary,
    };
  }

  const latestHealth = await waitForCoreReady({
    timeoutMs: options?.waitTimeoutMs ?? DEFAULT_WAIT_HEALTH_TIMEOUT_MS,
    pollIntervalMs: HEALTH_POLL_INTERVAL_MS,
    probeTimeoutMs: FAST_HEALTH_PROBE_TIMEOUT_MS,
    onProgress: options?.onProgress
      ? (phaseText) => {
          options.onProgress?.(phaseText);
        }
      : undefined,
  });

  return {
    stackDir,
    composeFilePath,
    startedContainers: true,
    coreHealthy: latestHealth.reachable && latestHealth.status === "ok",
    health: latestHealth,
  };
}

/**
 * Синоним `ensureGlobalStackUp` для идемпотентного развёртывания и запуска глобального Docker-стека.
 */
export const ensureGlobalDockerStack = ensureGlobalStackUp;

/**
 * Асинхронно останавливает контейнеры глобального стека через `docker compose down`
 * с использованием `runSubprocess` без удаления именных томов БД.
 */
export async function stopGlobalDockerStack(): Promise<StopDockerStackResult> {
  const stackDir = getGlobalFactoryDir();
  const composeFilePath = path.join(stackDir, COMPOSE_REL_PATH);
  if (!(await pathExists(composeFilePath))) {
    return {
      stopped: false,
      composeFilePath,
      errorMessage: `No global stack found at ${composeFilePath}`,
    };
  }

  const result = await runSubprocess(
    DOCKER_BINARY,
    ["compose", "-f", composeFilePath, "down"],
    {
      cwd: stackDir,
      timeoutMs: DOCKER_COMPOSE_TIMEOUT_MS,
    },
  );

  if (result.exitCode !== 0) {
    return {
      stopped: false,
      composeFilePath,
      errorMessage:
        result.stderr.trim() ||
        result.stdout.trim() ||
        "Failed to stop Docker Compose stack",
    };
  }

  return { stopped: true, composeFilePath };
}

/**
 * Останавливает контейнеры глобального стека через синхронный `docker compose down` без удаления
 * именных томов БД (сохранено для обратной совместимости с синхронными вызовами).
 */
export function stopGlobalStackDown(): StopDockerStackResult {
  const stackDir = getGlobalFactoryDir();
  const composeFilePath = path.join(stackDir, COMPOSE_REL_PATH);
  if (!fs.existsSync(composeFilePath)) {
    return {
      stopped: false,
      composeFilePath,
      errorMessage: `No global stack found at ${composeFilePath}`,
    };
  }

  const proc = spawnSync(DOCKER_BINARY, ["compose", "-f", composeFilePath, "down"], {
    cwd: stackDir,
    encoding: "utf-8",
    timeout: DOCKER_COMPOSE_TIMEOUT_MS,
  });

  if (proc.error || proc.status !== 0) {
    return {
      stopped: false,
      composeFilePath,
      errorMessage:
        proc.error?.message ||
        proc.stderr?.trim() ||
        "Failed to stop Docker Compose stack",
    };
  }

  return { stopped: true, composeFilePath };
}
