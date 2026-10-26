/**
 * Слой 1 (Host CLI Docker Adapter): управление файловыми ассетами глобального Docker-стека.
 * Синхронизирует `Dockerfile`, `docker-compose.yml`, `.env`, `pyproject.toml`, `src/core` и `dist`
 * в `~/.mcp-coop-agentic-factory/` и атомарно генерирует POSIX-лаунчер хуков `bin/run-hook.sh`.
 */

import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { pathExists, writeFileAtomic } from "../common/index.ts";
import {
  DEFAULT_CORE_BASE_URL,
  ENV_VARS,
  GLOBAL_FACTORY_DIR_NAME,
  GLOBAL_FACTORY_RELATIVE_PATHS,
  resolveCoreBaseUrl,
  resolveGlobalFactoryDir,
} from "../config/index.ts";

export {
  DEFAULT_CORE_BASE_URL,
  GLOBAL_FACTORY_DIR_NAME,
  resolveCoreBaseUrl,
};

export const FACTORY_HOME_ENV_KEY = ENV_VARS.FACTORY_HOME;
export const CORE_URL_ENV_KEY = ENV_VARS.CORE_URL;
export const HOOK_LAUNCHER_REL_PATH = GLOBAL_FACTORY_RELATIVE_PATHS.HOOK_RUNNER_SCRIPT;

const DOCKER_DIR_NAME = "docker" as const;
const SRC_DIR_NAME = "src" as const;
const BIN_DIR_NAME = "bin" as const;
const DIST_REL_PATH = "dist" as const;
const DIST_INDEX_FILE = "index.js" as const;
const NODE_MODULES_REL_PATH = "node_modules" as const;
const PYPROJECT_REL_PATH = "pyproject.toml" as const;
const README_REL_PATH = "README.md" as const;
const DOCKERIGNORE_REL_PATH = ".dockerignore" as const;
const PACKAGE_JSON_REL_PATH = "package.json" as const;
const ENV_EXAMPLE_FILE_NAME = ".env.example" as const;
const PYCACHE_DIR_NAME = "__pycache__" as const;
const EXECUTABLE_FILE_MODE = 0o755 as const;

export const COMPOSE_REL_PATH = path.posix.join(
  DOCKER_DIR_NAME,
  GLOBAL_FACTORY_RELATIVE_PATHS.COMPOSE_FILE,
);
export const DOCKERFILE_REL_PATH = path.posix.join(
  DOCKER_DIR_NAME,
  GLOBAL_FACTORY_RELATIVE_PATHS.DOCKERFILE,
);
export const CORE_SRC_REL_PATH = GLOBAL_FACTORY_RELATIVE_PATHS.PYTHON_SRC_DIR;
export const SEEDS_REL_PATH = GLOBAL_FACTORY_RELATIVE_PATHS.SEEDS_DIR;
export const ENV_FILE_NAME = GLOBAL_FACTORY_RELATIVE_PATHS.ENV_FILE;

const SINGLE_ASSET_RELATIVE_PATHS = [
  PYPROJECT_REL_PATH,
  README_REL_PATH,
  DOCKERIGNORE_REL_PATH,
  PACKAGE_JSON_REL_PATH,
  DOCKERFILE_REL_PATH,
  COMPOSE_REL_PATH,
] as const;

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

/**
 * Результат синхронизации файловых ассетов глобального Docker-стека.
 */
export interface GlobalStackAssetsResult {
  readonly stackDir: string;
  readonly composeFilePath: string;
  readonly launcherPath: string;
  readonly envFilePath: string;
}

/**
 * Возвращает абсолютный путь к глобальной директории стека (`~/.mcp-coop-agentic-factory`).
 * Поддерживает переопределение через аргумент или переменную окружения `MCP_COOP_FACTORY_HOME`.
 */
export function getGlobalFactoryDir(homeDirOverride?: string): string {
  return resolveGlobalFactoryDir(homeDirOverride);
}

/**
 * Возвращает абсолютный путь к глобальному POSIX-раннеру хуков (`~/.mcp-coop-agentic-factory/bin/run-hook.sh`).
 */
export function getGlobalHookRunnerPath(homeDirOverride?: string): string {
  return path.join(getGlobalFactoryDir(homeDirOverride), HOOK_LAUNCHER_REL_PATH);
}

/**
 * Вычисляет корень npm/bun-пакета относительно текущего модуля (`src/cli/docker/assets.ts` или `dist/`).
 * Последовательно проверяет родительские уровни на наличие `pyproject.toml` или `package.json`.
 */
export function resolveCliPackageRoot(): string {
  const currentFileDir = path.dirname(fileURLToPath(import.meta.url));
  const candidateLevels = [
    path.resolve(currentFileDir, "../../.."),
    path.resolve(currentFileDir, "../.."),
    path.resolve(currentFileDir, ".."),
  ] as const;

  for (const candidate of candidateLevels) {
    if (fs.existsSync(path.join(candidate, PYPROJECT_REL_PATH))) {
      return candidate;
    }
  }

  for (const candidate of candidateLevels) {
    if (fs.existsSync(path.join(candidate, PACKAGE_JSON_REL_PATH))) {
      return candidate;
    }
  }

  return path.resolve(currentFileDir, "../../..");
}

/**
 * Синоним `resolveCliPackageRoot` для унифицированного определения корня репозитория из CLI-пакетов.
 */
export const resolveRepoRootFromCli = resolveCliPackageRoot;

