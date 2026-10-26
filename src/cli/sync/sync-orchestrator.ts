/**
 * Слой 1 (Host CLI Sync — Orchestrator): высокоуровневый оркестратор синхронизации артефактов проекта.
 * Координирует обновление глобального Docker-стека, создание каркаса `.context/`, компиляцию бандла
 * в Python-ядре (с автономным фолбэком при оффлайне) и атомарную материализацию файлов адаптера.
 */

import { OFFLINE_ORCHESTRATOR_SLUG } from "../config/index.ts";
import { registerProjectWithCore } from "../core-api/index.ts";
import { type AgentId, type WorkspaceProfile } from "../detector/index.ts";
import { syncGlobalFactoryStackFiles } from "../docker/index.ts";
import {
  buildOfflineBootstrapSkillMarkdown,
  buildOfflineFallbackFiles,
  buildProfileOverridesPayload,
  ensureContextGraphScaffold,
  FACTORY_HOOK_SIGNATURE,
  materializeFiles,
  resolveEffectiveStackTags,
  saveProjectHarnessConfig,
  type MaterializationReport,
  type ProjectHarnessManifest,
  type RenderedFilePayload,
} from "../materializer/index.ts";

export const HARNESS_ORCHESTRATOR_SLUG = OFFLINE_ORCHESTRATOR_SLUG;
const OFFLINE_BUNDLE_SOURCE = "seed_fallback" as const;
const DEFAULT_COMPILER_SOURCE = "core_compiler" as const;

export interface WorkspaceArtifactsSyncResult {
  report: MaterializationReport;
  persistedInDb: boolean;
  source: string;
}

/**
 * Вычисляет относительный путь для оффлайн-материализации скилла в зависимости от целевого ИИ-агента.
 */
export function resolveOfflineSkillPath(agent: AgentId, slug: string): string {
  if (agent === "antigravity") {
    return `.agents/plugins/${FACTORY_HOOK_SIGNATURE}/skills/${slug}/SKILL.md`;
  }
  if (agent === "claude-code") {
    return `.claude/skills/${slug}/SKILL.md`;
  }
  return `.agents/skills/${slug}/SKILL.md`;
}

/**
 * Синхронизирует артефакты рабочей директории: создаёт каркас `.context/`, запрашивает скомпилированный
 * бандл у ядра (`registerProjectWithCore`) или генерирует автономный фолбэк, материализует файлы
 * с очисткой отключённых скиллов/хуков и сохраняет обновлённый `.context/harness.json`.
 */
export async function syncWorkspaceArtifacts(
  profile: WorkspaceProfile,
  manifest: ProjectHarnessManifest,
): Promise<WorkspaceArtifactsSyncResult> {
  syncGlobalFactoryStackFiles();
  const effectiveTags = resolveEffectiveStackTags(manifest);
  ensureContextGraphScaffold(
    profile.workspacePath,
    profile.projectName,
    effectiveTags,
  );

  const coreResponse = await registerProjectWithCore({
    workspace_path: profile.workspacePath,
    project_name: profile.projectName,
    agent_type: manifest.agent,
    stack_tags: effectiveTags,
    profile_overrides: buildProfileOverridesPayload(manifest),
  });

  const artifactObj =
    coreResponse && typeof coreResponse.artifact === "object"
      ? (coreResponse.artifact as Record<string, unknown>)
      : null;

  let files: RenderedFilePayload[];
  let source: string = OFFLINE_BUNDLE_SOURCE;
  const persistedInDb = Boolean(coreResponse?.persisted_in_db);

  if (artifactObj && Array.isArray(artifactObj.files)) {
    files = artifactObj.files as RenderedFilePayload[];
    source =
      typeof artifactObj.source === "string"
        ? artifactObj.source
        : DEFAULT_COMPILER_SOURCE;
  } else {
    files = [...buildOfflineFallbackFiles(manifest.agent, effectiveTags)];
    if (
      !manifest.overrides.disabled_components.includes(HARNESS_ORCHESTRATOR_SLUG)
    ) {
      files.push({
        relative_path: resolveOfflineSkillPath(
          manifest.agent,
          HARNESS_ORCHESTRATOR_SLUG,
        ),
        content: buildOfflineBootstrapSkillMarkdown(effectiveTags),
        write_strategy: "overwrite",
      });
    }
  }

  const report = materializeFiles(profile.workspacePath, files, {
    disabledHooks: manifest.overrides.disabled_hooks,
    pruneFromManifest: manifest,
  });

  saveProjectHarnessConfig(profile.workspacePath, manifest);
  return { report, persistedInDb, source };
}
