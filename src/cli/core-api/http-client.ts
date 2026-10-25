/**
 * Слой 1 (Host CLI Core API Adapter): единый низкоуровневый HTTP-транспорт к Python Core (`FastAPI`).
 * Инкапсулирует комбинированные сигналы отмены (`AbortSignal.timeout` + `AbortSignal.any`),
 * унифицированное извлечение деталей ошибок из ответов FastAPI и обёртку `Result<T, CoreApiError>`.
 */

import {
  err,
  isJsonObject,
  ok,
  type Result,
  toError,
} from "../common/index.ts";
import {
  resolveCoreBaseUrl,
  TIMEOUTS_MS,
} from "../config/index.ts";

export const HEALTH_ENDPOINT = "/health" as const;

export type CoreHttpMethod = "GET" | "POST" | "PATCH" | "DELETE";

export interface CoreApiError {
  readonly statusCode: number | null;
  readonly message: string;
  readonly endpoint: string;
  readonly cause?: unknown;
}

export interface CoreRequestOptions {
  readonly method?: CoreHttpMethod | undefined;
  readonly body?: unknown;
  readonly timeoutMs?: number | undefined;
  readonly baseUrl?: string | undefined;
  readonly signal?: AbortSignal | undefined;
  readonly headers?: Readonly<Record<string, string>> | undefined;
}

export interface CoreHealthStatus {
  readonly healthy?: boolean | undefined;
  readonly statusText?: string | undefined;
  readonly latencyMs?: number | undefined;
  readonly payload?: Record<string, unknown> | undefined;
  readonly reachable: boolean;
  readonly status: string;
  readonly version?: string | undefined;
  readonly postgres_connected: boolean;
  readonly redis_connected: boolean;
  readonly decisions_api_configured: boolean;
  readonly embedding_api_configured: boolean;
  readonly antigravity_sdk_configured?: boolean | undefined;
  readonly mcp_endpoint?: string | undefined;
}

/**
 * Нормализует относительный путь эндпоинта или полный URL в целевой адрес запроса к ядру.
 */
function buildTargetUrl(endpointPath: string, baseUrlOverride?: string): string {
  if (/^https?:\/\//i.test(endpointPath)) {
    return endpointPath;
  }
  const baseUrl = resolveCoreBaseUrl(baseUrlOverride);
  const normalizedPath = endpointPath.startsWith("/") ? endpointPath : `/${endpointPath}`;
  return `${baseUrl}${normalizedPath}`;
}

/**
 * Извлекает человекочитаемое описание ошибки из JSON-ответа FastAPI (`detail`, `error`, `message`)
 * или возвращает HTTP-статус, если тело ответа не является валидным JSON-объектом.
 */
function extractFastApiErrorMessage(parsedBody: unknown, statusCode: number, statusText: string): string {
  if (isJsonObject(parsedBody)) {
    if (typeof parsedBody["detail"] === "string" && parsedBody["detail"].trim().length > 0) {
      return parsedBody["detail"].trim();
    }
    if (Array.isArray(parsedBody["detail"]) && parsedBody["detail"].length > 0) {
      return JSON.stringify(parsedBody["detail"]);
    }
    if (typeof parsedBody["error"] === "string" && parsedBody["error"].trim().length > 0) {
      return parsedBody["error"].trim();
    }
    if (typeof parsedBody["message"] === "string" && parsedBody["message"].trim().length > 0) {
      return parsedBody["message"].trim();
    }
  }
  return statusText
    ? `Core API responded with HTTP ${String(statusCode)} (${statusText})`
    : `Core API responded with HTTP ${String(statusCode)}`;
}

/**
 * Выполняет типизированный HTTP-запрос к Python Core и возвращает `Result<TResponse, CoreApiError>`,
 * предотвращая необработанные исключения при недоступности Docker-контейнера (политика Fail-Open).
 */
