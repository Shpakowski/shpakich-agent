/**
 * Слой конфигурации CLI (`src/cli/config`): реестр поддерживаемых ИИ-агентов, диалектов протокола
 * и канонических событий жизненных хуков. Метаданные агентов динамически загружаются из
 * `seeds/harness-seed.json` (`Single Source of Truth`), сохраняя полную статическую типизацию `AgentType`.
 */

import { loadHarnessSeedCatalog } from "./stack-signatures.ts";

export const AGENT_TYPES = [
  "antigravity",
  "claude-code",
  "codex",
  "cursor",
  "opencode",
] as const;

export type AgentType = (typeof AGENT_TYPES)[number];

export const AGENT_DIALECTS = [
  "ANTIGRAVITY_PROTOJSON",
  "CLAUDE_CODEX_V1",
  "CURSOR_V1",
] as const;

export type AgentDialect = (typeof AGENT_DIALECTS)[number];

export const HOOK_EVENT_TYPES = [
  "SessionStart",
  "PreToolUse",
  "PostToolUse",
  "BeforeSubmitPrompt",
  "Stop",
] as const;

export type HookEventType = (typeof HOOK_EVENT_TYPES)[number];

/**
 * Полный дескриптор поддерживаемого ИИ-агента: связывает идентификатор CLI с именем для TUI,
 * подсказкой по конфигурационным файлам, диалектом хуков и маркерами автоопределения в репозитории.
 */
export interface AgentDescriptor {
  readonly id: AgentType;
  readonly displayName: string;
  readonly configHint: string;
  readonly dialect: AgentDialect;
  readonly workspaceMarkers: readonly string[];
}

function resolveAgentDialectFromSeed(
  rawDialect: string | undefined,
  agentId: AgentType,
): AgentDialect {
  const normalized = (rawDialect ?? "").trim().toUpperCase();
  if (normalized.includes("ANTIGRAVITY") || normalized.includes("AGY")) {
    return "ANTIGRAVITY_PROTOJSON";
  }
  if (normalized.includes("CURSOR")) {
    return "CURSOR_V1";
  }
  if (
    normalized.includes("CLAUDE") ||
    normalized.includes("CODEX") ||
    normalized.includes("OPENCODE")
  ) {
    return "CLAUDE_CODEX_V1";
  }
  if (agentId === "antigravity") {
    return "ANTIGRAVITY_PROTOJSON";
  }
  if (agentId === "cursor") {
    return "CURSOR_V1";
  }
  return "CLAUDE_CODEX_V1";
}

function buildAgentMetadataFromSeed(): Readonly<
  Record<AgentType, AgentDescriptor>
> {
  const seedAgents = loadHarnessSeedCatalog().agents;
  const byId = new Map<string, (typeof seedAgents)[number]>();
  for (const entry of seedAgents) {
    if (typeof entry.agent_type === "string") {
      byId.set(entry.agent_type.trim().toLowerCase(), entry);
    }
  }

  const buildEntry = (agentId: AgentType): AgentDescriptor => {
    const seedEntry = byId.get(agentId);
    const displayName =
      seedEntry?.cli_display_name ?? seedEntry?.display_name ?? agentId;
    const configHint = seedEntry?.config_hint ?? "";
    const workspaceMarkers = Array.isArray(seedEntry?.workspace_markers)
      ? seedEntry.workspace_markers.filter(
          (m): m is string => typeof m === "string" && m.trim().length > 0,
        )
      : [];
    const dialect = resolveAgentDialectFromSeed(
      seedEntry?.hooks?.dialect,
      agentId,
    );

    return {
      id: agentId,
      displayName,
      configHint,
      dialect,
      workspaceMarkers,
    };
  };

  return {
    antigravity: buildEntry("antigravity"),
    "claude-code": buildEntry("claude-code"),
    codex: buildEntry("codex"),
    cursor: buildEntry("cursor"),
    opencode: buildEntry("opencode"),
  };
}

export const AGENT_METADATA: Readonly<Record<AgentType, AgentDescriptor>> =
  buildAgentMetadataFromSeed();

export const AGENT_DISPLAY_NAMES: Readonly<Record<AgentType, string>> = {
  antigravity: AGENT_METADATA.antigravity.displayName,
  "claude-code": AGENT_METADATA["claude-code"].displayName,
  codex: AGENT_METADATA.codex.displayName,
  cursor: AGENT_METADATA.cursor.displayName,
  opencode: AGENT_METADATA.opencode.displayName,
} as const;

export const AGENT_CONFIG_HINTS: Readonly<Record<AgentType, string>> = {
  antigravity: AGENT_METADATA.antigravity.configHint,
  "claude-code": AGENT_METADATA["claude-code"].configHint,
  codex: AGENT_METADATA.codex.configHint,
  cursor: AGENT_METADATA.cursor.configHint,
  opencode: AGENT_METADATA.opencode.configHint,
} as const;

export const SUPPORTED_AGENTS_LABEL: string = AGENT_TYPES.join(", ");

const HOOK_EVENT_LOOKUP: Readonly<Record<string, HookEventType>> = {
  sessionstart: "SessionStart",
  "session-start": "SessionStart",
  session_start: "SessionStart",
  pretooluse: "PreToolUse",
  "pre-tool-use": "PreToolUse",
  pre_tool_use: "PreToolUse",
  posttooluse: "PostToolUse",
  "post-tool-use": "PostToolUse",
  post_tool_use: "PostToolUse",
  beforesubmitprompt: "BeforeSubmitPrompt",
  "before-submit-prompt": "BeforeSubmitPrompt",
  before_submit_prompt: "BeforeSubmitPrompt",
  preinvocation: "BeforeSubmitPrompt",
  "pre-invocation": "BeforeSubmitPrompt",
  pre_invocation: "BeforeSubmitPrompt",
  userpromptsubmit: "BeforeSubmitPrompt",
  "user-prompt-submit": "BeforeSubmitPrompt",
  user_prompt_submit: "BeforeSubmitPrompt",
  stop: "Stop",
  agentstop: "Stop",
  "agent-stop": "Stop",
  agent_stop: "Stop",
  subagentstop: "Stop",
  "subagent-stop": "Stop",
  subagent_stop: "Stop",
} as const;

/**
 * Проверяет, принадлежит ли произвольная строка каноническому объединению `AgentType`.
 */
export function isValidAgentType(value: string): value is AgentType {
  return (AGENT_TYPES as readonly string[]).includes(value);
}

/**
 * Приводит вендорское или CLI-имя жизненного хука (включая kebab-case, snake_case и алиасы
 * `PreInvocation` / `UserPromptSubmit`) к каноническому типу `HookEventType`.
 */
export function normalizeHookEventName(rawEvent: string): HookEventType | null {
  const normalizedKey = rawEvent.trim().toLowerCase();
  if (!normalizedKey) {
    return null;
  }
  return HOOK_EVENT_LOOKUP[normalizedKey] ?? null;
}
