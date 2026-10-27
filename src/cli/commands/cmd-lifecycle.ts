/**
 * Слой 1 (Host CLI Commands — Lifecycle Slice): команды жизненного цикла глобального стека и UI.
 * Реализует обработчики `status`, `up`, `down` и `ui`, координируя проверку здоровья ядра,
 * оркестрацию `docker compose`, открытие браузера и рендеринг экранов в TUI.
 */

import { spawn } from "node:child_process";
import process from "node:process";
import { styleText } from "node:util";
import { intro } from "@clack/prompts";
import { resolveCoreBaseUrl } from "../config/index.ts";
import { fetchHarnessCatalog, listTier2Providers } from "../core-api/index.ts";
import {
  AUTO_AGENT_OPTION,
  inspectWorkspace,
  type WorkspaceProfile,
} from "../detector/index.ts";
import {
  checkCoreHealth,
  ensureGlobalStackUp,
  isPostgresKnowledgeEmpty,
  listAvailableBackupSnapshots,
  restorePostgresSnapshotFile,
  stopGlobalStackDown,
} from "../docker/index.ts";
import {
  hydrateManifestFromCore,
  loadProjectHarnessConfig,
  resolveEffectiveStackTags,
} from "../materializer/index.ts";
import {
  createPipelineProgress,
  promptSnapshotRestoreSelection,
  renderCliBanner,
  renderDownCommand,
  renderStatusCommand,
  renderUiCommand,
  renderUpCommand,
} from "../tui/index.ts";
import {
  CLI_VERSION,
  type ParsedCliContext,
  UI_ENDPOINT,
} from "./arg-parser.ts";

const OPEN_BROWSER_DARWIN = "open" as const;
const OPEN_BROWSER_LINUX = "xdg-open" as const;
const OPEN_BROWSER_WIN32 = "cmd" as const;

/**
 * Открывает указанный URL в системном браузере пользователя по умолчанию.
 * Запускает платформо-зависимую утилиту (`open` / `cmd /c start` / `xdg-open`)
 * в откреплённом (`detached`) процессе с `.unref()`, не блокируя завершение CLI.
 */
export async function launchSystemBrowser(url: string): Promise<boolean> {
  try {
    let child;
    if (process.platform === "darwin") {
      child = spawn(OPEN_BROWSER_DARWIN, [url], {
        detached: true,
        stdio: "ignore",
      });
    } else if (process.platform === "win32") {
      child = spawn(OPEN_BROWSER_WIN32, ["/c", "start", "", url], {
        detached: true,
        stdio: "ignore",
      });
    } else {
      child = spawn(OPEN_BROWSER_LINUX, [url], {
        detached: true,
        stdio: "ignore",
      });
    }
    child.on("error", () => {});
    child.unref();
    return true;
  } catch {
    return false;
  }
}

/**
 * Собирает диагностическую сводку состояния рабочей папки, манифеста, здоровья ядра
 * и пула провайдеров Tier 2 (`status`) и выводит карточку в TUI.
 */
export async function handleStatusCommand(
  ctx: ParsedCliContext,
  runtime: string,
): Promise<void> {
  const profile = inspectWorkspace(process.cwd(), ctx.agent);
  const baseManifest = loadProjectHarnessConfig(
    profile.workspacePath,
    profile.agent,
    profile.stackTags,
  );
  const [health, providers, coreCatalog] = await Promise.all([
    checkCoreHealth(),
    listTier2Providers(),
    fetchHarnessCatalog({ workspacePath: profile.workspacePath }),
  ]);
  const manifest = hydrateManifestFromCore(baseManifest, coreCatalog);
  const effectiveProfile: WorkspaceProfile = {
    ...profile,
    agent: ctx.agent !== AUTO_AGENT_OPTION ? profile.agent : manifest.agent,
    stackTags: resolveEffectiveStackTags(manifest),
  };
  renderStatusCommand(effectiveProfile, CLI_VERSION, runtime, {
    health,
    providers,
    manifest,
  });
}

