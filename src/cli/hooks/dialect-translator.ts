/**
 * Слой 3C (Host CLI Hooks Runner): канонизация имён событий жизненных хуков и трансляция
 * вердиктов аналитического ядра (`CoreHookResponse`) в агент-специфичный JSON-протокол `stdout`
 * для 5 поддерживаемых агентов (`antigravity`, `claude-code`, `cursor`, `codex`, `opencode`).
 */

import { isJsonObject } from "../common/index.ts";
import type { AgentType } from "../config/index.ts";
import type {
  CoreHookResponsePayload,
  CoreHookSyncFilePayload,
} from "../core-api/index.ts";

export const CANONICAL_HOOK_EVENTS = [
  "session-start",
  "pre-tool-use",
  "post-tool-use",
  "pre-invocation",
  "stop",
] as const;

export type CanonicalHookEvent = (typeof CANONICAL_HOOK_EVENTS)[number];
export type HookEventName = CanonicalHookEvent;

export const FALLBACK_ALLOW_LINE = '{"decision":"allow"}\n' as const;

const DEFAULT_DENY_REASON =
  "Action blocked by MCP-COOP Harness policy guardrail." as const;

export const HOOK_EVENT_ALIASES: Readonly<Record<string, CanonicalHookEvent>> =
  {
    "session-start": "session-start",
    sessionstart: "session-start",
    session_start: "session-start",
    "pre-tool-use": "pre-tool-use",
    pretooluse: "pre-tool-use",
    pre_tool_use: "pre-tool-use",
    beforetool: "pre-tool-use",
    "before-tool": "pre-tool-use",
    before_tool: "pre-tool-use",
    beforeshellexecution: "pre-tool-use",
    "post-tool-use": "post-tool-use",
    posttooluse: "post-tool-use",
    post_tool_use: "post-tool-use",
    aftertool: "post-tool-use",
    "after-tool": "post-tool-use",
    after_tool: "post-tool-use",
    afterfileedit: "post-tool-use",
    "pre-invocation": "pre-invocation",
    preinvocation: "pre-invocation",
    pre_invocation: "pre-invocation",
    userpromptsubmit: "pre-invocation",
    "user-prompt-submit": "pre-invocation",
    user_prompt_submit: "pre-invocation",
    beforesubmitprompt: "pre-invocation",
    "before-submit-prompt": "pre-invocation",
    before_submit_prompt: "pre-invocation",
    beforeagent: "pre-invocation",
    "before-agent": "pre-invocation",
    before_agent: "pre-invocation",
    stop: "stop",
    agentstop: "stop",
    "agent-stop": "stop",
    agent_stop: "stop",
    subagentstop: "stop",
    "subagent-stop": "stop",
    subagent_stop: "stop",
    afteragent: "stop",
    "after-agent": "stop",
    after_agent: "stop",
  } as const;

export interface CoreHookSyncFile extends CoreHookSyncFilePayload {
  readonly path: string;
  readonly content: string;
  readonly executable?: boolean | undefined;
}

export interface CoreHookResponse extends CoreHookResponsePayload {
  readonly decision?: string | undefined;
  readonly reason?: string | undefined;
  readonly stopReason?: string | undefined;
  readonly permission?: string | undefined;
  readonly user_message?: string | undefined;
  readonly agent_message?: string | undefined;
  readonly systemMessage?: string | undefined;
  readonly additionalContext?: string | undefined;
  readonly hookSpecificOutput?: Record<string, unknown> | undefined;
  readonly sync_files?: readonly CoreHookSyncFile[] | undefined;
  readonly _syncFiles?: CoreHookSyncFilePayload[] | undefined;
}

export interface HookExecutionResult {
  readonly event: CanonicalHookEvent | null;
  readonly agent: AgentType;
  readonly workspacePath: string;
  readonly stdoutJson: Record<string, unknown>;
  readonly stdoutLine: string;
  readonly exitCode: 0;
  readonly syncedFilesCount: number;
  readonly blockedLocally: boolean;
  readonly failOpenTriggered: boolean;
}

/**
 * Проверяет, принадлежит ли строка каноническому списку CLI-событий `CANONICAL_HOOK_EVENTS`.
 */
export function isCanonicalHookEvent(
  value: string,
): value is CanonicalHookEvent {
  return (CANONICAL_HOOK_EVENTS as readonly string[]).includes(value);
}

/**
 * Алиас `isCanonicalHookEvent` для обратной совместимости.
 */
export const isHookEventName = isCanonicalHookEvent;

/**
 * Нормализует входное имя события хука (с учётом вендорских алиасов `SessionStart`, `PreToolUse`,
 * `UserPromptSubmit`, `BeforeSubmitPrompt`, `SubagentStop`) в канонический тип `CanonicalHookEvent`.
 */
