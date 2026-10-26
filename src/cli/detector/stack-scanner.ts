/**
 * Слой 1 (Host CLI Detector): сканирование файловой системы проекта и манифестов зависимостей
 * (`package.json`, `pyproject.toml`, `requirements*.txt`, `pom.xml`, `build.gradle*`, `Cargo.toml`,
 * `go.mod`, Docker-файлов) для автоматического выведения тегов технологического стека.
 * Поддерживает динамические правила детекции из PostgreSQL (`GET /api/harness/components`)
 * с фолбэком на `seeds/harness-seed.json`.
 */

import fs from "node:fs";
import path from "node:path";

import {
  isJsonObject,
  pathExistsSync,
  readFileUtf8IfExistsSync,
} from "../common/index.ts";
import {
  FILE_MARKER_TAG_MAP,
  IGNORED_SCAN_DIRECTORIES,
  LIMITS,
  loadHarnessSeedCatalog,
  NODE_DEP_TAG_MAP,
  STACK_SIGNATURE_RULES,
  type StackSignatureRule,
  UNIVERSAL_STACK_TAGS,
} from "../config/index.ts";
import type { HarnessComponentItem } from "../core-api/endpoints-harness.ts";

export const GLOBAL_STACK_TAG = UNIVERSAL_STACK_TAGS[0];

const PACKAGE_JSON_FILE_NAME = "package.json" as const;
const REQUIREMENTS_FILE_PATTERN = /^requirements(?:[-_.][a-z0-9_.-]+)?\.txt$/i;

export const ROOT_TEXT_MANIFEST_REL_PATHS = [
  "pyproject.toml",
  "requirements.txt",
  "docker-compose.yml",
  "docker/docker-compose.yml",
  "compose.yaml",
  "pom.xml",
  "build.gradle",
  "build.gradle.kts",
  "Cargo.toml",
  "go.mod",
] as const;

const SUBDIR_TEXT_MANIFEST_FILE_NAMES: ReadonlySet<string> = new Set([
  "pyproject.toml",
  "pom.xml",
  "build.gradle",
  "build.gradle.kts",
  "Cargo.toml",
  "go.mod",
]);

export interface FileStackRule {
  readonly relativePaths: readonly string[];
  readonly tags: readonly string[];
}

export interface TextManifestTokenRule {
  readonly pattern: RegExp;
  readonly tag: string;
}

/**
 * Статус технологического тега в каталоге БД (`active skill` vs `empty stub`).
 */
export interface CatalogTagStatusInfo {
  readonly hasContent: boolean;
  readonly title: string;
  readonly description: string;
}

/**
 * Таблица соответствия файлов проекта базовым тегам платформ и языков,
 * сформированная из единого реестра сигнатур `STACK_SIGNATURE_RULES`.
 */
export const FILE_STACK_RULES: readonly FileStackRule[] =
  STACK_SIGNATURE_RULES.filter(
    (rule): rule is typeof rule & { readonly fileMarkers: readonly string[] } =>
      Array.isArray(rule.fileMarkers) && rule.fileMarkers.length > 0,
  ).map((rule) => ({
    relativePaths: rule.fileMarkers,
    tags: [rule.tag],
  }));

/**
 * Таблица соответствия зависимостей `package.json` специализированным тегам
 * экосистемы Node.js / TypeScript (реэкспорт `NODE_DEP_TAG_MAP` для обратной совместимости).
 */
export const PACKAGE_JSON_DEPENDENCY_RULES: Readonly<Record<string, string>> =
  NODE_DEP_TAG_MAP;

/**
 * Правила извлечения тегов инфраструктурных сервисов и библиотек из текстовых манифестов.
 */
export const TEXT_MANIFEST_TOKEN_RULES: readonly TextManifestTokenRule[] =
  STACK_SIGNATURE_RULES.filter(
    (rule): rule is typeof rule & { readonly contentPattern: RegExp } =>
      rule.contentPattern instanceof RegExp,
  ).map((rule) => ({
    pattern: rule.contentPattern,
    tag: rule.tag,
  }));

