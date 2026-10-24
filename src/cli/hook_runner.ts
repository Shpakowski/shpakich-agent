/**
 * Слой 1 (Host CLI Injector): тонкий локальный транспорт жизненных хуков (`hook <event>`).
 * Не содержит захардкоженной бизнес-логики скриптов: читает JSON-контекст из `stdin`
 * от любого из 5 поддерживаемых ИИ-агентов, проксирует событие в глобальное Docker-ядро
 * (`POST /api/hooks/{event}`), где `HookScriptDispatcher` динамически отбирает по тегам проекта
 * и векторным эмбеддингам (`pgvector`) нужные скрипты из таблицы `harness_components`,
 * и возвращает ответ в `stdout` (с безопасным Fail-Open разрешением при остановленном Docker).
 */

import fs from "node:fs";
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

const AGENT_TYPE_HEADER = "X-Agent-Type" as const;
const FALLBACK_ALLOW_LINE = '{"decision":"allow"}\n' as const;

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

/**
 * Формирует диалект-специфичный разрешающий ответ хука для автономного режима (Fail-Open),
 * когда глобальный Docker-стек временно остановлен пользователем.
 */
export function buildDialectAllowResponse(
  event: HookEventName,
  agent: AgentId,
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

function resolveWorkspaceFromStdin(payload: Record<string, unknown>): string {
  const wsArray = payload.workspacePaths ?? payload.workspace_roots;
  if (
    Array.isArray(wsArray) &&
    typeof wsArray[0] === "string" &&
    wsArray[0].trim()
  ) {
    return path.resolve(wsArray[0].trim());
  }
  const direct =
    (typeof payload.workspace_path === "string" && payload.workspace_path) ||
    (typeof payload.cwd === "string" && payload.cwd) ||
    (typeof payload.directory === "string" && payload.directory) ||
    process.cwd();
  return path.resolve(direct.trim());
}

function readWorkspaceContextIndex(workspacePath: string): string | undefined {
  const indexPath = path.join(workspacePath, CONTEXT_GRAPH_INDEX_REL_PATH);
  if (!fs.existsSync(indexPath)) {
    return undefined;
  }
  try {
    const raw = fs.readFileSync(indexPath, "utf-8").trim();
    return raw.length > 0 ? raw : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Главная точка входа подкоманды `hook <event> [--agent <id>]`.
 * Отправляет JSON-событие в Docker (`POST /api/hooks/<event>`), где динамически отбираются
 * и исполняются скрипты `harness_components` из PostgreSQL.
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

  const baseUrl = resolveCoreBaseUrl() || DEFAULT_CORE_BASE_URL;
  const enrichedPayload: Record<string, unknown> = {
    ...stdinPayload,
    agent_type: manifest.agent,
    workspace_path: workspacePath,
    stack_tags: resolveEffectiveStackTags(manifest),
  };

  if (event === "session-start" || event === "pre-invocation") {
    const contextIndexMd = readWorkspaceContextIndex(workspacePath);
    if (contextIndexMd) {
      enrichedPayload.context_index_md = contextIndexMd;
    }
  }

  if (
    manifest.overrides.enabled_components.length > 0 ||
    manifest.overrides.disabled_components.length > 0 ||
    manifest.overrides.disabled_hooks.length > 0
  ) {
    enrichedPayload.profile_overrides = buildProfileOverridesPayload(manifest);
  }

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

      process.stdout.write(`${JSON.stringify(resp)}\n`);
      return;
    }
  } catch {
    // При остановленном Docker-стеке возвращаем безопасный Fail-Open ответ диалекта.
  }

  const fallbackResp = buildDialectAllowResponse(event, manifest.agent);
  process.stdout.write(`${JSON.stringify(fallbackResp)}\n`);
}
