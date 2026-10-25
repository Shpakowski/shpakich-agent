/**
 * Слой конфигурации CLI (`src/cli/config`): декларативные таблицы сигнатур технологического стека
 * и список игнорируемых служебных директорий при сканировании рабочего пространства проекта.
 * Позволяет расширять правила распознавания языков, фреймворков и БД без изменения кода детектора (OCP).
 */

export const IGNORED_SCAN_DIRECTORIES: ReadonlySet<string> = new Set([
  ".git",
  "node_modules",
  ".venv",
  "venv",
  "__pycache__",
  "dist",
  "build",
  ".next",
  ".turbo",
  ".cache",
  ".agents",
  ".context",
]);

export const UNIVERSAL_STACK_TAGS = ["global"] as const;

/**
 * Карта соответствия относительных путей файлов-маркеров базовым тегам платформ и языков.
 */
export const FILE_MARKER_TAG_MAP: Readonly<Record<string, readonly string[]>> = {
  "package.json": ["nodejs"],
  "tsconfig.json": ["typescript"],
  "bun.lockb": ["bun"],
  "bun.lock": ["bun"],
  "bunfig.toml": ["bun"],
  "pyproject.toml": ["python"],
  "requirements.txt": ["python"],
  Dockerfile: ["docker"],
  "docker-compose.yml": ["docker"],
  "compose.yaml": ["docker"],
  "docker/Dockerfile": ["docker"],
  "docker/docker-compose.yml": ["docker"],
  "Cargo.toml": ["rust"],
  "go.mod": ["go"],
} as const;

/**
 * Карта соответствия пакетов из `package.json` (`dependencies`, `devDependencies`, `peerDependencies`)
 * специализированным тегам экосистемы Node.js / TypeScript.
 */
export const NODE_DEP_TAG_MAP: Readonly<Record<string, string>> = {
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
 * Карта соответствия токенов зависимостей Python и инфраструктурных манифестов (`pyproject.toml`,
 * `requirements.txt`, `docker-compose.yml`) каноническим тегам стека.
 */
export const PYTHON_DEP_TAG_MAP: Readonly<Record<string, string>> = {
  fastapi: "fastapi",
  fastmcp: "fastmcp",
  mcp: "fastmcp",
  pydantic: "pydantic",
  asyncpg: "asyncpg",
  sqlalchemy: "sqlalchemy",
  pytest: "pytest",
  pgvector: "pgvector",
  postgres: "postgresql",
  postgresql: "postgresql",
  psycopg: "postgresql",
  psycopg2: "postgresql",
  redis: "redis",
} as const;

/**
 * Унифицированное правило детекции тега технологического стека по файловым маркерам,
 * npm/Python-зависимостям или регулярному выражению по текстовым манифестам.
 */
export interface StackSignatureRule {
  readonly tag: string;
  readonly fileMarkers?: readonly string[];
  readonly nodeDependencies?: readonly string[];
  readonly pythonDependencies?: readonly string[];
  readonly contentPattern?: RegExp;
}

/**
 * Единый реестр сигнатур стека для детерминированного профилирования рабочей директории.
 */
export const STACK_SIGNATURE_RULES: readonly StackSignatureRule[] = [
  {
    tag: "nodejs",
    fileMarkers: ["package.json"],
  },
  {
    tag: "typescript",
    fileMarkers: ["tsconfig.json"],
    nodeDependencies: ["typescript"],
  },
  {
    tag: "bun",
    fileMarkers: ["bun.lockb", "bun.lock", "bunfig.toml"],
  },
  {
    tag: "python",
    fileMarkers: ["pyproject.toml", "requirements.txt"],
  },
  {
    tag: "docker",
    fileMarkers: [
      "Dockerfile",
      "docker-compose.yml",
      "compose.yaml",
      "docker/Dockerfile",
      "docker/docker-compose.yml",
    ],
  },
  {
    tag: "rust",
    fileMarkers: ["Cargo.toml"],
  },
  {
    tag: "go",
    fileMarkers: ["go.mod"],
  },
  {
    tag: "react",
    nodeDependencies: ["react"],
  },
  {
    tag: "next",
    nodeDependencies: ["next"],
  },
  {
    tag: "vue",
    nodeDependencies: ["vue"],
  },
  {
    tag: "svelte",
    nodeDependencies: ["svelte"],
  },
  {
    tag: "express",
    nodeDependencies: ["express"],
  },
  {
    tag: "fastify",
    nodeDependencies: ["fastify"],
  },
  {
    tag: "nestjs",
    nodeDependencies: ["nestjs", "@nestjs/core"],
  },
  {
    tag: "prisma",
    nodeDependencies: ["prisma", "@prisma/client"],
  },
  {
    tag: "drizzle-orm",
    nodeDependencies: ["drizzle-orm"],
  },
  {
    tag: "vitest",
    nodeDependencies: ["vitest"],
  },
  {
    tag: "playwright",
    nodeDependencies: ["playwright", "@playwright/test"],
  },
  {
    tag: "tailwindcss",
    nodeDependencies: ["tailwindcss"],
  },
  {
    tag: "fastapi",
    pythonDependencies: ["fastapi"],
    contentPattern: /\bfastapi\b/i,
  },
  {
    tag: "fastmcp",
    pythonDependencies: ["fastmcp", "mcp"],
    contentPattern: /\b(?:fastmcp|mcp)\b/i,
  },
  {
    tag: "pydantic",
    pythonDependencies: ["pydantic"],
    contentPattern: /\bpydantic\b/i,
  },
  {
    tag: "asyncpg",
    pythonDependencies: ["asyncpg"],
    contentPattern: /\basyncpg\b/i,
  },
  {
    tag: "sqlalchemy",
    pythonDependencies: ["sqlalchemy"],
    contentPattern: /\bsqlalchemy\b/i,
  },
  {
    tag: "pytest",
    pythonDependencies: ["pytest"],
    contentPattern: /\bpytest\b/i,
  },
  {
    tag: "pgvector",
    pythonDependencies: ["pgvector"],
    contentPattern: /\bpgvector\b/i,
  },
  {
    tag: "postgresql",
    pythonDependencies: ["postgres", "postgresql", "psycopg", "psycopg2"],
    contentPattern: /\bpostgres(?:ql)?\b/i,
  },
  {
    tag: "redis",
    pythonDependencies: ["redis"],
    contentPattern: /\bredis\b/i,
  },
] as const;