interface DiscoveredManifestPaths {
  readonly packageJsonPaths: readonly string[];
  readonly textManifestPaths: readonly string[];
  readonly markerTags: readonly string[];
}

/**
 * Выполняет ограниченный по глубине (`LIMITS.MAX_SCAN_DEPTH`) обход рабочей директории,
 * пропуская служебные каталоги из `IGNORED_SCAN_DIRECTORIES`, для поиска файловых маркеров
 * и текстовых манифестов в корне и подпапках монорепозитория.
 */
function discoverWorkspaceManifests(
  workspaceDir: string,
  rules: readonly StackSignatureRule[],
): DiscoveredManifestPaths {
  const packageJsonSet = new Set<string>();
  const textManifestSet = new Set<string>();
  const markerTagSet = new Set<string>();

  const basenameMarkerMap = new Map<string, string[]>();
  for (const rule of rules) {
    if (!rule.fileMarkers) {
      continue;
    }
    for (const markerRelPath of rule.fileMarkers) {
      const candidatePath = path.join(workspaceDir, markerRelPath);
      if (pathExistsSync(candidatePath)) {
        markerTagSet.add(rule.tag);
      }
      if (!markerRelPath.includes("/")) {
        const list = basenameMarkerMap.get(markerRelPath) ?? [];
        if (!list.includes(rule.tag)) {
          list.push(rule.tag);
        }
        basenameMarkerMap.set(markerRelPath, list);
      }
    }
  }

  const rootPackageJson = path.join(workspaceDir, PACKAGE_JSON_FILE_NAME);
  if (pathExistsSync(rootPackageJson)) {
    packageJsonSet.add(rootPackageJson);
  }

  for (const relPath of ROOT_TEXT_MANIFEST_REL_PATHS) {
    const fullPath = path.join(workspaceDir, relPath);
    if (pathExistsSync(fullPath)) {
      textManifestSet.add(fullPath);
    }
  }

  function walkDirectory(currentDir: string, currentDepth: number): void {
    if (currentDepth > LIMITS.MAX_SCAN_DEPTH) {
      return;
    }

    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(currentDir, { withFileTypes: true });
    } catch {
      return;
    }

    for (const entry of entries) {
      const entryName = entry.name;
      const fullEntryPath = path.join(currentDir, entryName);

      if (entry.isDirectory()) {
        if (
          IGNORED_SCAN_DIRECTORIES.has(entryName) ||
          entryName.startsWith(".")
        ) {
          continue;
        }
        walkDirectory(fullEntryPath, currentDepth + 1);
        continue;
      }

      if (!entry.isFile()) {
        continue;
      }

      const directMarkerTags = basenameMarkerMap.get(entryName);
      if (directMarkerTags) {
        for (const tag of directMarkerTags) {
          markerTagSet.add(tag);
        }
      }

      if (entryName === PACKAGE_JSON_FILE_NAME) {
        packageJsonSet.add(fullEntryPath);
      } else if (SUBDIR_TEXT_MANIFEST_FILE_NAMES.has(entryName)) {
        textManifestSet.add(fullEntryPath);
      } else if (REQUIREMENTS_FILE_PATTERN.test(entryName)) {
        textManifestSet.add(fullEntryPath);
        const reqTags = basenameMarkerMap.get("requirements.txt");
        if (reqTags) {
          for (const tag of reqTags) {
            markerTagSet.add(tag);
          }
        }
      }
    }
  }

  walkDirectory(workspaceDir, 1);

  return {
    packageJsonPaths: Array.from(packageJsonSet),
    textManifestPaths: Array.from(textManifestSet),
    markerTags: Array.from(markerTagSet),
  };
}

/**
 * Безопасно читает и разбирает `package.json` для извлечения тегов npm-зависимостей
 * из секций `dependencies`, `devDependencies` и `peerDependencies`.
 */