/**
 * Проверяет доступность Python-ядра, формирует URL интерактивного граф-визуализатора
 * с привязкой к текущему `workspace` и открывает вкладку браузера (`ui`).
 */
export async function handleUiCommand(
  ctx: ParsedCliContext,
  runtime: string,
): Promise<void> {
  const profile = inspectWorkspace(process.cwd(), ctx.agent);
  const health = await checkCoreHealth();
  const baseUrl = resolveCoreBaseUrl();
  const targetUrl = `${baseUrl}${UI_ENDPOINT}?workspace=${encodeURIComponent(profile.workspacePath)}`;
  let openedBrowser = false;
  if (!ctx.noOpen && health.reachable) {
    openedBrowser = await launchSystemBrowser(targetUrl);
  }
  renderUiCommand({
    version: CLI_VERSION,
    runtime,
    url: targetUrl,
    openedBrowser,
    coreReachable: health.reachable,
    postgresConnected: health.postgres_connected,
  });
}

/**
 * Поднимает глобальный Docker-стек (`~/.mcp-coop-agentic-factory/`) с отображением
 * прогресс-спиннера, ожидает готовности `/health` и при наличии слепков в `.backups/`
 * предлагает выбрать слепок БД (или автоматически накатывает самый свежий слепок на пустую БД в `--yes`).
 */
export async function handleUpCommand(
  ctx: ParsedCliContext,
  runtime: string,
): Promise<void> {
  intro(renderCliBanner(CLI_VERSION, runtime, "Global Stack Startup"));
  const startedAt = Date.now();
  const progress = createPipelineProgress();
  progress.start(
    "[1/3] Syncing runtime & Docker stack files to ~/.mcp-coop-agentic-factory/...",
  );
  const dockerResult = await ensureGlobalStackUp({
    skipAutoRestore: true,
    onProgress: (phaseText: string) => progress.update(phaseText),
  });
  const elapsedSec = ((Date.now() - startedAt) / 1000).toFixed(1);
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
        `⚠ Failed to bring up global Docker stack (${elapsedSec}s)`,
      ),
    );
  }

  if (dockerResult.coreHealthy && dockerResult.health.postgres_connected) {
    const snapshots = listAvailableBackupSnapshots(process.cwd());
    if (snapshots.length > 0) {
      const isEmpty = (await isPostgresKnowledgeEmpty()) === true;
      const selection = await promptSnapshotRestoreSelection({
        snapshots,
        isDbEmpty: isEmpty,
        yes: ctx.yes,
      });
      if (selection.action === "cancelled") {
        process.exitCode = 1;
        return;
      }
      if (selection.action === "restore") {
        const restoreSpinner = createPipelineProgress();
        restoreSpinner.start(
          "Restoring selected PostgreSQL knowledge snapshot...",
        );
        const restoreRes = await restorePostgresSnapshotFile(
          selection.snapshotPath,
          {
            onProgress: (msg) => restoreSpinner.update(msg),
          },
        );
        if (restoreRes.restored) {
          restoreSpinner.stop(
            styleText("green", "✔ PostgreSQL knowledge snapshot restored"),
          );
        } else {
          restoreSpinner.stop(
            styleText(
              "yellow",
              `⚠ Snapshot restore failed: ${restoreRes.errorMessage ?? "unknown error"}`,
            ),
          );
        }
        dockerResult.restore = restoreRes;
      } else {
        dockerResult.restore = {
          restored: false,
          snapshotPath: snapshots[0]?.path,
          skippedReason: selection.reason,
        };
      }
    }
  }

  renderUpCommand(CLI_VERSION, runtime, dockerResult);
}

/**
 * Останавливает глобальный Docker-стек (`docker compose down`) с сохранением
 * персистентных томов PostgreSQL и Redis (`down`).
 */
export async function handleDownCommand(
  _ctx: ParsedCliContext,
  runtime: string,
): Promise<void> {
  const stopResult = stopGlobalStackDown();
  renderDownCommand(CLI_VERSION, runtime, stopResult);
}
