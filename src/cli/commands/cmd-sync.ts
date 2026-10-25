/**
 * Слой 1 (Host CLI Commands — Sync Slice): обработчик команды двусторонней синхронизации `sync`.
 * Оркестрирует гидратацию манифеста из ядра, материализацию `Harness²`, ингестию кастомных
 * скиллов/правил (`POST /api/sync/ingest`), 3-Way Hash Sync `.context/` и авто-исцеление графа.
 */

import process from "node:process";
import {
  type ContextHealResponse,
  fetchHarnessCatalog,
  healProjectContext,
  ingestWorkspaceComponents,
  registerProjectWithCore,
  syncWorkspaceContextNodes,
} from "../core-api/index.ts";
import { AUTO_AGENT_OPTION, inspectWorkspace } from "../detector/index.ts";
import {
  buildProfileOverridesPayload,
  hydrateManifestFromCore,
  loadProjectHarnessConfig,
  resolveEffectiveStackTags,
} from "../materializer/index.ts";
import {
  applyContextSyncResults,
  collectWorkspaceContextNodes,
  scanCustomWorkspaceComponents,
  syncWorkspaceArtifacts,
} from "../sync/index.ts";
import { renderSyncSummary } from "../tui/index.ts";
import type { ParsedCliContext } from "./arg-parser.ts";

/**
 * Выполняет двустороннюю синхронизацию рабочей директории с глобальным ядром (`sync`):
 * обновляет скомпилированные файлы адаптеров, загружает новые пользовательские компоненты,
 * синхронизирует узлы `.context/` по 3-Way Hash и выводит сводный отчёт в TUI.
 */
export async function handleSyncCommand(
  ctx: ParsedCliContext,
  _runtime: string,
): Promise<void> {
  const profile = inspectWorkspace(process.cwd(), ctx.agent);
  const baseManifest = loadProjectHarnessConfig(
    profile.workspacePath,
    profile.agent,
    profile.stackTags,
  );
  const coreCatalog = await fetchHarnessCatalog({
    workspacePath: profile.workspacePath,
  });
  if (!coreCatalog) {
    throw new Error(
      'Global Docker stack is offline. Run "mcp-coop-agentic-factory up" to start the Core DB before syncing.',
    );
  }

  const manifest = hydrateManifestFromCore(baseManifest, coreCatalog);
  if (ctx.agent !== AUTO_AGENT_OPTION) {
    manifest.agent = profile.agent;
  } else {
    profile.agent = manifest.agent;
  }
  profile.stackTags = resolveEffectiveStackTags(manifest);

  const { report, source } = await syncWorkspaceArtifacts(profile, manifest);

  const customCandidates = scanCustomWorkspaceComponents(
    profile.workspacePath,
    manifest,
    profile.stackTags,
  );
  const ingestReport = await ingestWorkspaceComponents({
    workspace_path: profile.workspacePath,
    stack_tags: profile.stackTags,
    components: customCandidates,
  });

  const localContextNodes = collectWorkspaceContextNodes(
    profile.workspacePath,
    manifest.context_base_hashes,
  );
  const contextSyncReport = await syncWorkspaceContextNodes({
    workspace_path: profile.workspacePath,
    nodes: localContextNodes,
  });

  if (contextSyncReport) {
    const appliedCtx = applyContextSyncResults(
      profile.workspacePath,
      contextSyncReport.items,
      manifest.context_base_hashes,
    );
    manifest.context_base_hashes = appliedCtx.nextBaseHashes;
    for (const writtenCtxPath of appliedCtx.writtenPaths) {
      if (!report.written.includes(writtenCtxPath)) {
        report.written.push(writtenCtxPath);
      }
    }
    await registerProjectWithCore({
      workspace_path: profile.workspacePath,
      project_name: profile.projectName,
      agent_type: manifest.agent,
      stack_tags: profile.stackTags,
      profile_overrides: buildProfileOverridesPayload(manifest),
    });
  }

  let healReport: ContextHealResponse | null = null;
  if (manifest.agent === "antigravity") {
    healReport = await healProjectContext({
      workspace_path: profile.workspacePath,
      agent_type: manifest.agent,
    });
  }

  renderSyncSummary({
    profile,
    manifest,
    report,
    source,
    healReport,
    ingestReport,
    contextSyncReport,
  });
}
