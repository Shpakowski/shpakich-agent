/**
 * Слой 1 (Host CLI Injector): двусторонний сканер пользовательских компонентов и 3-Way Hash Sync графа `.context/`.
 * Отвечает за обнаружение кастомных скиллов и правил в рабочей директории с защитой от самоинжеста (`Anti-Self-Ingest`),
 * вычисление канонических SHA-256 хешей и атомарную запись синхронизированных/смерженных узлов `.context/` (`SPEC-002`).
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  FACTORY_HOOK_SIGNATURE,
  type ProjectHarnessManifest,
} from "./materializer.ts";

export const SYSTEM_BOOTSTRAP_SLUGS = [
  "harness-orchestrator",
  "core-engineering-mentor",
  "block-destructive-commands",
  "context-graph-discipline",
  "context-graph-steward",
  "inject-context-map",
  "track-file-changes",
  "enforce-context-sync",
  "mcp-coop-factory",
] as const;

const SYSTEM_BOOTSTRAP_SLUG_SET: ReadonlySet<string> = new Set(
  SYSTEM_BOOTSTRAP_SLUGS,
);

const CUSTOM_SKILL_PARENT_DIRS = [
  ".agents/skills",
  ".claude/skills",
  ".cursor/skills",
] as const;

const CUSTOM_RULE_DIRS = [
  ".agents/rules",
  ".claude/rules",
  ".cursor/rules",
] as const;

const SKILL_FILE_NAME = "SKILL.md" as const;
const RULE_EXTENSIONS = new Set([".md", ".mdc"]);
const PLUGIN_IGNORE_PREFIX = `.agents/plugins/${FACTORY_HOOK_SIGNATURE}/` as const;
const CONTEXT_ROOT_REL_DIR = ".context" as const;

const CONTEXT_ROOT_DOC_FILES: ReadonlyArray<
  readonly [relativePath: string, entityClass: string]
> = [
  ["index.md", "GraphIndex"],
  ["schema.md", "Contract"],
] as const;

const CONTEXT_ENTITY_SUBDIRS: ReadonlyArray<
  readonly [subDir: string, entityClass: string]
> = [
  ["entities/modules", "Module"],
  ["entities/contracts", "Contract"],
  ["entities/decisions", "Decision"],
] as const;

export interface WorkspaceComponentCandidatePayload {
  slug: string;
  component_type: "skill" | "system_prompt" | "tool_guidance";
  title: string;
  description: string;
  content: string;
  content_hash: string;
  stack_tags: string[];
  file_updated_at?: string;
  metadata?: Record<string, unknown>;
}

export interface ComponentIngestItemPayload {
  slug: string;
  component_type: string;
  action: string;
  version: number;
  reason?: string | null;
}

export interface ComponentIngestReportPayload {
  status?: string;
  workspace_path: string;
  processed: number;
  created: number;
  updated: number;
  unchanged: number;
  rejected: number;
  items: ComponentIngestItemPayload[];
}

export interface ContextNodeSyncInputPayload {
  relative_path: string;
  node_id: string;
  entity_class: string;
  content: string;
  content_hash: string;
  base_hash: string;
  file_updated_at?: string;
}

export interface ContextNodeSyncItemPayload {
  relative_path: string;
  node_id: string;
  action: "unchanged" | "pushed_to_db" | "pulled_from_db" | "merged";
  content_hash: string;
  base_hash: string;
  content?: string | null;
}

export interface ContextSyncReportPayload {
  status?: string;
  workspace_path: string;
  unchanged: number;
  pushed_to_db: number;
  pulled_from_db: number;
  merged: number;
  items: ContextNodeSyncItemPayload[];
}

export interface ConsolidationClusterPayload {
  canonical_slug: string;
  component_type: string;
  duplicate_slugs: string[];
  merged_stack_tags: string[];
  max_similarity: number;
  synthesized_title: string;
  synthesized_description: string;
  synthesized_content: string;
  applied: boolean;
}

export interface ConsolidationReportPayload {
  status?: string;
  dry_run: boolean;
  clusters_found: number;
  components_archived: number;
  clusters: ConsolidationClusterPayload[];
}

/**
 * Вычисляет канонический SHA-256 дайджест текста с нормализацией переводов строк (`\r\n` -> `\n`)
 * и обрезкой крайних пробельных символов, полностью совместимый с Python `compute_normalized_content_hash`.
 */
export function computeNormalizedContentHash(content: string): string {
  const normalized = content.replace(/\r\n?/g, "\n").trim();
  return crypto.createHash("sha256").update(normalized, "utf-8").digest("hex");
}

