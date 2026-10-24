/**
 * Слой 1 (Host CLI Injector): универсальный локальный исполнитель жизненных хуков (`hook <event>`).
 * Принимает JSON-контекст из `stdin` от любого из 5 поддерживаемых ИИ-агентов, обогащает его
 * пер-проектным манифестом `.context/harness.json`, проксирует запрос в аналитическое ядро
 * (`POST /api/hooks/{event}`) с жёстким таймаутом и обеспечивает автономный Fail-Open фолбэк
 * (включая локальный Контур 2 инъекции `.context/index.md` и Контур 3 защиты графа на `Stop`).
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

import {
  type AgentId,
  CONTEXT_GRAPH_INDEX_REL_PATH,
  inspectWorkspace,
} from "./detector.ts";
import {
  DEFAULT_CORE_BASE_URL,
  HOOKS_BASE_ENDPOINT,
  resolveCoreBaseUrl,
} from "./docker.ts";
import {
  ALL_HOOK_EVENTS,
  type HookEventName,
  type RenderedFilePayload,
  buildProfileOverridesPayload,
  isHookEventName,
  loadProjectHarnessConfig,
  materializeFiles,
  resolveEffectiveStackTags,
} from "./materializer.ts";

export const STDIN_READ_TIMEOUT_MS = 300 as const;
export const HOOK_HTTP_TIMEOUT_MS = 1800 as const;
export const SESSION_TMP_PREFIX = "mcp-coop-session-" as const;

export const FILE_MUTATION_TOOLS = [
  "write_file",
  "edit_file",
  "Write",
  "Edit",
  "MultiEdit",
  "replace_file_content",
  "multi_replace_file_content",
] as const;

export const CODE_FILE_EXTENSIONS = [
  ".py",
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".rs",
  ".go",
  ".sol",
  ".sql",
  ".sh",
  ".yml",
  ".yaml",
  ".toml",
] as const;

const DEFAULT_CONVERSATION_ID = "default" as const;
const AGENT_TYPE_HEADER = "X-Agent-Type" as const;
const CONTEXT_DIR_PREFIX = ".context/" as const;
const FALLBACK_ALLOW_LINE = '{"decision":"allow"}\n' as const;

const CONTOUR_3_STOP_REASON =
  "[MCP-COOP Context Gate]: Code or configuration files were modified in this session, but the Context Graph in `.context/` (`index.md` or `.context/entities/`) has not been updated yet. Please synchronize `.context/` and append an entry to `AI Traces` before finishing. Always reply to the user in the language they use in chat." as const;

const OFFLINE_CONTEXT_HEADER =
  "[MCP-COOP Context Graph]: Active project context graph map (`.context/index.md`).\nUse links from this map for targeted node reading and always update the graph after modifying project files:\n\n" as const;

const HOOK_EVENT_ALIASES: Readonly<Record<string, HookEventName>> = {
  "session-start": "session-start",
  sessionstart: "session-start",
  session_start: "session-start",
  "pre-tool-use": "pre-tool-use",
  pretooluse: "pre-tool-use",
  pre_tool_use: "pre-tool-use",
  "post-tool-use": "post-tool-use",
  posttooluse: "post-tool-use",
  post_tool_use: "post-tool-use",
  "pre-invocation": "pre-invocation",
  preinvocation: "pre-invocation",
  pre_invocation: "pre-invocation",
  userpromptsubmit: "pre-invocation",
  "user-prompt-submit": "pre-invocation",
  user_prompt_submit: "pre-invocation",
  stop: "stop",
  agentstop: "stop",
  subagentstop: "stop",
} as const;

export interface LocalSessionState {
  conversation_id: string;
  workspace_path: string;
  code_modified: boolean;
  context_updated: boolean;
  reminder_sent: boolean;
  updated_at: string;
}

/**
 * Нормализует входное имя события хука (с учётом вендорских алиасов `SessionStart`, `PreToolUse`,
 * `UserPromptSubmit` и канонических имён `ALL_HOOK_EVENTS`) в строгий тип `HookEventName`.
 */