/**
 * Генерирует универсальный POSIX-лаунчер `~/.mcp-coop-agentic-factory/bin/run-hook.sh`.
 * Скрипт автоматически находит `bun` или `node` в стандартных путях macOS/Linux (даже при вызове
 * из неинтерактивного `/bin/sh` в GUI IDE), а при полном отсутствии рантаймов выводит понятную
 * инструкцию со ссылками на официальные сайты установки.
 */
export function buildHookLauncherScript(fallbackPackageDistEntry: string): string {
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

function copyRepositoryAssetsToStackDirSync(
  packageRoot: string,
  stackDir: string,
): void {
  if (path.resolve(packageRoot) === path.resolve(stackDir)) {
    return;
  }

  for (const relPath of SINGLE_ASSET_RELATIVE_PATHS) {
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

  const srcSeedsDir = path.join(packageRoot, SEEDS_REL_PATH);
  const destSeedsDir = path.join(stackDir, SEEDS_REL_PATH);
  if (fs.existsSync(srcSeedsDir)) {
    fs.cpSync(srcSeedsDir, destSeedsDir, {
      recursive: true,
      force: true,
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

/**
 * Асинхронно синхронизирует ассеты глобального Docker-стека (`Dockerfile`, `docker-compose.yml`,
 * `pyproject.toml`, `src/core/`, `dist/`) в `~/.mcp-coop-agentic-factory/` и атомарно записывает
 * `bin/run-hook.sh` и дефолтный `.env` через `writeFileAtomic`.
 */
export async function syncGlobalStackAssets(
  homeDirOverride?: string,
): Promise<GlobalStackAssetsResult> {
  const stackDir = getGlobalFactoryDir(homeDirOverride);
  const packageRoot = resolveCliPackageRoot();
  const composeFilePath = path.join(stackDir, COMPOSE_REL_PATH);
  const launcherPath = path.join(stackDir, HOOK_LAUNCHER_REL_PATH);
  const envFilePath = path.join(stackDir, ENV_FILE_NAME);

  await Promise.all([
    fsp.mkdir(path.join(stackDir, DOCKER_DIR_NAME), { recursive: true }),
    fsp.mkdir(path.join(stackDir, SRC_DIR_NAME), { recursive: true }),
    fsp.mkdir(path.join(stackDir, SEEDS_REL_PATH), { recursive: true }),
    fsp.mkdir(path.join(stackDir, BIN_DIR_NAME), { recursive: true }),
  ]);

  copyRepositoryAssetsToStackDirSync(packageRoot, stackDir);

  const fallbackDistEntry = path.join(packageRoot, DIST_REL_PATH, DIST_INDEX_FILE);
  await writeFileAtomic(launcherPath, buildHookLauncherScript(fallbackDistEntry), {
    mode: EXECUTABLE_FILE_MODE,
  });

  if (!(await pathExists(envFilePath))) {
    const srcEnvExample = path.join(packageRoot, ENV_EXAMPLE_FILE_NAME);
    if (await pathExists(srcEnvExample)) {
      const exampleContent = await fsp.readFile(srcEnvExample, "utf-8");
      await writeFileAtomic(envFilePath, exampleContent);
    } else {
      await writeFileAtomic(envFilePath, DEFAULT_ENV_TEMPLATE);
    }
  }

  return {
    stackDir,
    composeFilePath,
    launcherPath,
    envFilePath,
  };
}

/**
 * Синоним `syncGlobalStackAssets` для гарантии наличия актуальных Docker-манифестов и раннера хуков.
 */
export const ensureGlobalDockerAssets = syncGlobalStackAssets;

/**
 * Синхронная версия синхронизации файлов глобального стека (`~/.mcp-coop-agentic-factory/`)
 * для обратной совместимости с синхронными вызовами материализатора и CLI.
 */
export function syncGlobalFactoryStackFiles(
  homeDirOverride?: string,
): GlobalStackAssetsResult {
  const stackDir = getGlobalFactoryDir(homeDirOverride);
  const packageRoot = resolveCliPackageRoot();
  const composeFilePath = path.join(stackDir, COMPOSE_REL_PATH);
  const launcherPath = path.join(stackDir, HOOK_LAUNCHER_REL_PATH);
  const envFilePath = path.join(stackDir, ENV_FILE_NAME);

  fs.mkdirSync(path.join(stackDir, DOCKER_DIR_NAME), { recursive: true });
  fs.mkdirSync(path.join(stackDir, SRC_DIR_NAME), { recursive: true });
  fs.mkdirSync(path.join(stackDir, SEEDS_REL_PATH), { recursive: true });
  fs.mkdirSync(path.join(stackDir, BIN_DIR_NAME), { recursive: true });

  copyRepositoryAssetsToStackDirSync(packageRoot, stackDir);

  const fallbackDistEntry = path.join(packageRoot, DIST_REL_PATH, DIST_INDEX_FILE);
  fs.writeFileSync(launcherPath, buildHookLauncherScript(fallbackDistEntry), "utf-8");
  try {
    fs.chmodSync(launcherPath, EXECUTABLE_FILE_MODE);
  } catch {
    // На платформах без POSIX-прав доступа chmod безопасно пропускается.
  }

  if (!fs.existsSync(envFilePath)) {
    const srcEnvExample = path.join(packageRoot, ENV_EXAMPLE_FILE_NAME);
    if (fs.existsSync(srcEnvExample)) {
      fs.copyFileSync(srcEnvExample, envFilePath);
    } else {
      fs.writeFileSync(envFilePath, DEFAULT_ENV_TEMPLATE, "utf-8");
    }
  }

  return {
    stackDir,
    composeFilePath,
    launcherPath,
    envFilePath,
  };
}
