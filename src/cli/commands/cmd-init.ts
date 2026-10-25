/**
 * Слой 1 (Host CLI Commands — Init Slice): обработчик команды `init`.
 * Координирует инспекцию проекта (`detector`), интерактивный визард (`tui`),
 * подъём глобального Docker-стека (`docker`), компиляцию и материализацию артефактов (`sync`).
 */

import path from "node:path";
import process from "node:process";
import { styleText } from "node:util";
import { inspectWorkspace } from "../detector/index.ts";
import { ensureGlobalStackUp } from "../docker/index.ts";
import {
  HARNESS_MANIFEST_REL_PATH,
  loadProjectHarnessConfig,
} from "../materializer/index.ts";
import { syncWorkspaceArtifacts } from "../sync/index.ts";
import {
  createPipelineProgress,
  renderInitSummary,
  runInitWizard,
} from "../tui/index.ts";
import { CLI_VERSION, type ParsedCliContext } from "./arg-parser.ts";

/**
 * Выполняет полный цикл инициализации рабочего пространства проекта (`init`):
 * подтверждает целевого агента и теги стека, поднимает глобальный Docker-стек
 * и материализует конфиги адаптеров, хуков и графа `.context/`.
 */
export async function handleInitCommand(
  ctx: ParsedCliContext,
  runtime: string,
): Promise<void> {
  const profile = inspectWorkspace(process.cwd(), ctx.agent);
  const confirmedProfile = await runInitWizard(profile, {
    yes: ctx.yes,
    version: CLI_VERSION,
    runtime,
  });
  if (!confirmedProfile) {
    process.exitCode = 1;
    return;
  }

  const manifest = loadProjectHarnessConfig(
    confirmedProfile.workspacePath,
    confirmedProfile.agent,
    confirmedProfile.stackTags,
  );
  manifest.agent = confirmedProfile.agent;
  manifest.stack.detected_tags = [...confirmedProfile.stackTags];

  const pipelineStart = Date.now();
  const progress = createPipelineProgress();
  progress.start(
    "[1/4] Syncing runtime & Docker stack files to ~/.mcp-coop-agentic-factory/...",
  );
  const dockerResult = await ensureGlobalStackUp({
    onProgress: (phaseText: string) => progress.update(phaseText),
  });
  progress.update(
    "[4/4] Compiling Harness² bundle & materializing workspace files...",
  );
  const { report, persistedInDb } = await syncWorkspaceArtifacts(
    confirmedProfile,
    manifest,
  );
  const elapsedSec = ((Date.now() - pipelineStart) / 1000).toFixed(1);
  if (dockerResult.coreHealthy) {
    progress.stop(
      styleText(
        "green",
        `✔ Global Docker stack & Core API online (${elapsedSec}s)`,
      ),
    );
  } else {
    progress.stop(
      styleText(
        "yellow",
        `⚠ Docker stack unavailable — initialized in Offline Seed Mode (${elapsedSec}s)`,
      ),
    );
  }

  renderInitSummary({
    profile: confirmedProfile,
    dockerResult,
    report,
    manifestPath: path.join(
      confirmedProfile.workspacePath,
      HARNESS_MANIFEST_REL_PATH,
    ),
    persistedInDb,
  });
}
