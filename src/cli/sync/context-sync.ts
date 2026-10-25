/**
 * Слой 1 (Host CLI Sync — Context Graph 3-Way Hash Sync): сборщик и материализатор узлов `.context/`.
 * Собирает локальные Markdown-узлы графа с расчётом канонических SHA-256 дайджестов и атомарно
 * записывает на диск результаты серверной 3-Way Hash сверки (`pulled_from_db`, `merged`).
 */

import fs from "node:fs";
import path from "node:path";

import {
  computeNormalizedContentHash,
  resolveSafeWorkspacePath,
  writeFileAtomicSync,
} from "../common/index.ts";
import { WORKSPACE_RELATIVE_PATHS } from "../config/index.ts";
import {
  type ContextNodeSyncInputPayload,
  type ContextNodeSyncItemPayload,
  type ContextSyncReportPayload,
} from "../core-api/index.ts";

export {
  type ContextNodeSyncInputPayload,
  type ContextNodeSyncItemPayload,
  type ContextSyncReportPayload,
};

const CONTEXT_ROOT_REL_DIR = WORKSPACE_RELATIVE_PATHS.CONTEXT_DIR;
const MARKDOWN_EXTENSION = ".md" as const;
const HIDDEN_ENTRY_PREFIX = "." as const;

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

export interface AppliedContextSyncSummary {
  writtenPaths: string[];
  nextBaseHashes: Record<string, string>;
}

/**
 * Рекурсивно обходит поддиректорию сущностей графа (паттерн `Folder-per-Subsystem`),
 * пропуская скрытые элементы и символические ссылки, и возвращает лексикографически
 * отсортированный список относительных POSIX-путей ко всем `.md` узлам.
 */
function collectMarkdownFilesRecursively(
  baseDirAbs: string,
  currentRelPrefix: string,
): string[] {
  let entries: fs.Dirent[] = [];
  try {
    entries = fs.readdirSync(baseDirAbs, { withFileTypes: true });
  } catch {
    return [];
  }

  entries.sort((a, b) => a.name.localeCompare(b.name));
  const collected: string[] = [];

  for (const entry of entries) {
    if (
      entry.name.startsWith(HIDDEN_ENTRY_PREFIX) ||
      entry.isSymbolicLink()
    ) {
      continue;
    }
    const nextRelPath = `${currentRelPrefix}/${entry.name}`;
    const nextAbsPath = path.join(baseDirAbs, entry.name);

    if (entry.isDirectory()) {
      collected.push(
        ...collectMarkdownFilesRecursively(nextAbsPath, nextRelPath),
      );
      continue;
    }
    if (entry.isFile() && entry.name.endsWith(MARKDOWN_EXTENSION)) {
      collected.push(nextRelPath);
    }
  }

  return collected;
}

/**
 * Собирает локальные файлы Контекстного Графа (`.context/index.md`, `.context/schema.md`,
 * `.context/entities/**`) с вычислением `content_hash` и подстановкой `base_hash` для 3-Way Hash Sync.
 */
export function collectWorkspaceContextNodes(
  workspaceRoot: string,
  baseHashes?: Readonly<Record<string, string>>,
): ContextNodeSyncInputPayload[] {
  const contextRoot = path.join(
    path.resolve(workspaceRoot),
    CONTEXT_ROOT_REL_DIR,
  );
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
      const nodeId = path.basename(relPath, MARKDOWN_EXTENSION);
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
      // Безопасно пропускаем недоступный при чтении узел графа.
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
    const relPaths = collectMarkdownFilesRecursively(dirAbs, subDir);
    for (const relPath of relPaths) {
      pushIfExists(relPath, entityClass);
    }
  }

  return nodes;
}

/**
 * Применяет результаты серверной 3-Way Hash синхронизации `.context/`: атомарно записывает на диск
 * узлы со статусами `pulled_from_db` и `merged`, проверяя границы директории через `resolveSafeWorkspacePath`,
 * и обновляет карту `base_hash` для следующей сверки.
 */
export function applyContextSyncResults(
  workspaceRoot: string,
  items: readonly ContextNodeSyncItemPayload[],
  prevBaseHashes?: Readonly<Record<string, string>>,
): AppliedContextSyncSummary {
  const resolvedContextRoot = path.resolve(
    workspaceRoot,
    CONTEXT_ROOT_REL_DIR,
  );
  const nextBaseHashes: Record<string, string> = { ...(prevBaseHashes ?? {}) };
  const writtenPaths: string[] = [];

  for (const item of items) {
    const cleanRel = item.relative_path.replace(/\\/g, "/").replace(/^\/+/, "");
    nextBaseHashes[cleanRel] = item.base_hash || item.content_hash;

    if (
      (item.action === "pulled_from_db" || item.action === "merged") &&
      typeof item.content === "string"
    ) {
      const targetAbs = resolveSafeWorkspacePath(resolvedContextRoot, cleanRel);
      if (targetAbs === null) {
        continue;
      }
      writeFileAtomicSync(targetAbs, item.content);
      writtenPaths.push(`${CONTEXT_ROOT_REL_DIR}/${cleanRel}`);
    }
  }

  return { writtenPaths, nextBaseHashes };
}