function extractPackageJsonFileTags(
  packageJsonPath: string,
  rules: readonly StackSignatureRule[],
): string[] {
  const rawContent = readFileUtf8IfExistsSync(packageJsonPath);
  if (rawContent === null) {
    return [];
  }

  const nodeDepTags = new Map<string, string[]>();
  for (const rule of rules) {
    if (!rule.nodeDependencies) {
      continue;
    }
    for (const dep of rule.nodeDependencies) {
      const list = nodeDepTags.get(dep) ?? [];
      if (!list.includes(rule.tag)) {
        list.push(rule.tag);
      }
      nodeDepTags.set(dep, list);
    }
  }

  try {
    const parsed: unknown = JSON.parse(rawContent);
    if (!isJsonObject(parsed)) {
      return [];
    }

    const dependencySections = [
      parsed.dependencies,
      parsed.devDependencies,
      parsed.peerDependencies,
    ];

    const matchedTags: string[] = [];
    for (const section of dependencySections) {
      if (!isJsonObject(section)) {
        continue;
      }
      for (const pkgName of Object.keys(section)) {
        const mappedTags = nodeDepTags.get(pkgName);
        if (mappedTags) {
          matchedTags.push(...mappedTags);
        }
      }
    }

    return matchedTags;
  } catch {
    return [];
  }
}

/**
 * Сканирует текстовый манифест (`pyproject.toml`, `requirements*.txt`, `docker-compose.yml`,
 * `pom.xml`, `build.gradle*`, `Cargo.toml`, `go.mod`) по зависимостям и регулярным выражениям правил.
 */
function extractTextManifestFileTags(
  manifestPath: string,
  rules: readonly StackSignatureRule[],
): string[] {
  const content = readFileUtf8IfExistsSync(manifestPath);
  if (content === null) {
    return [];
  }

  const matchedTags: string[] = [];

  for (const rule of rules) {
    let matched = false;
    if (rule.pythonDependencies) {
      for (const depToken of rule.pythonDependencies) {
        const tokenRegex = new RegExp(`\\b${depToken}\\b`, "i");
        if (tokenRegex.test(content)) {
          matchedTags.push(rule.tag);
          matched = true;
          break;
        }
      }
    }
    if (!matched && rule.contentPattern instanceof RegExp) {
      if (rule.contentPattern.test(content)) {
        matchedTags.push(rule.tag);
      }
    }
  }

  return matchedTags;
}

/**
 * Формирует список технологических тегов проекта для селективной фильтрации правил `Harness²` и скиллов.
 * Первым элементом всегда фиксируются универсальные теги (`"global"`), за которыми следуют
 * уникальные теги стека проекта в алфавитном порядке.
 */
export function detectStackTags(
  workspaceDir: string,
  customRules?: readonly StackSignatureRule[],
): string[] {
  const activeRules = customRules ?? STACK_SIGNATURE_RULES;
  const resolvedDir = path.resolve(workspaceDir);
  const discoveredTags = new Set<string>();

  const manifests = discoverWorkspaceManifests(resolvedDir, activeRules);

  for (const tag of manifests.markerTags) {
    discoveredTags.add(tag);
  }

  for (const packageJsonPath of manifests.packageJsonPaths) {
    for (const tag of extractPackageJsonFileTags(
      packageJsonPath,
      activeRules,
    )) {
      discoveredTags.add(tag);
    }
  }

  for (const manifestPath of manifests.textManifestPaths) {
    for (const tag of extractTextManifestFileTags(manifestPath, activeRules)) {
      discoveredTags.add(tag);
    }
  }

  for (const universalTag of UNIVERSAL_STACK_TAGS) {
    discoveredTags.delete(universalTag);
  }

  const sortedProjectTags = Array.from(discoveredTags).sort((a, b) =>
    a.localeCompare(b),
  );

  return [...UNIVERSAL_STACK_TAGS, ...sortedProjectTags];
}

