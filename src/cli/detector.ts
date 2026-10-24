/**
 * Слой 1 (Host CLI Injector): детектор технологического стека проекта и целевого ИИ-агента.
 * Сканирует рабочую директорию (`package.json`, `pyproject.toml`, Docker-манифесты, маркерные папки IDE)
 * для формирования списка тегов `stackTags` (используемых при фильтрации правил `Harness²` и скиллов в БД)
 * и автоматического выбора адаптера ИИ-агента. Декларативные таблицы правил используются вместо
 * каскадных условных ветвлений, чтобы расширение поддерживаемых технологий и агентов не требовало
 * модификации алгоритмов обхода файловой системы (принцип Open-Closed).
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";

export const SUPPORTED_AGENTS = [
  "antigravity",
  "claude-code",
  "cursor",
  "codex",
  "opencode",
] as const;

export type AgentId = (typeof SUPPORTED_AGENTS)[number];

export const AGENT_DISPLAY_NAMES: Readonly<Record<AgentId, string>> = {
  antigravity: "Google Antigravity",
  "claude-code": "Claude Code",
  cursor: "Cursor",
  codex: "OpenAI Codex CLI",
  opencode: "OpenCode",
} as const;

export const AGENT_CONFIG_HINTS: Readonly<Record<AgentId, string>> = {
  antigravity: ".agents/plugins & hooks.json",
  "claude-code": ".claude/settings.json & CLAUDE.md",
  cursor: ".cursor/rules/*.mdc & hooks.json",
  codex: ".codex/hooks.json & AGENTS.md",
  opencode: ".opencode/plugins & opencode.json",
} as const;

export const AUTO_AGENT_OPTION = "auto" as const;
export type AgentOption = AgentId | typeof AUTO_AGENT_OPTION;

export const DEFAULT_AGENT: AgentId = "antigravity";
export const GLOBAL_STACK_TAG = "global" as const;
export const CONTEXT_GRAPH_INDEX_REL_PATH = ".context/index.md";
export const AGENT_ENV_KEYS = ["MCP_COOP_DEFAULT_AGENT", "DEFAULT_AGENT"] as const;

const PACKAGE_JSON_REL_PATH = "package.json";

const TEXT_MANIFEST_REL_PATHS = [
  "pyproject.toml",
  "requirements.txt",
  "docker-compose.yml",
  "docker/docker-compose.yml",
  "compose.yaml",
] as const;

export type AgentResolutionSource =
  | "cli_flag"
  | "environment"
  | "workspace_marker"
  | "default_fallback";

export interface ResolvedAgentInfo {
  agent: AgentId;
  source: AgentResolutionSource;
  matchedMarker?: string;
}

export interface WorkspaceProfile {
  workspacePath: string;
  projectName: string;
  agent: AgentId;
  agentSource: AgentResolutionSource;
  matchedAgentMarker?: string;
  stackTags: string[];
  hasContextGraph: boolean;
  contextGraphUpdatedAt?: string;
}

export interface FileStackRule {
  readonly relativePaths: readonly string[];
  readonly tags: readonly string[];
}

export interface TextManifestTokenRule {
  readonly pattern: RegExp;
  readonly tag: string;
}

export interface AgentWorkspaceMarkerRule {
  readonly agent: AgentId;
  readonly markers: readonly string[];
}

/**
 * Таблица соответствия файлов проекта базовым тегам платформ и языков.
 * Достаточно наличия хотя бы одного файла из `relativePaths`, чтобы присвоить проекту указанные `tags`.
 */
export const FILE_STACK_RULES: readonly FileStackRule[] = [
  {
    relativePaths: [PACKAGE_JSON_REL_PATH],
    tags: ["nodejs"],
  },
  {
    relativePaths: ["tsconfig.json"],
    tags: ["typescript"],
  },
  {
    relativePaths: ["bun.lockb", "bun.lock", "bunfig.toml"],
    tags: ["bun"],
  },
  {
    relativePaths: ["pyproject.toml", "requirements.txt"],
    tags: ["python"],
  },
  {
    relativePaths: [
      "Dockerfile",
      "docker-compose.yml",
      "compose.yaml",
      "docker/Dockerfile",
      "docker/docker-compose.yml",
    ],
    tags: ["docker"],
  },
  {
    relativePaths: ["Cargo.toml"],
    tags: ["rust"],
  },
  {
    relativePaths: ["go.mod"],
    tags: ["go"],
  },
] as const;

