/**
 * Слой конфигурации CLI (`src/cli/config`): реестр поддерживаемых ИИ-агентов, диалектов протокола
 * и канонических событий жизненных хуков. Выступает единым источником истины (Single Source of Truth)
 * для детектора окружения, компилятора профилей `Harness²` и локального прокси-раннера хуков.
 */

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

export const AGENT_METADATA = {
  antigravity: {
    id: "antigravity",
    displayName: "Google Antigravity",
    configHint: ".agents/plugins & hooks.json",
    dialect: "ANTIGRAVITY_PROTOJSON",
    workspaceMarkers: [".agents", ".gemini/antigravity"],
  },
  "claude-code": {
    id: "claude-code",
    displayName: "Claude Code",
    configHint: ".claude/settings.json & CLAUDE.md",
    dialect: "CLAUDE_CODEX_V1",
    workspaceMarkers: [".claude", "CLAUDE.md"],
  },
  codex: {
    id: "codex",
    displayName: "OpenAI Codex CLI",
    configHint: ".codex/hooks.json & AGENTS.md",
    dialect: "CLAUDE_CODEX_V1",
    workspaceMarkers: [".codex"],
  },
  cursor: {
    id: "cursor",
    displayName: "Cursor",
    configHint: ".cursor/rules/*.mdc & hooks.json",
    dialect: "CURSOR_V1",
    workspaceMarkers: [".cursor", ".cursorrules"],
  },
  opencode: {
    id: "opencode",
    displayName: "OpenCode",
    configHint: ".opencode/plugins & opencode.json",
    dialect: "CLAUDE_CODEX_V1",
    workspaceMarkers: [".opencode"],
  },
} as const satisfies Record<AgentType, AgentDescriptor>;

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
