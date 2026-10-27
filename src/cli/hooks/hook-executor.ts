/**
 * Слой 3C (Host CLI Hooks Runner): оркестратор выполнения жизненного цикла хука (`hook <event>`).
 * Обеспечивает локальный fast-path перехват деструктивных bash-команд на `PreToolUse`,
 * отправку контекста в аналитическое ядро (`POST /api/hooks/{event}`), атомарное применение
 * `sync_files` на диск и гарантированный Fail-Open (`exit code 0`) при остановленном Docker.
 */

import { Buffer } from "node:buffer";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  isJsonObject,
  readFileUtf8IfExistsSync,
  resolveSafeWorkspacePath,
  writeFileAtomicSync,
} from "../common/index.ts";
import {
  type AgentType,
  DEFAULT_CORE_BASE_URL,
  isValidAgentType,
  resolveCoreBaseUrl,
  TIMEOUTS_MS,
  WORKSPACE_RELATIVE_PATHS,
} from "../config/index.ts";
import {
  AGENT_TYPE_HEADER,
  HOOKS_BASE_ENDPOINT,
  requestCoreJson,
} from "../core-api/index.ts";
import {
  AUTO_AGENT_OPTION,
  inspectWorkspace,
  resolveContextIndexPath,
} from "../detector/index.ts";
import {
  buildFailOpenStdout,
  type CanonicalHookEvent,
  type CoreHookResponse,
  type CoreHookSyncFile,
  FALLBACK_ALLOW_LINE,
  formatHookStdoutLine,
  type HookExecutionResult,
  resolveCanonicalHookEvent,
  translateToAgentStdout,
} from "./dialect-translator.ts";
import {
  extractShellCommandFromHookPayload,
  extractWorkspacePathFromHookPayload,
  readHookStdinPayload,
} from "./stdin-reader.ts";

export const HOOK_HTTP_TIMEOUT_MS = TIMEOUTS_MS.HOOK_HTTP_MS;
export const STOP_HOOK_HTTP_TIMEOUT_MS = TIMEOUTS_MS.CORE_REGISTRATION_MS;

const EXECUTABLE_FILE_MODE = 0o755 as const;
const LEGACY_HARNESS_MANIFEST_REL_PATH =
  ".agents/mcp-coop-harness.json" as const;
const TRANSCRIPT_TAIL_MAX_BYTES = 65_536 as const;
const TRANSCRIPT_PATH_KEYS = [
  "transcriptPath",
  "transcript_path",
  "transcript",
] as const;

const RM_ROOT_COMMAND_PATTERN =
  /(?:^|[;&|]\s*|\bsudo\s+)rm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*\s+|--(?:recursive|force|no-preserve-root)\s+)*(?:-[a-zA-Z]*[rf][a-zA-Z]*|--(?:recursive|force))\s+\/(?:\*|\s|$|[;&|])/i;

const GIT_PUSH_SEGMENT_PATTERN = /\bgit\s+push\b([^;&|]*)/i;
const GIT_FORCE_FLAG_PATTERN =
  /(?:^|\s)(?:--force(?:-with-lease)?|-[a-zA-Z]*f[a-zA-Z]*)(?:\s|$)/i;
const GIT_PROTECTED_BRANCH_PATTERN =
  /(?:^|\s|:|\+)(?:refs\/heads\/)?(?:main|master)(?:\s|$)/i;
const GIT_PLUS_PROTECTED_REF_PATTERN =
  /(?:^|\s)\+(?:HEAD:)?(?:refs\/heads\/)?(?:main|master)(?:\s|$)/i;

const GIT_RESET_HARD_PATTERN = /\bgit\s+reset\b[^;&|]*\s--hard(?:\s|$|[;&|])/i;

export type LocalGuardrailRuleId =
  | "destructive-rm-root"
  | "destructive-git-force-push"
  | "destructive-git-reset-hard";

