/**
 * Слой 1 (Host CLI Materializer): хранилище и нормализатор пер-проектного манифеста `Harness`.
 * Отвечает за структуру `ProjectHarnessManifest`, чтение/очистку `.context/harness.json`, гидратацию
 * оверрайдов из PostgreSQL (`Core API`) и вычисление результирующего набора тегов стека.
 */

import fs from "node:fs";

import {
  computeSha256Hex,
  pathExistsSync,
  readFileUtf8IfExistsSync,
  requireSafeWorkspacePath as resolveSafeWorkspacePath,
  writeFileAtomicSync,
} from "../common/index.ts";
import {
  WORKSPACE_RELATIVE_PATHS,
} from "../config/index.ts";
import {
  type AgentId,
  GLOBAL_STACK_TAG,
  isSupportedAgent,
} from "../detector/index.ts";

export const HARNESS_MANIFEST_REL_PATH =
  WORKSPACE_RELATIVE_PATHS.HARNESS_MANIFEST;
export const HARNESS_MANIFEST_SCHEMA_VERSION = "2026.2" as const;
export const HARNESS_MANIFEST_VERSION = HARNESS_MANIFEST_SCHEMA_VERSION;
export const FACTORY_HOOK_SIGNATURE = "mcp-coop" as const;

export const ALL_HOOK_EVENTS = [
  "session-start",
  "pre-tool-use",
  "post-tool-use",
  "pre-invocation",
  "stop",
] as const;

export type HookEventName = (typeof ALL_HOOK_EVENTS)[number];

export const FILE_WRITE_STRATEGIES = [
  "overwrite",
  "create_if_missing",
  "merge_json",
  "merge_toml",
  "json_deep_merge",
  "markdown_section_merge",
  "toml_section_merge",
] as const;

export type FileWriteStrategy = (typeof FILE_WRITE_STRATEGIES)[number];
export type WriteStrategy = FileWriteStrategy;

export interface RenderedFilePayload {
  relative_path: string;
  content: string;
  write_strategy: WriteStrategy;
  description?: string | undefined;
}

export interface HarnessOverrides {
  enabled_components: string[];
  disabled_components: string[];
  disabled_hooks: HookEventName[];
}

export interface ProjectHarnessManifest {
  version: string;
  agent: AgentId;
  stack: {
    detected_tags: string[];
    extra_tags: string[];
    excluded_tags: string[];
  };
  overrides: HarnessOverrides;
  managed_files: string[];
  context_base_hashes?: Record<string, string> | undefined;
  updated_at: string;
}

export interface ProfileOverridesPayload extends Record<string, unknown> {
  agent: AgentId;
  extra_tags: string[];
  excluded_tags: string[];
  enabled_components: string[];
  disabled_components: string[];
  disabled_hooks: HookEventName[];
  managed_files: string[];
  context_base_hashes: Record<string, string>;
}

export interface MaterializationReport {
  written: string[];
  skipped: string[];
  pruned: string[];
}

export interface MaterializeOptions {
  disabledHooks?: readonly string[] | undefined;
  disabledComponents?: readonly string[] | undefined;
  pruneFromManifest?: ProjectHarnessManifest | undefined;
}

/**
 * Проверяет, принадлежит ли строка каноническому перечислению событий жизненных хуков.
 */
export function isHookEventName(value: string): value is HookEventName {
  return (ALL_HOOK_EVENTS as readonly string[]).includes(value);
}

/**
 * Вычисляет SHA-256 дайджест текстового содержимого через общий криптографический примитив CLI.
 */
export function computeContentSha256(content: string): string {
  return computeSha256Hex(content);
}

/**
 * Возвращает относительный префикс директории скилла для конкретного ИИ-агента,
 * чтобы точечно фильтровать или удалять отключённые пользователем скиллы.
 */
