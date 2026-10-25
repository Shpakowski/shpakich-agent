/**
 * Слой 1 (Host CLI Common Foundation): парсер YAML frontmatter и генератор семантических тегов.
 * Разбирает метаданные Markdown-узлов Контекстного Графа (`.context/`) и скиллов (`SKILL.md`),
 * а также извлекает детерминированные теги стека из идентификаторов (`slug`) компонентов.
 */

import { normalizeLineEndings } from "./hashing.ts";

const FRONTMATTER_OPEN_FENCE = "---\n" as const;
const FRONTMATTER_CLOSE_FENCE_MARKER = "\n---" as const;
const MIN_SLUG_TAG_LENGTH = 2 as const;

const DEFAULT_SLUG_STOP_WORDS: ReadonlySet<string> = new Set([
  "and",
  "for",
  "from",
  "into",
  "the",
  "via",
  "with",
]);

/**
 * Результат разбора YAML frontmatter и основного тела Markdown-документа.
 */
export interface ParsedFrontmatter {
  readonly hasFrontmatter: boolean;
  readonly frontmatterRaw: string;
  readonly body: string;
  readonly scalars: Readonly<Record<string, string>>;
  readonly lists: Readonly<Record<string, readonly string[]>>;
}

function stripYamlQuotes(raw: string): string {
  return raw.replace(/^["'>|-]+|["']+$/g, "").trim();
}

function parseYamlLines(yamlBlock: string): {
  scalars: Record<string, string>;
  lists: Record<string, string[]>;
} {
  const scalars: Record<string, string> = {};
  const lists: Record<string, string[]> = {};
  let activeListKey: string | null = null;

  for (const rawLine of normalizeLineEndings(yamlBlock).split("\n")) {
    const trimmed = rawLine.trim();
    if (trimmed.length === 0 || trimmed.startsWith("#")) {
      continue;
    }

    const listMatch = /^\s*-\s+(.*)$/.exec(rawLine);
    if (listMatch && activeListKey !== null) {
      const itemValue = stripYamlQuotes(listMatch[1] ?? "");
      if (itemValue.length > 0) {
        const currentList = lists[activeListKey] ?? [];
        currentList.push(itemValue);
        lists[activeListKey] = currentList;
      }
      continue;
    }

    const kvMatch = /^([a-zA-Z0-9_-]+)\s*:\s*(.*)$/.exec(trimmed);
    if (!kvMatch) {
      activeListKey = null;
      continue;
    }

    const key = (kvMatch[1] ?? "").trim();
    const rawValue = (kvMatch[2] ?? "").trim();
    if (key.length === 0) {
      activeListKey = null;
      continue;
    }

    if (rawValue.length === 0) {
      activeListKey = key;
      if (!(key in lists)) {
        lists[key] = [];
      }
      continue;
    }

    activeListKey = null;
    if (rawValue.startsWith("[") && rawValue.endsWith("]")) {
      const inner = rawValue.slice(1, -1).trim();
      const items =
        inner.length === 0
          ? []
          : inner
              .split(",")
              .map((part) => stripYamlQuotes(part))
              .filter((part) => part.length > 0);
      lists[key] = items;
      continue;
    }

    const cleanedScalar = stripYamlQuotes(rawValue);
    if (cleanedScalar.length > 0) {
      scalars[key] = cleanedScalar;
    }
  }

  return { scalars, lists };
}

/**
 * Отделяет блок YAML frontmatter (`--- ... ---`) от тела Markdown-файла и разбирает
 * скалярные поля и списки без внешних YAML-зависимостей.
 */
export function parseMarkdownFrontmatter(
  rawContent: string,
): ParsedFrontmatter {
  const normalized = normalizeLineEndings(rawContent);
  if (!normalized.startsWith(FRONTMATTER_OPEN_FENCE)) {
    return {
      hasFrontmatter: false,
      frontmatterRaw: "",
      body: normalized,
      scalars: {},
      lists: {},
    };
  }

  let searchFrom = FRONTMATTER_OPEN_FENCE.length;
  let closeIdx = -1;
  let bodyStartIdx = -1;

  while (searchFrom <= normalized.length) {
    const candidateIdx = normalized.indexOf(
      FRONTMATTER_CLOSE_FENCE_MARKER,
      searchFrom,
    );
    if (candidateIdx === -1) {
      break;
    }
    const afterFenceIdx = candidateIdx + FRONTMATTER_CLOSE_FENCE_MARKER.length;
    const nextNewlineIdx = normalized.indexOf("\n", afterFenceIdx);
    const trailingOnFenceLine =
      nextNewlineIdx === -1
        ? normalized.slice(afterFenceIdx)
        : normalized.slice(afterFenceIdx, nextNewlineIdx);

    if (trailingOnFenceLine.trim().length === 0) {
      closeIdx = candidateIdx;
      bodyStartIdx =
        nextNewlineIdx === -1 ? normalized.length : nextNewlineIdx + 1;
      break;
    }
    searchFrom = afterFenceIdx;
  }

  if (closeIdx === -1) {
    return {
      hasFrontmatter: false,
      frontmatterRaw: "",
      body: normalized,
      scalars: {},
      lists: {},
    };
  }

  const frontmatterRaw = normalized.slice(
    FRONTMATTER_OPEN_FENCE.length,
    closeIdx,
  );
  const body = normalized.slice(bodyStartIdx);
  const { scalars, lists } = parseYamlLines(frontmatterRaw);

  return {
    hasFrontmatter: true,
    frontmatterRaw,
    body,
    scalars,
    lists,
  };
}

function resolveYamlTables(frontmatterBlock: string): {
  readonly scalars: Readonly<Record<string, string>>;
  readonly lists: Readonly<Record<string, readonly string[]>>;
} {
  const normalized = normalizeLineEndings(frontmatterBlock);
  if (normalized.startsWith(FRONTMATTER_OPEN_FENCE)) {
    const parsed = parseMarkdownFrontmatter(normalized);
    if (parsed.hasFrontmatter) {
      return { scalars: parsed.scalars, lists: parsed.lists };
    }
  }
  return parseYamlLines(normalized);
}

/**
 * Извлекает одиночное строковое значение по ключу `key` (регистронезависимо) из блока YAML frontmatter.
 */
export function extractFrontmatterScalar(
  frontmatterBlock: string,
  key: string,
): string | null {
  const targetKey = key.trim().toLowerCase();
  if (targetKey.length === 0) {
    return null;
  }
  const { scalars } = resolveYamlTables(frontmatterBlock);
  for (const [k, v] of Object.entries(scalars)) {
    if (k.toLowerCase() === targetKey && v.length > 0) {
      return v;
    }
  }
  return null;
}

/**
 * Извлекает список строковых значений по ключу `key` (регистронезависимо) из блока YAML frontmatter,
 * поддерживая как блочные (`- item`), так и инлайновые (`[a, b]`) списки.
 */
export function extractFrontmatterList(
  frontmatterBlock: string,
  key: string,
): string[] {
  const targetKey = key.trim().toLowerCase();
  if (targetKey.length === 0) {
    return [];
  }
  const { scalars, lists } = resolveYamlTables(frontmatterBlock);
  for (const [k, items] of Object.entries(lists)) {
    if (k.toLowerCase() === targetKey) {
      return [...items];
    }
  }
  for (const [k, scalarVal] of Object.entries(scalars)) {
    if (k.toLowerCase() === targetKey && scalarVal.length > 0) {
      return scalarVal
        .split(",")
        .map((part) => stripYamlQuotes(part))
        .filter((part) => part.length > 0);
    }
  }
  return [];
}

/**
 * Извлекает детерминированный список семантических тегов из `slug` компонента:
 * разбивает по не-алфавитно-цифровым разделителям (`[^a-z0-9]+`), отсеивает короткие токены (`< 2`)
 * и стоп-слова, сохраняя порядок первого вхождения без дубликатов.
 */
export function extractTagsFromSlug(
  slug: string,
  extraStopWords?: ReadonlySet<string>,
): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  const tokens = slug.trim().toLowerCase().split(/[^a-z0-9]+/);

  for (const token of tokens) {
    if (token.length < MIN_SLUG_TAG_LENGTH) {
      continue;
    }
    if (
      DEFAULT_SLUG_STOP_WORDS.has(token) ||
      (extraStopWords !== undefined && extraStopWords.has(token))
    ) {
      continue;
    }
    if (!seen.has(token)) {
      seen.add(token);
      result.push(token);
    }
  }

  return result;
}
