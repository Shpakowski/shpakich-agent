/**
 * Слой 1 (Host CLI Detector): сканирование файловой системы проекта и манифестов зависимостей
 * (`package.json`, `pyproject.toml`, `requirements*.txt`, Docker-файлов) для автоматического
 * выведения тегов технологического стека. Опирается на декларативные таблицы из `src/cli/config/`.
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
  NODE_DEP_TAG_MAP,
  PYTHON_DEP_TAG_MAP,
  STACK_SIGNATURE_RULES,
  UNIVERSAL_STACK_TAGS,
} from "../config/index.ts";

export const GLOBAL_STACK_TAG = UNIVERSAL_STACK_TAGS[0];

const PACKAGE_JSON_FILE_NAME = "package.json" as const;
const PYPROJECT_TOML_FILE_NAME = "pyproject.toml" as const;
const REQUIREMENTS_FILE_PATTERN = /^requirements(?:[-_.][a-z0-9_.-]+)?\.txt$/i;

const ROOT_TEXT_MANIFEST_REL_PATHS = [
  "pyproject.toml",
  "requirements.txt",
  "docker-compose.yml",
  "docker/docker-compose.yml",
  "compose.yaml",
] as const;

export interface FileStackRule {
  readonly relativePaths: readonly string[];
  readonly tags: readonly string[];
}

export interface TextManifestTokenRule {
  readonly pattern: RegExp;
  readonly tag: string;
}

/**
 * Таблица соответствия файлов проекта базовым тегам платформ и языков,
 * сформированная из единого реестра сигнатур `STACK_SIGNATURE_RULES`.
 */
export const FILE_STACK_RULES: readonly FileStackRule[] = STACK_SIGNATURE_RULES.filter(
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
 * Правила извлечения тегов инфраструктурных сервисов и Python-библиотек из текстовых манифестов.
 * Регулярные выражения с границами слов (`\b`) предотвращают ложные срабатывания на подстроках.
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
 * пропуская служебные каталоги из `IGNORED_SCAN_DIRECTORIES`, для поиска `package.json`,
 * `pyproject.toml` и вариаций `requirements*.txt` в корне и подпапках монорепозитория.
 */
function discoverWorkspaceManifests(workspaceDir: string): DiscoveredManifestPaths {
  const packageJsonSet = new Set<string>();
  const textManifestSet = new Set<string>();
  const markerTagSet = new Set<string>();

  for (const [relPath, tags] of Object.entries(FILE_MARKER_TAG_MAP)) {
    const candidatePath = path.join(workspaceDir, relPath);
    if (pathExistsSync(candidatePath)) {
      for (const tag of tags) {
        markerTagSet.add(tag);
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

      if (entryName === PACKAGE_JSON_FILE_NAME) {
        packageJsonSet.add(fullEntryPath);
        markerTagSet.add("nodejs");
      } else if (entryName === PYPROJECT_TOML_FILE_NAME) {
        textManifestSet.add(fullEntryPath);
        markerTagSet.add("python");
      } else if (REQUIREMENTS_FILE_PATTERN.test(entryName)) {
        textManifestSet.add(fullEntryPath);
        markerTagSet.add("python");
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
function extractPackageJsonFileTags(packageJsonPath: string): string[] {
  const rawContent = readFileUtf8IfExistsSync(packageJsonPath);
  if (rawContent === null) {
    return [];
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
        const mappedTag = NODE_DEP_TAG_MAP[pkgName];
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
 * Сканирует текстовый манифест (`pyproject.toml`, `requirements*.txt`, `docker-compose.yml`)
 * по словарю `PYTHON_DEP_TAG_MAP` и регулярным выражениям `TEXT_MANIFEST_TOKEN_RULES`.
 */
function extractTextManifestFileTags(manifestPath: string): string[] {
  const content = readFileUtf8IfExistsSync(manifestPath);
  if (content === null) {
    return [];
  }

  const matchedTags: string[] = [];

  for (const [depToken, mappedTag] of Object.entries(PYTHON_DEP_TAG_MAP)) {
    const tokenRegex = new RegExp(`\\b${depToken}\\b`, "i");
    if (tokenRegex.test(content)) {
      matchedTags.push(mappedTag);
    }
  }

  for (const rule of TEXT_MANIFEST_TOKEN_RULES) {
    if (rule.pattern.test(content)) {
      matchedTags.push(rule.tag);
    }
  }

  return matchedTags;
}

/**
 * Формирует список технологических тегов проекта для селективной фильтрации правил `Harness²` и скиллов.
 * Первым элементом всегда фиксируются универсальные теги (`"global"`), гарантирующие подключение
 * базовых инвариантов фабрики, за которыми следуют уникальные теги стека проекта в алфавитном порядке.
 */
export function detectStackTags(workspaceDir: string): string[] {
  const resolvedDir = path.resolve(workspaceDir);
  const discoveredTags = new Set<string>();

  const manifests = discoverWorkspaceManifests(resolvedDir);

  for (const tag of manifests.markerTags) {
    discoveredTags.add(tag);
  }

  for (const packageJsonPath of manifests.packageJsonPaths) {
    for (const tag of extractPackageJsonFileTags(packageJsonPath)) {
      discoveredTags.add(tag);
    }
  }

  for (const manifestPath of manifests.textManifestPaths) {
    for (const tag of extractTextManifestFileTags(manifestPath)) {
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
export function getAllKnownStackTags(): string[] {
  const knownTags = new Set<string>();

  for (const tags of Object.values(FILE_MARKER_TAG_MAP)) {
    for (const tag of tags) {
      knownTags.add(tag);
    }
  }

  for (const tag of Object.values(NODE_DEP_TAG_MAP)) {
    knownTags.add(tag);
  }

  for (const tag of Object.values(PYTHON_DEP_TAG_MAP)) {
    knownTags.add(tag);
  }

  for (const rule of TEXT_MANIFEST_TOKEN_RULES) {
    knownTags.add(rule.tag);
  }

  for (const universalTag of UNIVERSAL_STACK_TAGS) {
    knownTags.delete(universalTag);
  }

  return Array.from(knownTags).sort((a, b) => a.localeCompare(b));
}
