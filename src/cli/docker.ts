/**
 * Слой 1 (Host CLI Injector): менеджер жизненного цикла глобального Docker-стека и HTTP-клиент ядра.
 * Синхронизирует исходники Python-ядра и Docker Compose манифесты в `~/.mcp-coop-agentic-factory/`,
 * управляет контейнерами (`up -d` / `down`) с политикой Fail-Open и предоставляет типизированные
 * методы для взаимодействия с REST API аналитического ядра (`127.0.0.1:8787`).
 */

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

export const GLOBAL_FACTORY_DIR_NAME = ".mcp-coop-agentic-factory" as const;
export const FACTORY_HOME_ENV_KEY = "MCP_COOP_FACTORY_HOME" as const;
export const CORE_URL_ENV_KEY = "MCP_COOP_CORE_URL" as const;

export const DEFAULT_CORE_BASE_URL = "http://127.0.0.1:8787" as const;
export const HEALTH_ENDPOINT = "/health" as const;
export const REGISTER_PROJECT_ENDPOINT = "/api/projects/register" as const;
export const PROVIDERS_ENDPOINT = "/api/providers" as const;
export const HARNESS_COMPONENTS_ENDPOINT = "/api/harness/components" as const;
export const HOOKS_BASE_ENDPOINT = "/api/hooks" as const;
export const HOOK_LAUNCHER_REL_PATH = "bin/run-hook.sh" as const;

const DEFAULT_HEALTH_TIMEOUT_MS = 1500 as const;
const FAST_HEALTH_PROBE_TIMEOUT_MS = 1200 as const;
const DEFAULT_WAIT_HEALTH_TIMEOUT_MS = 10_000 as const;
const HEALTH_POLL_INTERVAL_MS = 600 as const;
const DOCKER_COMPOSE_TIMEOUT_MS = 180_000 as const;
const DOCKER_PROGRESS_TICK_MS = 1000 as const;
const MAX_ERROR_SUMMARY_LENGTH = 140 as const;
const DEFAULT_REGISTER_TIMEOUT_MS = 4000 as const;
const DEFAULT_API_TIMEOUT_MS = 3000 as const;

const DOCKER_BINARY = "docker" as const;
const COMPOSE_REL_PATH = "docker/docker-compose.yml" as const;
const DOCKERFILE_REL_PATH = "docker/Dockerfile" as const;
const PYPROJECT_REL_PATH = "pyproject.toml" as const;
const README_REL_PATH = "README.md" as const;
const DOCKERIGNORE_REL_PATH = ".dockerignore" as const;
const PACKAGE_JSON_REL_PATH = "package.json" as const;
const CORE_SRC_REL_PATH = "src/core" as const;
const DIST_REL_PATH = "dist" as const;
const NODE_MODULES_REL_PATH = "node_modules" as const;
const ENV_FILE_NAME = ".env" as const;
const ENV_EXAMPLE_FILE_NAME = ".env.example" as const;
const PYCACHE_DIR_NAME = "__pycache__" as const;
const EXECUTABLE_FILE_MODE = 0o755 as const;

const DEFAULT_ENV_TEMPLATE = [
  "# MCP-COOP Agentic Factory Global Stack Environment",
  "PORT=8787",
  "POSTGRES_PORT=54329",
  "REDIS_PORT=63799",
  "OPENAI_API_KEY=",
  "EMBEDDING_API_KEY=",
  "GEMINI_API_KEY=",
  "ANTIGRAVITY_API_KEY=",
  "",
].join("\n");

export interface CoreHealthStatus {
  reachable: boolean;
  status: string;
  version?: string;
  postgres_connected: boolean;
  redis_connected: boolean;
  decisions_api_configured: boolean;
  embedding_api_configured: boolean;
  antigravity_sdk_configured?: boolean;
  mcp_endpoint?: string;
}

