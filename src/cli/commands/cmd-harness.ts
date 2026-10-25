/**
 * Слой 1 (Host CLI Commands — Harness Slice): обработчик команды `harness`.
 * Управляет каталогом компонентов `Harness²`, локальными и глобальными оверрайдами
 * скиллов/правил/хуков/тегов и консолидацией дублирующихся компонентов в ядре.
 */

import process from "node:process";
import {
  consolidateHarnessComponents,
  fetchHarnessCatalog,
  patchGlobalComponentStatus,
} from "../core-api/index.ts";
import { GLOBAL_STACK_TAG, inspectWorkspace } from "../detector/index.ts";
import { resolveCanonicalHookEvent } from "../hooks/index.ts";
import {
  hydrateManifestFromCore,
  loadProjectHarnessConfig,
  resolveEffectiveStackTags,
} from "../materializer/index.ts";
import { syncWorkspaceArtifacts } from "../sync/index.ts";
import {
  renderConsolidationView,
  renderHarnessCatalogView,
  renderQuickMutationResult,
  renderSyncSummary,
} from "../tui/index.ts";
import {
  CLI_VERSION,
  HARNESS_SUBCOMMANDS,
  type ParsedCliContext,
} from "./arg-parser.ts";

/**
 * Исполняет подкоманды `harness` (`list`, `enable`, `disable`, `hook-enable`,
 * `hook-disable`, `tag-add`, `tag-exclude`, `consolidate`) и перекомпилирует
 * локальные артефакты при изменении проектного манифеста.
 */
export async function handleHarnessCommand(
  ctx: ParsedCliContext,
  runtime: string,
): Promise<void> {
  const subcmd = (ctx.subargs[0] ?? "list").trim().toLowerCase();
  const targetArg = (ctx.subargs[1] ?? "").trim().toLowerCase();
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
      'Global Docker stack is offline. Run "mcp-coop-agentic-factory up" to start the Core DB.',
    );
  }
  const manifest = hydrateManifestFromCore(baseManifest, coreCatalog);
  profile.agent = manifest.agent;
  profile.stackTags = resolveEffectiveStackTags(manifest);

  if (subcmd === "list") {
    let items = coreCatalog.components;
    if (ctx.typeFilter) {
      const wantedType = ctx.typeFilter.trim().toLowerCase();
      items = items.filter((i) => i.component_type.toLowerCase() === wantedType);
    }
    if (ctx.tagFilter) {
      const wantedTag = ctx.tagFilter.trim().toLowerCase();
      items = items.filter((i) =>
        i.stack_tags.some((t) => t.toLowerCase() === wantedTag),
      );
    }
    renderHarnessCatalogView({
      version: CLI_VERSION,
      runtime,
      items,
      manifest,
      showAll: ctx.all,
    });
    return;
  }

  if (subcmd === "consolidate") {
    const consolidateReport = await consolidateHarnessComponents({
      stack_tags: profile.stackTags,
      component_type: ctx.typeFilter,
      dry_run: ctx.dryRun,
    });
    if (!consolidateReport) {
      throw new Error("Failed to execute Harness² consolidation on Core API.");
    }
    renderConsolidationView({
      version: CLI_VERSION,
      runtime,
      report: consolidateReport,
    });
    return;
  }

  if (!targetArg) {
    throw new Error(`Missing target argument for "harness ${subcmd}".`);
  }

  if ((subcmd === "enable" || subcmd === "disable") && ctx.global) {
    const enableFlag = subcmd === "enable";
    const patched = await patchGlobalComponentStatus(targetArg, enableFlag);
    if (!patched) {
      throw new Error(
        `Failed to update global component "${targetArg}" (Core offline or slug not found).`,
      );
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Global Harness² Update",
      `Global component "${targetArg}" ${enableFlag ? "enabled (composed)" : "disabled"} in PostgreSQL.`,
    );
    return;
  }

  if (subcmd === "enable") {
    manifest.overrides.disabled_components =
      manifest.overrides.disabled_components.filter((c) => c !== targetArg);
    if (!manifest.overrides.enabled_components.includes(targetArg)) {
      manifest.overrides.enabled_components.push(targetArg);
    }
  } else if (subcmd === "disable") {
    manifest.overrides.enabled_components =
      manifest.overrides.enabled_components.filter((c) => c !== targetArg);
    if (!manifest.overrides.disabled_components.includes(targetArg)) {
      manifest.overrides.disabled_components.push(targetArg);
    }
  } else if (subcmd === "hook-enable" || subcmd === "hook-disable") {
    const hookEvent = resolveCanonicalHookEvent(targetArg);
    if (!hookEvent) {
      throw new Error(`Unsupported hook event "${targetArg}".`);
    }
    manifest.overrides.disabled_hooks =
      manifest.overrides.disabled_hooks.filter((h) => h !== hookEvent);
    if (subcmd === "hook-disable") {
      manifest.overrides.disabled_hooks.push(hookEvent);
    }
  } else if (subcmd === "tag-add") {
    manifest.stack.excluded_tags = manifest.stack.excluded_tags.filter(
      (t) => t !== targetArg,
    );
    if (!manifest.stack.extra_tags.includes(targetArg)) {
      manifest.stack.extra_tags.push(targetArg);
    }
  } else if (subcmd === "tag-exclude") {
    if (targetArg === GLOBAL_STACK_TAG) {
      throw new Error(`Cannot exclude the mandatory "${GLOBAL_STACK_TAG}" tag.`);
    }
    manifest.stack.extra_tags = manifest.stack.extra_tags.filter(
      (t) => t !== targetArg,
    );
    if (!manifest.stack.excluded_tags.includes(targetArg)) {
      manifest.stack.excluded_tags.push(targetArg);
    }
  } else {
    throw new Error(
      `Unknown harness subcommand "${subcmd}". Supported: ${HARNESS_SUBCOMMANDS.join(", ")}.`,
    );
  }

  const { report, source } = await syncWorkspaceArtifacts(profile, manifest);
  renderSyncSummary({ profile, manifest, report, source });
}