export interface LocalGuardrailVerdict {
  readonly blocked: boolean;
  readonly ruleId?: LocalGuardrailRuleId | undefined;
  readonly reason?: string | undefined;
}

export interface ExecuteHookOptions {
  readonly rawEvent: string;
  readonly agentFlag?: string | undefined;
  readonly stdinPayload?: Record<string, unknown> | undefined;
  readonly workspacePathOverride?: string | undefined;
  readonly baseUrl?: string | undefined;
  readonly timeoutMs?: number | undefined;
}

interface WorkspaceManifestOverrides {
  readonly agent?: AgentType | undefined;
  readonly stackTags: readonly string[];
  readonly profileOverrides?: Record<string, readonly string[]> | undefined;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const result: string[] = [];
  for (const item of value) {
    if (typeof item === "string" && item.trim().length > 0) {
      result.push(item.trim());
    }
  }
  return result;
}

function loadWorkspaceManifestOverrides(
  workspacePath: string,
  defaultAgent: AgentType,
  detectedStackTags: readonly string[],
): WorkspaceManifestOverrides {
  const candidateRelPaths = [
    WORKSPACE_RELATIVE_PATHS.MANIFEST_FILE,
    LEGACY_HARNESS_MANIFEST_REL_PATH,
  ] as const;

  for (const relPath of candidateRelPaths) {
    const absPath = path.join(workspacePath, relPath);
    const rawContent = readFileUtf8IfExistsSync(absPath);
    if (!rawContent) {
      continue;
    }

    try {
      const parsed: unknown = JSON.parse(rawContent);
      if (!isJsonObject(parsed)) {
        continue;
      }

      const rawAgent = parsed["agent"];
      const resolvedAgent =
        typeof rawAgent === "string" && isValidAgentType(rawAgent.trim())
          ? (rawAgent.trim() as AgentType)
          : defaultAgent;

      const manifestTags = [
        ...toStringArray(parsed["stack_tags"]),
        ...toStringArray(parsed["stackTags"]),
      ];

      const rawOverrides = isJsonObject(parsed["overrides"])
        ? parsed["overrides"]
        : undefined;
      const extraTags = rawOverrides
        ? toStringArray(rawOverrides["extra_stack_tags"])
        : [];
      const excludedTags = new Set(
        rawOverrides
          ? toStringArray(rawOverrides["excluded_stack_tags"]).map((tag) =>
              tag.toLowerCase(),
            )
          : [],
      );

      const mergedTagsSet = new Set<string>();
      for (const tag of [...detectedStackTags, ...manifestTags, ...extraTags]) {
        const normalized = tag.trim().toLowerCase();
        if (normalized.length > 0 && !excludedTags.has(normalized)) {
          mergedTagsSet.add(normalized);
        }
      }

      const enabledComponents = rawOverrides
        ? toStringArray(rawOverrides["enabled_components"])
        : [];
      const disabledComponents = rawOverrides
        ? toStringArray(rawOverrides["disabled_components"])
        : [];
      const disabledHooks = rawOverrides
        ? toStringArray(rawOverrides["disabled_hooks"])
        : [];

      const hasProfileOverrides =
        enabledComponents.length > 0 ||
        disabledComponents.length > 0 ||
        disabledHooks.length > 0;

      return {
        agent: resolvedAgent,
        stackTags: [...mergedTagsSet].sort(),
        profileOverrides: hasProfileOverrides
          ? {
              enabled_components: enabledComponents,
              disabled_components: disabledComponents,
              disabled_hooks: disabledHooks,
            }
          : undefined,
      };
    } catch {
      // При повреждённом локальном манифесте используем автоопределённый профиль рабочего пространства.
    }
  }

  return {
    agent: defaultAgent,
    stackTags: [...detectedStackTags],
  };
}

