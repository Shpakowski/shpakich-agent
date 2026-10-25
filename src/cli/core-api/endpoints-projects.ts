/**
 * Слой 1 (Host CLI Core API Adapter): клиентские эндпоинты регистрации рабочего пространства
 * проекта (`POST /api/projects/register`) и автономного самоисцеления графа (`POST /api/context/heal`).
 * Работает поверх единого транспорта `requestCoreJson` с сохранением контрактов Fail-Open.
 */

import { TIMEOUTS_MS } from "../config/index.ts";
import { requestCoreJson } from "./http-client.ts";

export const REGISTER_PROJECT_ENDPOINT = "/api/projects/register" as const;
export const CONTEXT_HEAL_ENDPOINT = "/api/context/heal" as const;

export interface RegisterProjectPayload {
  workspace_path: string;
  project_name: string;
  agent_type: string;
  stack_tags: string[];
  profile_overrides: Record<string, unknown>;
}

export interface ContextHealRequestPayload {
  workspace_path: string;
  agent_type?: string | undefined;
  changed_files?: string[] | undefined;
  diff_summary?: string | undefined;
}

export interface ContextHealResponse {
  workspace_path: string;
  executed: boolean;
  applied_nodes: string[];
  skipped_reason: string;
  summary: string;
  validation_passed: boolean;
}

/**
 * Регистрирует рабочую директорию проекта в ядре (`POST /api/projects/register`)
 * и возвращает скомпилированный артефакт харнеса (`CompiledHarnessArtifact`).
 */
export async function registerProjectWithCore(
  payload: RegisterProjectPayload,
  timeoutMs: number = TIMEOUTS_MS.CORE_REGISTRATION_MS,
): Promise<Record<string, unknown> | null> {
  const result = await requestCoreJson<Record<string, unknown>>(REGISTER_PROJECT_ENDPOINT, {
    method: "POST",
    body: payload,
    timeoutMs,
  });
  return result.ok ? result.value : null;
}

/**
 * Запускает фоновое самоисцеление графа `.context/` через микро-агента `AntigravitySDKService` (`POST /api/context/heal`).
 */
export async function healProjectContext(
  payload: ContextHealRequestPayload,
  timeoutMs: number = TIMEOUTS_MS.CORE_HEAL_MS,
): Promise<ContextHealResponse | null> {
  const result = await requestCoreJson<ContextHealResponse>(CONTEXT_HEAL_ENDPOINT, {
    method: "POST",
    body: payload,
    timeoutMs,
  });
  return result.ok ? result.value : null;
}
