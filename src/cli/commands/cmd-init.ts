/**
 * Слой 1 (Host CLI Commands — Init Slice): обработчик команды `init` (DB-First Architecture).
 * Сначала синхронизирует `seeds/` и поднимает глобальный Docker-стек (`ensureGlobalStackUp`),
 * затем загружает живой каталог компонентов и правил детекции из PostgreSQL (`GET /api/harness/components`),
 * профилирует рабочее пространство (`inspectWorkspace`), запускает интерактивный визард (`runInitWizard`)
 * и материализует артефакты адаптера (`syncWorkspaceArtifacts`).
 */

import path from "node:path";
import process from "node:process";
import { styleText } from "node:util";
import { buildStackSignatureRulesFromComponents } from "../config/index.ts";
import { fetchHarnessComponents } from "../core-api/endpoints-harness.ts";
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
 * Выполняет полный DB-First цикл инициализации рабочего пространства проекта (`init`):
 * 1) синхронизирует `seeds/` и поднимает глобальный Docker-стек с PostgreSQL и Redis;
 * 2) запрашивает актуальный каталог компонентов и правил детекции стека из БД ядра;
 * 3) выполняет детекцию стека и запускает TUI-визард с индикацией активных скиллов и пустых заглушек;
 * 4) компилирует и материализует конфиги адаптеров, хуков и графа `.context/`.
 */
export async function handleInitCommand(
  ctx: ParsedCliContext,
  runtime: string,
): Promise<void> {
  const pipelineStart = Date.now();
  const progress = createPipelineProgress();
  progress.start(
    "[1/3] Syncing seeds & starting global Docker stack...",
  );
  const dockerResult = await ensureGlobalStackUp({
    onProgress: (phaseText: string) => progress.update(phaseText),
  });

  progress.update(
    "[2/3] Fetching live Harness² catalog & detector signatures from PostgreSQL...",
  );
  const catalogRes = await fetchHarnessComponents({
    workspacePath: process.cwd(),
  });
  const dbRules =
    catalogRes.ok && catalogRes.data
      ? buildStackSignatureRulesFromComponents(catalogRes.data.components)
      : undefined;

  const bootElapsedSec = ((Date.now() - pipelineStart) / 1000).toFixed(1);
  if (dockerResult.coreHealthy && catalogRes.ok) {
    progress.stop(
      styleText(
        "green",
        `✔ Global Docker stack & PostgreSQL catalog ready (${bootElapsedSec}s)`,
      ),
    );
  } else {
    progress.stop(
      styleText(
        "yellow",
        `⚠ Docker stack unavailable — using local seed catalog fallback (${bootElapsedSec}s)`,
      ),
    );
  }

  const profile = inspectWorkspace(
    process.cwd(),
    ctx.agent,
    process.env,
    dbRules,
  );
  const confirmedProfile = await runInitWizard(profile, {
    yes: ctx.yes,
    version: CLI_VERSION,
    runtime,
    catalogComponents:
      catalogRes.ok && catalogRes.data
        ? catalogRes.data.components
        : undefined,
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

  const compileProgress = createPipelineProgress();
  compileProgress.start(
    "[3/3] Compiling Harness² bundle & materializing workspace files...",
  );
  const { report, persistedInDb } = await syncWorkspaceArtifacts(
    confirmedProfile,
    manifest,
  );
  const totalElapsedSec = ((Date.now() - pipelineStart) / 1000).toFixed(1);
  if (dockerResult.coreHealthy && persistedInDb) {
    compileProgress.stop(
      styleText(
        "green",
        `✔ Workspace artifacts materialized & registered in PostgreSQL (${totalElapsedSec}s)`,
      ),
    );
  } else {
    compileProgress.stop(
      styleText(
        "yellow",
        `⚠ Workspace artifacts materialized in Offline Seed Mode (${totalElapsedSec}s)`,
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
