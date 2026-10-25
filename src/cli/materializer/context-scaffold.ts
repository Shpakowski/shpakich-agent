/**
 * Слой 1 (Host CLI Materializer): идемпотентный генератор каркаса Контекстного Графа (`.context/`).
 * Создаёт стартовую карту `.context/index.md`, шаблоны `SPEC.md` и `architecture.md`, а также
 * иерархию директорий `entities/{modules,contracts,decisions}` без перезаписи существующих узлов.
 */

import fs from "node:fs";

import {
  pathExistsSync,
  requireSafeWorkspacePath as resolveSafeWorkspacePath,
  writeFileAtomicSync,
} from "../common/index.ts";
import {
  MANIFEST_SCHEMA_VERSION,
  WORKSPACE_RELATIVE_PATHS,
} from "../config/index.ts";
import {
  CONTEXT_GRAPH_INDEX_REL_PATH,
  GLOBAL_STACK_TAG,
} from "../detector/index.ts";

export const CONTEXT_DIR_REL_PATH = WORKSPACE_RELATIVE_PATHS.CONTEXT_DIR;

export const CONTEXT_ENTITY_DIRS = [
  WORKSPACE_RELATIVE_PATHS.CONTEXT_MODULES_DIR,
  WORKSPACE_RELATIVE_PATHS.CONTEXT_CONTRACTS_DIR,
  WORKSPACE_RELATIVE_PATHS.CONTEXT_DECISIONS_DIR,
] as const;

export const CONTEXT_TEMPLATES_VERSION = MANIFEST_SCHEMA_VERSION;

function formatTodayIsoDate(today?: string): string {
  return today && today.trim().length > 0
    ? today.trim()
    : new Date().toISOString().slice(0, 10);
}

function formatStackLine(stackTags: readonly string[]): string {
  return stackTags.length > 0 ? stackTags.join(", ") : GLOBAL_STACK_TAG;
}

/**
 * Генерирует стартовый Markdown-шаблон главной карты графа `.context/index.md`.
 */
export function buildContextIndexMarkdown(
  projectName: string,
  stackTags: readonly string[],
  today?: string,
): string {
  const dateStr = formatTodayIsoDate(today);
  const stackLine = formatStackLine(stackTags);
  return [
    "---",
    "type: GraphIndex",
    `version: "${CONTEXT_TEMPLATES_VERSION}"`,
    `project: ${projectName}`,
    `updated_at: "${dateStr}"`,
    "---",
    "",
    `# Context Graph Map (\`${projectName}\`)`,
    "",
    `> **Active Stack Tags:** \`${stackLine}\``,
    "",
    "## 1. Architectural Modules (`.context/entities/modules/`)",
    "*(No module nodes registered yet)*",
    "",
    "## 2. Contracts & Interfaces (`.context/entities/contracts/`)",
    "*(No contract nodes registered yet)*",
    "",
    "## 3. Architectural Decisions (`.context/entities/decisions/`)",
    "*(No decision nodes registered yet)*",
    "",
  ].join("\n");
}

/**
 * Генерирует стартовый Markdown-шаблон спецификации `.context/SPEC.md`.
 */
export function buildContextSpecMarkdown(
  projectName: string,
  stackTags: readonly string[],
  today?: string,
): string {
  const dateStr = formatTodayIsoDate(today);
  const stackLine = formatStackLine(stackTags);
  return [
    "---",
    "type: ProjectSpec",
    `version: "${CONTEXT_TEMPLATES_VERSION}"`,
    `project: ${projectName}`,
    `updated_at: "${dateStr}"`,
    "---",
    "",
    `# Project Specification (\`${projectName}\`)`,
    "",
    `> **Active Stack Tags:** \`${stackLine}\``,
    "",
    "## 1. Goals & Scope",
    "- Define core functional and non-functional requirements here.",
    "",
    "## 2. Domain Invariants",
    "- Document key domain invariants and contracts here.",
    "",
  ].join("\n");
}

/**
 * Генерирует стартовый Markdown-шаблон архитектурного обзора `.context/architecture.md`.
 */
export function buildContextArchitectureMarkdown(
  projectName: string,
  stackTags: readonly string[],
  today?: string,
): string {
  const dateStr = formatTodayIsoDate(today);
  const stackLine = formatStackLine(stackTags);
  return [
    "---",
    "type: ArchitectureOverview",
    `version: "${CONTEXT_TEMPLATES_VERSION}"`,
    `project: ${projectName}`,
    `updated_at: "${dateStr}"`,
    "---",
    "",
    `# Architecture Overview (\`${projectName}\`)`,
    "",
    `> **Active Stack Tags:** \`${stackLine}\``,
    "",
    "## 1. Layer Boundaries",
    "- Domain Core -> Application Use Cases -> Inbound/Outbound Adapters.",
    "",
    "## 2. Context Graph Synchronization",
    "- All structural changes must be reflected in `.context/index.md` and `.context/entities/`.",
    "",
  ].join("\n");
}

/**
 * Создаёт каркас Контекстного Графа (`.context/index.md` и директории `.context/entities/*`),
 * если он ещё не инициализирован в рабочей директории проекта.
 */
export function ensureContextGraphScaffold(
  workspaceRoot: string,
  projectName: string,
  stackTags: readonly string[],
  options?: { includeExtendedDocs?: boolean | undefined },
): boolean {
  for (const relDir of CONTEXT_ENTITY_DIRS) {
    const fullDir = resolveSafeWorkspacePath(workspaceRoot, relDir);
    fs.mkdirSync(fullDir, { recursive: true });
  }

  const today = formatTodayIsoDate();

  if (options?.includeExtendedDocs) {
    const specPath = resolveSafeWorkspacePath(workspaceRoot, ".context/SPEC.md");
    if (!pathExistsSync(specPath)) {
      writeFileAtomicSync(
        specPath,
        buildContextSpecMarkdown(projectName, stackTags, today),
      );
    }
    const archPath = resolveSafeWorkspacePath(
      workspaceRoot,
      ".context/architecture.md",
    );
    if (!pathExistsSync(archPath)) {
      writeFileAtomicSync(
        archPath,
        buildContextArchitectureMarkdown(projectName, stackTags, today),
      );
    }
  }

  const indexPath = resolveSafeWorkspacePath(
    workspaceRoot,
    CONTEXT_GRAPH_INDEX_REL_PATH,
  );
  if (pathExistsSync(indexPath)) {
    return false;
  }

  const content = buildContextIndexMarkdown(projectName, stackTags, today);
  writeFileAtomicSync(indexPath, content);
  return true;
}
