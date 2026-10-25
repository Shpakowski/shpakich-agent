/**
 * Слой 1 (Host CLI Core API Adapter): эндпоинт диспетчеризации событий жизненного цикла хуков
 * (`POST /api/hooks/{event}`). Передаёт контекст вызова от ИИ-агента в `HookScriptDispatcher`
 * аналитического ядра и возвращает диалектный вердикт с возможным списком файлов `_syncFiles`.
 */

import type { Result } from "../common/index.ts";
import { type HookEventType, TIMEOUTS_MS } from "../config/index.ts";
import { type CoreApiError, requestCoreJson } from "./http-client.ts";

export const HOOKS_BASE_ENDPOINT = "/api/hooks" as const;
export const AGENT_TYPE_HEADER = "X-Agent-Type" as const;

export interface CoreHookSyncFilePayload {
  path: string;
  content: string;
  executable?: boolean | undefined;
}

export interface CoreHookResponsePayload {
  decision?: string | undefined;
  reason?: string | undefined;
  systemMessage?: string | undefined;
  additionalContext?: string | undefined;
  hookSpecificOutput?: Record<string, unknown> | undefined;
  _syncFiles?: CoreHookSyncFilePayload[] | undefined;
  [key: string]: unknown;
}

/**
 * Отправляет JSON-событие жизненного цикла хука в ядро (`POST /api/hooks/{event}`)
 * с пробросом заголовка `X-Agent-Type` и жёстким таймаутом `HOOK_HTTP_MS`.
 */
export async function dispatchHookToCore(
  eventName: HookEventType,
  payload: Record<string, unknown>,
  options?: { baseUrl?: string | undefined; timeoutMs?: number | undefined },
): Promise<Result<CoreHookResponsePayload, CoreApiError>> {
  const encodedEvent = encodeURIComponent(eventName);
  const agentHeaderValue =
    typeof payload["agent_type"] === "string" && payload["agent_type"].trim().length > 0
      ? payload["agent_type"].trim()
      : undefined;

  return await requestCoreJson<CoreHookResponsePayload>(
    `${HOOKS_BASE_ENDPOINT}/${encodedEvent}`,
    {
      method: "POST",
      body: payload,
      baseUrl: options?.baseUrl,
      timeoutMs: options?.timeoutMs ?? TIMEOUTS_MS.HOOK_HTTP_MS,
      ...(agentHeaderValue ? { headers: { [AGENT_TYPE_HEADER]: agentHeaderValue } } : {}),
    },
  );
}
