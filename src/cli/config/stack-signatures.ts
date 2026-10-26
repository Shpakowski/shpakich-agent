/**
 * Слой конфигурации CLI (`src/cli/config`): загрузчик эталонных JSON-сидов (`seeds/harness-seed.json`,
 * `seeds/prompts-config.json`), динамический построитель сигнатур технологического стека и список
 * игнорируемых служебных директорий при сканировании рабочего пространства проекта.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  GLOBAL_FACTORY_RELATIVE_PATHS,
  resolveGlobalFactoryDir,
} from "./ports-and-paths.ts";

export const HARNESS_SEED_FILE_NAME = "harness-seed.json" as const;
export const PROMPTS_CONFIG_FILE_NAME = "prompts-config.json" as const;

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
 * Декларативные правила распознавания стека из метаданных компонента (`seeds/harness-seed.json` или БД).
 */
export interface SeedComponentDetectors {
  readonly file_markers?: readonly string[] | undefined;
  readonly node_packages?: readonly string[] | undefined;
  readonly python_packages?: readonly string[] | undefined;
  readonly content_patterns?: readonly string[] | undefined;
}

/**
 * Запись компонента `Harness²` в `seeds/harness-seed.json`.
 */
export interface SeedComponentEntry {
  readonly slug: string;
  readonly component_type: string;
  readonly stack_tags: readonly string[];
  readonly title: string;
  readonly description: string;
  readonly content: string;
  readonly metadata?:
    | {
        readonly execution_mode?: string | undefined;
        readonly category?: string | undefined;
        readonly is_empty_stub?: boolean | undefined;
        readonly detectors?: SeedComponentDetectors | undefined;
        readonly [key: string]: unknown;
      }
    | undefined;
  readonly status?: string | undefined;
  readonly score?: number | undefined;
  readonly version?: number | undefined;
}

/**
 * Запись профиля поддерживаемого ИИ-агента в `seeds/harness-seed.json`.
 */
export interface SeedAgentEntry {
  readonly agent_type: string;
  readonly display_name: string;
  readonly cli_display_name?: string | undefined;
  readonly config_hint?: string | undefined;
  readonly workspace_markers?: readonly string[] | undefined;
  readonly hooks?:
    | {
        readonly dialect?: string | undefined;
        readonly [key: string]: unknown;
      }
    | undefined;
  readonly [key: string]: unknown;
}

/**
 * Полная типизированная структура файла `seeds/harness-seed.json`.
 */
export interface HarnessSeedCatalog {
  readonly version: string;
  readonly plugin?:
    | {
        readonly id?: string | undefined;
        readonly display_name?: string | undefined;
        readonly description?: string | undefined;
        readonly mcp_server_key?: string | undefined;
        readonly codex_mcp_server_key?: string | undefined;
        readonly default_agent?: string | undefined;
      }
    | undefined;
  readonly legacy_slugs_to_purge?: readonly string[] | undefined;
  readonly agents: readonly SeedAgentEntry[];
  readonly components: readonly SeedComponentEntry[];
}

/**
 * Универсальный интерфейс источника правил детекции: подходит как для записей из `seeds/harness-seed.json`
 * (`metadata.detectors`), так и для объектов из живого ответа `GET /api/harness/components` (`detectors`).
 */
export interface CatalogComponentDetectorSource {
  readonly stack_tags: readonly string[];
  readonly detectors?: SeedComponentDetectors | undefined;
  readonly metadata?:
    | {
        readonly detectors?: SeedComponentDetectors | undefined;
        readonly [key: string]: unknown;
      }
    | undefined;
}

/**
 * Унифицированное правило детекции тега технологического стека по файловым маркерам,
 * npm/Python-зависимостям или регулярному выражению по текстовым манифестам.
 */
export interface StackSignatureRule {
  readonly tag: string;
  readonly fileMarkers?: readonly string[] | undefined;
  readonly nodeDependencies?: readonly string[] | undefined;
  readonly pythonDependencies?: readonly string[] | undefined;
  readonly contentPattern?: RegExp | undefined;
}

let cachedHarnessSeedCatalog: HarnessSeedCatalog | null = null;
let cachedPromptsConfigJson: Record<string, unknown> | null = null;