export async function requestCoreJson<TResponse = Record<string, unknown>>(
  endpointPath: string,
  options?: CoreRequestOptions,
): Promise<Result<TResponse, CoreApiError>> {
  const method = options?.method ?? "GET";
  const timeoutMs = options?.timeoutMs ?? TIMEOUTS_MS.CORE_HTTP_DEFAULT_MS;
  const targetUrl = buildTargetUrl(endpointPath, options?.baseUrl);

  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const compositeSignal = options?.signal
    ? AbortSignal.any([options.signal, timeoutSignal])
    : timeoutSignal;

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options?.headers ?? {}),
  };

  let serializedBody: string | undefined;
  if (options?.body !== undefined) {
    if (!headers["Content-Type"] && !headers["content-type"]) {
      headers["Content-Type"] = "application/json";
    }
    serializedBody = typeof options.body === "string" ? options.body : JSON.stringify(options.body);
  }

  try {
    const response = await fetch(targetUrl, {
      method,
      headers,
      ...(serializedBody !== undefined ? { body: serializedBody } : {}),
      signal: compositeSignal,
    });

    const rawText = await response.text();
    let parsedJson: unknown = undefined;
    if (rawText.trim().length > 0) {
      try {
        parsedJson = JSON.parse(rawText) as unknown;
      } catch (parseCause: unknown) {
        if (response.ok) {
          return err({
            statusCode: response.status,
            message: `Failed to parse JSON response from ${endpointPath}`,
            endpoint: endpointPath,
            cause: parseCause,
          });
        }
      }
    }

    if (!response.ok) {
      return err({
        statusCode: response.status,
        message: extractFastApiErrorMessage(parsedJson, response.status, response.statusText),
        endpoint: endpointPath,
        cause: parsedJson,
      });
    }

    return ok((parsedJson ?? ({} as unknown)) as TResponse);
  } catch (caught: unknown) {
    const errorObj = toError(caught);
    return err({
      statusCode: null,
      message: errorObj.message,
      endpoint: endpointPath,
      cause: caught,
    });
  }
}

function createUnreachableHealthStatus(statusText: string, latencyMs: number): CoreHealthStatus {
  return {
    healthy: false,
    statusText,
    latencyMs,
    reachable: false,
    status: statusText,
    postgres_connected: false,
    redis_connected: false,
    decisions_api_configured: false,
    embedding_api_configured: false,
  };
}

/**
 * Опрашивает диагностический эндпоинт `/health` аналитического ядра с замером латентности.
 * Поддерживает вызов как `(baseUrl?, timeoutMs?)`, так и `(timeoutMs?, baseUrl?)` для обратной совместимости.
 */
export async function checkCoreHealth(
  baseUrl?: string,
  timeoutMs?: number,
): Promise<CoreHealthStatus>;
export async function checkCoreHealth(
  timeoutMs?: number,
  baseUrl?: string,
): Promise<CoreHealthStatus>;
export async function checkCoreHealth(
  firstArg?: string | number,
  secondArg?: number | string,
): Promise<CoreHealthStatus> {
  const baseUrl =
    typeof firstArg === "string"
      ? firstArg
      : typeof secondArg === "string"
        ? secondArg
        : undefined;
  const timeoutMs =
    typeof firstArg === "number"
      ? firstArg
      : typeof secondArg === "number"
        ? secondArg
        : TIMEOUTS_MS.HEALTH_CHECK_MS;

  const startedAt = Date.now();
  const result = await requestCoreJson<Record<string, unknown>>(HEALTH_ENDPOINT, {
    method: "GET",
    baseUrl,
    timeoutMs,
  });
  const latencyMs = Math.max(0, Date.now() - startedAt);

  if (!result.ok) {
    const statusText =
      result.error.statusCode !== null
        ? `http_${String(result.error.statusCode)}`
        : "unreachable";
    return createUnreachableHealthStatus(statusText, latencyMs);
  }

  const data = result.value;
  const status = typeof data["status"] === "string" ? data["status"] : "ok";
  const healthy = status === "ok" || status === "healthy";

  return {
    healthy,
    statusText: status,
    latencyMs,
    payload: data,
    reachable: true,
    status,
    ...(typeof data["version"] === "string" ? { version: data["version"] } : {}),
    postgres_connected: Boolean(data["postgres_connected"]),
    redis_connected: Boolean(data["redis_connected"]),
    decisions_api_configured: Boolean(data["decisions_api_configured"]),
    embedding_api_configured: Boolean(data["embedding_api_configured"]),
    ...(typeof data["antigravity_sdk_configured"] === "boolean"
      ? { antigravity_sdk_configured: data["antigravity_sdk_configured"] }
      : {}),
    ...(typeof data["mcp_endpoint"] === "string" ? { mcp_endpoint: data["mcp_endpoint"] } : {}),
  };
}