function normalizeSlug(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

const MIN_SLUG_TAG_LENGTH = 2 as const;

/**
 * Извлекает детерминированный набор семантических тегов напрямую из имени компонента (`slug`),
 * разбивая его по дефисам и подчёркиваниям без копирования всех тегов рабочего пространства.
 */
export function extractTagsFromSlug(
  slug: string,
  fallbackTags: readonly string[] = [],
): string[] {
  const normalized = normalizeSlug(slug);
  const tokens = normalized
    .split(/[-_]+/)
    .map((tok) => tok.trim().toLowerCase())
    .filter((tok) => tok.length >= MIN_SLUG_TAG_LENGTH);
  const unique = Array.from(new Set(tokens));
  return unique.length > 0 ? unique : [...fallbackTags];
}

function extractMarkdownMetadata(
  rawContent: string,
  fallbackSlug: string,
): { title: string; description: string } {
  const normalized = rawContent.replace(/\r\n?/g, "\n");
  let fmTitle = "";
  let fmDescription = "";
  let bodyPart = normalized;

  if (normalized.startsWith("---\n")) {
    const endIdx = normalized.indexOf("\n---", 4);
    if (endIdx > 0) {
      const fmBlock = normalized.slice(4, endIdx);
      bodyPart = normalized.slice(endIdx + 4);
      for (const line of fmBlock.split("\n")) {
        const match = /^([a-zA-Z0-9_-]+)\s*:\s*(.*)$/.exec(line.trim());
        if (!match) {
          continue;
        }
        const key = (match[1] ?? "").toLowerCase();
        const val = (match[2] ?? "").replace(/^["'>|-]+|["']+$/g, "").trim();
        if ((key === "title" || key === "name") && val && !fmTitle) {
          fmTitle = val;
        } else if (key === "description" && val && !fmDescription) {
          fmDescription = val;
        }
      }
    }
  }

  let headingTitle = "";
  let firstParagraph = "";
  for (const rawLine of bodyPart.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("```")) {
      continue;
    }
    if (line.startsWith("#")) {
      if (!headingTitle) {
        headingTitle = line.replace(/^#+\s*/, "").trim();
      }
      continue;
    }
    if (!firstParagraph) {
      firstParagraph = line.slice(0, 200);
    }
  }

  return {
    title: headingTitle || fmTitle || fallbackSlug,
    description: fmDescription || firstParagraph || headingTitle || fallbackSlug,
  };
}

/**
 * Сканирует пользовательские директории скиллов и правил проекта, исключая системные файлы плагина,
 * управляемые файлы `manifest.managed_files` и системные bootstrap-компоненты (`Anti-Self-Ingest`).
 */
export function scanCustomWorkspaceComponents(
  workspaceRoot: string,
  manifest: ProjectHarnessManifest,
  effectiveTags: readonly string[],
): WorkspaceComponentCandidatePayload[] {
  const resolvedRoot = path.resolve(workspaceRoot);
  const managedSet = new Set(
    manifest.managed_files.map((rel) => rel.replace(/\\/g, "/").replace(/^\/+/, "")),
  );
  const candidates: WorkspaceComponentCandidatePayload[] = [];
  const seenSlugs = new Set<string>();

  for (const skillParentRel of CUSTOM_SKILL_PARENT_DIRS) {
    const parentAbs = path.join(resolvedRoot, skillParentRel);
    if (!fs.existsSync(parentAbs)) {
      continue;
    }
    let entries: fs.Dirent[] = [];
    try {
      entries = fs.readdirSync(parentAbs, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) {
        continue;
      }
      const relPath = `${skillParentRel}/${entry.name}/${SKILL_FILE_NAME}`;
      if (relPath.startsWith(PLUGIN_IGNORE_PREFIX) || managedSet.has(relPath)) {
        continue;
      }
      const slug = normalizeSlug(entry.name);
      if (!slug || SYSTEM_BOOTSTRAP_SLUG_SET.has(slug) || seenSlugs.has(slug)) {
        continue;
      }
      const skillFileAbs = path.join(parentAbs, entry.name, SKILL_FILE_NAME);
      if (!fs.existsSync(skillFileAbs)) {
        continue;
      }
      try {
        const stat = fs.statSync(skillFileAbs);
        const content = fs.readFileSync(skillFileAbs, "utf-8");
        if (content.trim().length === 0) {
          continue;
        }
        const meta = extractMarkdownMetadata(content, slug);
        seenSlugs.add(slug);
        candidates.push({
          slug,
          component_type: "skill",
          title: meta.title,
          description: meta.description,
          content,
          content_hash: computeNormalizedContentHash(content),
          stack_tags: extractTagsFromSlug(slug, effectiveTags),
          file_updated_at: stat.mtime.toISOString(),
          metadata: { source_relative_path: relPath },
        });
      } catch {
        continue;
      }
    }
  }

  for (const ruleDirRel of CUSTOM_RULE_DIRS) {
    const ruleDirAbs = path.join(resolvedRoot, ruleDirRel);
    if (!fs.existsSync(ruleDirAbs)) {
      continue;
    }
    let entries: fs.Dirent[] = [];
    try {
      entries = fs.readdirSync(ruleDirAbs, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isFile()) {
        continue;
      }
      const ext = path.extname(entry.name).toLowerCase();
      if (!RULE_EXTENSIONS.has(ext)) {
        continue;
      }
      const relPath = `${ruleDirRel}/${entry.name}`;
      if (relPath.startsWith(PLUGIN_IGNORE_PREFIX) || managedSet.has(relPath)) {
        continue;
      }
      const slug = normalizeSlug(path.basename(entry.name, ext));
      if (!slug || SYSTEM_BOOTSTRAP_SLUG_SET.has(slug) || seenSlugs.has(slug)) {
        continue;
      }
      const ruleFileAbs = path.join(ruleDirAbs, entry.name);
      try {
        const stat = fs.statSync(ruleFileAbs);
        const content = fs.readFileSync(ruleFileAbs, "utf-8");
        if (content.trim().length === 0) {
          continue;
        }
        const meta = extractMarkdownMetadata(content, slug);
        seenSlugs.add(slug);
        candidates.push({
          slug,
          component_type: "system_prompt",
          title: meta.title,
          description: meta.description,
          content,
          content_hash: computeNormalizedContentHash(content),
          stack_tags: extractTagsFromSlug(slug, effectiveTags),
          file_updated_at: stat.mtime.toISOString(),
          metadata: { source_relative_path: relPath },
        });
      } catch {
        continue;
      }
    }
  }

  return candidates;
}

/**
 * Собирает локальные файлы Контекстного Графа (`.context/index.md`, `.context/schema.md`,
 * `.context/entities/*`) с вычислением `content_hash` и подстановкой `base_hash` для 3-Way Hash Sync.
 */
export function collectWorkspaceContextNodes(
  workspaceRoot: string,
  baseHashes?: Readonly<Record<string, string>>,
): ContextNodeSyncInputPayload[] {
  const contextRoot = path.join(path.resolve(workspaceRoot), CONTEXT_ROOT_REL_DIR);
  if (!fs.existsSync(contextRoot)) {
    return [];
  }
  const nodes: ContextNodeSyncInputPayload[] = [];

  const pushIfExists = (relPath: string, entityClass: string): void => {
    const absPath = path.join(contextRoot, relPath);
    if (!fs.existsSync(absPath)) {
      return;
    }
    try {
      const stat = fs.statSync(absPath);
      if (!stat.isFile()) {
        return;
      }
      const content = fs.readFileSync(absPath, "utf-8");
      const nodeId = path.basename(relPath, ".md");
      nodes.push({
        relative_path: relPath,
        node_id: nodeId,
        entity_class: entityClass,
        content,
        content_hash: computeNormalizedContentHash(content),
        base_hash: baseHashes?.[relPath] ?? "",
        file_updated_at: stat.mtime.toISOString(),
      });
    } catch {
      // Безопасно пропускаем недоступный файл.
    }
  };

  for (const [relPath, entityClass] of CONTEXT_ROOT_DOC_FILES) {
    pushIfExists(relPath, entityClass);
  }

  for (const [subDir, entityClass] of CONTEXT_ENTITY_SUBDIRS) {
    const dirAbs = path.join(contextRoot, subDir);
    if (!fs.existsSync(dirAbs)) {
      continue;
    }
    try {
      const files = fs
        .readdirSync(dirAbs)
        .filter((f) => f.endsWith(".md"))
        .sort((a, b) => a.localeCompare(b));
      for (const fileName of files) {
        pushIfExists(`${subDir}/${fileName}`, entityClass);
      }
    } catch {
      continue;
    }
  }

  return nodes;
}

/**
 * Применяет результаты серверной 3-Way Hash синхронизации `.context/`: атомарно записывает на диск
 * узлы со статусами `pulled_from_db` и `merged`, а также обновляет карту `base_hash` для следующей сверки.
 */
export function applyContextSyncResults(
  workspaceRoot: string,
  items: readonly ContextNodeSyncItemPayload[],
  prevBaseHashes?: Readonly<Record<string, string>>,
): { writtenPaths: string[]; nextBaseHashes: Record<string, string> } {
  const resolvedContextRoot = path.resolve(workspaceRoot, CONTEXT_ROOT_REL_DIR);
  const nextBaseHashes: Record<string, string> = { ...(prevBaseHashes ?? {}) };
  const writtenPaths: string[] = [];

  for (const item of items) {
    const cleanRel = item.relative_path.replace(/\\/g, "/").replace(/^\/+/, "");
    nextBaseHashes[cleanRel] = item.base_hash || item.content_hash;

    if (
      (item.action === "pulled_from_db" || item.action === "merged") &&
      typeof item.content === "string"
    ) {
      const targetAbs = path.resolve(resolvedContextRoot, cleanRel);
      const rootPrefix = resolvedContextRoot.endsWith(path.sep)
        ? resolvedContextRoot
        : `${resolvedContextRoot}${path.sep}`;
      if (!targetAbs.startsWith(rootPrefix)) {
        continue;
      }
      fs.mkdirSync(path.dirname(targetAbs), { recursive: true });
      const tempPath = `${targetAbs}.tmp-${process.pid}-${Date.now()}`;
      fs.writeFileSync(tempPath, item.content, "utf-8");
      fs.renameSync(tempPath, targetAbs);
      writtenPaths.push(`${CONTEXT_ROOT_REL_DIR}/${cleanRel}`);
    }
  }

  return { writtenPaths, nextBaseHashes };
}