function resolveSeedFilePath(fileName: string): string {
  const currentFileDir = path.dirname(fileURLToPath(import.meta.url));
  const candidateRoots = [
    path.resolve(currentFileDir, "../../.."),
    path.resolve(currentFileDir, "../.."),
    path.resolve(currentFileDir, ".."),
    resolveGlobalFactoryDir(),
  ] as const;

  for (const rootDir of candidateRoots) {
    const candidatePath = path.join(
      rootDir,
      GLOBAL_FACTORY_RELATIVE_PATHS.SEEDS_DIR,
      fileName,
    );
    if (fs.existsSync(candidatePath)) {
      return candidatePath;
    }
  }

  return path.join(
    resolveGlobalFactoryDir(),
    GLOBAL_FACTORY_RELATIVE_PATHS.SEEDS_DIR,
    fileName,
  );
}

/**
 * Загружает и кеширует каталог `seeds/harness-seed.json` из корня пакета CLI
 * либо из глобальной директории `~/.mcp-coop-agentic-factory/seeds/`.
 */
export function loadHarnessSeedCatalog(): HarnessSeedCatalog {
  if (cachedHarnessSeedCatalog !== null) {
    return cachedHarnessSeedCatalog;
  }

  const seedPath = resolveSeedFilePath(HARNESS_SEED_FILE_NAME);
  try {
    const raw = fs.readFileSync(seedPath, "utf-8");
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed === "object" && parsed !== null) {
      const obj = parsed as Record<string, unknown>;
      cachedHarnessSeedCatalog = {
        version: typeof obj["version"] === "string" ? obj["version"] : "2026.2",
        plugin:
          typeof obj["plugin"] === "object" && obj["plugin"] !== null
            ? (obj["plugin"] as HarnessSeedCatalog["plugin"])
            : undefined,
        legacy_slugs_to_purge: Array.isArray(obj["legacy_slugs_to_purge"])
          ? (obj["legacy_slugs_to_purge"] as readonly string[])
          : undefined,
        agents: Array.isArray(obj["agents"])
          ? (obj["agents"] as readonly SeedAgentEntry[])
          : [],
        components: Array.isArray(obj["components"])
          ? (obj["components"] as readonly SeedComponentEntry[])
          : [],
      };
      return cachedHarnessSeedCatalog;
    }
  } catch {
    // Fallback to empty structure if seed file is unreadable.
  }

  cachedHarnessSeedCatalog = {
    version: "2026.2",
    agents: [],
    components: [],
  };
  return cachedHarnessSeedCatalog;
}

/**
 * Загружает и кеширует конфигурацию шаблонов промптов `seeds/prompts-config.json`
 * из корня пакета CLI либо из `~/.mcp-coop-agentic-factory/seeds/`.
 */
export function loadPromptsConfigJson(): Record<string, unknown> {
  if (cachedPromptsConfigJson !== null) {
    return cachedPromptsConfigJson;
  }

  const promptsPath = resolveSeedFilePath(PROMPTS_CONFIG_FILE_NAME);
  try {
    const raw = fs.readFileSync(promptsPath, "utf-8");
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed)
    ) {
      cachedPromptsConfigJson = parsed as Record<string, unknown>;
      return cachedPromptsConfigJson;
    }
  } catch {
    // Fallback to empty object if prompts config is unreadable.
  }

  cachedPromptsConfigJson = {};
  return cachedPromptsConfigJson;
}

interface MutableRuleAccumulator {
  readonly fileMarkers: Set<string>;
  readonly nodeDependencies: Set<string>;
  readonly pythonDependencies: Set<string>;
  readonly contentPatterns: Set<string>;
}

/**
 * Динамически строит массив правил детекции стека `StackSignatureRule[]` из списка компонентов
 * (полученных из живого ответа БД `GET /api/harness/components` или из `seeds/harness-seed.json`).
 */