/**
 * Возвращает полный отсортированный каталог всех поддерживаемых доменных тегов стека
 * (без базового тега `"global"`). Используется интерактивным визардом `init` для отображения
 * полного чеклиста доступных технологий поверх автообнаруженных маркеров проекта.
 */
export function getAllKnownStackTags(
  customRules?: readonly StackSignatureRule[],
): string[] {
  const activeRules = customRules ?? STACK_SIGNATURE_RULES;
  const knownTags = new Set<string>();

  for (const rule of activeRules) {
    knownTags.add(rule.tag);
  }

  if (!customRules) {
    for (const tags of Object.values(FILE_MARKER_TAG_MAP)) {
      for (const tag of tags) {
        knownTags.add(tag);
      }
    }
  }

  for (const universalTag of UNIVERSAL_STACK_TAGS) {
    knownTags.delete(universalTag);
  }

  return Array.from(knownTags).sort((a, b) => a.localeCompare(b));
}

/**
 * Строит карту статуса технологических тегов (исключая `"global"`), определяя для каждого тега,
 * подкреплён ли он активным скиллом в БД (`hasContent: true`, например `typescript`, `nodejs`)
 * или является пустой технологической заглушкой (`hasContent: false`, например `java`, `python`, `fastapi`).
 */
export function getCatalogTagStatusMap(
  catalogComponents?: readonly HarnessComponentItem[],
): ReadonlyMap<string, CatalogTagStatusInfo> {
  const statusMap = new Map<string, CatalogTagStatusInfo>();
  const universalSet = new Set<string>(UNIVERSAL_STACK_TAGS);

  interface NormalizedTagSource {
    readonly componentType: string;
    readonly stackTags: readonly string[];
    readonly title: string;
    readonly description: string;
    readonly hasContent: boolean;
  }

  const sources: NormalizedTagSource[] =
    catalogComponents && catalogComponents.length > 0
      ? catalogComponents.map((item) => {
          const hasContent =
            typeof item.has_content === "boolean"
              ? item.has_content && item.is_empty_stub !== true
              : typeof item.is_empty_stub === "boolean"
                ? !item.is_empty_stub
                : item.status !== "disabled";
          return {
            componentType: item.component_type.toLowerCase(),
            stackTags: item.stack_tags,
            title: item.title,
            description: item.description,
            hasContent,
          };
        })
      : loadHarnessSeedCatalog().components.map((seedItem) => {
          const isStub = seedItem.metadata?.is_empty_stub === true;
          const hasContent =
            !isStub &&
            typeof seedItem.content === "string" &&
            seedItem.content.trim().length > 0;
          return {
            componentType: seedItem.component_type.toLowerCase(),
            stackTags: seedItem.stack_tags,
            title: seedItem.title,
            description: seedItem.description,
            hasContent,
          };
        });

  // Pass 1: evaluate `skill` components first so technology stub skills (`is_empty_stub: true`)
  // accurately determine whether a technology tag has an active skill in the DB.
  for (const src of sources) {
    if (src.componentType !== "skill") {
      continue;
    }
    for (const tag of src.stackTags) {
      if (universalSet.has(tag)) {
        continue;
      }
      const existing = statusMap.get(tag);
      if (!existing || (!existing.hasContent && src.hasContent)) {
        statusMap.set(tag, {
          hasContent: src.hasContent,
          title: src.title,
          description: src.description,
        });
      }
    }
  }

  // Pass 2: include any remaining domain tags defined only on non-skill components.
  for (const src of sources) {
    if (src.componentType === "skill") {
      continue;
    }
    for (const tag of src.stackTags) {
      if (universalSet.has(tag) || statusMap.has(tag)) {
        continue;
      }
      statusMap.set(tag, {
        hasContent: src.hasContent,
        title: src.title,
        description: src.description,
      });
    }
  }

  return statusMap;
}
