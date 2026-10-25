/**
 * Слой 1 (Host CLI Injector): материализатор файлов адаптеров ИИ-агентов и пер-проектного манифеста.
 * Обеспечивает атомарную запись скомпилированных артефактов `Harness²` (`overwrite`, `create_if_missing`,
 * `merge_json`, `merge_toml`), идемпотентное слияние JSON/TOML-конфигураций без затирания пользовательских
 * настроек, очистку устаревших управляемых файлов при смене стека и хранение `.context/harness.json`.
 */

import fs from "node:fs";
import path from "node:path";

import {
  type AgentId,
  CONTEXT_GRAPH_INDEX_REL_PATH,
  GLOBAL_STACK_TAG,
  isSupportedAgent,
} from "./detector.ts";

export const HARNESS_MANIFEST_REL_PATH = ".context/harness.json" as const;
export const FACTORY_HOOK_SIGNATURE = "mcp-coop-agentic-factory" as const;
export const HARNESS_MANIFEST_VERSION = "2026.2" as const;

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
] as const;

export type FileWriteStrategy = (typeof FILE_WRITE_STRATEGIES)[number];

const CONTEXT_ENTITIES_DIRS = [
  ".context/entities/modules",
  ".context/entities/contracts",
  ".context/entities/decisions",
] as const;

const DEFAULT_OFFLINE_MCP_URL = "http://127.0.0.1:8787/mcp" as const;
const DEFAULT_MCP_SERVER_KEY = "mcp-coop-factory" as const;
const CODEX_MCP_SERVER_KEY = "mcp_coop_factory" as const;
const DEFAULT_HOOK_TIMEOUT_SECONDS = 15 as const;

export interface RenderedFilePayload {
  relative_path: string;
  content: string;
  write_strategy: FileWriteStrategy;
  description?: string;
}

export interface ProjectHarnessManifest {
  version: string;
  agent: AgentId;
  stack: {
    detected_tags: string[];
    extra_tags: string[];
    excluded_tags: string[];
  };
  overrides: {
    enabled_components: string[];
    disabled_components: string[];
    disabled_hooks: HookEventName[];
  };
  managed_files: string[];
  context_base_hashes?: Record<string, string>;
  updated_at: string;
}

export interface MaterializationReport {
  written: string[];
  skipped: string[];
  pruned: string[];
}

export interface MaterializeOptions {
  disabledHooks?: readonly string[];
  pruneFromManifest?: ProjectHarnessManifest;
}

/**
 * Проверяет, принадлежит ли строка каноническому перечислению событий жизненных хуков.
 */
