/**
 * Слой 1 (Host CLI Core API Adapter): эндпоинты инжеста пользовательских компонентов (`POST /api/sync/ingest`)
 * и двусторонней 3-Way Hash синхронизации узлов графа `.context/` (`POST /api/sync/context`).
 * Передаёт хеши и метаданные рабочей директории в `WorkspaceSyncEngine` аналитического ядра.
 */

import { TIMEOUTS_MS } from "../config/index.ts";
import { requestCoreJson } from "./http-client.ts";

export const SYNC_INGEST_ENDPOINT = "/api/sync/ingest" as const;
export const SYNC_CONTEXT_ENDPOINT = "/api/sync/context" as const;

export interface WorkspaceComponentCandidatePayload {
  slug: string;
  component_type: "skill" | "system_prompt" | "tool_guidance";
  title: string;
  description: string;
  content: string;
  content_hash: string;
  stack_tags: string[];
  file_updated_at?: string | undefined;
  metadata?: Record<string, unknown> | undefined;
}

export interface ComponentIngestItemPayload {
  slug: string;
  component_type: string;
  action: string;
  version: number;
  reason?: string | null | undefined;
}

export interface ComponentIngestReportPayload {
  status?: string | undefined;
  workspace_path: string;
  processed: number;
  created: number;
  updated: number;
  unchanged: number;
  rejected: number;
  items: ComponentIngestItemPayload[];
}

export interface ContextNodeSyncInputPayload {
  relative_path: string;
  node_id: string;
  entity_class: string;
  content: string;
  content_hash: string;
  base_hash: string;
  file_updated_at?: string | undefined;
}

export interface ContextNodeSyncItemPayload {
  relative_path: string;
  node_id: string;
  action: "unchanged" | "pushed_to_db" | "pulled_from_db" | "merged";
  content_hash: string;
  base_hash: string;
  content?: string | null | undefined;
}

export interface ContextSyncReportPayload {
  status?: string | undefined;
  workspace_path: string;
  unchanged: number;
  pushed_to_db: number;
  pulled_from_db: number;
  merged: number;
  items: ContextNodeSyncItemPayload[];
}

/**
 * Отправляет кастомные скиллы и правила рабочей директории на серверный инжест (`POST /api/sync/ingest`).
 */
export async function ingestWorkspaceComponents(
  payload: {
    workspace_path: string;
    stack_tags: readonly string[];
    components: readonly WorkspaceComponentCandidatePayload[];
  },
  timeoutMs: number = TIMEOUTS_MS.CORE_INGEST_MS,
): Promise<ComponentIngestReportPayload | null> {
  const result = await requestCoreJson<ComponentIngestReportPayload>(SYNC_INGEST_ENDPOINT, {
    method: "POST",
    body: payload,
    timeoutMs,
  });
  return result.ok ? result.value : null;
}

/**
 * Выполняет 3-Way Hash сверку и AST-мерж локальных узлов `.context/` с таблицей `project_context_nodes` (`POST /api/sync/context`).
 */
export async function syncWorkspaceContextNodes(
  payload: {
    workspace_path: string;
    nodes: readonly ContextNodeSyncInputPayload[];
  },
  timeoutMs: number = TIMEOUTS_MS.CORE_CONTEXT_SYNC_MS,
): Promise<ContextSyncReportPayload | null> {
  const result = await requestCoreJson<ContextSyncReportPayload>(SYNC_CONTEXT_ENDPOINT, {
    method: "POST",
    body: payload,
    timeoutMs,
  });
  return result.ok ? result.value : null;
}