export function resolveCanonicalHookEvent(
  rawEvent: string,
): HookEventName | null {
  const cleaned = rawEvent.trim().toLowerCase();
  if (isHookEventName(cleaned)) {
    return cleaned;
  }
  return HOOK_EVENT_ALIASES[cleaned] ?? null;
}
/**
 * Неблокирующе читает JSON-пэйлоад из `process.stdin` с жёстким таймаутом `timeoutMs`.
 * Возвращает пустой объект `{}` при запуске в интерактивном TTY, пустом потоке или ошибке парсинга.
 */
export async function readStdinJson(
  timeoutMs: number = STDIN_READ_TIMEOUT_MS,
): Promise<Record<string, unknown>> {
  if (process.stdin.isTTY) {
    return {};
  }

  return new Promise<Record<string, unknown>>((resolve) => {
    const chunks: Buffer[] = [];
    let settled = false;

    const finalize = (rawText: string): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      process.stdin.removeListener("data", onData);
      process.stdin.removeListener("end", onEnd);
      process.stdin.removeListener("error", onError);
      try {
        process.stdin.pause();
      } catch {
        // Безопасно игнорируем закрытие потока при завершении CLI-процесса.
      }

      const trimmed = rawText.trim();
      if (trimmed.length === 0) {
        resolve({});
        return;
      }
      try {
        const parsed = JSON.parse(trimmed) as unknown;
        if (
          typeof parsed === "object" &&
          parsed !== null &&
          !Array.isArray(parsed)
        ) {
          resolve(parsed as Record<string, unknown>);
          return;
        }
      } catch {
        // При невалидном JSON во входном пайпе продолжаем работу в режиме Fail-Open.
      }
      resolve({});
    };

    const onData = (chunk: Buffer | string): void => {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    };
    const onEnd = (): void => {
      finalize(Buffer.concat(chunks).toString("utf-8"));
    };
    const onError = (): void => {
      finalize("");
    };

    const timer = setTimeout(() => {
      finalize(Buffer.concat(chunks).toString("utf-8"));
    }, timeoutMs);

    process.stdin.on("data", onData);
    process.stdin.once("end", onEnd);
    process.stdin.once("error", onError);
    process.stdin.resume();
  });
}

function getSessionStateFilePath(conversationId: string): string {
  const safeId =
    conversationId.replace(/[^a-zA-Z0-9_-]/g, "") || DEFAULT_CONVERSATION_ID;
  return path.join(os.tmpdir(), `${SESSION_TMP_PREFIX}${safeId}.json`);
}

/**
 * Загружает локальное состояние сессии Контура 3 из `/tmp/mcp-coop-session-<id>.json`
 * или инициализирует чистую структуру, если файл ещё не создан.
 */
export function loadLocalSessionState(
  conversationId: string,
  workspacePath: string,
): LocalSessionState {
  const filePath = getSessionStateFilePath(conversationId);
  const fallback: LocalSessionState = {
    conversation_id: conversationId,
    workspace_path: workspacePath,
    code_modified: false,
    context_updated: false,
    reminder_sent: false,
    updated_at: new Date().toISOString(),
  };

  if (!fs.existsSync(filePath)) {
    return fallback;
  }

  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return {
      conversation_id:
        typeof parsed.conversation_id === "string"
          ? parsed.conversation_id
          : conversationId,
      workspace_path:
        typeof parsed.workspace_path === "string"
          ? parsed.workspace_path
          : workspacePath,
      code_modified: Boolean(parsed.code_modified),
      context_updated: Boolean(parsed.context_updated),
      reminder_sent: Boolean(parsed.reminder_sent),
      updated_at:
        typeof parsed.updated_at === "string"
          ? parsed.updated_at
          : fallback.updated_at,
    };
  } catch {
    return fallback;
  }
}