export function resolveDisabledSkillPrefix(
  agent: AgentId,
  slug: string,
): string {
  const cleanSlug = slug.trim().toLowerCase();
  switch (agent) {
    case "antigravity":
      return `.agents/plugins/${FACTORY_HOOK_SIGNATURE}/skills/${cleanSlug}/`;
    case "claude-code":
      return `.claude/skills/${cleanSlug}/`;
    case "cursor":
      return `.cursor/skills/${cleanSlug}/`;
    case "codex":
      return `.agents/skills/${cleanSlug}/`;
    case "opencode":
      return `.opencode/skills/${cleanSlug}/`;
  }
}

/**
 * Извлекает канонический `slug` скилла из относительного пути рабочей директории
 * (например, `.claude/skills/my-skill/SKILL.md` -> `my-skill`).
 */
export function resolveSkillSlugFromRelPath(
  relativePath: string,
): string | null {
  const normalized = relativePath.replace(/\\/g, "/").replace(/^\/+/, "");
  const match = /(?:^|\/)skills\/([^/]+)(?:\/SKILL\.md|\/|$)/i.exec(normalized);
  if (!match || !match[1]) {
    return null;
  }
  const slug = match[1].trim().toLowerCase();
  return slug.length > 0 ? slug : null;
}

/**
 * Извлекает канонический `slug` правила из относительного пути рабочей директории
 * (например, `.cursor/rules/clean-arch.mdc` -> `clean-arch`).
 */
export function resolveRuleSlugFromRelPath(
  relativePath: string,
): string | null {
  const normalized = relativePath.replace(/\\/g, "/").replace(/^\/+/, "");
  const match = /(?:^|\/)rules\/([^/]+)\.(?:md|mdc)$/i.exec(normalized);
  if (!match || !match[1]) {
    return null;
  }
  const slug = match[1].trim().toLowerCase();
  return slug.length > 0 ? slug : null;
}

function normalizeUniqueStrings(
  values: unknown,
  lowerCase: boolean = true,
): string[] {
  if (!Array.isArray(values)) {
    return [];
  }
  const unique = new Set<string>();
  for (const item of values) {
    if (typeof item !== "string") {
      continue;
    }
    const cleaned = lowerCase ? item.trim().toLowerCase() : item.trim();
    if (cleaned.length > 0) {
      unique.add(cleaned);
    }
  }
  return Array.from(unique).sort((a, b) => a.localeCompare(b));
}

function normalizeHookEvents(values: unknown): HookEventName[] {
  const normalized = normalizeUniqueStrings(values, true);
  return ALL_HOOK_EVENTS.filter((event) => normalized.includes(event));
}

function normalizeStringRecord(value: unknown): Record<string, string> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {};
  }
  const result: Record<string, string> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    if (typeof v === "string" && k.trim().length > 0 && v.trim().length > 0) {
      result[k.trim()] = v.trim();
    }
  }
  return result;
}

/**
 * Создаёт дефолтный манифест `.context/harness.json` для нового или впервые синхронизируемого проекта.
 */
export function createDefaultManifest(
  agent: AgentId,
  detectedTags: readonly string[] = [],
): ProjectHarnessManifest {
  const normalizedDetected = normalizeUniqueStrings(
    [GLOBAL_STACK_TAG, ...detectedTags],
    true,
  );
  return {
    version: HARNESS_MANIFEST_SCHEMA_VERSION,
    agent,
    stack: {
      detected_tags: normalizedDetected,
      extra_tags: [],
      excluded_tags: [],
    },
    overrides: {
      enabled_components: [],
      disabled_components: [],
      disabled_hooks: [],
    },
    managed_files: [],
    context_base_hashes: {},
    updated_at: new Date().toISOString(),
  };
}

export const createDefaultHarnessManifest = createDefaultManifest;

/**
 * Загружает пер-проектный манифест `.context/harness.json`, объединяя сохранённые пользовательские
 * оверрайды (`extra_tags`, `excluded_tags`, `enabled_components`, `disabled_components`, `disabled_hooks`)
 * с актуальными автообнаруженными тегами стека `detectedTags`.
 */