export function buildStackSignatureRulesFromComponents(
  components: readonly CatalogComponentDetectorSource[],
): readonly StackSignatureRule[] {
  const accumulators = new Map<string, MutableRuleAccumulator>();
  const universalSet = new Set<string>(UNIVERSAL_STACK_TAGS);

  for (const comp of components) {
    const detectors = comp.detectors ?? comp.metadata?.detectors;
    if (!detectors) {
      continue;
    }

    const domainTags = comp.stack_tags.filter((tag) => !universalSet.has(tag));
    for (const tag of domainTags) {
      let acc = accumulators.get(tag);
      if (!acc) {
        acc = {
          fileMarkers: new Set<string>(),
          nodeDependencies: new Set<string>(),
          pythonDependencies: new Set<string>(),
          contentPatterns: new Set<string>(),
        };
        accumulators.set(tag, acc);
      }

      if (Array.isArray(detectors.file_markers)) {
        for (const marker of detectors.file_markers) {
          if (typeof marker === "string" && marker.trim().length > 0) {
            acc.fileMarkers.add(marker.trim());
          }
        }
      }

      if (Array.isArray(detectors.node_packages)) {
        for (const pkg of detectors.node_packages) {
          if (typeof pkg === "string" && pkg.trim().length > 0) {
            acc.nodeDependencies.add(pkg.trim());
          }
        }
      }

      if (Array.isArray(detectors.python_packages)) {
        for (const pkg of detectors.python_packages) {
          if (typeof pkg === "string" && pkg.trim().length > 0) {
            acc.pythonDependencies.add(pkg.trim());
          }
        }
      }

      if (Array.isArray(detectors.content_patterns)) {
        for (const pattern of detectors.content_patterns) {
          if (typeof pattern === "string" && pattern.trim().length > 0) {
            acc.contentPatterns.add(pattern.trim());
          }
        }
      }
    }
  }

  const rules: StackSignatureRule[] = [];
  for (const [tag, acc] of accumulators.entries()) {
    const fileMarkers = Array.from(acc.fileMarkers);
    const nodeDependencies = Array.from(acc.nodeDependencies);
    const pythonDependencies = Array.from(acc.pythonDependencies);
    const patternStrings = Array.from(acc.contentPatterns);

    if (
      fileMarkers.length === 0 &&
      nodeDependencies.length === 0 &&
      pythonDependencies.length === 0 &&
      patternStrings.length === 0
    ) {
      continue;
    }

    let contentPattern: RegExp | undefined;
    if (patternStrings.length > 0) {
      const combinedPattern =
        patternStrings.length === 1
          ? (patternStrings[0] ?? "")
          : patternStrings.map((p) => `(?:${p})`).join("|");
      try {
        contentPattern = new RegExp(combinedPattern, "i");
      } catch {
        contentPattern = undefined;
      }
    }

    rules.push({
      tag,
      ...(fileMarkers.length > 0 ? { fileMarkers } : {}),
      ...(nodeDependencies.length > 0 ? { nodeDependencies } : {}),
      ...(pythonDependencies.length > 0 ? { pythonDependencies } : {}),
      ...(contentPattern ? { contentPattern } : {}),
    });
  }

  return rules;
}

function buildFileMarkerTagMap(
  rules: readonly StackSignatureRule[],
): Readonly<Record<string, readonly string[]>> {
  const map: Record<string, string[]> = {};
  for (const rule of rules) {
    if (!rule.fileMarkers) {
      continue;
    }
    for (const marker of rule.fileMarkers) {
      const existing = map[marker] ?? [];
      if (!existing.includes(rule.tag)) {
        existing.push(rule.tag);
      }
      map[marker] = existing;
    }
  }
  return map;
}

function buildNodeDepTagMap(
  rules: readonly StackSignatureRule[],
): Readonly<Record<string, string>> {
  const map: Record<string, string> = {};
  for (const rule of rules) {
    if (!rule.nodeDependencies) {
      continue;
    }
    for (const dep of rule.nodeDependencies) {
      map[dep] = rule.tag;
    }
  }
  return map;
}

function buildPythonDepTagMap(
  rules: readonly StackSignatureRule[],
): Readonly<Record<string, string>> {
  const map: Record<string, string> = {};
  for (const rule of rules) {
    if (!rule.pythonDependencies) {
      continue;
    }
    for (const dep of rule.pythonDependencies) {
      map[dep] = rule.tag;
    }
  }
  return map;
}

/**
 * Единый реестр сигнатур стека, динамически построенный из `seeds/harness-seed.json`.
 */
export const STACK_SIGNATURE_RULES: readonly StackSignatureRule[] =
  buildStackSignatureRulesFromComponents(loadHarnessSeedCatalog().components);

/**
 * Карта соответствия относительных путей файлов-маркеров базовым тегам платформ и языков.
 */
export const FILE_MARKER_TAG_MAP: Readonly<Record<string, readonly string[]>> =
  buildFileMarkerTagMap(STACK_SIGNATURE_RULES);

/**
 * Карта соответствия пакетов из `package.json` (`dependencies`, `devDependencies`, `peerDependencies`)
 * специализированным тегам экосистемы Node.js / TypeScript.
 */
export const NODE_DEP_TAG_MAP: Readonly<Record<string, string>> =
  buildNodeDepTagMap(STACK_SIGNATURE_RULES);

/**
 * Карта соответствия токенов зависимостей Python и инфраструктурных манифестов (`pyproject.toml`,
 * `requirements.txt`, `docker-compose.yml`) каноническим тегам стека.
 */
export const PYTHON_DEP_TAG_MAP: Readonly<Record<string, string>> =
  buildPythonDepTagMap(STACK_SIGNATURE_RULES);
