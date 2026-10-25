/**
 * Слой 1 (Host CLI Core API Adapter): клиентские эндпоинты управления каталогом `Harness²`
 * (`GET/PATCH /api/harness/components`) и семантической консолидации (`POST /api/harness/consolidate`).
 * Обеспечивает типизированное получение эффективного состояния компонентов для заданного стека проекта.
 */

import { isJsonObject } from "../common/index.ts";
import { TIMEOUTS_MS } from "../config/index.ts";
import { requestCoreJson } from "./http-client.ts";

export const HARNESS_COMPONENTS_ENDPOINT = "/api/harness/components" as const;
export const HARNESS_CONSOLIDATE_ENDPOINT = "/api/harness/consolidate" as const;

export interface CatalogComponentItem {
  slug: string;
  component_type: string;
  scope: string;
  stack_tags: string[];
  title: string;
  description: string;
  status: string;
  score: number;
  version: number;
  effective_state: string;
}

export type HarnessCatalogItem = CatalogComponentItem;

export interface HarnessCatalogResponse {
  count: number;
  stack_tags: string[];
  agent_type?: string | undefined;
  profile_overrides: Record<string, unknown>;
  components: CatalogComponentItem[];
}

export interface ConsolidationClusterPayload {
  canonical_slug: string;
  component_type: string;
  duplicate_slugs: string[];
  merged_stack_tags: string[];
  max_similarity: number;
  synthesized_title: string;
  synthesized_description: string;
  synthesized_content: string;
  applied: boolean;
}

export interface ConsolidationReportPayload {
  status?: string | undefined;
  dry_run: boolean;
  clusters_found: number;
  components_archived: number;
  clusters: ConsolidationClusterPayload[];
}

export type ConsolidateHarnessResult = ConsolidationReportPayload;

export interface ConsolidateHarnessRequestPayload {
  stack_tags?: readonly string[] | undefined;
  component_type?: string | undefined;
  dry_run?: boolean | undefined;
}

/**
 * Запрашивает каталог всех компонентов `Harness²` (`GET /api/harness/components`)
 * с вычисленным статусом (`effective_state`) для указанного проекта и стека.
 */
export async function fetchHarnessCatalog(
  params: { workspacePath?: string | undefined; stackTags?: readonly string[] | undefined },
  timeoutMs: number = TIMEOUTS_MS.CORE_HYDRATION_MS,
): Promise<HarnessCatalogResponse | null> {
  const searchParams = new URLSearchParams();
  if (params.workspacePath) {
    searchParams.set("workspace_path", params.workspacePath);
  }
  if (params.stackTags && params.stackTags.length > 0) {
    searchParams.set("stack_tags", params.stackTags.join(","));
  }
  const queryString = searchParams.toString();
  const endpointPath = queryString
    ? `${HARNESS_COMPONENTS_ENDPOINT}?${queryString}`
    : HARNESS_COMPONENTS_ENDPOINT;

  const result = await requestCoreJson<Record<string, unknown>>(endpointPath, {
    method: "GET",
    timeoutMs,
  });
  if (!result.ok) {
    return null;
  }

  const data = result.value;
  const rawOverrides = isJsonObject(data["profile_overrides"])
    ? data["profile_overrides"]
    : {};
  const resolvedAgent =
    (typeof data["agent_type"] === "string" && data["agent_type"]) ||
    (typeof rawOverrides["agent"] === "string" && rawOverrides["agent"]) ||
    undefined;

  return {
    count: typeof data["count"] === "number" ? data["count"] : 0,
    stack_tags: Array.isArray(data["stack_tags"]) ? (data["stack_tags"] as string[]) : [],
    ...(resolvedAgent ? { agent_type: resolvedAgent } : {}),
    profile_overrides: rawOverrides,
    components: Array.isArray(data["components"])
      ? (data["components"] as CatalogComponentItem[])
      : [],
  };
}

/**
 * Переключает глобальный статус компонента `Harness²` (`PATCH /api/harness/components/{slug}`).
 */
export async function patchGlobalComponentStatus(
  slug: string,
  enabled: boolean,
  timeoutMs: number = TIMEOUTS_MS.CORE_HTTP_DEFAULT_MS,
): Promise<Record<string, unknown> | null> {
  const encodedSlug = encodeURIComponent(slug.trim());
  const result = await requestCoreJson<Record<string, unknown>>(
    `${HARNESS_COMPONENTS_ENDPOINT}/${encodedSlug}`,
    {
      method: "PATCH",
      body: { enabled },
      timeoutMs,
    },
  );
  return result.ok ? result.value : null;
}

/**
 * Запускает серверную кластеризацию и Lineage-слияние семантических дубликатов `HarnessConsolidator` (`POST /api/harness/consolidate`).
 */
export async function consolidateHarnessComponents(
  payload: ConsolidateHarnessRequestPayload,
  timeoutMs: number = TIMEOUTS_MS.CORE_CONSOLIDATE_MS,
): Promise<ConsolidationReportPayload | null> {
  const result = await requestCoreJson<ConsolidationReportPayload>(
    HARNESS_CONSOLIDATE_ENDPOINT,
    {
      method: "POST",
      body: payload,
      timeoutMs,
    },
  );
  return result.ok ? result.value : null;
}