/**
 * Сохраняет состояние сессии Контура 3 в системную временную директорию.
 */
export function saveLocalSessionState(state: LocalSessionState): void {
  const filePath = getSessionStateFilePath(state.conversation_id);
  const updated: LocalSessionState = {
    ...state,
    updated_at: new Date().toISOString(),
  };
  try {
    fs.writeFileSync(filePath, `${JSON.stringify(updated, null, 2)}\n`, "utf-8");
  } catch {
    // Ошибки записи во временную директорию не должны блокировать работу агента.
  }
}
function isMutationToolName(rawName: string): boolean {
  const normalized = rawName.trim().toLowerCase();
  if (normalized.length === 0) {
    return false;
  }
  return FILE_MUTATION_TOOLS.some(
    (candidate) => candidate.toLowerCase() === normalized,
  );
}

function hasTrackedCodeExtension(filePath: string): boolean {
  const ext = path.extname(filePath).toLowerCase();
  return (CODE_FILE_EXTENSIONS as readonly string[]).includes(ext);
}

/**
 * Анализирует событие `post-tool-use` для локального Контура 3: фиксирует модификацию
 * исходного кода (`code_modified = true`) и синхронизацию графа `.context/` (`context_updated = true`).
 */
export function trackLocalPostToolUse(
  payload: Record<string, unknown>,
  workspacePath: string,
  conversationId: string,
): void {
  if (payload.error) {
    return;
  }

  const toolCallObj =
    typeof payload.toolCall === "object" && payload.toolCall !== null
      ? (payload.toolCall as Record<string, unknown>)
      : undefined;

  const rawToolName =
    (typeof payload.tool_name === "string" && payload.tool_name) ||
    (typeof payload.toolName === "string" && payload.toolName) ||
    (typeof toolCallObj?.name === "string" && toolCallObj.name) ||
    (typeof payload.tool === "string" && payload.tool) ||
    "";

  if (!isMutationToolName(rawToolName)) {
    return;
  }

  const rawArgsCandidate =
    payload.tool_input ??
    payload.tool_args ??
    toolCallObj?.args ??
    payload.args;
  const argsObj =
    typeof rawArgsCandidate === "object" && rawArgsCandidate !== null
      ? (rawArgsCandidate as Record<string, unknown>)
      : {};

  const rawTargetPath =
    (typeof argsObj.Path === "string" && argsObj.Path) ||
    (typeof argsObj.path === "string" && argsObj.path) ||
    (typeof argsObj.file_path === "string" && argsObj.file_path) ||
    (typeof argsObj.TargetFile === "string" && argsObj.TargetFile) ||
    "";

  if (!rawTargetPath.trim()) {
    return;
  }

  const resolvedWorkspace = path.resolve(workspacePath);
  const resolvedTarget = path.resolve(resolvedWorkspace, rawTargetPath.trim());
  const relPath = path
    .relative(resolvedWorkspace, resolvedTarget)
    .replace(/\\/g, "/");

  if (relPath.startsWith("..") || path.isAbsolute(relPath)) {
    return;
  }

  const state = loadLocalSessionState(conversationId, resolvedWorkspace);
  if (
    relPath === ".context" ||
    relPath.startsWith(CONTEXT_DIR_PREFIX) ||
    rawTargetPath.replace(/\\/g, "/").includes(`/${CONTEXT_DIR_PREFIX}`)
  ) {
    state.context_updated = true;
    state.reminder_sent = false;
    saveLocalSessionState(state);
    return;
  }

  if (hasTrackedCodeExtension(relPath)) {
    state.code_modified = true;
    state.context_updated = false;
    saveLocalSessionState(state);
  }
}

/**
 * Формирует диалект-специфичный разрешающий ответ хука для автономного режима (Fail-Open)
 * или при отключённом событии в `disabled_hooks`.
 */
