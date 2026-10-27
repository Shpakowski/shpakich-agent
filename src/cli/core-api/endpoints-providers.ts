/**
 * Слой 1 (Host CLI Core API Adapter): эндпоинты управления провайдерами второго контура решений
 * (`GET/POST/PATCH/DELETE /api/providers`). Позволяет просматривать состояние Circuit Breaker,
 * регистрировать LLM-провайдеров и менять приоритеты в `RuntimeProviderPool`.
 */

import { TIMEOUTS_MS } from "../config/index.ts";
import { requestCoreJson } from "./http-client.ts";

export const PROVIDERS_ENDPOINT = "/api/providers" as const;

export interface ProviderStatusItem {
  name: string;
  dialect: string;
  base_url: string;
  model: string;
  priority: number;
  timeout_ms: number;
  enabled: boolean;
  has_api_key?: boolean | undefined;
  api_key_configured?: boolean | undefined;
  health_status: string;
  cooldown_remaining_seconds?: number | undefined;
  cooldown_seconds_remaining?: number | undefined;
  circuit_reason?: string | null | undefined;
  last_error?: string | null | undefined;
}

export type Tier2ProviderRecord = ProviderStatusItem;

export type UpsertProviderPayload = Record<string, unknown>;
export type PatchProviderPayload = Record<string, unknown>;

/**
 * Возвращает отсортированный список провайдеров второго контура (`GET /api/providers`).
 */
export async function listTier2Providers(
  timeoutMs: number = TIMEOUTS_MS.CORE_HTTP_DEFAULT_MS,
): Promise<ProviderStatusItem[] | null> {
  const result = await requestCoreJson<Record<string, unknown>>(PROVIDERS_ENDPOINT, {
    method: "GET",
    timeoutMs,
  });
  if (!result.ok) {
    return null;
  }
  const providers = result.value["providers"];
  return Array.isArray(providers) ? (providers as ProviderStatusItem[]) : [];
}

/**
 * Добавляет или полностью обновляет провайдера Tier 2 (`POST /api/providers`).
 */
export async function upsertTier2Provider(
  config: UpsertProviderPayload,
  timeoutMs: number = TIMEOUTS_MS.CORE_HTTP_DEFAULT_MS,
): Promise<Record<string, unknown> | null> {
  const result = await requestCoreJson<Record<string, unknown>>(PROVIDERS_ENDPOINT, {
    method: "POST",
    body: config,
    timeoutMs,
  });
  return result.ok ? result.value : null;
}

/**
 * Частично обновляет конфигурацию провайдера Tier 2 или сбрасывает его Circuit Breaker (`PATCH /api/providers/{name}`).
 */
export async function patchTier2Provider(
  name: string,
  patch: PatchProviderPayload,
  timeoutMs: number = TIMEOUTS_MS.CORE_HTTP_DEFAULT_MS,
): Promise<Record<string, unknown> | null> {
  const encodedName = encodeURIComponent(name.trim());
  const result = await requestCoreJson<Record<string, unknown>>(
    `${PROVIDERS_ENDPOINT}/${encodedName}`,
    {
      method: "PATCH",
      body: patch,
      timeoutMs,
    },
  );
  return result.ok ? result.value : null;
}

/**
 * Удаляет провайдера Tier 2 из рантайм-пула и таблицы `decision_providers` (`DELETE /api/providers/{name}`).
 */
export async function deleteTier2Provider(
  name: string,
  timeoutMs: number = TIMEOUTS_MS.CORE_HTTP_DEFAULT_MS,
): Promise<boolean> {
  const encodedName = encodeURIComponent(name.trim());
  const result = await requestCoreJson<Record<string, unknown>>(
    `${PROVIDERS_ENDPOINT}/${encodedName}`,
    {
      method: "DELETE",
      timeoutMs,
    },
  );
  return result.ok;
}
