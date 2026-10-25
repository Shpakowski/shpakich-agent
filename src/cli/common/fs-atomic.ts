/**
 * Слой 1 (Host CLI Common Foundation): безопасные и атомарные операции с файловой системой.
 * Обеспечивает защиту от выхода за пределы рабочей директории (`Path Traversal`), атомарную запись
 * через временный файл с переименованием (`temp + rename`) и очистку опустевших родительских папок.
 */

import crypto from "node:crypto";
import fsSync from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";

const UTF8_ENCODING = "utf-8" as const;
const ENOENT_ERROR_CODE = "ENOENT" as const;
const NULL_BYTE = "\0" as const;

export interface AtomicWriteOptions {
  readonly mode?: number | undefined;
}

function isErrnoExceptionWithCode(error: unknown, code: string): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    (error as { readonly code?: unknown }).code === code
  );
}

/**
 * Разрешает относительный путь внутри `workspaceRoot` и возвращает абсолютный путь,
 * либо `null`, если путь содержит нулевые байты, является абсолютным или выходит за пределы `workspaceRoot`.
 */
export function resolveSafeWorkspacePath(
  workspaceRoot: string,
  relativePath: string,
): string | null {
  if (
    relativePath.trim().length === 0 ||
    relativePath.includes(NULL_BYTE) ||
    path.win32.isAbsolute(relativePath) ||
    path.posix.isAbsolute(relativePath)
  ) {
    return null;
  }

  const resolvedRoot = path.resolve(workspaceRoot);
  const normalizedRel = relativePath.replace(/\\/g, "/").replace(/^\/+/, "");
  const resolvedTarget = path.resolve(resolvedRoot, normalizedRel);
  const rootPrefix = resolvedRoot.endsWith(path.sep)
    ? resolvedRoot
    : `${resolvedRoot}${path.sep}`;

  if (
    resolvedTarget !== resolvedRoot &&
    !resolvedTarget.startsWith(rootPrefix)
  ) {
    return null;
  }

  return resolvedTarget;
}

/**
 * Проверяет, что относительный путь безопасно разрешается строго внутри границ `workspaceRoot` без `..`-обхода.
 */
export function isSafeRelativeWorkspacePath(
  workspaceRoot: string,
  relativePath: string,
): boolean {
  return resolveSafeWorkspacePath(workspaceRoot, relativePath) !== null;
}

/**
 * Разрешает относительный путь внутри `workspaceRoot` или выбрасывает ошибку безопасности при попытке выхода за пределы рабочей директории.
 */
export function requireSafeWorkspacePath(
  workspaceRoot: string,
  relativePath: string,
): string {
  const resolved = resolveSafeWorkspacePath(workspaceRoot, relativePath);
  if (resolved === null) {
    throw new Error(
      `Refusing to access path outside workspace root: "${relativePath}"`,
    );
  }
  return resolved;
}

/**
 * Атомарно записывает текстовый файл в UTF-8 через создание временного файла `.tmp-${pid}-${uuid}`
 * в целевой директории и последующий `fs.rename`, гарантируя удаление временного файла при сбое.
 */
export async function writeFileAtomic(
  targetPath: string,
  content: string,
  options?: AtomicWriteOptions,
): Promise<void> {
  const resolvedTarget = path.resolve(targetPath);
  const parentDir = path.dirname(resolvedTarget);
  await fs.mkdir(parentDir, { recursive: true });

  const tempPath = `${resolvedTarget}.tmp-${String(process.pid)}-${crypto.randomUUID()}`;
  try {
    await fs.writeFile(tempPath, content, UTF8_ENCODING);
    if (options?.mode !== undefined) {
      await fs.chmod(tempPath, options.mode);
    }
    await fs.rename(tempPath, resolvedTarget);
  } catch (cause: unknown) {
    try {
      await fs.rm(tempPath, { force: true });
    } catch {
      // Игнорируем вторичную ошибку очистки временного файла при откате.
    }
    throw new Error(`Failed to atomically write file "${resolvedTarget}"`, {
      cause,
    });
  }
}

/**
 * Читает текстовый файл в кодировке UTF-8 или возвращает `null`, если файл не существует (`ENOENT`).
 * Реальные ошибки доступа и ввода-вывода пробрасываются с сохранением исходной причины `{ cause }`.
 */
