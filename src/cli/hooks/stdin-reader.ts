/**
 * Слой 3C (Host CLI Hooks Runner): неблокирующее чтение JSON-пейлоада из `process.stdin`
 * с жёстким таймаутом и защитой от зависаний интерактивного TTY, а также функции
 * безопасного извлечения рабочей директории, имени инструмента и bash-команды из сырого события.
 */

import { Buffer } from "node:buffer";
import path from "node:path";
import process from "node:process";

import { isJsonObject } from "../common/index.ts";
import { TIMEOUTS_MS } from "../config/index.ts";

export const STDIN_READ_TIMEOUT_MS = TIMEOUTS_MS.STDIN_READ_MS;

const UTF8_ENCODING = "utf-8" as const;

const WORKSPACE_ARRAY_KEYS = ["workspacePaths", "workspace_roots"] as const;
const WORKSPACE_STRING_KEYS = [
  "workspace_path",
  "workspacePath",
  "cwd",
  "project_dir",
  "projectDir",
  "directory",
] as const;

const TOOL_NAME_KEYS = ["tool_name", "toolName", "tool", "name"] as const;
const TOOL_INPUT_KEYS = [
  "tool_input",
  "toolInput",
  "input",
  "arguments",
  "parameters",
] as const;
const SHELL_COMMAND_KEYS = [
  "command",
  "cmd",
  "commandLine",
  "CommandLine",
  "script",
] as const;
const PROMPT_STRING_KEYS = [
  "prompt",
  "user_prompt",
  "userPrompt",
  "message",
  "query",
] as const;
const SESSION_ID_KEYS = [
  "session_id",
  "sessionId",
  "conversation_id",
  "conversationId",
] as const;

/**
 * Неблокирующе читает JSON-пейлоад жизненного хука из `process.stdin` с таймаутом `timeoutMs`.
 * Возвращает пустой объект `{}` при запуске в интерактивном TTY, пустом потоке или невалидном JSON (Fail-Open).
 */
export async function readHookStdinPayload(
  timeoutMs: number = STDIN_READ_TIMEOUT_MS,
): Promise<Record<string, unknown>> {
  if (process.stdin.isTTY) {
    return {};
  }

  return await new Promise<Record<string, unknown>>((resolve) => {
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
        // Игнорируем ошибку паузы потока при завершении дочернего CLI-процесса.
      }

      const trimmed = rawText.trim();
      if (trimmed.length === 0) {
        resolve({});
        return;
      }

      try {
        const parsed: unknown = JSON.parse(trimmed);
        if (isJsonObject(parsed)) {
          resolve({ ...parsed });
          return;
        }
      } catch {
        // При повреждённом JSON во входном пайпе возвращаем пустой объект для сохранения Fail-Open.
      }

      resolve({});
    };

    const onData = (chunk: Buffer | string): void => {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    };

    const onEnd = (): void => {
      finalize(Buffer.concat(chunks).toString(UTF8_ENCODING));
    };

    const onError = (): void => {
      finalize("");
    };

    const timer = setTimeout(() => {
      finalize(Buffer.concat(chunks).toString(UTF8_ENCODING));
    }, timeoutMs);

    process.stdin.on("data", onData);
    process.stdin.once("end", onEnd);
    process.stdin.once("error", onError);
    process.stdin.resume();
  });
}

/**
 * Алиас `readHookStdinPayload` для обратной совместимости с публичным контрактом `hook_runner.ts`.
 */
export const readStdinJson = readHookStdinPayload;

/**
 * Извлекает абсолютный путь к рабочей директории проекта из JSON-пейлоада хука
 * (поддерживая массивы `workspacePaths`/`workspace_roots` и поля `workspace_path`/`cwd`/`directory`)
 * с фолбэком на `fallbackCwd` или `process.cwd()`.
 */
