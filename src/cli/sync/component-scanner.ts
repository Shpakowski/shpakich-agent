/**
 * Слой 1 (Host CLI Sync — Component Scanner): сканер пользовательских скиллов и правил рабочей директории.
 * Обнаруживает локальные компоненты в `.agents/`, `.claude/` и `.cursor/` с защитой от самоинжеста (`Anti-Self-Ingest`),
 * опираясь на общий парсер YAML frontmatter и каноническое SHA-256 хеширование из `src/cli/common/`.
 */

import fs from "node:fs";
import path from "node:path";

import {
  computeNormalizedContentHash,
  extractFrontmatterList,
  extractFrontmatterScalar,
  extractTagsFromSlug as extractTagsFromSlugCommon,
  parseMarkdownFrontmatter,
} from "../common/index.ts";
import { SLUG_TAG_STOP_WORDS } from "../config/index.ts";
import {
  type ComponentIngestItemPayload,
  type ComponentIngestReportPayload,
  type ConsolidationClusterPayload,
  type ConsolidationReportPayload,
  type WorkspaceComponentCandidatePayload,
} from "../core-api/index.ts";
import {
  FACTORY_HOOK_SIGNATURE,
  type ProjectHarnessManifest,
} from "../materializer/index.ts";

export {
  computeNormalizedContentHash,
  extractFrontmatterScalar,
  type ComponentIngestItemPayload,
  type ComponentIngestReportPayload,
  type ConsolidationClusterPayload,
  type ConsolidationReportPayload,
  type WorkspaceComponentCandidatePayload,
};

export const SYSTEM_BOOTSTRAP_SLUGS = [
  "coop-orchestrator",
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

export const CUSTOM_SKILL_PARENT_DIRS = [
  ".agents/skills",
  ".claude/skills",
  ".cursor/skills",
] as const;

export const CUSTOM_RULE_DIRS = [
  ".agents/rules",
  ".claude/rules",
  ".cursor/rules",
] as const;

const SKILL_FILE_NAME = "SKILL.md" as const;
const RULE_EXTENSIONS: ReadonlySet<string> = new Set([".md", ".mdc"]);
const PLUGIN_IGNORE_PREFIX = `.agents/plugins/${FACTORY_HOOK_SIGNATURE}/` as const;
const FIRST_PARAGRAPH_MAX_CHARS = 200 as const;
const SLUG_STOP_WORDS_SET: ReadonlySet<string> = new Set(SLUG_TAG_STOP_WORDS);

function normalizeSlug(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Отделяет сырой блок YAML frontmatter от основного Markdown-тела документа,
 * делегируя разбор каноническому парсеру `parseMarkdownFrontmatter` из `src/cli/common/`.
 */
export function extractFrontmatterBlock(rawContent: string): {
  frontmatter: string;
  body: string;
} {
  const parsed = parseMarkdownFrontmatter(rawContent);
  return {
    frontmatter: parsed.frontmatterRaw,
    body: parsed.body,
  };
}

/**
 * Извлекает список тегов технологического стека (`stack_tags` или `tags`) из блока YAML frontmatter.
 */
export function extractFrontmatterTags(frontmatterBlock: string): string[] {
  const stackTags = extractFrontmatterList(frontmatterBlock, "stack_tags");
  if (stackTags.length > 0) {
    return stackTags;
  }
  return extractFrontmatterList(frontmatterBlock, "tags");
}

/**
 * Выводит детерминированный набор семантических тегов из идентификатора компонента (`slug`),
 * возвращая `fallbackTags`, если после фильтрации стоп-слов и коротких токенов список пуст.
 */
export function inferStackTagsFromSlug(
  slug: string,
  fallbackTags: readonly string[] = [],
): string[] {
  const normalized = normalizeSlug(slug);
  const tokens = extractTagsFromSlugCommon(normalized, SLUG_STOP_WORDS_SET);
  return tokens.length > 0 ? tokens : [...fallbackTags];
}

/**
 * Совместимый алиас `inferStackTagsFromSlug` для сохранения контракта `workspace_sync.ts`.
 */
export function extractTagsFromSlug(
  slug: string,
  fallbackTags: readonly string[] = [],
): string[] {
  return inferStackTagsFromSlug(slug, fallbackTags);
}

function extractMarkdownMetadata(
  rawContent: string,
  fallbackSlug: string,
): { title: string; description: string } {
  const parsed = parseMarkdownFrontmatter(rawContent);
  const fmTitle = parsed.hasFrontmatter
    ? (extractFrontmatterScalar(parsed.frontmatterRaw, "title") ??
      extractFrontmatterScalar(parsed.frontmatterRaw, "name") ??
      "")
    : "";
  const fmDescription = parsed.hasFrontmatter
    ? (extractFrontmatterScalar(parsed.frontmatterRaw, "description") ?? "")
    : "";

  let headingTitle = "";
  let firstParagraph = "";
  for (const rawLine of parsed.body.split("\n")) {
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
      firstParagraph = line.slice(0, FIRST_PARAGRAPH_MAX_CHARS);
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
    manifest.managed_files.map((rel) =>
      rel.replace(/\\/g, "/").replace(/^\/+/, ""),
    ),
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
          stack_tags: inferStackTagsFromSlug(slug, effectiveTags),
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
          stack_tags: inferStackTagsFromSlug(slug, effectiveTags),
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