export function isHookEventName(value: string): value is HookEventName {
  return (ALL_HOOK_EVENTS as readonly string[]).includes(value);
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

function resolveSafeWorkspacePath(
  workspaceRoot: string,
  relativePath: string,
): string {
  const resolvedRoot = path.resolve(workspaceRoot);
  const normalizedRel = relativePath.replace(/\\/g, "/").replace(/^\/+/, "");
  const resolvedTarget = path.resolve(resolvedRoot, normalizedRel);
  const rootPrefix = resolvedRoot.endsWith(path.sep)
    ? resolvedRoot
    : `${resolvedRoot}${path.sep}`;
  if (resolvedTarget !== resolvedRoot && !resolvedTarget.startsWith(rootPrefix)) {
    throw new Error(
      `Refusing to access path outside workspace root: "${relativePath}"`,
    );
  }
  return resolvedTarget;
}

function atomicWriteTextFile(targetPath: string, content: string): void {
  const parentDir = path.dirname(targetPath);
  fs.mkdirSync(parentDir, { recursive: true });
  const tempPath = `${targetPath}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(tempPath, content, "utf-8");
  fs.renameSync(tempPath, targetPath);
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
export function createDefaultHarnessManifest(
  agent: AgentId,
  detectedTags: readonly string[],
): ProjectHarnessManifest {
  const normalizedDetected = normalizeUniqueStrings(
    [GLOBAL_STACK_TAG, ...detectedTags],
    true,
  );
  return {
    version: HARNESS_MANIFEST_VERSION,
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

/**
 * Загружает пер-проектный манифест `.context/harness.json`, объединяя сохранённые пользовательские
 * оверрайды (`extra_tags`, `excluded_tags`, `enabled_components`, `disabled_components`, `disabled_hooks`)
 * с актуальными автообнаруженными тегами стека `detectedTags`.
 */
export function loadProjectHarnessConfig(
  workspaceRoot: string,
  fallbackAgent: AgentId,
  detectedTags: readonly string[],
): ProjectHarnessManifest {
  const manifestPath = resolveSafeWorkspacePath(
    workspaceRoot,
    HARNESS_MANIFEST_REL_PATH,
  );
  const baseManifest = createDefaultHarnessManifest(
    fallbackAgent,
    detectedTags,
  );
  if (!fs.existsSync(manifestPath)) {
    return baseManifest;
  }

  try {
    const rawText = fs.readFileSync(manifestPath, "utf-8");
    const parsed = JSON.parse(rawText) as Record<string, unknown>;
    const rawAgent =
      typeof parsed.agent === "string" ? parsed.agent.trim().toLowerCase() : "";
    const resolvedAgent: AgentId = isSupportedAgent(rawAgent)
      ? rawAgent
      : fallbackAgent;

    const stackObj =
      parsed.stack && typeof parsed.stack === "object"
        ? (parsed.stack as Record<string, unknown>)
        : {};
    const overridesObj =
      parsed.overrides && typeof parsed.overrides === "object"
        ? (parsed.overrides as Record<string, unknown>)
        : {};

    const excludedTags = normalizeUniqueStrings(
      stackObj.excluded_tags,
      true,
    ).filter((tag) => tag !== GLOBAL_STACK_TAG);
    const extraTags = normalizeUniqueStrings(stackObj.extra_tags, true).filter(
      (tag) => !excludedTags.includes(tag),
    );

    return {
      version:
        typeof parsed.version === "string" && parsed.version.trim().length > 0
          ? parsed.version.trim()
          : HARNESS_MANIFEST_VERSION,
      agent: resolvedAgent,
      stack: {
        detected_tags: baseManifest.stack.detected_tags,
        extra_tags: extraTags,
        excluded_tags: excludedTags,
      },
      overrides: {
        enabled_components: normalizeUniqueStrings(
          overridesObj.enabled_components,
          true,
        ),
        disabled_components: normalizeUniqueStrings(
          overridesObj.disabled_components,
          true,
        ),
        disabled_hooks: normalizeHookEvents(overridesObj.disabled_hooks),
      },
      managed_files: normalizeUniqueStrings(parsed.managed_files, false),
      context_base_hashes: normalizeStringRecord(parsed.context_base_hashes),
      updated_at:
        typeof parsed.updated_at === "string"
          ? parsed.updated_at
          : baseManifest.updated_at,
    };
  } catch {
    return baseManifest;
  }
}

/**
 * Очищает устаревший локальный файл `.context/harness.json`, если он остался на диске,
 * так как единственным источником истины (Single Source of Truth) выступает таблица `projects` в PostgreSQL.
 */
export function saveProjectHarnessConfig(
  workspaceRoot: string,
  _manifest: ProjectHarnessManifest,
): void {
  try {
    const manifestPath = resolveSafeWorkspacePath(
      workspaceRoot,
      HARNESS_MANIFEST_REL_PATH,
    );
    if (fs.existsSync(manifestPath)) {
      fs.rmSync(manifestPath, { force: true });
    }
  } catch {
    // Безопасно пропускаем, если файл уже отсутствует.
  }
}

/**
 * Гидрирует конфигурацию проекта `ProjectHarnessManifest` данными, полученными из PostgreSQL
 * через аналитическое ядро Docker (`GET /api/harness/components`).
 */
export function hydrateManifestFromCore(
  baseManifest: ProjectHarnessManifest,
  coreData: {
    stack_tags?: readonly string[];
    agent_type?: string;
    profile_overrides?: Record<string, unknown>;
  } | null,
): ProjectHarnessManifest {
  if (!coreData) {
    return baseManifest;
  }

  const ov = coreData.profile_overrides ?? {};
  const rawAgent =
    (typeof coreData.agent_type === "string" && coreData.agent_type.trim().toLowerCase()) ||
    (typeof ov.agent === "string" && ov.agent.trim().toLowerCase()) ||
    "";
  const resolvedAgent: AgentId = isSupportedAgent(rawAgent)
    ? rawAgent
    : baseManifest.agent;

  const coreTags =
    Array.isArray(coreData.stack_tags) && coreData.stack_tags.length > 0
      ? normalizeUniqueStrings(coreData.stack_tags, true)
      : baseManifest.stack.detected_tags;

  const excludedTags = normalizeUniqueStrings(ov.excluded_tags, true).filter(
    (tag) => tag !== GLOBAL_STACK_TAG,
  );
  const extraTags = normalizeUniqueStrings(ov.extra_tags, true).filter(
    (tag) => !excludedTags.includes(tag),
  );

  return {
    version: HARNESS_MANIFEST_VERSION,
    agent: resolvedAgent,
    stack: {
      detected_tags: coreTags.includes(GLOBAL_STACK_TAG)
        ? coreTags
        : [GLOBAL_STACK_TAG, ...coreTags],
      extra_tags: extraTags,
      excluded_tags: excludedTags,
    },
    overrides: {
      enabled_components: normalizeUniqueStrings(ov.enabled_components, true),
      disabled_components: normalizeUniqueStrings(ov.disabled_components, true),
      disabled_hooks: normalizeHookEvents(ov.disabled_hooks),
    },
    managed_files:
      Array.isArray(ov.managed_files) && ov.managed_files.length > 0
        ? normalizeUniqueStrings(ov.managed_files, false)
        : baseManifest.managed_files,
    context_base_hashes: {
      ...(baseManifest.context_base_hashes ?? {}),
      ...normalizeStringRecord(ov.context_base_hashes),
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
): Record<string, unknown> {
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
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isDisabledFactoryCommand(
  command: string,
  disabledSet: ReadonlySet<string>,
): boolean {
  for (const eventName of disabledSet) {
    if (
      command.includes(`hook ${eventName} `) ||
      command.endsWith(`hook ${eventName}`)
    ) {
      return true;
    }
  }
  return false;
}

function filterHookEntriesList(
  entries: readonly unknown[],
  stripAllFactory: boolean,
  disabledSet: ReadonlySet<string>,
): unknown[] {
  const kept: unknown[] = [];
  for (const entry of entries) {
    if (!isPlainObject(entry)) {
      kept.push(entry);
      continue;
    }
    if (Array.isArray(entry.hooks)) {
      const filteredInner = filterHookEntriesList(
        entry.hooks,
        stripAllFactory,
        disabledSet,
      );
      if (filteredInner.length > 0) {
        kept.push({ ...entry, hooks: filteredInner });
      }
      continue;
    }
    const cmd = typeof entry.command === "string" ? entry.command : "";
    if (cmd.includes(FACTORY_HOOK_SIGNATURE)) {
      if (stripAllFactory || isDisabledFactoryCommand(cmd, disabledSet)) {
        continue;
      }
    }
    kept.push(entry);
  }
  return kept;
}

function mergeHookEventMap(
  existingMap: Record<string, unknown>,
  incomingMap: Record<string, unknown>,
  disabledSet: ReadonlySet<string>,
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  const allKeys = new Set([
    ...Object.keys(existingMap),
    ...Object.keys(incomingMap),
  ]);

  for (const key of allKeys) {
    const existingVal = existingMap[key];
    const incomingVal = incomingMap[key];
    if (!Array.isArray(existingVal) && !Array.isArray(incomingVal)) {
      result[key] = incomingVal !== undefined ? incomingVal : existingVal;
      continue;
    }
    const preservedExisting = Array.isArray(existingVal)
      ? filterHookEntriesList(existingVal, true, disabledSet)
      : [];
    const allowedIncoming = Array.isArray(incomingVal)
      ? filterHookEntriesList(incomingVal, false, disabledSet)
      : [];
    const mergedEntries = [...preservedExisting, ...allowedIncoming];
    if (mergedEntries.length > 0) {
      result[key] = mergedEntries;
    }
  }
  return result;
}

/**
 * Идемпотентно объединяет существующий и новый JSON-конфиги (`mcp.json`, `settings.json`, `hooks.json`,
 * `opencode.json`). Сохраняет пользовательские MCP-серверы и кастомные хуки, заменяя только записи
 * с сигнатурой `mcp-coop-agentic-factory` и исключая события из `disabledHooks`.
 */
export function mergeJsonContent(
  existingRaw: string,
  incomingRaw: string,
  disabledHooks: readonly string[] = [],
): string {
  let incomingObj: Record<string, unknown>;
  try {
    const parsedIncoming = JSON.parse(incomingRaw) as unknown;
    if (!isPlainObject(parsedIncoming)) {
      return incomingRaw;
    }
    incomingObj = parsedIncoming;
  } catch {
    return incomingRaw;
  }

  let existingObj: Record<string, unknown> = {};
  if (existingRaw.trim().length > 0) {
    try {
      const parsedExisting = JSON.parse(existingRaw) as unknown;
      if (isPlainObject(parsedExisting)) {
        existingObj = parsedExisting;
      }
    } catch {
      existingObj = {};
    }
  }

  const merged: Record<string, unknown> = { ...existingObj };
  const disabledSet = new Set(
    disabledHooks.map((item) => item.trim().toLowerCase()),
  );

  for (const [key, incValue] of Object.entries(incomingObj)) {
    const exValue = existingObj[key];

    if (
      (key === "mcpServers" || key === "mcp") &&
      isPlainObject(incValue)
    ) {
      merged[key] = isPlainObject(exValue)
        ? { ...exValue, ...incValue }
        : { ...incValue };
    } else if (
      key === "instructions" &&
      Array.isArray(incValue)
    ) {
      const exList = Array.isArray(exValue) ? exValue : [];
      const combined = new Set<string>();
      for (const item of [...exList, ...incValue]) {
        if (typeof item === "string" && item.trim().length > 0) {
          combined.add(item);
        }
      }
      merged[key] = Array.from(combined);
    } else if (
      (key === "hooks" || key === FACTORY_HOOK_SIGNATURE) &&
      isPlainObject(incValue)
    ) {
      merged[key] = mergeHookEventMap(
        isPlainObject(exValue) ? exValue : {},
        incValue,
        disabledSet,
      );
    } else {
      merged[key] = incValue;
    }
  }

  return `${JSON.stringify(merged, null, 2)}\n`;
}
interface TomlSection {
  header: string;
  lines: string[];
}

function parseTomlSections(raw: string): TomlSection[] {
  const sections: TomlSection[] = [{ header: "", lines: [] }];
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  for (const line of lines) {
    const match = /^\s*\[([^\]]+)\]\s*$/.exec(line);
    if (match && match[1]) {
      sections.push({ header: match[1].trim(), lines: [line] });
    } else {
      const current = sections[sections.length - 1];
      if (current) {
        current.lines.push(line);
      }
    }
  }
  return sections;
}

/**
 * Секционный слиятель для `.codex/config.toml`. Гарантирует установку `codex_hooks = true`
 * в секции `[features]` и обновляет блок `[mcp_servers.mcp_coop_factory]`, сохраняя все
 * остальные пользовательские секции и комментарии.
 */
export function mergeTomlContent(
  existingRaw: string,
  incomingRaw: string,
): string {
  if (existingRaw.trim().length === 0) {
    return `${incomingRaw.trim()}\n`;
  }

  const existingSections = parseTomlSections(existingRaw);
  const incomingSections = parseTomlSections(incomingRaw);
  const incomingMcpSection = incomingSections.find(
    (sec) => sec.header === `mcp_servers.${CODEX_MCP_SERVER_KEY}`,
  );

  let hasFeatures = false;
  for (const sec of existingSections) {
    if (sec.header === "features") {
      hasFeatures = true;
      let foundKey = false;
      sec.lines = sec.lines.map((line) => {
        if (/^\s*codex_hooks\s*=/.test(line)) {
          foundKey = true;
          return "codex_hooks = true";
        }
        return line;
      });
      if (!foundKey) {
        sec.lines.push("codex_hooks = true");
      }
    }
  }

  if (!hasFeatures) {
    existingSections.push({
      header: "features",
      lines: ["[features]", "codex_hooks = true"],
    });
  }

  const targetMcpHeader = `mcp_servers.${CODEX_MCP_SERVER_KEY}`;
  const replacementMcpLines = incomingMcpSection
    ? incomingMcpSection.lines.filter((line, idx, arr) =>
        idx < arr.length - 1 ? true : line.trim().length > 0,
      )
    : [
        `[${targetMcpHeader}]`,
        "enabled = true",
        `url = "${DEFAULT_OFFLINE_MCP_URL}"`,
      ];

  const existingMcpIdx = existingSections.findIndex(
    (sec) => sec.header === targetMcpHeader,
  );
  if (existingMcpIdx >= 0) {
    existingSections[existingMcpIdx] = {
      header: targetMcpHeader,
      lines: replacementMcpLines,
    };
  } else {
    existingSections.push({
      header: targetMcpHeader,
      lines: replacementMcpLines,
    });
  }

  const renderedBlocks = existingSections
    .map((sec) => sec.lines.join("\n").trim())
    .filter((block) => block.length > 0);

  return `${renderedBlocks.join("\n\n")}\n`;
}

function pruneEmptyParentDirs(workspaceRoot: string, filePath: string): void {
  const resolvedRoot = path.resolve(workspaceRoot);
  let currentDir = path.dirname(filePath);
  for (let depth = 0; depth < 3; depth += 1) {
    if (
      currentDir === resolvedRoot ||
      !currentDir.startsWith(`${resolvedRoot}${path.sep}`)
    ) {
      break;
    }
    try {
      const entries = fs.readdirSync(currentDir);
      if (entries.length > 0) {
        break;
      }
      fs.rmdirSync(currentDir);
      currentDir = path.dirname(currentDir);
    } catch {
      break;
    }
  }
}
/**
 * Применяет список скомпилированных файлов `files` к рабочей директории проекта согласно их
 * `write_strategy`, удаляет устаревшие управляемые файлы при наличии `pruneFromManifest`
 * и возвращает отчёт о записанных, пропущенных и удалённых путях.
 */
export function materializeFiles(
  workspaceRoot: string,
  files: readonly RenderedFilePayload[],
  options?: MaterializeOptions,
): MaterializationReport {
  const written: string[] = [];
  const skipped: string[] = [];
  const pruned: string[] = [];
  const disabledHooks = options?.disabledHooks ?? [];
  const incomingManagedSet = new Set<string>();

  for (const file of files) {
    const normalizedRel = file.relative_path
      .replace(/\\/g, "/")
      .replace(/^\/+/, "");
    const targetPath = resolveSafeWorkspacePath(workspaceRoot, normalizedRel);
    const exists = fs.existsSync(targetPath);

    if (file.write_strategy === "overwrite") {
      incomingManagedSet.add(normalizedRel);
    }

    if (file.write_strategy === "create_if_missing" && exists) {
      skipped.push(normalizedRel);
      continue;
    }

    const existingContent = exists ? fs.readFileSync(targetPath, "utf-8") : "";
    let nextContent = file.content;

    if (file.write_strategy === "merge_json") {
      nextContent = mergeJsonContent(
        existingContent,
        file.content,
        disabledHooks,
      );
    } else if (file.write_strategy === "merge_toml") {
      nextContent = mergeTomlContent(existingContent, file.content);
    } else if (
      file.write_strategy === "overwrite" &&
      normalizedRel.endsWith("hooks.json") &&
      disabledHooks.length > 0
    ) {
      nextContent = mergeJsonContent("", file.content, disabledHooks);
    }

    if (exists && existingContent === nextContent) {
      skipped.push(normalizedRel);
      continue;
    }

    atomicWriteTextFile(targetPath, nextContent);
    written.push(normalizedRel);
  }

  const manifest = options?.pruneFromManifest;
  if (manifest) {
    for (const oldRelPath of manifest.managed_files) {
      const normalizedOld = oldRelPath.replace(/\\/g, "/").replace(/^\/+/, "");
      if (incomingManagedSet.has(normalizedOld)) {
        continue;
      }
      try {
        const oldTargetPath = resolveSafeWorkspacePath(
          workspaceRoot,
          normalizedOld,
        );
        if (fs.existsSync(oldTargetPath)) {
          fs.rmSync(oldTargetPath, { force: true });
          pruneEmptyParentDirs(workspaceRoot, oldTargetPath);
          pruned.push(normalizedOld);
        }
      } catch {
        continue;
      }
    }
    manifest.managed_files = Array.from(incomingManagedSet).sort((a, b) =>
      a.localeCompare(b),
    );
  }

  return { written, skipped, pruned };
}

/**
 * Создаёт каркас Контекстного Графа (`.context/index.md` и директории `.context/entities/*`),
 * если он ещё не инициализирован в рабочей директории проекта.
 */
export function ensureContextGraphScaffold(
  workspaceRoot: string,
  projectName: string,
  stackTags: readonly string[],
): boolean {
  for (const relDir of CONTEXT_ENTITIES_DIRS) {
    const fullDir = resolveSafeWorkspacePath(workspaceRoot, relDir);
    fs.mkdirSync(fullDir, { recursive: true });
  }

  const indexPath = resolveSafeWorkspacePath(
    workspaceRoot,
    CONTEXT_GRAPH_INDEX_REL_PATH,
  );
  if (fs.existsSync(indexPath)) {
    return false;
  }

  const today = new Date().toISOString().slice(0, 10);
  const stackLine = stackTags.length > 0 ? stackTags.join(", ") : GLOBAL_STACK_TAG;
  const content = [
    "---",
    "type: GraphIndex",
    `version: "${HARNESS_MANIFEST_VERSION}"`,
    `project: ${projectName}`,
    `updated_at: "${today}"`,
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

  atomicWriteTextFile(indexPath, content);
  return true;
}
function buildHookCommand(event: HookEventName, agent: AgentId): string {
  return `"$HOME/.mcp-coop-agentic-factory/bin/run-hook.sh" hook ${event} --agent ${agent}`;
}

function buildClaudeCodexHooksPayload(agent: AgentId): string {
  const eventPairs: ReadonlyArray<readonly [HookEventName, string]> = [
    ["session-start", "SessionStart"],
    ["pre-tool-use", "PreToolUse"],
    ["post-tool-use", "PostToolUse"],
    ["pre-invocation", "UserPromptSubmit"],
    ["stop", "Stop"],
  ];
  const hooks: Record<string, unknown> = {};
  for (const [canonical, vendor] of eventPairs) {
    hooks[vendor] = [
      {
        matcher: "*",
        hooks: [
          {
            type: "command",
            command: buildHookCommand(canonical, agent),
            timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
          },
        ],
      },
    ];
  }
  return `${JSON.stringify({ hooks }, null, 2)}\n`;
}

/**
 * Генерирует минимальный автономный набор файлов адаптера (`RenderedFilePayload[]`) для выбранного
 * ИИ-агента, когда глобальный Docker-контейнер ядра недоступен во время выполнения `init` или `sync`.
 */
export function buildOfflineFallbackFiles(
  agent: AgentId,
  stackTags: readonly string[],
): RenderedFilePayload[] {
  const normalizedTags =
    stackTags.length > 0 ? stackTags.join(", ") : GLOBAL_STACK_TAG;
  const baseRulesMd = [
    "# MCP-COOP Agentic Factory — Active Engineering Rules (`Harness²` Offline Seed)",
    "",
    `> **Compiled Stack Tags:** \`${normalizedTags}\``,
    "",
    "- Keep `.context/index.md` and `.context/entities/` synchronized with architectural changes.",
    "- Enforce KISS, DRY, SOLID, YAGNI, and Zero-Hardcode across all modules.",
    "- Query cross-project memory and the Context Graph via FastMCP tools when Core is online.",
    "",
  ].join("\n");

  if (agent === "antigravity") {
    const agyRoot = `.agents/plugins/${FACTORY_HOOK_SIGNATURE}`;
    return [
      {
        relative_path: `${agyRoot}/plugin.json`,
        content: `${JSON.stringify(
          {
            name: FACTORY_HOOK_SIGNATURE,
            displayName: "MCP-COOP Agentic Factory",
            version: "0.1.0",
            description:
              "Self-evolving Harness² engineering rules, PreToolUse guardrails, and .context/ Graph-as-Code sync.",
            disabled: false,
            suggestedPrompts: [
              "Inspect the .context/ knowledge graph and validate all nodes",
            ],
          },
          null,
          2,
        )}\n`,
        write_strategy: "overwrite",
      },
      {
        relative_path: `${agyRoot}/rules/AGENTS.md`,
        content: baseRulesMd,
        write_strategy: "overwrite",
      },
      {
        relative_path: `${agyRoot}/mcp_config.json`,
        content: `${JSON.stringify(
          { mcpServers: { [DEFAULT_MCP_SERVER_KEY]: { url: DEFAULT_OFFLINE_MCP_URL } } },
          null,
          2,
        )}\n`,
        write_strategy: "overwrite",
      },
      {
        relative_path: `${agyRoot}/hooks.json`,
        content: `${JSON.stringify(
          {
            [FACTORY_HOOK_SIGNATURE]: {
              enabled: true,
              SessionStart: [
                {
                  type: "command",
                  command: buildHookCommand("session-start", agent),
                  timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
                },
              ],
              PreToolUse: [
                {
                  matcher: ".*",
                  hooks: [
                    {
                      type: "command",
                      command: buildHookCommand("pre-tool-use", agent),
                      timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
                    },
                  ],
                },
              ],
              PostToolUse: [
                {
                  matcher: ".*",
                  hooks: [
                    {
                      type: "command",
                      command: buildHookCommand("post-tool-use", agent),
                      timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
                    },
                  ],
                },
              ],
              PreInvocation: [
                {
                  type: "command",
                  command: buildHookCommand("pre-invocation", agent),
                  timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
                },
              ],
              Stop: [
                {
                  type: "command",
                  command: buildHookCommand("stop", agent),
                  timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
                },
              ],
            },
          },
          null,
          2,
        )}\n`,
        write_strategy: "overwrite",
      },
    ];
  }

  if (agent === "claude-code") {
    return [
      {
        relative_path: ".claude/rules/mcp-coop-factory.md",
        content: baseRulesMd,
        write_strategy: "overwrite",
      },
      {
        relative_path: ".mcp.json",
        content: `${JSON.stringify(
          {
            mcpServers: {
              [DEFAULT_MCP_SERVER_KEY]: {
                type: "http",
                url: DEFAULT_OFFLINE_MCP_URL,
              },
            },
          },
          null,
          2,
        )}\n`,
        write_strategy: "merge_json",
      },
      {
        relative_path: ".claude/settings.json",
        content: buildClaudeCodexHooksPayload(agent),
        write_strategy: "merge_json",
      },
    ];
  }

  if (agent === "cursor") {
    const cursorMdc = `---\ndescription: MCP-COOP Agentic Factory always-on engineering rules\nglobs: "*"\nalwaysApply: true\n---\n\n${baseRulesMd}`;
    return [
      {
        relative_path: ".cursor/rules/mcp-coop-factory.mdc",
        content: cursorMdc,
        write_strategy: "overwrite",
      },
      {
        relative_path: ".cursor/mcp.json",
        content: `${JSON.stringify(
          { mcpServers: { [DEFAULT_MCP_SERVER_KEY]: { url: DEFAULT_OFFLINE_MCP_URL } } },
          null,
          2,
        )}\n`,
        write_strategy: "merge_json",
      },
      {
        relative_path: ".cursor/hooks.json",
        content: `${JSON.stringify(
          {
            version: 1,
            hooks: {
              sessionStart: [{ command: buildHookCommand("session-start", agent), timeout: DEFAULT_HOOK_TIMEOUT_SECONDS }],
              preToolUse: [{ command: buildHookCommand("pre-tool-use", agent), timeout: DEFAULT_HOOK_TIMEOUT_SECONDS }],
              postToolUse: [{ command: buildHookCommand("post-tool-use", agent), timeout: DEFAULT_HOOK_TIMEOUT_SECONDS }],
              beforeSubmitPrompt: [{ command: buildHookCommand("pre-invocation", agent), timeout: DEFAULT_HOOK_TIMEOUT_SECONDS }],
              stop: [{ command: buildHookCommand("stop", agent), timeout: DEFAULT_HOOK_TIMEOUT_SECONDS }],
            },
          },
          null,
          2,
        )}\n`,
        write_strategy: "merge_json",
      },
    ];
  }

  if (agent === "codex") {
    return [
      {
        relative_path: "AGENTS.md",
        content: baseRulesMd,
        write_strategy: "overwrite",
      },
      {
        relative_path: ".codex/config.toml",
        content: `[features]\ncodex_hooks = true\n\n[mcp_servers.${CODEX_MCP_SERVER_KEY}]\nenabled = true\nurl = "${DEFAULT_OFFLINE_MCP_URL}"\n`,
        write_strategy: "merge_toml",
      },
      {
        relative_path: ".codex/hooks.json",
        content: buildClaudeCodexHooksPayload(agent),
        write_strategy: "merge_json",
      },
    ];
  }

  return [
    {
      relative_path: ".opencode/rules/mcp-coop-factory.md",
      content: baseRulesMd,
      write_strategy: "overwrite",
    },
    {
      relative_path: "opencode.json",
      content: `${JSON.stringify(
        {
          $schema: "https://opencode.ai/config.json",
          instructions: ["AGENTS.md", ".context/index.md", ".opencode/rules/*.md"],
          mcp: {
            [DEFAULT_MCP_SERVER_KEY]: {
              type: "remote",
              url: DEFAULT_OFFLINE_MCP_URL,
              enabled: true,
            },
          },
        },
        null,
        2,
      )}\n`,
      write_strategy: "merge_json",
    },
    {
      relative_path: ".opencode/plugins/mcp-coop-factory.ts",
      content: [
        "// Auto-generated OpenCode lifecycle plugin for MCP-COOP Agentic Factory",
        "export const McpCoopFactoryPlugin = async ({ $ }: { $: any }) => ({",
        '  "tool.execute.before": async () => {',
        `    await $\`${buildHookCommand("pre-tool-use", agent)}\`;`,
        "  },",
        '  "tool.execute.after": async () => {',
        `    await $\`${buildHookCommand("post-tool-use", agent)}\`;`,
        "  },",
        '  "session.idle": async () => {',
        `    await $\`${buildHookCommand("stop", agent)}\`;`,
        "  },",
        "});",
        "export default McpCoopFactoryPlugin;",
        "",
      ].join("\n"),
      write_strategy: "overwrite",
    },
  ];
}