/**
 * Таблица соответствия зависимостей `package.json` (`dependencies`, `devDependencies`, `peerDependencies`)
 * специализированным тегам фреймворков и инструментов экосистемы JavaScript/TypeScript.
 */
export const PACKAGE_JSON_DEPENDENCY_RULES: Readonly<Record<string, string>> = {
  typescript: "typescript",
  react: "react",
  next: "next",
  vue: "vue",
  svelte: "svelte",
  express: "express",
  fastify: "fastify",
  nestjs: "nestjs",
  "@nestjs/core": "nestjs",
  prisma: "prisma",
  "@prisma/client": "prisma",
  "drizzle-orm": "drizzle-orm",
  vitest: "vitest",
  playwright: "playwright",
  "@playwright/test": "playwright",
  tailwindcss: "tailwindcss",
} as const;

/**
 * Правила извлечения тегов инфраструктурных сервисов и Python-библиотек из текстовых манифестов.
 * Регулярные выражения с границами слов (`\b`) предотвращают ложные срабатывания на подстроках.
 */
export const TEXT_MANIFEST_TOKEN_RULES: readonly TextManifestTokenRule[] = [
  { pattern: /\bfastapi\b/i, tag: "fastapi" },
  { pattern: /\b(?:fastmcp|mcp)\b/i, tag: "fastmcp" },
  { pattern: /\bpydantic\b/i, tag: "pydantic" },
  { pattern: /\basyncpg\b/i, tag: "asyncpg" },
  { pattern: /\bsqlalchemy\b/i, tag: "sqlalchemy" },
  { pattern: /\bpytest\b/i, tag: "pytest" },
  { pattern: /\bpgvector\b/i, tag: "pgvector" },
  { pattern: /\bpostgres(?:ql)?\b/i, tag: "postgresql" },
  { pattern: /\bredis\b/i, tag: "redis" },
] as const;

/**
 * Приоритетный список маркеров рабочей директории для автоопределения целевого ИИ-агента.
 * Порядок элементов определяет приоритет выбора, если в репозитории присутствует несколько конфигураций.
 */
export const AGENT_WORKSPACE_MARKERS: readonly AgentWorkspaceMarkerRule[] = [
  {
    agent: "antigravity",
    markers: [".agents", ".gemini/antigravity"],
  },
  {
    agent: "claude-code",
    markers: [".claude", "CLAUDE.md"],
  },
  {
    agent: "cursor",
    markers: [".cursor", ".cursorrules"],
  },
  {
    agent: "codex",
    markers: [".codex"],
  },
  {
    agent: "opencode",
    markers: [".opencode"],
  },
] as const;

/**
 * Проверяет, входит ли переданный строковый идентификатор в список поддерживаемых ИИ-агентов.
 */
export function isSupportedAgent(value: string): value is AgentId {
  return (SUPPORTED_AGENTS as readonly string[]).includes(value);
}

/**
 * Безопасно читает и разбирает `package.json` для извлечения тегов npm-зависимостей.
 * При отсутствии файла или синтаксической ошибке JSON возвращает пустой список без прерывания работы CLI.
 */
function extractPackageJsonStackTags(workspaceDir: string): string[] {
  const packageJsonPath = path.join(workspaceDir, PACKAGE_JSON_REL_PATH);
  if (!fs.existsSync(packageJsonPath)) {
    return [];
  }

  try {
    const rawContent = fs.readFileSync(packageJsonPath, "utf-8");
    const parsed = JSON.parse(rawContent) as Record<string, unknown>;
    const dependencySections = [
      parsed.dependencies,
      parsed.devDependencies,
      parsed.peerDependencies,
    ];

    const matchedTags: string[] = [];
    for (const section of dependencySections) {
      if (!section || typeof section !== "object") {
        continue;
      }
      for (const pkgName of Object.keys(section as Record<string, unknown>)) {
        const mappedTag = PACKAGE_JSON_DEPENDENCY_RULES[pkgName];
        if (mappedTag) {
          matchedTags.push(mappedTag);
        }
      }
    }

    return matchedTags;
  } catch {
    return [];
  }
}