export function buildDialectAllowResponse(
  event: HookEventName,
  agent: AgentId,
  systemMessage?: string,
): Record<string, unknown> {
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

  if (event === "session-start" && systemMessage && systemMessage.trim().length > 0) {
    if (agent === "antigravity") {
      return {
        decision: "allow",
        hookSpecificOutput: {
          injectSteps: [
            {
              role: "system",
              content: systemMessage,
              isEphemeral: false,
            },
          ],
        },
      };
    }
    if (agent === "claude-code" || agent === "codex") {
      return {
        hookSpecificOutput: {
          hookEventName: "SessionStart",
          additionalContext: systemMessage,
        },
      };
    }
    if (agent === "cursor") {
      return { additional_context: systemMessage };
    }
    return { decision: "allow", systemMessage };
  }

  return { decision: "allow" };
}
/**
 * Формирует диалект-специфичный блокирующий ответ на событие `stop`, принуждающий агента
 * синхронизировать `.context/` перед завершением хода (Контур 3).
 */
export function buildDialectStopBlockResponse(
  agent: AgentId,
  reason: string,
): Record<string, unknown> {
  if (agent === "claude-code" || agent === "codex") {
    return { decision: "block", reason };
  }
  if (agent === "cursor") {
    return { followup_message: reason };
  }
  return { decision: "continue", reason };
}

function resolveWorkspaceFromStdin(payload: Record<string, unknown>): string {
  const wsArray = payload.workspacePaths ?? payload.workspace_roots;
  if (Array.isArray(wsArray) && typeof wsArray[0] === "string" && wsArray[0].trim()) {
    return path.resolve(wsArray[0].trim());
  }
  const direct =
    (typeof payload.workspace_path === "string" && payload.workspace_path) ||
    (typeof payload.cwd === "string" && payload.cwd) ||
    (typeof payload.directory === "string" && payload.directory) ||
    process.cwd();
  return path.resolve(direct.trim());
}

function resolveConversationIdFromStdin(payload: Record<string, unknown>): string {
  const candidate =
    (typeof payload.conversation_id === "string" && payload.conversation_id) ||
    (typeof payload.session_id === "string" && payload.session_id) ||
    (typeof payload.conversationId === "string" && payload.conversationId) ||
    (typeof payload.sessionID === "string" && payload.sessionID) ||
    DEFAULT_CONVERSATION_ID;
  return candidate.trim() || DEFAULT_CONVERSATION_ID;
}

function loadOfflineContextSystemMessage(workspacePath: string): string | undefined {
  const indexPath = path.join(workspacePath, CONTEXT_GRAPH_INDEX_REL_PATH);
  if (!fs.existsSync(indexPath)) {
    return undefined;
  }
  try {
    const raw = fs.readFileSync(indexPath, "utf-8").trim();
    return raw.length > 0 ? `${OFFLINE_CONTEXT_HEADER}${raw}` : undefined;
  } catch {
    return undefined;
  }
}

function isServerResponseBlockingStop(resp: Record<string, unknown>): boolean {
  return (
    resp.decision === "continue" ||
    resp.decision === "block" ||
    typeof resp.followup_message === "string"
  );
}

function evaluateLocalContour3StopGate(
  conversationId: string,
  workspacePath: string,
  agent: AgentId,
  stdinPayload: Record<string, unknown>,
): Record<string, unknown> | null {
  if (stdinPayload.stop_hook_active || stdinPayload.stopHookActive || stdinPayload.error) {
    return null;
  }
  const state = loadLocalSessionState(conversationId, workspacePath);
  if (state.code_modified && !state.context_updated && !state.reminder_sent) {
    state.reminder_sent = true;
    saveLocalSessionState(state);
    return buildDialectStopBlockResponse(agent, CONTOUR_3_STOP_REASON);
  }
  if (state.context_updated || state.reminder_sent) {
    state.code_modified = false;
    state.context_updated = false;
    state.reminder_sent = false;
    saveLocalSessionState(state);
  }
  return null;
}

