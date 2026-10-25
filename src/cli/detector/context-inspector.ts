/**
 * Слой 1 (Host CLI Detector): инспекция `.context/index.md` и состояния графа знаний
 * в рабочем пространстве проекта. Определяет физическое наличие индексного узла
 * и извлекает отформатированную метку времени его последней модификации.
 */

import fs from "node:fs";
import path from "node:path";

import { readFileUtf8IfExistsSync } from "../common/index.ts";
import { WORKSPACE_RELATIVE_PATHS } from "../config/index.ts";

export const CONTEXT_GRAPH_INDEX_REL_PATH =
  WORKSPACE_RELATIVE_PATHS.CONTEXT_INDEX;

export interface ContextGraphInspection {
  readonly hasContextGraph: boolean;
  readonly contextGraphUpdatedAt?: string;
}

/**
 * Приводит переданный путь (корневую директорию рабочей области или прямой путь к `index.md`)
 * к абсолютному пути файла `.context/index.md`.
 */
export function resolveContextIndexPath(workspaceOrIndexPath: string): string {
  const resolved = path.resolve(workspaceOrIndexPath);
  if (
    resolved.endsWith(WORKSPACE_RELATIVE_PATHS.CONTEXT_INDEX) ||
    path.basename(resolved) === "index.md"
  ) {
    return resolved;
  }
  return path.join(resolved, WORKSPACE_RELATIVE_PATHS.CONTEXT_INDEX);
}

/**
 * Проверяет существование и читаемость корневого индекса графа знаний `.context/index.md`
 * в указанном рабочем пространстве через безопасный хелпер `readFileUtf8IfExistsSync`.
 */
export function hasWorkspaceContextGraph(workspacePath: string): boolean {
  const indexFilePath = resolveContextIndexPath(workspacePath);
  return readFileUtf8IfExistsSync(indexFilePath) !== null;
}

/**
 * Проверяет наличие `.context/index.md` через `readFileUtf8IfExistsSync` и форматирует время
 * последней модификации файла в локальном часовом поясе (`YYYY-MM-DD HH:mm`) для карточек CLI.
 * Поддерживает передачу как корня рабочей области `workspacePath`, так и прямого пути к `index.md`.
 */
export function getContextGraphLastUpdated(
  workspacePath: string,
): string | undefined {
  const indexFilePath = resolveContextIndexPath(workspacePath);
  const content = readFileUtf8IfExistsSync(indexFilePath);
  if (content === null) {
    return undefined;
  }

  try {
    const stats = fs.statSync(indexFilePath);
    const mtime = stats.mtime;
    const year = mtime.getFullYear();
    const month = String(mtime.getMonth() + 1).padStart(2, "0");
    const day = String(mtime.getDate()).padStart(2, "0");
    const hours = String(mtime.getHours()).padStart(2, "0");
    const minutes = String(mtime.getMinutes()).padStart(2, "0");
    return `${year}-${month}-${day} ${hours}:${minutes}`;
  } catch {
    return undefined;
  }
}

/**
 * Возвращает агрегированный статус присутствия и дату актуальности графа `.context/` для проекта.
 */
export function inspectContextGraph(
  workspacePath: string,
): ContextGraphInspection {
  const updatedAt = getContextGraphLastUpdated(workspacePath);
  if (updatedAt === undefined) {
    return {
      hasContextGraph: false,
    };
  }

  return {
    hasContextGraph: true,
    contextGraphUpdatedAt: updatedAt,
  };
}