export function extractWorkspacePathFromHookPayload(
  payload: Record<string, unknown>,
  fallbackCwd?: string | undefined,
): string {
  for (const arrayKey of WORKSPACE_ARRAY_KEYS) {
    const candidateArray = payload[arrayKey];
    if (Array.isArray(candidateArray)) {
      const firstEntry = candidateArray[0];
      if (typeof firstEntry === "string" && firstEntry.trim().length > 0) {
        return path.resolve(firstEntry.trim());
      }
    }
  }

  for (const key of WORKSPACE_STRING_KEYS) {
    const candidate = payload[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return path.resolve(candidate.trim());
    }
  }

  const effectiveFallback =
    typeof fallbackCwd === "string" && fallbackCwd.trim().length > 0
      ? fallbackCwd.trim()
      : process.cwd();
  return path.resolve(effectiveFallback);
}

/**
 * Алиас `extractWorkspacePathFromHookPayload` для обратной совместимости.
 */
export const resolveWorkspaceFromStdin = extractWorkspacePathFromHookPayload;

/**
 * Извлекает имя вызываемого инструмента из сырого JSON-пейлоада хука (`PreToolUse` / `PostToolUse`).
 */
export function extractToolNameFromHookPayload(
  payload: Record<string, unknown>,
): string | undefined {
  for (const key of TOOL_NAME_KEYS) {
    const candidate = payload[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate.trim();
    }
  }
  return undefined;
}

/**
 * Извлекает объект аргументов вызываемого инструмента (`tool_input` / `toolInput` / `arguments`)
 * из сырого JSON-пейлоада хука, поддерживая как вложенный объект, так и JSON-строку.
 */
export function extractToolInputFromHookPayload(
  payload: Record<string, unknown>,
): Record<string, unknown> {
  for (const key of TOOL_INPUT_KEYS) {
    const candidate = payload[key];
    if (isJsonObject(candidate)) {
      return { ...candidate };
    }
    if (typeof candidate === "string" && candidate.trim().startsWith("{")) {
      try {
        const parsed: unknown = JSON.parse(candidate.trim());
        if (isJsonObject(parsed)) {
          return { ...parsed };
        }
      } catch {
        // Игнорируем невалидную JSON-строку аргументов и продолжаем поиск по остальным ключам.
      }
    }
  }
  return {};
}

/**
 * Извлекает строку shell/bash-команды из сырого JSON-пейлоада хука `PreToolUse`
 * (проверяя как вложенный объект `tool_input`, так и верхний уровень для диалекта Cursor `beforeShellExecution`).
 */
export function extractShellCommandFromHookPayload(
  payload: Record<string, unknown>,
): string | undefined {
  const toolInput = extractToolInputFromHookPayload(payload);
  for (const key of SHELL_COMMAND_KEYS) {
    const nestedCandidate = toolInput[key];
    if (
      typeof nestedCandidate === "string" &&
      nestedCandidate.trim().length > 0
    ) {
      return nestedCandidate.trim();
    }
  }

  for (const key of SHELL_COMMAND_KEYS) {
    const directCandidate = payload[key];
    if (
      typeof directCandidate === "string" &&
      directCandidate.trim().length > 0
    ) {
      return directCandidate.trim();
    }
  }

  return undefined;
}

/**
 * Извлекает текст пользовательского промпта из сырого JSON-пейлоада хука `PreInvocation` / `UserPromptSubmit`.
 */
export function extractUserPromptFromHookPayload(
  payload: Record<string, unknown>,
): string | undefined {
  for (const key of PROMPT_STRING_KEYS) {
    const candidate = payload[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate.trim();
    }
  }
  return undefined;
}

/**
 * Извлекает идентификатор текущей сессии или диалога из сырого JSON-пейлоада хука.
 */
export function extractSessionIdFromHookPayload(
  payload: Record<string, unknown>,
): string | undefined {
  for (const key of SESSION_ID_KEYS) {
    const candidate = payload[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate.trim();
    }
  }
  return undefined;
}