/**
 * Главная точка входа подкоманды `hook <event> [--agent <id>]`.
 * Выполняет маршрутизацию события в Python-ядро с автоматической материализацией `_syncFiles`
 * и автономным фолбэком Контуров 2 и 3 при недоступности контейнера.
 */
export async function executeHookCommand(
  rawEvent: string,
  agentFlag: string,
): Promise<void> {
  const event = resolveCanonicalHookEvent(rawEvent);
  if (!event) {
    process.stdout.write(FALLBACK_ALLOW_LINE);
    return;
  }

  const stdinPayload = await readStdinJson();
  const workspacePath = resolveWorkspaceFromStdin(stdinPayload);
  const profile = inspectWorkspace(workspacePath, agentFlag);
  const manifest = loadProjectHarnessConfig(
    workspacePath,
    profile.agent,
    profile.stackTags,
  );

  if (manifest.overrides.disabled_hooks.includes(event)) {
    const disabledResp = buildDialectAllowResponse(event, manifest.agent);
    process.stdout.write(`${JSON.stringify(disabledResp)}\n`);
    return;
  }

  const conversationId = resolveConversationIdFromStdin(stdinPayload);
  if (event === "post-tool-use") {
    trackLocalPostToolUse(stdinPayload, workspacePath, conversationId);
  }

  const baseUrl = resolveCoreBaseUrl() || DEFAULT_CORE_BASE_URL;
  const enrichedPayload: Record<string, unknown> = {
    ...stdinPayload,
    agent_type: manifest.agent,
    workspace_path: workspacePath,
    stack_tags: resolveEffectiveStackTags(manifest),
    profile_overrides: buildProfileOverridesPayload(manifest),
  };

  try {
    const response = await fetch(`${baseUrl}${HOOKS_BASE_ENDPOINT}/${event}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [AGENT_TYPE_HEADER]: manifest.agent,
      },
      body: JSON.stringify(enrichedPayload),
      signal: AbortSignal.timeout(HOOK_HTTP_TIMEOUT_MS),
    });

    if (response.ok) {
      const resp = (await response.json()) as Record<string, unknown>;
      if (Array.isArray(resp._syncFiles)) {
        materializeFiles(
          workspacePath,
          resp._syncFiles as RenderedFilePayload[],
          { disabledHooks: manifest.overrides.disabled_hooks },
        );
        delete resp._syncFiles;
      }

      if (event === "stop" && !isServerResponseBlockingStop(resp)) {
        const localGate = evaluateLocalContour3StopGate(
          conversationId,
          workspacePath,
          manifest.agent,
          stdinPayload,
        );
        if (localGate) {
          process.stdout.write(`${JSON.stringify(localGate)}\n`);
          return;
        }
      }

      process.stdout.write(`${JSON.stringify(resp)}\n`);
      return;
    }
  } catch {
    // При сетевой ошибке или таймауте переключаемся на локальный автономный контур (Fail-Open).
  }

  if (event === "session-start") {
    const sysMsg = loadOfflineContextSystemMessage(workspacePath);
    const offlineResp = buildDialectAllowResponse(
      "session-start",
      manifest.agent,
      sysMsg,
    );
    process.stdout.write(`${JSON.stringify(offlineResp)}\n`);
    return;
  }

  if (event === "stop") {
    const localGate = evaluateLocalContour3StopGate(
      conversationId,
      workspacePath,
      manifest.agent,
      stdinPayload,
    );
    if (localGate) {
      process.stdout.write(`${JSON.stringify(localGate)}\n`);
      return;
    }
  }

  const fallbackResp = buildDialectAllowResponse(event, manifest.agent);
  process.stdout.write(`${JSON.stringify(fallbackResp)}\n`);
}