export function resolveCanonicalHookEvent(
  rawEvent: string,
): CanonicalHookEvent | null {
  const cleaned = rawEvent.trim().toLowerCase();
  if (!cleaned) {
    return null;
  }
  if (isCanonicalHookEvent(cleaned)) {
    return cleaned;
  }
  return HOOK_EVENT_ALIASES[cleaned] ?? null;
}

/**
 * Формирует диалект-специфичный разрешающий ответ хука для автономного режима (Fail-Open),
 * когда глобальный Docker-стек временно остановлен или недоступен.
 */
export function buildDialectAllowResponse(
  event: CanonicalHookEvent,
  agent: AgentType,
): Record<string, unknown> {
  if (event === "post-tool-use") {
    return {};
  }

  if (event === "pre-tool-use") {
    if (agent === "claude-code" || agent === "codex") {
      return {
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "allow",
        },
      };
    }
    if (agent === "cursor") {
      return { permission: "allow" };
    }
    return { decision: "allow" };
  }

  if (event === "session-start" || event === "pre-invocation") {
    return {};
  }

  if (agent === "antigravity") {
    return { decision: "allow" };
  }
  return {};
}

/**
 * Строит безопасный Fail-Open объект для вывода в `stdout` при недоступности Docker-ядра
 * или неизвестном имени события хука.
 */
export function buildFailOpenStdout(
  event: CanonicalHookEvent | null,
  agent: AgentType,
): Record<string, unknown> {
  if (event === null) {
    return { decision: "allow" };
  }
  return buildDialectAllowResponse(event, agent);
}

function stripInternalSyncFields(
  response: CoreHookResponse,
): Record<string, unknown> {
  const cleaned: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(response)) {
    if (key === "_syncFiles" || key === "sync_files") {
      continue;
    }
    if (value !== undefined) {
      cleaned[key] = value;
    }
  }
  return cleaned;
}

/**
 * Транслирует ответ аналитического ядра (`CoreHookResponse`) или локального перехватчика
 * в агент-специфичный JSON-протокол `stdout` (`hookSpecificOutput` для `claude-code`/`codex`,
 * `permission` для `cursor`, `decision` для `antigravity`/`opencode`).
 */
export function translateToAgentStdout(
  event: CanonicalHookEvent,
  agent: AgentType,
  response: CoreHookResponse,
): Record<string, unknown> {
  const cleaned = stripInternalSyncFields(response);
  const rawDecision =
    typeof response.decision === "string"
      ? response.decision.toLowerCase()
      : undefined;
  const rawPermission =
    typeof response.permission === "string"
      ? response.permission.toLowerCase()
      : undefined;

  const isDenied =
    rawDecision === "deny" ||
    rawDecision === "block" ||
    rawPermission === "deny";

  if (isDenied) {
    const reason =
      (typeof response.reason === "string" && response.reason.trim()) ||
      (typeof response.stopReason === "string" && response.stopReason.trim()) ||
      (typeof response.agent_message === "string" &&
        response.agent_message.trim()) ||
      (typeof response.user_message === "string" &&
        response.user_message.trim()) ||
      DEFAULT_DENY_REASON;

    if (event === "pre-tool-use") {
      if (agent === "claude-code" || agent === "codex") {
        if (isJsonObject(cleaned["hookSpecificOutput"])) {
          return cleaned;
        }
        const result: Record<string, unknown> = {
          hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: reason,
          },
        };
        if (typeof response.systemMessage === "string") {
          result["systemMessage"] = response.systemMessage;
        }
        return result;
      }

      if (agent === "cursor") {
        return {
          permission: "deny",
          user_message:
            typeof response.user_message === "string"
              ? response.user_message
              : reason,
          agent_message:
            typeof response.agent_message === "string"
              ? response.agent_message
              : reason,
        };
      }

      const result: Record<string, unknown> = {
        ...cleaned,
        decision: "deny",
        reason,
      };
      return result;
    }

    if (event === "stop") {
      return {
        ...cleaned,
        decision: "block",
        reason,
      };
    }

    return {
      ...cleaned,
      decision: "deny",
      reason,
    };
  }

  if (Object.keys(cleaned).length === 0) {
    return buildDialectAllowResponse(event, agent);
  }

  if (event === "pre-tool-use" && rawDecision === "allow") {
    if (
      (agent === "claude-code" || agent === "codex") &&
      !isJsonObject(cleaned["hookSpecificOutput"])
    ) {
      const { decision: _ignoredDecision, ...rest } = cleaned;
      return {
        ...rest,
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "allow",
        },
      };
    }

    if (agent === "cursor" && typeof cleaned["permission"] !== "string") {
      const { decision: _ignoredDecision, ...rest } = cleaned;
      return {
        ...rest,
        permission: "allow",
      };
    }
  }

  return cleaned;
}

/**
 * Сериализует объект ответа хука в однострочный JSON с завершающим переводом строки для `process.stdout`.
 */
export function formatHookStdoutLine(payload: Record<string, unknown>): string {
  return `${JSON.stringify(payload)}\n`;
}
