/**
 * Слой 1 (Host CLI Materializer): движок атомарной записи файлов и очистки устаревших артефактов.
 * Записывает скомпилированные файлы в рабочую директорию проекта, защищает от выхода за пределы
 * `workspaceRoot` и удаляет отключённые пользователем скиллы/правила вместе с пустыми директориями.
 */

import fs from "node:fs";
import path from "node:path";

import {
  pathExistsSync,
  readFileUtf8IfExistsSync,
  removeEmptyParentDirsSync,
  requireSafeWorkspacePath as resolveSafeWorkspacePath,
  writeFileAtomicSync,
} from "../common/index.ts";
import {
  type MaterializationReport,
  type MaterializeOptions,
  type ProjectHarnessManifest,
  type RenderedFilePayload,
  resolveDisabledSkillPrefix,
  resolveRuleSlugFromRelPath,
  resolveSkillSlugFromRelPath,
} from "./manifest-store.ts";
import { applyWriteStrategy } from "./mergers.ts";

/**
 * Удаляет пустые родительские директории (до 3 уровней вверх) после удаления файла скилла или правила,
 * не выходя за пределы `workspaceRoot`.
 */
export function cleanupEmptySkillParent(
  workspaceRoot: string,
  filePath: string,
): void {
  const resolvedRoot = path.resolve(workspaceRoot);
  removeEmptyParentDirsSync(path.dirname(filePath), resolvedRoot);
}

/**
 * Удаляет с диска устаревшие управляемые файлы из `manifest.managed_files`, не вошедшие в новую
 * выгрузку `incomingManagedSet`, а также принудительно очищает директории отключённых скиллов.
 */
export function pruneDisabledWorkspaceArtifacts(
  workspaceRoot: string,
  manifest: ProjectHarnessManifest,
  incomingManagedSet: ReadonlySet<string> = new Set(),
): string[] {
  const pruned: string[] = [];
  const prunedSet = new Set<string>();

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
      if (pathExistsSync(oldTargetPath)) {
        fs.rmSync(oldTargetPath, { force: true });
        cleanupEmptySkillParent(workspaceRoot, oldTargetPath);
        pruned.push(normalizedOld);
        prunedSet.add(normalizedOld);
      }
    } catch {
      continue;
    }
  }

  for (const disabledSlug of manifest.overrides.disabled_components) {
    const skillPrefix = resolveDisabledSkillPrefix(
      manifest.agent,
      disabledSlug,
    );
    const skillFileRel = `${skillPrefix}SKILL.md`;
    if (incomingManagedSet.has(skillFileRel) || prunedSet.has(skillFileRel)) {
      continue;
    }
    try {
      const targetSkillFile = resolveSafeWorkspacePath(
        workspaceRoot,
        skillFileRel,
      );
      if (pathExistsSync(targetSkillFile)) {
        fs.rmSync(targetSkillFile, { force: true });
        cleanupEmptySkillParent(workspaceRoot, targetSkillFile);
        pruned.push(skillFileRel);
        prunedSet.add(skillFileRel);
      }
    } catch {
      continue;
    }
  }

  manifest.managed_files = Array.from(incomingManagedSet).sort((a, b) =>
    a.localeCompare(b),
  );

  return pruned;
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
  const disabledHooks = options?.disabledHooks ?? [];
  const disabledComponents = new Set(
    (options?.disabledComponents ?? []).map((slug) =>
      slug.trim().toLowerCase(),
    ),
  );
  const incomingManagedSet = new Set<string>();

  for (const file of files) {
    const normalizedRel = file.relative_path
      .replace(/\\/g, "/")
      .replace(/^\/+/, "");

    if (disabledComponents.size > 0) {
      const skillSlug = resolveSkillSlugFromRelPath(normalizedRel);
      const ruleSlug = resolveRuleSlugFromRelPath(normalizedRel);
      if (
        (skillSlug && disabledComponents.has(skillSlug)) ||
        (ruleSlug && disabledComponents.has(ruleSlug))
      ) {
        skipped.push(normalizedRel);
        continue;
      }
    }

    const targetPath = resolveSafeWorkspacePath(workspaceRoot, normalizedRel);
    const existingContent = readFileUtf8IfExistsSync(targetPath);

    if (file.write_strategy === "overwrite") {
      incomingManagedSet.add(normalizedRel);
    }

    const outcome = applyWriteStrategy(
      file.write_strategy,
      existingContent,
      file.content,
      {
        relativePath: normalizedRel,
        disabledHooks,
      },
    );

    if (!outcome.shouldWrite) {
      skipped.push(normalizedRel);
      continue;
    }

    writeFileAtomicSync(targetPath, outcome.content);
    written.push(normalizedRel);
  }

  const pruned = options?.pruneFromManifest
    ? pruneDisabledWorkspaceArtifacts(
        workspaceRoot,
        options.pruneFromManifest,
        incomingManagedSet,
      )
    : [];

  return { written, skipped, pruned };
}