export function loadProjectHarnessConfig(
  workspaceRoot: string,
  fallbackAgent: AgentId,
  detectedTags: readonly string[] = [],
): ProjectHarnessManifest {
  const baseManifest = createDefaultManifest(fallbackAgent, detectedTags);

  let manifestPath: string;
  try {
    manifestPath = resolveSafeWorkspacePath(
      workspaceRoot,
      HARNESS_MANIFEST_REL_PATH,
    );
  } catch {
    return baseManifest;
  }

  const rawText = readFileUtf8IfExistsSync(manifestPath);
  if (rawText === null) {
    return baseManifest;
  }

  try {
    const parsed = JSON.parse(rawText) as Record<string, unknown>;
    const rawAgentVal = parsed["agent"];
    const rawAgent =
      typeof rawAgentVal === "string" ? rawAgentVal.trim().toLowerCase() : "";
    const resolvedAgent: AgentId = isSupportedAgent(rawAgent)
      ? rawAgent
      : fallbackAgent;

    const rawStack = parsed["stack"];
    const stackObj =
      rawStack && typeof rawStack === "object" && !Array.isArray(rawStack)
        ? (rawStack as Record<string, unknown>)
        : {};
    const rawOverrides = parsed["overrides"];
    const overridesObj =
      rawOverrides &&
      typeof rawOverrides === "object" &&
      !Array.isArray(rawOverrides)
        ? (rawOverrides as Record<string, unknown>)
        : {};

    const excludedTags = normalizeUniqueStrings(
      stackObj["excluded_tags"],
      true,
    ).filter((tag) => tag !== GLOBAL_STACK_TAG);
    const extraTags = normalizeUniqueStrings(
      stackObj["extra_tags"],
      true,
    ).filter((tag) => !excludedTags.includes(tag));

    const rawVersion = parsed["version"];
    const rawUpdatedAt = parsed["updated_at"];

    return {
      version:
        typeof rawVersion === "string" && rawVersion.trim().length > 0
          ? rawVersion.trim()
          : HARNESS_MANIFEST_SCHEMA_VERSION,
      agent: resolvedAgent,
      stack: {
        detected_tags: baseManifest.stack.detected_tags,
        extra_tags: extraTags,
        excluded_tags: excludedTags,
      },
      overrides: {
        enabled_components: normalizeUniqueStrings(
          overridesObj["enabled_components"],
          true,
        ),
        disabled_components: normalizeUniqueStrings(
          overridesObj["disabled_components"],
          true,
        ),
        disabled_hooks: normalizeHookEvents(overridesObj["disabled_hooks"]),
      },
      managed_files: normalizeUniqueStrings(parsed["managed_files"], false),
      context_base_hashes: normalizeStringRecord(parsed["context_base_hashes"]),
      updated_at:
        typeof rawUpdatedAt === "string"
          ? rawUpdatedAt
          : baseManifest.updated_at,
    };
  } catch {
    return baseManifest;
  }
}

/**
 * Очищает устаревший локальный файл `.context/harness.json`, если он остался на диске,
 * так как единственным источником истины (Single Source of Truth) выступает таблица `projects` в PostgreSQL.
 * При передаче `options.persistLocalFile = true` атомарно сохраняет манифест на диск.
 */
export function saveProjectHarnessConfig(
  workspaceRoot: string,
  manifest: ProjectHarnessManifest,
  options?: { persistLocalFile?: boolean | undefined },
): void {
  try {
    const manifestPath = resolveSafeWorkspacePath(
      workspaceRoot,
      HARNESS_MANIFEST_REL_PATH,
    );
    if (options?.persistLocalFile) {
      writeFileAtomicSync(
        manifestPath,
        `${JSON.stringify(manifest, null, 2)}\n`,
      );
      return;
    }
    if (pathExistsSync(manifestPath)) {
      fs.rmSync(manifestPath, { force: true });
    }
  } catch {
    // Безопасно пропускаем ошибки удаления отсутствующего файла.
  }
}

/**
 * Гидрирует конфигурацию проекта `ProjectHarnessManifest` данными, полученными из PostgreSQL
 * через аналитическое ядро Docker (`GET /api/harness/components`).
 */
