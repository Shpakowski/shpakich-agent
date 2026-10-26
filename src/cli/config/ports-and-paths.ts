/**
 * Слой конфигурации CLI (`src/cli/config`): сетевые порты, переменные окружения, имена контейнеров
 * и канонические относительные пути в глобальной директории фабрики (`~/.mcp-coop-agentic-factory`)
 * и рабочем пространстве проекта (`.agents/`, `.context/`).
 */

import os from "node:os";
import path from "node:path";
import process from "node:process";

export const DEFAULT_CORE_HOST = "127.0.0.1" as const;

export const DEFAULT_SERVICE_PORTS = {
  CORE_API: 8787,
  POSTGRES: 5432,
  REDIS: 6379,
} as const;

export const DEFAULT_CORE_BASE_URL = "http://127.0.0.1:8787" as const;

export const ENV_VARS = {
  AGENT: "MCP_COOP_AGENT",
  CORE_URL: "MCP_COOP_CORE_URL",
  FACTORY_HOME: "MCP_COOP_FACTORY_HOME",
  WORKSPACE: "MCP_COOP_WORKSPACE",
} as const;

export const GLOBAL_FACTORY_DIR_NAME = ".mcp-coop-agentic-factory" as const;

export const GLOBAL_FACTORY_RELATIVE_PATHS = {
  COMPOSE_FILE: "docker-compose.yml",
  DOCKERFILE: "Dockerfile",
  ENV_FILE: ".env",
  HOOK_RUNNER_SCRIPT: "bin/run-hook.sh",
  PYTHON_SRC_DIR: "src/core",
  SEEDS_DIR: "seeds",
} as const;

export const WORKSPACE_RELATIVE_PATHS = {
  AGENTS_DIR: ".agents",
  MANIFEST_FILE: ".agents/.coop-manifest.json",
  HARNESS_MANIFEST: ".context/harness.json",
  SKILLS_DIR: ".agents/skills",
  RULES_DIR: ".agents/rules",
  CONTEXT_DIR: ".context",
  CONTEXT_INDEX: ".context/index.md",
  CONTEXT_SCHEMA: ".context/schema.md",
  CONTEXT_ENTITIES_DIR: ".context/entities",
  CONTEXT_MODULES_DIR: ".context/entities/modules",
  CONTEXT_CONTRACTS_DIR: ".context/entities/contracts",
  CONTEXT_DECISIONS_DIR: ".context/entities/decisions",
} as const;

export const DOCKER_CONTAINER_NAMES = {
  CORE: "mcp-coop-core",
  POSTGRES: "mcp-coop-postgres",
  REDIS: "mcp-coop-redis",
} as const;

export const OFFLINE_ORCHESTRATOR_SLUG = "coop-orchestrator" as const;
export const HARNESS_CONSOLIDATION_THRESHOLD_HOURS = 24 as const;
export const MANIFEST_SCHEMA_VERSION = "1.0.0" as const;
export const CLI_VERSION = "0.1.0" as const;

/**
 * Вычисляет абсолютный путь к глобальной директории Docker-стека фабрики с учётом
 * явного аргумента `homeDirOverride`, переменной окружения `MCP_COOP_FACTORY_HOME` и домашней папки ОС.
 */
export function resolveGlobalFactoryDir(homeDirOverride?: string): string {
  const explicitOverride = homeDirOverride?.trim();
  if (explicitOverride) {
    return path.resolve(explicitOverride);
  }

  const envOverride = process.env[ENV_VARS.FACTORY_HOME]?.trim();
  if (envOverride) {
    return path.resolve(envOverride);
  }

  return path.join(os.homedir(), GLOBAL_FACTORY_DIR_NAME);
}

/**
 * Разрешает базовый HTTP URL аналитического ядра без завершающего слэша, приоритизируя
 * переданный `overrideUrl`, затем `MCP_COOP_CORE_URL` и дефолтный `http://127.0.0.1:8787`.
 */
export function resolveCoreBaseUrl(overrideUrl?: string): string {
  const explicitUrl = overrideUrl?.trim();
  if (explicitUrl) {
    return explicitUrl.replace(/\/+$/, "");
  }

  const envUrl = process.env[ENV_VARS.CORE_URL]?.trim();
  if (envUrl) {
    return envUrl.replace(/\/+$/, "");
  }

  return DEFAULT_CORE_BASE_URL;
}

/**
 * Формирует полный URL интерактивного веб-визуализатора топологии `Harness` и графа `.context/`.
 */
export function resolveUiDashboardUrl(baseUrl?: string): string {
  return `${resolveCoreBaseUrl(baseUrl)}/ui`;
}

/**
 * Формирует полный URL локального шлюза `FastMCP` (Streamable HTTP транспорт `/mcp`).
 */
export function resolveMcpEndpointUrl(baseUrl?: string): string {
  return `${resolveCoreBaseUrl(baseUrl)}/mcp`;
}