export interface DockerBootstrapResult {
  stackDir: string;
  composeFilePath: string;
  startedContainers: boolean;
  coreHealthy: boolean;
  health: CoreHealthStatus;
  errorMessage?: string;
}

export interface CatalogComponentItem {
  slug: string;
  component_type: string;
  scope: string;
  stack_tags: string[];
  title: string;
  description: string;
  status: string;
  score: number;
  version: number;
  effective_state: string;
}

export interface ProviderStatusItem {
  name: string;
  dialect: string;
  base_url: string;
  model: string;
  priority: number;
  timeout_ms: number;
  enabled: boolean;
  has_api_key: boolean;
  health_status: string;
  cooldown_remaining_seconds: number;
  last_error?: string | null;
}

/**
 * Разрешает базовый URL аналитического ядра с поддержкой переопределения через окружение.
 */
export function resolveCoreBaseUrl(explicitBaseUrl?: string): string {
  const raw = explicitBaseUrl ?? process.env[CORE_URL_ENV_KEY] ?? DEFAULT_CORE_BASE_URL;
  return raw.replace(/\/+$/, "");
}

/**
 * Возвращает абсолютный путь к глобальной директории стека (`~/.mcp-coop-agentic-factory`).
 * Поддерживает переопределение через `MCP_COOP_FACTORY_HOME` для изолированного тестирования.
 */
export function getGlobalFactoryDir(): string {
  const customHome = process.env[FACTORY_HOME_ENV_KEY]?.trim();
  if (customHome) {
    return path.resolve(customHome);
  }
  return path.join(os.homedir(), GLOBAL_FACTORY_DIR_NAME);
}

/**
 * Вычисляет корень npm/bun-пакета относительно текущего модуля (`src/cli/docker.ts` или `dist/docker.js`).
 */
export function resolveCliPackageRoot(): string {
  const currentFileDir = path.dirname(fileURLToPath(import.meta.url));
  const twoLevelsUp = path.resolve(currentFileDir, "../..");
  if (fs.existsSync(path.join(twoLevelsUp, PYPROJECT_REL_PATH))) {
    return twoLevelsUp;
  }
  return path.resolve(currentFileDir, "..");
}

/**
 * Генерирует универсальный POSIX-лаунчер `~/.mcp-coop-agentic-factory/bin/run-hook.sh`.
 * Скрипт автоматически находит `bun` или `node` в стандартных путях macOS/Linux (даже при вызове
 * из неинтерактивного `/bin/sh` в GUI IDE), а при полном отсутствии рантаймов выводит понятную
 * инструкцию со ссылками на официальные сайты установки.
 */