export function hydrateManifestFromCore(
  baseManifest: ProjectHarnessManifest,
  coreData: {
    stack_tags?: readonly string[] | undefined;
    agent_type?: string | undefined;
    profile_overrides?: Record<string, unknown> | undefined;
  } | null,
): ProjectHarnessManifest {
  if (!coreData) {
    return baseManifest;
  }

  const ov = coreData.profile_overrides ?? {};
  const ovAgent = ov["agent"];
  const rawAgent =
    (typeof coreData.agent_type === "string" &&
      coreData.agent_type.trim().toLowerCase()) ||
    (typeof ovAgent === "string" && ovAgent.trim().toLowerCase()) ||
    "";
  const resolvedAgent: AgentId = isSupportedAgent(rawAgent)
    ? rawAgent
    : baseManifest.agent;

  const coreTags =
    Array.isArray(coreData.stack_tags) && coreData.stack_tags.length > 0
      ? normalizeUniqueStrings(coreData.stack_tags, true)
      : baseManifest.stack.detected_tags;

  const excludedTags = normalizeUniqueStrings(ov["excluded_tags"], true).filter(
    (tag) => tag !== GLOBAL_STACK_TAG,
  );
  const extraTags = normalizeUniqueStrings(ov["extra_tags"], true).filter(
    (tag) => !excludedTags.includes(tag),
  );

  const ovManagedFiles = ov["managed_files"];

  return {
    version: HARNESS_MANIFEST_SCHEMA_VERSION,
    agent: resolvedAgent,
    stack: {
      detected_tags: coreTags.includes(GLOBAL_STACK_TAG)
        ? coreTags
        : [GLOBAL_STACK_TAG, ...coreTags],
      extra_tags: extraTags,
      excluded_tags: excludedTags,
    },
    overrides: {
      enabled_components: normalizeUniqueStrings(
        ov["enabled_components"],
        true,
      ),
      disabled_components: normalizeUniqueStrings(
        ov["disabled_components"],
        true,
      ),
      disabled_hooks: normalizeHookEvents(ov["disabled_hooks"]),
    },
    managed_files:
      Array.isArray(ovManagedFiles) && ovManagedFiles.length > 0
        ? normalizeUniqueStrings(ovManagedFiles, false)
        : baseManifest.managed_files,
    context_base_hashes: {
      ...(baseManifest.context_base_hashes ?? {}),
      ...normalizeStringRecord(ov["context_base_hashes"]),
    },
    updated_at: new Date().toISOString(),
  };
}

/**
 * Вычисляет результирующий набор тегов стека проекта по формуле:
 * `(global ∪ detected_tags ∪ extra_tags) \ (excluded_tags \ {global})`.
 */
export function resolveEffectiveStackTags(
  manifest: ProjectHarnessManifest,
): string[] {
  const excludedSet = new Set(
    manifest.stack.excluded_tags
      .map((tag) => tag.trim().toLowerCase())
      .filter((tag) => tag !== GLOBAL_STACK_TAG),
  );
  const combined = new Set<string>([GLOBAL_STACK_TAG]);
  for (const tag of [
    ...manifest.stack.detected_tags,
    ...manifest.stack.extra_tags,
  ]) {
    const cleaned = tag.trim().toLowerCase();
    if (cleaned.length > 0 && !excludedSet.has(cleaned)) {
      combined.add(cleaned);
    }
  }
  combined.delete(GLOBAL_STACK_TAG);
  return [
    GLOBAL_STACK_TAG,
    ...Array.from(combined).sort((a, b) => a.localeCompare(b)),
  ];
}

/**
 * Формирует словарь `profile_overrides` для сохранения в таблице `projects` PostgreSQL (`POST /api/projects/register`).
 */
export function buildProfileOverridesPayload(
  manifest: ProjectHarnessManifest,
): ProfileOverridesPayload {
  return {
    agent: manifest.agent,
    extra_tags: manifest.stack.extra_tags,
    excluded_tags: manifest.stack.excluded_tags,
    enabled_components: manifest.overrides.enabled_components,
    disabled_components: manifest.overrides.disabled_components,
    disabled_hooks: manifest.overrides.disabled_hooks,
    managed_files: manifest.managed_files,
    context_base_hashes: manifest.context_base_hashes ?? {},
  };
}
