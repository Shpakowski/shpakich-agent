/**
 * Слой 3C (Host CLI Hooks Runner): публичный фасад (Barrel) пакета `src/cli/hooks/`.
 * Реэкспортирует неблокирующий ридер `stdin`, транслятор диалектов хуков, локальные guardrails
 * и оркестратор `executeHookCommand` для соблюдения архитектурного инварианта `Barrel per Entity`.
 */

export {
  buildDialectAllowResponse,
  buildFailOpenStdout,
  CANONICAL_HOOK_EVENTS,
  type CanonicalHookEvent,
  type CoreHookResponse,
  type CoreHookSyncFile,
  FALLBACK_ALLOW_LINE,
  formatHookStdoutLine,
  HOOK_EVENT_ALIASES,
  type HookEventName,
  type HookExecutionResult,
  isCanonicalHookEvent,
  isHookEventName,
  resolveCanonicalHookEvent,
  translateToAgentStdout,
} from "./dialect-translator.ts";

export {
  applyHookSyncFiles,
  type ExecuteHookOptions,
  executeHook,
  executeHookCommand,
  forwardHookToCore,
  HOOK_HTTP_TIMEOUT_MS,
  inspectLocalPreToolUseGuardrail,
  type LocalGuardrailRuleId,
  type LocalGuardrailVerdict,
  STOP_HOOK_HTTP_TIMEOUT_MS,
} from "./hook-executor.ts";

export {
  extractSessionIdFromHookPayload,
  extractShellCommandFromHookPayload,
  extractToolInputFromHookPayload,
  extractToolNameFromHookPayload,
  extractUserPromptFromHookPayload,
  extractWorkspacePathFromHookPayload,
  readHookStdinPayload,
  readStdinJson,
  resolveWorkspaceFromStdin,
  STDIN_READ_TIMEOUT_MS,
} from "./stdin-reader.ts";