function buildHookLauncherScript(fallbackPackageDistEntry: string): string {
  const escapedFallback = fallbackPackageDistEntry.replace(/"/g, '\\"');
  return [
    "#!/bin/sh",
    "# Auto-generated universal hook launcher for MCP-COOP Agentic Factory.",
    'export PATH="$HOME/.bun/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"',
    'if [ -d "$HOME/.nvm/versions/node" ]; then',
    '  LATEST_NVM_NODE="$(ls -1d "$HOME/.nvm/versions/node/"* 2>/dev/null | tail -n 1)"',
    '  if [ -n "$LATEST_NVM_NODE" ] && [ -d "$LATEST_NVM_NODE/bin" ]; then',
    '    export PATH="$LATEST_NVM_NODE/bin:$PATH"',
    "  fi",
    "fi",
    "",
    'FACTORY_HOME="${MCP_COOP_FACTORY_HOME:-$HOME/.mcp-coop-agentic-factory}"',
    'CLI_ENTRY="$FACTORY_HOME/dist/index.js"',
    'if [ ! -f "$CLI_ENTRY" ]; then',
    `  CLI_ENTRY="${escapedFallback}"`,
    "fi",
    "",
    'if [ "$1" = "hook" ]; then',
    "  shift",
    "fi",
    "",
    'if command -v bun >/dev/null 2>&1 && [ -f "$CLI_ENTRY" ]; then',
    '  exec bun "$CLI_ENTRY" hook "$@"',
    'elif command -v node >/dev/null 2>&1 && [ -f "$CLI_ENTRY" ]; then',
    '  exec node "$CLI_ENTRY" hook "$@"',
    "elif command -v bunx >/dev/null 2>&1; then",
    '  exec bunx mcp-coop-agentic-factory hook "$@"',
    "elif command -v npx >/dev/null 2>&1; then",
    '  exec npx mcp-coop-agentic-factory hook "$@"',
    "else",
    '  echo "[MCP-COOP Factory] Error: Neither Bun nor Node.js runtime was found on this machine." >&2',
    '  echo "Please install one of the supported JavaScript runtimes:" >&2',
    '  echo "  • Bun (recommended): https://bun.sh  (curl -fsSL https://bun.sh/install | bash)" >&2',
    '  echo "  • Node.js (>= 20):   https://nodejs.org" >&2',
    "  exit 1",
    "fi",
    "",
  ].join("\n");
}

/**
 * Синхронизирует `pyproject.toml`, `README.md`, `.dockerignore`, `docker/*`, `src/core/`,
 * скомпилированный `dist/` и лаунчер `bin/run-hook.sh` в `~/.mcp-coop-agentic-factory/`.
 */
export function syncGlobalFactoryStackFiles(): {
  stackDir: string;
  composeFilePath: string;
  launcherPath: string;
} {
  const stackDir = getGlobalFactoryDir();
  const packageRoot = resolveCliPackageRoot();
  const composeFilePath = path.join(stackDir, COMPOSE_REL_PATH);
  const launcherPath = path.join(stackDir, HOOK_LAUNCHER_REL_PATH);

  fs.mkdirSync(path.join(stackDir, "docker"), { recursive: true });
  fs.mkdirSync(path.join(stackDir, "src"), { recursive: true });
  fs.mkdirSync(path.join(stackDir, "bin"), { recursive: true });

  if (path.resolve(packageRoot) !== path.resolve(stackDir)) {
    const singleFiles = [
      PYPROJECT_REL_PATH,
      README_REL_PATH,
      DOCKERIGNORE_REL_PATH,
      PACKAGE_JSON_REL_PATH,
      DOCKERFILE_REL_PATH,
      COMPOSE_REL_PATH,
    ] as const;
    for (const relPath of singleFiles) {
      const srcFile = path.join(packageRoot, relPath);
      const destFile = path.join(stackDir, relPath);
      if (fs.existsSync(srcFile)) {
        fs.mkdirSync(path.dirname(destFile), { recursive: true });
        fs.copyFileSync(srcFile, destFile);
      }
    }

    const srcCoreDir = path.join(packageRoot, CORE_SRC_REL_PATH);
    const destCoreDir = path.join(stackDir, CORE_SRC_REL_PATH);
    if (fs.existsSync(srcCoreDir)) {
      fs.cpSync(srcCoreDir, destCoreDir, {
        recursive: true,
        force: true,
        filter: (sourcePath: string) => path.basename(sourcePath) !== PYCACHE_DIR_NAME,
      });
    }

    const srcDistDir = path.join(packageRoot, DIST_REL_PATH);
    const destDistDir = path.join(stackDir, DIST_REL_PATH);
    if (fs.existsSync(srcDistDir)) {
      fs.cpSync(srcDistDir, destDistDir, {
        recursive: true,
        force: true,
      });
    }

    const srcNodeModules = path.join(packageRoot, NODE_MODULES_REL_PATH);
    const destNodeModules = path.join(stackDir, NODE_MODULES_REL_PATH);
    if (fs.existsSync(srcNodeModules) && !fs.existsSync(destNodeModules)) {
      try {
        fs.symlinkSync(srcNodeModules, destNodeModules, "dir");
      } catch {
        // Игнорируем ошибку симлинка на файловых системах без поддержки символьных ссылок.
      }
    }
  }

  const fallbackDistEntry = path.join(packageRoot, DIST_REL_PATH, "index.js");
  fs.writeFileSync(launcherPath, buildHookLauncherScript(fallbackDistEntry), "utf-8");
  try {
    fs.chmodSync(launcherPath, EXECUTABLE_FILE_MODE);
  } catch {
    // На платформах без POSIX-прав доступа chmod безопасно пропускается.
  }

  const destEnvPath = path.join(stackDir, ENV_FILE_NAME);
  if (!fs.existsSync(destEnvPath)) {
    const srcEnvExample = path.join(packageRoot, ENV_EXAMPLE_FILE_NAME);
    if (fs.existsSync(srcEnvExample)) {
      fs.copyFileSync(srcEnvExample, destEnvPath);
    } else {
      fs.writeFileSync(destEnvPath, DEFAULT_ENV_TEMPLATE, "utf-8");
    }
  }

  return { stackDir, composeFilePath, launcherPath };
}

function createUnreachableHealthStatus(status = "unreachable"): CoreHealthStatus {
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
    const data = (await response.json()) as Record<string, unknown>;
    return {
      reachable: true,
      status: typeof data.status === "string" ? data.status : "ok",
      version: typeof data.version === "string" ? data.version : undefined,
      postgres_connected: Boolean(data.postgres_connected),
      redis_connected: Boolean(data.redis_connected),
      decisions_api_configured: Boolean(data.decisions_api_configured),
      embedding_api_configured: Boolean(data.embedding_api_configured),
      mcp_endpoint: typeof data.mcp_endpoint === "string" ? data.mcp_endpoint : undefined,
    };
  } catch {
    return createUnreachableHealthStatus();
  }
}

function sleepMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
    lines.find((line) => /failed to solve:|error:|cannot connect|daemon/i.test(line)) ??
    lines[lines.length - 1] ??
    "Docker daemon is unavailable or compose command failed";

  const compact = priorityLine.replace(/\s+/g, " ").trim();
  if (compact.length <= MAX_ERROR_SUMMARY_LENGTH) {
    return compact;
  }
  return `${compact.slice(0, MAX_ERROR_SUMMARY_LENGTH - 3)}...`;
}

async function runDockerComposeUpAsync(
  composeArgs: string[],
  stackDir: string,
  onProgress?: (phaseText: string) => void,
): Promise<{ ok: boolean; errorSummary?: string }> {
  return new Promise((resolve) => {
    const startedAt = Date.now();
    let currentPhase = "Starting Docker Compose stack...";
    const outputTail: string[] = [];
    let settled = false;

    const emitProgress = (): void => {
      if (!onProgress) {
        return;
      }
      const elapsedSec = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
      onProgress(`[2/4] Docker Compose: ${currentPhase} (${elapsedSec}s)`);
    };

    emitProgress();
    const tickTimer = setInterval(emitProgress, DOCKER_PROGRESS_TICK_MS);

    let child: ReturnType<typeof spawn>;
    try {
      child = spawn(DOCKER_BINARY, composeArgs, {
        cwd: stackDir,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (err: unknown) {
      clearInterval(tickTimer);
      const msg = err instanceof Error ? err.message : String(err);
      resolve({ ok: false, errorSummary: formatDockerErrorSummary(msg) });
      return;
    }

    const timeoutTimer = setTimeout(() => {
      if (!settled) {
        settled = true;
        clearInterval(tickTimer);
        child.kill("SIGTERM");
        resolve({
          ok: false,
          errorSummary: `Docker Compose timed out after ${Math.round(DOCKER_COMPOSE_TIMEOUT_MS / 1000)}s`,
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
  });
}

/**
 * Гарантирует готовность глобального Docker-стека: синхронизирует файлы в `~/.mcp-coop-agentic-factory/`,
 * проверяет текущее здоровье ядра и при необходимости асинхронно запускает `docker compose up -d --build`
 * с живой трансляцией статуса в колбэк `onProgress`.
 */
export async function ensureGlobalStackUp(options?: {
  skipDockerBuild?: boolean;
  waitTimeoutMs?: number;
  onProgress?: (phaseText: string) => void;
}): Promise<DockerBootstrapResult> {
  options?.onProgress?.(
    "[1/4] Syncing runtime & Docker stack files to ~/.mcp-coop-agentic-factory/...",
  );
  const { stackDir, composeFilePath } = syncGlobalFactoryStackFiles();

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

  if (!fs.existsSync(composeFilePath)) {
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

  const waitTimeoutMs = options?.waitTimeoutMs ?? DEFAULT_WAIT_HEALTH_TIMEOUT_MS;
  const maxAttempts = Math.max(1, Math.ceil(waitTimeoutMs / HEALTH_POLL_INTERVAL_MS));
  const deadline = Date.now() + waitTimeoutMs;
  let attempt = 1;
  options?.onProgress?.(
    `[3/4] Waiting for FastAPI + FastMCP Core readiness (/health) — attempt ${attempt}/${maxAttempts}...`,
  );
  let latestHealth = await checkCoreHealth(FAST_HEALTH_PROBE_TIMEOUT_MS);

  while (!latestHealth.reachable && Date.now() < deadline) {
    await sleepMs(HEALTH_POLL_INTERVAL_MS);
    attempt += 1;
    options?.onProgress?.(
      `[3/4] Waiting for FastAPI + FastMCP Core readiness (/health) — attempt ${Math.min(attempt, maxAttempts)}/${maxAttempts}...`,
    );
    latestHealth = await checkCoreHealth(FAST_HEALTH_PROBE_TIMEOUT_MS);
  }

  return {
    stackDir,
    composeFilePath,
    startedContainers: true,
    coreHealthy: latestHealth.reachable && latestHealth.status === "ok",
    health: latestHealth,
  };
}

/**
 * Останавливает контейнеры глобального стека через `docker compose down` без удаления именных томов БД.
 */
export function stopGlobalStackDown(): {
  stopped: boolean;
  composeFilePath: string;
  errorMessage?: string;
} {
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

/**
 * Регистрирует рабочую директорию проекта в ядре (`POST /api/projects/register`)
 * и возвращает скомпилированный артефакт харнеса (`CompiledHarnessArtifact`).
 */
export async function registerProjectWithCore(
  payload: {
    workspace_path: string;
    project_name: string;
    agent_type: string;
    stack_tags: string[];
    profile_overrides: Record<string, unknown>;
  },
  timeoutMs: number = DEFAULT_REGISTER_TIMEOUT_MS,
): Promise<Record<string, unknown> | null> {
  try {
    const response = await fetch(`${resolveCoreBaseUrl()}${REGISTER_PROJECT_ENDPOINT}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * Запрашивает каталог всех компонентов `Harness²` (`GET /api/harness/components`)
 * с вычисленным статусом (`effective_state`) для указанного проекта и стека.
 */
export async function fetchHarnessCatalog(
  params: { workspacePath?: string; stackTags?: readonly string[] },
  timeoutMs: number = DEFAULT_API_TIMEOUT_MS,
): Promise<{
  count: number;
  stack_tags: string[];
  agent_type?: string;
  profile_overrides: Record<string, unknown>;
  components: CatalogComponentItem[];
} | null> {
  try {
    const url = new URL(`${resolveCoreBaseUrl()}${HARNESS_COMPONENTS_ENDPOINT}`);
    if (params.workspacePath) {
      url.searchParams.set("workspace_path", params.workspacePath);
    }
    if (params.stackTags && params.stackTags.length > 0) {
      url.searchParams.set("stack_tags", params.stackTags.join(","));
    }
    const response = await fetch(url.toString(), {
      method: "GET",
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) {
      return null;
    }
    const data = (await response.json()) as Record<string, unknown>;
    const rawOverrides =
      typeof data.profile_overrides === "object" &&
      data.profile_overrides !== null &&
      !Array.isArray(data.profile_overrides)
        ? (data.profile_overrides as Record<string, unknown>)
        : {};
    const resolvedAgent =
      (typeof data.agent_type === "string" && data.agent_type) ||
      (typeof rawOverrides.agent === "string" && rawOverrides.agent) ||
      undefined;
    return {
      count: typeof data.count === "number" ? data.count : 0,
      stack_tags: Array.isArray(data.stack_tags) ? (data.stack_tags as string[]) : [],
      agent_type: resolvedAgent,
      profile_overrides: rawOverrides,
      components: Array.isArray(data.components)
        ? (data.components as CatalogComponentItem[])
        : [],
    };
  } catch {
    return null;
  }
}

/**
 * Переключает глобальный статус компонента `Harness²` (`PATCH /api/harness/components/{slug}`).
 */
export async function patchGlobalComponentStatus(
  slug: string,
  enabled: boolean,
  timeoutMs: number = DEFAULT_API_TIMEOUT_MS,
): Promise<Record<string, unknown> | null> {
  try {
    const encodedSlug = encodeURIComponent(slug.trim());
    const response = await fetch(
      `${resolveCoreBaseUrl()}${HARNESS_COMPONENTS_ENDPOINT}/${encodedSlug}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
        signal: AbortSignal.timeout(timeoutMs),
      },
    );
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * Возвращает отсортированный список провайдеров второго контура (`GET /api/providers`).
 */
export async function listTier2Providers(
  timeoutMs: number = DEFAULT_API_TIMEOUT_MS,
): Promise<ProviderStatusItem[] | null> {
  try {
    const response = await fetch(`${resolveCoreBaseUrl()}${PROVIDERS_ENDPOINT}`, {
      method: "GET",
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) {
      return null;
    }
    const data = (await response.json()) as Record<string, unknown>;
    return Array.isArray(data.providers) ? (data.providers as ProviderStatusItem[]) : [];
  } catch {
    return null;
  }
}

/**
 * Добавляет или полностью обновляет провайдера Tier 2 (`POST /api/providers`).
 */
export async function upsertTier2Provider(
  config: Record<string, unknown>,
  timeoutMs: number = DEFAULT_API_TIMEOUT_MS,
): Promise<Record<string, unknown> | null> {
  try {
    const response = await fetch(`${resolveCoreBaseUrl()}${PROVIDERS_ENDPOINT}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * Частично обновляет конфигурацию провайдера Tier 2 или сбрасывает его Circuit Breaker (`PATCH /api/providers/{name}`).
 */
export async function patchTier2Provider(
  name: string,
  patch: Record<string, unknown>,
  timeoutMs: number = DEFAULT_API_TIMEOUT_MS,
): Promise<Record<string, unknown> | null> {
  try {
    const encodedName = encodeURIComponent(name.trim());
    const response = await fetch(
      `${resolveCoreBaseUrl()}${PROVIDERS_ENDPOINT}/${encodedName}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
        signal: AbortSignal.timeout(timeoutMs),
      },
    );
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * Удаляет провайдера Tier 2 из рантайм-пула и таблицы `decision_providers` (`DELETE /api/providers/{name}`).
 */
export async function deleteTier2Provider(
  name: string,
  timeoutMs: number = DEFAULT_API_TIMEOUT_MS,
): Promise<boolean> {
  try {
    const encodedName = encodeURIComponent(name.trim());
    const response = await fetch(
      `${resolveCoreBaseUrl()}${PROVIDERS_ENDPOINT}/${encodedName}`,
      {
        method: "DELETE",
        signal: AbortSignal.timeout(timeoutMs),
      },
    );
    return response.ok;
  } catch {
    return false;
  }
}