export async function readFileUtf8IfExists(
  targetPath: string,
): Promise<string | null> {
  try {
    return await fs.readFile(targetPath, UTF8_ENCODING);
  } catch (cause: unknown) {
    if (isErrnoExceptionWithCode(cause, ENOENT_ERROR_CODE)) {
      return null;
    }
    throw new Error(`Failed to read UTF-8 file "${targetPath}"`, { cause });
  }
}

/**
 * Проверяет существование файла или директории по указанному пути.
 */
export async function pathExists(targetPath: string): Promise<boolean> {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

/**
 * Последовательно удаляет опустевшие родительские директории начиная со `startDir` вверх по дереву,
 * останавливаясь строго перед корневой границей `stopAtRootDir`.
 */
export async function removeEmptyParentDirs(
  startDir: string,
  stopAtRootDir: string,
): Promise<void> {
  const resolvedStopRoot = path.resolve(stopAtRootDir);
  const stopRootPrefix = resolvedStopRoot.endsWith(path.sep)
    ? resolvedStopRoot
    : `${resolvedStopRoot}${path.sep}`;
  let currentDir = path.resolve(startDir);

  while (
    currentDir !== resolvedStopRoot &&
    currentDir.startsWith(stopRootPrefix)
  ) {
    try {
      const entries = await fs.readdir(currentDir);
      if (entries.length > 0) {
        break;
      }
      await fs.rmdir(currentDir);
      const parentDir = path.dirname(currentDir);
      if (parentDir === currentDir) {
        break;
      }
      currentDir = parentDir;
    } catch {
      break;
    }
  }
}

/**
 * Синхронно и атомарно записывает текстовый файл в UTF-8 через создание временного файла `.tmp-${pid}-${uuid}`
 * в целевой директории и последующий `fsSync.renameSync`, гарантируя удаление временного файла при сбое.
 */
export function writeFileAtomicSync(
  targetPath: string,
  content: string,
  options?: AtomicWriteOptions,
): void {
  const resolvedTarget = path.resolve(targetPath);
  const parentDir = path.dirname(resolvedTarget);
  fsSync.mkdirSync(parentDir, { recursive: true });

  const tempPath = `${resolvedTarget}.tmp-${String(process.pid)}-${crypto.randomUUID()}`;
  try {
    fsSync.writeFileSync(tempPath, content, UTF8_ENCODING);
    if (options?.mode !== undefined) {
      fsSync.chmodSync(tempPath, options.mode);
    }
    fsSync.renameSync(tempPath, resolvedTarget);
  } catch (cause: unknown) {
    try {
      fsSync.rmSync(tempPath, { force: true });
    } catch {
      // Игнорируем вторичную ошибку очистки временного файла при откате.
    }
    throw new Error(`Failed to atomically write file "${resolvedTarget}"`, {
      cause,
    });
  }
}

/**
 * Синхронно читает текстовый файл в кодировке UTF-8 или возвращает `null`, если файл не существует (`ENOENT`).
 */
export function readFileUtf8IfExistsSync(targetPath: string): string | null {
  try {
    return fsSync.readFileSync(targetPath, UTF8_ENCODING);
  } catch (cause: unknown) {
    if (isErrnoExceptionWithCode(cause, ENOENT_ERROR_CODE)) {
      return null;
    }
    throw new Error(`Failed to read UTF-8 file "${targetPath}"`, { cause });
  }
}

/**
 * Синхронно проверяет существование файла или директории по указанному пути.
 */
export function pathExistsSync(targetPath: string): boolean {
  try {
    fsSync.accessSync(targetPath);
    return true;
  } catch {
    return false;
  }
}

/**
 * Синхронно удаляет опустевшие родительские директории начиная со `startDir` вверх по дереву,
 * останавливаясь строго перед корневой границей `stopAtRootDir`.
 */
export function removeEmptyParentDirsSync(
  startDir: string,
  stopAtRootDir: string,
): void {
  const resolvedStopRoot = path.resolve(stopAtRootDir);
  const stopRootPrefix = resolvedStopRoot.endsWith(path.sep)
    ? resolvedStopRoot
    : `${resolvedStopRoot}${path.sep}`;
  let currentDir = path.resolve(startDir);

  while (
    currentDir !== resolvedStopRoot &&
    currentDir.startsWith(stopRootPrefix)
  ) {
    try {
      const entries = fsSync.readdirSync(currentDir);
      if (entries.length > 0) {
        break;
      }
      fsSync.rmdirSync(currentDir);
      const parentDir = path.dirname(currentDir);
      if (parentDir === currentDir) {
        break;
      }
      currentDir = parentDir;
    } catch {
      break;
    }
  }
}