function readWorkspaceContextIndex(workspacePath: string): string | undefined {
  const indexPath = resolveContextIndexPath(workspacePath);
  const raw = readFileUtf8IfExistsSync(indexPath);
  if (!raw) {
    return undefined;
  }
  const trimmed = raw.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

/**
 * Читает последние 64 КБ файла транскрипта (`transcriptPath` / `transcript_path`) на хосте,
 * чтобы передать `transcript_tail` в изолированный Docker-контейнер аналитического ядра.
 */
export function readTranscriptTailIfExists(
  payload: Record<string, unknown>,
): string | undefined {
  let rawPath = "";
  for (const key of TRANSCRIPT_PATH_KEYS) {
    const candidate = payload[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      rawPath = candidate.trim();
      break;
    }
  }
  if (!rawPath) {
    return undefined;
  }

  let fd: number | undefined;
  try {
    const stat = fs.statSync(rawPath);
    if (!stat.isFile() || stat.size <= 0) {
      return undefined;
    }
    const readSize = Math.min(stat.size, TRANSCRIPT_TAIL_MAX_BYTES);
    const offset = Math.max(0, stat.size - readSize);
    const buffer = Buffer.alloc(readSize);
    fd = fs.openSync(rawPath, "r");
    const bytesRead = fs.readSync(fd, buffer, 0, readSize, offset);
    if (bytesRead <= 0) {
      return undefined;
    }
    const text = buffer.subarray(0, bytesRead).toString("utf-8").trim();
    return text.length > 0 ? text : undefined;
  } catch {
    return undefined;
  } finally {
    if (fd !== undefined) {
      try {
        fs.closeSync(fd);
      } catch {
        // Игнорируем ошибку закрытия дескриптора при чтении хвоста транскрипта.
      }
    }
  }
}

/**
 * Выполняет локальный fast-path перехват деструктивных shell-команд на событии `PreToolUse`
 * (`rm -rf /`, `git push --force` в `main`/`master`, `git reset --hard`) до сетевого обращения к ядру.
 */
export function inspectLocalPreToolUseGuardrail(
  payloadOrCommand: Record<string, unknown> | string,
): LocalGuardrailVerdict {
  const command =
    typeof payloadOrCommand === "string"
      ? payloadOrCommand.trim()
      : extractShellCommandFromHookPayload(payloadOrCommand);

  if (!command) {
    return { blocked: false };
  }

  if (RM_ROOT_COMMAND_PATTERN.test(command)) {
    return {
      blocked: true,
      ruleId: "destructive-rm-root",
      reason:
        "Destructive command blocked by local Harness guardrail: recursive root deletion (rm -rf /) is strictly forbidden.",
    };
  }

  const gitPushMatch = GIT_PUSH_SEGMENT_PATTERN.exec(command);
  if (gitPushMatch) {
    const pushArgs = gitPushMatch[1] ?? "";
    const isForcePush =
      (GIT_FORCE_FLAG_PATTERN.test(pushArgs) &&
        GIT_PROTECTED_BRANCH_PATTERN.test(pushArgs)) ||
      GIT_PLUS_PROTECTED_REF_PATTERN.test(pushArgs);

    if (isForcePush) {
      return {
        blocked: true,
        ruleId: "destructive-git-force-push",
        reason:
          "Destructive command blocked by local Harness guardrail: force-pushing to main or master is strictly forbidden.",
      };
    }
  }

  if (GIT_RESET_HARD_PATTERN.test(command)) {
    return {
      blocked: true,
      ruleId: "destructive-git-reset-hard",
      reason:
        "Destructive command blocked by local Harness guardrail: 'git reset --hard' requires explicit prior user confirmation.",
    };
  }

  return { blocked: false };
}

/**
 * Безопасно применяет список файлов синхронизации (`sync_files` / `_syncFiles`) из ответа ядра
 * на диск внутри `workspacePath`, проверяя отсутствие Path Traversal через `resolveSafeWorkspacePath`
 * и выполняя атомарную запись через `writeFileAtomicSync`.
 */
export function applyHookSyncFiles(
  workspacePath: string,
  syncFiles: readonly CoreHookSyncFile[] | undefined,
): number {
  if (!syncFiles || syncFiles.length === 0) {
    return 0;
  }

  let appliedCount = 0;
  for (const fileEntry of syncFiles) {
    if (
      typeof fileEntry.path !== "string" ||
      typeof fileEntry.content !== "string"
    ) {
      continue;
    }

    const safeTargetPath = resolveSafeWorkspacePath(
      workspacePath,
      fileEntry.path,
    );
    if (!safeTargetPath) {
      continue;
    }

    try {
      writeFileAtomicSync(
        safeTargetPath,
        fileEntry.content,
        fileEntry.executable ? { mode: EXECUTABLE_FILE_MODE } : undefined,
      );
      appliedCount += 1;
    } catch {
      // Сбой записи отдельного файла не должен прерывать выполнение хука (Fail-Open).
    }
  }

  return appliedCount;
}

/**
 * Проксирует JSON-пейлоад жизненного хука в глобальное аналитическое ядро (`POST /api/hooks/{event}`)
 * с заголовком `X-Agent-Type`. Возвращает `null` при остановленном Docker или сетевом таймауте.
 */
export async function forwardHookToCore(
  event: CanonicalHookEvent,
  agent: AgentType,
  payload: Record<string, unknown>,
  options?: {
    readonly baseUrl?: string | undefined;
    readonly timeoutMs?: number | undefined;
  },
): Promise<CoreHookResponse | null> {
  const effectiveBaseUrl =
    resolveCoreBaseUrl(options?.baseUrl) || DEFAULT_CORE_BASE_URL;
  const effectiveTimeoutMs =
    options?.timeoutMs ??
    (event === "stop" ? STOP_HOOK_HTTP_TIMEOUT_MS : HOOK_HTTP_TIMEOUT_MS);

  const result = await requestCoreJson<CoreHookResponse>(
    `${HOOKS_BASE_ENDPOINT}/${encodeURIComponent(event)}`,
    {
      method: "POST",
      body: payload,
      baseUrl: effectiveBaseUrl,
      timeoutMs: effectiveTimeoutMs,
      headers: {
        [AGENT_TYPE_HEADER]: agent,
      },
    },
  );

  if (!result.ok) {
    return null;
  }

  return result.value;
}

/**
 * Выполняет полный цикл обработки жизненного хука без принудительного завершения процесса:
 * читает `stdin`, проверяет локальные guardrails на `PreToolUse`, обращается к Core API,
 * материализует `sync_files` и возвращает типизированный результат `HookExecutionResult`.
 */
export async function executeHook(
  rawEventOrOptions: string | ExecuteHookOptions,
  agentFlag: string = AUTO_AGENT_OPTION,
  stdinPayloadOverride?: Record<string, unknown> | undefined,
): Promise<HookExecutionResult> {
  const options: ExecuteHookOptions =
    typeof rawEventOrOptions === "string"
      ? {
          rawEvent: rawEventOrOptions,
          agentFlag,
          stdinPayload: stdinPayloadOverride,
        }
      : rawEventOrOptions;

  const event = resolveCanonicalHookEvent(options.rawEvent);
  const stdinPayload = options.stdinPayload ?? (await readHookStdinPayload());
  const workspacePath = options.workspacePathOverride
    ? path.resolve(options.workspacePathOverride)
    : extractWorkspacePathFromHookPayload(stdinPayload);

  const requestedAgentFlag = options.agentFlag?.trim() || AUTO_AGENT_OPTION;
  const profile = inspectWorkspace(workspacePath, requestedAgentFlag);
  const manifestConfig = loadWorkspaceManifestOverrides(
    workspacePath,
    profile.agent,
    profile.stackTags,
  );

  const effectiveAgent: AgentType =
    requestedAgentFlag !== AUTO_AGENT_OPTION &&
    isValidAgentType(requestedAgentFlag)
      ? requestedAgentFlag
      : (manifestConfig.agent ?? profile.agent);

  if (!event) {
    const fallbackJson = buildFailOpenStdout(null, effectiveAgent);
    return {
      event: null,
      agent: effectiveAgent,
      workspacePath,
      stdoutJson: fallbackJson,
      stdoutLine: FALLBACK_ALLOW_LINE,
      exitCode: 0,
      syncedFilesCount: 0,
      blockedLocally: false,
      failOpenTriggered: true,
    };
  }

  if (event === "pre-tool-use") {
    const localVerdict = inspectLocalPreToolUseGuardrail(stdinPayload);
    if (localVerdict.blocked) {
      const stdoutJson = translateToAgentStdout(event, effectiveAgent, {
        decision: "deny",
        reason: localVerdict.reason,
      });
      return {
        event,
        agent: effectiveAgent,
        workspacePath,
        stdoutJson,
        stdoutLine: formatHookStdoutLine(stdoutJson),
        exitCode: 0,
        syncedFilesCount: 0,
        blockedLocally: true,
        failOpenTriggered: false,
      };
    }
  }

  const enrichedPayload: Record<string, unknown> = {
    ...stdinPayload,
    agent_type: effectiveAgent,
    workspace_path: workspacePath,
    stack_tags: manifestConfig.stackTags,
  };

  if (event === "session-start" || event === "pre-invocation") {
    const contextIndexMd = readWorkspaceContextIndex(workspacePath);
    if (contextIndexMd) {
      enrichedPayload["context_index_md"] = contextIndexMd;
    }
  }

  if (typeof enrichedPayload["transcript_tail"] !== "string") {
    const transcriptTail = readTranscriptTailIfExists(stdinPayload);
    if (transcriptTail) {
      enrichedPayload["transcript_tail"] = transcriptTail;
    }
  }

  if (manifestConfig.profileOverrides) {
    enrichedPayload["profile_overrides"] = manifestConfig.profileOverrides;
  }

  const coreResponse = await forwardHookToCore(
    event,
    effectiveAgent,
    enrichedPayload,
    {
      baseUrl: options.baseUrl,
      timeoutMs: options.timeoutMs,
    },
  );

  if (coreResponse !== null) {
    const syncFilesCandidates =
      coreResponse.sync_files ?? coreResponse._syncFiles;
    const syncedFilesCount = applyHookSyncFiles(
      workspacePath,
      syncFilesCandidates,
    );
    const stdoutJson = translateToAgentStdout(
      event,
      effectiveAgent,
      coreResponse,
    );
    return {
      event,
      agent: effectiveAgent,
      workspacePath,
      stdoutJson,
      stdoutLine: formatHookStdoutLine(stdoutJson),
      exitCode: 0,
      syncedFilesCount,
      blockedLocally: false,
      failOpenTriggered: false,
    };
  }

  const failOpenJson = buildFailOpenStdout(event, effectiveAgent);
  return {
    event,
    agent: effectiveAgent,
    workspacePath,
    stdoutJson: failOpenJson,
    stdoutLine: formatHookStdoutLine(failOpenJson),
    exitCode: 0,
    syncedFilesCount: 0,
    blockedLocally: false,
    failOpenTriggered: true,
  };
}

/**
 * Главная точка входа CLI-подкоманды `hook <event> [--agent <id>]`.
 * Выполняет жизненный цикл хука и записывает диалект-специфичный JSON-ответ в `process.stdout`
 * с гарантированным кодом возврата `0` (Fail-Open).
 */
export async function executeHookCommand(
  rawEvent: string,
  agentFlag: string = AUTO_AGENT_OPTION,
): Promise<HookExecutionResult> {
  const result = await executeHook(rawEvent, agentFlag);
  process.stdout.write(result.stdoutLine);
  return result;
}