/**
 * Сканирует текстовые манифесты (`pyproject.toml`, `requirements.txt`, `docker-compose.yml`)
 * на наличие сигнатур библиотек и инфраструктурных контейнеров.
 */
function extractTextManifestStackTags(workspaceDir: string): string[] {
  const matchedTags: string[] = [];

  for (const relPath of TEXT_MANIFEST_REL_PATHS) {
    const fullPath = path.join(workspaceDir, relPath);
    if (!fs.existsSync(fullPath)) {
      continue;
    }

    try {
      const content = fs.readFileSync(fullPath, "utf-8");
      for (const rule of TEXT_MANIFEST_TOKEN_RULES) {
        if (rule.pattern.test(content)) {
          matchedTags.push(rule.tag);
        }
      }
    } catch {
      continue;
    }
  }

  return matchedTags;
}

/**
 * Формирует список технологических тегов проекта для селективной фильтрации правил `Harness²` и скиллов.
 * Первым элементом всегда фиксируется тег `"global"`, гарантирующий подключение базовых инвариантов фабрики,
 * за которым следуют уникальные теги стека проекта в алфавитном порядке.
 */
export function detectStackTags(workspaceDir: string): string[] {
  const resolvedDir = path.resolve(workspaceDir);
  const discoveredTags = new Set<string>();

  for (const rule of FILE_STACK_RULES) {
    const hasMatchingFile = rule.relativePaths.some((relPath) =>
      fs.existsSync(path.join(resolvedDir, relPath)),
    );
    if (hasMatchingFile) {
      for (const tag of rule.tags) {
        discoveredTags.add(tag);
      }
    }
  }

  for (const tag of extractPackageJsonStackTags(resolvedDir)) {
    discoveredTags.add(tag);
  }

  for (const tag of extractTextManifestStackTags(resolvedDir)) {
    discoveredTags.add(tag);
  }

  discoveredTags.delete(GLOBAL_STACK_TAG);
  const sortedProjectTags = Array.from(discoveredTags).sort((a, b) =>
    a.localeCompare(b),
  );

  return [GLOBAL_STACK_TAG, ...sortedProjectTags];
}

/**
 * Возвращает полный отсортированный каталог всех поддерживаемых доменных тегов стека
 * (без базового тега `"global"`). Используется интерактивным визардом `init` для отображения
 * полного чеклиста доступных технологий поверх автообнаруженных маркеров проекта.
 */
export function getAllKnownStackTags(): string[] {
  const knownTags = new Set<string>();

  for (const rule of FILE_STACK_RULES) {
    for (const tag of rule.tags) {
      knownTags.add(tag);
    }
  }

  for (const tag of Object.values(PACKAGE_JSON_DEPENDENCY_RULES)) {
    knownTags.add(tag);
  }

  for (const rule of TEXT_MANIFEST_TOKEN_RULES) {
    knownTags.add(rule.tag);
  }

  knownTags.delete(GLOBAL_STACK_TAG);
  return Array.from(knownTags).sort((a, b) => a.localeCompare(b));
}

/**
 * Определяет целевого ИИ-агента для генерации конфигураций и хуков по каскаду приоритетов:
 * 1) явный CLI-флаг `--agent` (если не равен `"auto"`);
 * 2) переменные окружения (`MCP_COOP_DEFAULT_AGENT`, `DEFAULT_AGENT`);
 * 3) маркерные директории или файлы в корне проекта (`AGENT_WORKSPACE_MARKERS`);
 * 4) дефолтный агент фабрики (`DEFAULT_AGENT`).
 */
