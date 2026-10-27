/**
 * Слой 1 (Host CLI Commands — Providers Slice): обработчик команды `providers`.
 * Управляет пулом внешних LLM-провайдеров (`RuntimeProviderPool` Tier 2) через REST API ядра:
 * просмотр статуса, регистрация, смена приоритетов, сброс Circuit Breaker и удаление.
 */

import {
  deleteTier2Provider,
  listTier2Providers,
  patchTier2Provider,
  upsertTier2Provider,
} from "../core-api/index.ts";
import { renderProvidersView, renderQuickMutationResult } from "../tui/index.ts";
import {
  CLI_VERSION,
  PROVIDERS_SUBCOMMANDS,
  type ParsedCliContext,
} from "./arg-parser.ts";

export const DEFAULT_PROVIDER_DIALECT = "openai_decisions" as const;
export const DEFAULT_PROVIDER_URL = "https://api.openai.com/v1/decisions" as const;
export const DEFAULT_PROVIDER_MODEL = "gpt-6-luna" as const;
export const DEFAULT_PROVIDER_PRIORITY = 100 as const;

export const DIALECT_DEFAULTS: Readonly<
  Record<
    string,
    {
      readonly url: string;
      readonly model: string;
      readonly timeoutMs: number;
      readonly kindLabel: string;
    }
  >
> = {
  openai_decisions: {
    url: DEFAULT_PROVIDER_URL,
    model: DEFAULT_PROVIDER_MODEL,
    timeoutMs: 600,
    kindLabel: "Tier 2 Decision",
  },
  jev_clef: {
    url: DEFAULT_PROVIDER_URL,
    model: DEFAULT_PROVIDER_MODEL,
    timeoutMs: 600,
    kindLabel: "Tier 2 Decision",
  },
  openai_chat_schema: {
    url: "https://api.openai.com/v1/chat/completions",
    model: "gpt-4o-mini",
    timeoutMs: 1200,
    kindLabel: "Tier 2 Decision",
  },
  gemini_native: {
    url: "https://generativelanguage.googleapis.com/v1beta",
    model: "models/gemini-embedding-2",
    timeoutMs: 1500,
    kindLabel: "Embedding",
  },
  openai_compatible: {
    url: "https://api.openai.com/v1/embeddings",
    model: "text-embedding-3-small",
    timeoutMs: 1500,
    kindLabel: "Embedding",
  },
};

/**
 * Исполняет подкоманды `providers` (`list`, `add`, `enable`, `disable`,
 * `priority`, `remove`, `reset`) и рендерит результат мутации в TUI.
 */
export async function handleProvidersCommand(
  ctx: ParsedCliContext,
  runtime: string,
): Promise<void> {
  const subcmd = (ctx.subargs[0] ?? "list").trim().toLowerCase();
  const providerName = (ctx.subargs[1] ?? "").trim();

  if (subcmd === "list") {
    const providers = (await listTier2Providers()) ?? [];
    renderProvidersView({ version: CLI_VERSION, runtime, providers });
    return;
  }

  if (!providerName) {
    throw new Error(`Missing provider name for "providers ${subcmd}".`);
  }

  if (subcmd === "add") {
    const resolvedDialect = (ctx.dialect ?? DEFAULT_PROVIDER_DIALECT)
      .trim()
      .toLowerCase();
    const preset = DIALECT_DEFAULTS[resolvedDialect];
    const resolvedUrl = ctx.url ?? preset?.url ?? DEFAULT_PROVIDER_URL;
    const resolvedModel = ctx.model ?? preset?.model ?? DEFAULT_PROVIDER_MODEL;
    const resolvedTimeoutMs = preset?.timeoutMs ?? 600;
    const kindLabel = preset?.kindLabel ?? "Runtime";

    const created = await upsertTier2Provider({
      name: providerName,
      dialect: resolvedDialect,
      base_url: resolvedUrl,
      model: resolvedModel,
      api_key: ctx.key ?? "",
      priority: Number(ctx.priority ?? DEFAULT_PROVIDER_PRIORITY),
      timeout_ms: resolvedTimeoutMs,
      enabled: true,
    });
    if (!created) {
      throw new Error(`Failed to register provider "${providerName}".`);
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      `${kindLabel} Provider Registered`,
      `Provider "${providerName}" (${resolvedDialect} · ${resolvedModel}) added/updated in RuntimeProviderPool.`,
    );
    return;
  }

  if (subcmd === "enable" || subcmd === "disable") {
    const enabled = subcmd === "enable";
    const res = await patchTier2Provider(providerName, { enabled });
    if (!res) {
      throw new Error(`Failed to ${subcmd} provider "${providerName}".`);
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Tier 2 Provider Updated",
      `Provider "${providerName}" ${enabled ? "enabled" : "disabled"}.`,
    );
    return;
  }

  if (subcmd === "priority") {
    const rawPrio = ctx.subargs[2] ?? ctx.priority;
    if (!rawPrio || Number.isNaN(Number(rawPrio))) {
      throw new Error(
        "Numeric priority is required for `providers priority <name> <prio>`.",
      );
    }
    const res = await patchTier2Provider(providerName, {
      priority: Number(rawPrio),
    });
    if (!res) {
      throw new Error(`Failed to update priority for "${providerName}".`);
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Tier 2 Priority Updated",
      `Provider "${providerName}" priority set to ${Number(rawPrio)}.`,
    );
    return;
  }

  if (subcmd === "reset") {
    const res = await patchTier2Provider(providerName, { reset_circuit: true });
    if (!res) {
      throw new Error(`Failed to reset Circuit Breaker for "${providerName}".`);
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Circuit Breaker Reset",
      `Provider "${providerName}" cooldown cleared.`,
    );
    return;
  }

  if (subcmd === "remove") {
    const ok = await deleteTier2Provider(providerName);
    if (!ok) {
      throw new Error(`Failed to remove provider "${providerName}".`);
    }
    renderQuickMutationResult(
      CLI_VERSION,
      runtime,
      "Tier 2 Provider Removed",
      `Provider "${providerName}" deleted from pool.`,
    );
    return;
  }

  throw new Error(
    `Unknown providers subcommand "${subcmd}". Supported: ${PROVIDERS_SUBCOMMANDS.join(", ")}.`,
  );
}