export function resolveTargetAgent(
  workspaceDir: string,
  requestedAgent?: string,
  env: NodeJS.ProcessEnv = process.env,
): ResolvedAgentInfo {
  if (requestedAgent !== undefined) {
    const normalizedRequested = requestedAgent.trim().toLowerCase();
    if (
      normalizedRequested.length > 0 &&
      normalizedRequested !== AUTO_AGENT_OPTION
    ) {
      if (!isSupportedAgent(normalizedRequested)) {
        throw new Error(
          `Unsupported target agent "${requestedAgent}". Supported values: ${AUTO_AGENT_OPTION}, ${SUPPORTED_AGENTS.join(", ")}.`,
        );
      }
      return {
        agent: normalizedRequested,
        source: "cli_flag",
      };
    }
  }

  for (const envKey of AGENT_ENV_KEYS) {
    const rawEnvValue = env[envKey];
    if (!rawEnvValue) {
      continue;
    }
    const normalizedEnvValue = rawEnvValue.trim().toLowerCase();
    if (
      normalizedEnvValue.length === 0 ||
      normalizedEnvValue === AUTO_AGENT_OPTION
    ) {
      continue;
    }
    if (isSupportedAgent(normalizedEnvValue)) {
      return {
        agent: normalizedEnvValue,
        source: "environment",
        matchedMarker: envKey,
      };
    }
  }

  const resolvedDir = path.resolve(workspaceDir);
  for (const rule of AGENT_WORKSPACE_MARKERS) {
    for (const marker of rule.markers) {
      if (fs.existsSync(path.join(resolvedDir, marker))) {
        return {
          agent: rule.agent,
          source: "workspace_marker",
          matchedMarker: marker,
        };
      }
    }
  }

  return {
    agent: DEFAULT_AGENT,
    source: "default_fallback",
  };
}

/**
 * Читает метаданные файла индекса `.context/index.md` и форматирует время его последней модификации
 * в локальном часовом поясе (`YYYY-MM-DD HH:mm`) для диагностических карточек CLI.
 */
export function getContextGraphLastUpdated(
  indexFilePath: string,
): string | undefined {
  try {
    const stats = fs.statSync(indexFilePath);
    const mtime = stats.mtime;
    const year = mtime.getFullYear();
    const month = String(mtime.getMonth() + 1).padStart(2, "0");
    const day = String(mtime.getDate()).padStart(2, "0");
    const hours = String(mtime.getHours()).padStart(2, "0");
    const minutes = String(mtime.getMinutes()).padStart(2, "0");
    return `${year}-${month}-${day} ${hours}:${minutes}`;
  } catch {
    return undefined;
  }
}

/**
 * Собирает полный профиль рабочей директории проекта: абсолютный путь, имя проекта,
 * выбранного ИИ-агента с источником решения, теги технологического стека и статус графа `.context/`.
 */
export function inspectWorkspace(
  workspaceDir: string = process.cwd(),
  requestedAgent?: string,
  env: NodeJS.ProcessEnv = process.env,
): WorkspaceProfile {
  const resolvedWorkspacePath = path.resolve(workspaceDir);
  const projectName =
    path.basename(resolvedWorkspacePath) || GLOBAL_STACK_TAG;
  const resolvedAgent = resolveTargetAgent(
    resolvedWorkspacePath,
    requestedAgent,
    env,
  );
  const stackTags = detectStackTags(resolvedWorkspacePath);
  const contextIndexFullPath = path.join(
    resolvedWorkspacePath,
    CONTEXT_GRAPH_INDEX_REL_PATH,
  );
  const hasContextGraph = fs.existsSync(contextIndexFullPath);
  const contextGraphUpdatedAt = hasContextGraph
    ? getContextGraphLastUpdated(contextIndexFullPath)
    : undefined;

  return {
    workspacePath: resolvedWorkspacePath,
    projectName,
    agent: resolvedAgent.agent,
    agentSource: resolvedAgent.source,
    matchedAgentMarker: resolvedAgent.matchedMarker,
    stackTags,
    hasContextGraph,
    contextGraphUpdatedAt,
  };
}
