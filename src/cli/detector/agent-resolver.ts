/**
 * Слой 1 (Host CLI Detector): каскадное разрешение целевого ИИ-агента (`CLI flag -> ENV -> Workspace Markers -> Default`)
 * и комплексная инспекция рабочего пространства проекта (`inspectWorkspace`).
 * Объединяет результаты детекции агента, сканера стека и инспектора графа `.context/`.
 */

import path from "node:path";
import process from "node:process";

import { pathExistsSync } from "../common/index.ts";
import {
  AGENT_METADATA,
  type AgentType,
  ENV_VARS,
  isValidAgentType,
} from "../config/index.ts";
import { inspectContextGraph } from "./context-inspector.ts";
import { detectStackTags, GLOBAL_STACK_TAG } from "./stack-scanner.ts";

export const SUPPORTED_AGENTS = [
  "antigravity",
  "claude-code",
  "cursor",
  "codex",
  "opencode",
] as const satisfies readonly AgentType[];

export type AgentId = AgentType;

export const AUTO_AGENT_OPTION = "auto" as const;
export type AgentOption = AgentType | typeof AUTO_AGENT_OPTION;

export const DEFAULT_AGENT: AgentType = "antigravity";

export const AGENT_ENV_KEYS = [
  "MCP_COOP_DEFAULT_AGENT",
  "DEFAULT_AGENT",
  ENV_VARS.AGENT,
] as const;

export type AgentResolutionSource =
  | "cli_flag"
  | "environment"
  | "workspace_marker"
  | "default_fallback";

export interface AgentResolutionResult {
  agent: AgentType;
  source: AgentResolutionSource;
  matchedMarker?: string;
}

export type ResolvedAgentInfo = AgentResolutionResult;

export interface WorkspaceInspection {
  workspacePath: string;
  projectName: string;
  agent: AgentType;
  agentSource: AgentResolutionSource;
  matchedAgentMarker?: string;
  stackTags: string[];
  hasContextGraph: boolean;
  contextGraphUpdatedAt?: string;
}

export type WorkspaceProfile = WorkspaceInspection;

export interface AgentWorkspaceMarkerRule {
  readonly agent: AgentType;
  readonly markers: readonly string[];
}

/**
 * Приоритетный список маркеров рабочей директории для автоопределения целевого ИИ-агента.
 * Порядок элементов определяет приоритет выбора при наличии нескольких конфигураций в репозитории.
 */
export const AGENT_WORKSPACE_MARKERS: readonly AgentWorkspaceMarkerRule[] =
  SUPPORTED_AGENTS.map((agent) => ({
    agent,
    markers: AGENT_METADATA[agent].workspaceMarkers,
  }));

/**
 * Проверяет, входит ли переданный строковый идентификатор в список поддерживаемых ИИ-агентов.
 */
export function isSupportedAgent(value: string): value is AgentType {
  return isValidAgentType(value);
}

/**
 * Определяет целевого ИИ-агента для генерации конфигураций и хуков по каскаду приоритетов:
 * 1) явный CLI-флаг `--agent` (если не равен `"auto"`);
 * 2) переменные окружения (`MCP_COOP_DEFAULT_AGENT`, `DEFAULT_AGENT`, `MCP_COOP_AGENT`);
 * 3) маркерные директории или файлы в корне проекта (`AGENT_WORKSPACE_MARKERS`);
 * 4) дефолтный агент фабрики (`DEFAULT_AGENT`).
 */
export function resolveTargetAgent(
  workspaceDir: string,
  requestedAgent?: string,
  env: NodeJS.ProcessEnv = process.env,
): AgentResolutionResult {
  if (requestedAgent !== undefined) {
    const normalizedRequested = requestedAgent.trim().toLowerCase();
    if (
      normalizedRequested.length > 0 &&
      normalizedRequested !== AUTO_AGENT_OPTION
    ) {
      if (!isValidAgentType(normalizedRequested)) {
        throw new Error(
          `Unsupported target agent "${requestedAgent}". Supported values: ${AUTO_AGENT_OPTION}, ${SUPPORTED_AGENTS.join(", ")}.`,
        );
      }
      return {
        agent: normalizedRequested,
        source: "cli_flag",
      };
    }
  }

  for (const envKey of AGENT_ENV_KEYS) {
    const rawEnvValue = env[envKey];
    if (!rawEnvValue) {
      continue;
    }
    const normalizedEnvValue = rawEnvValue.trim().toLowerCase();
    if (
      normalizedEnvValue.length === 0 ||
      normalizedEnvValue === AUTO_AGENT_OPTION
    ) {
      continue;
    }
    if (isValidAgentType(normalizedEnvValue)) {
      return {
        agent: normalizedEnvValue,
        source: "environment",
        matchedMarker: envKey,
      };
    }
  }

  const resolvedDir = path.resolve(workspaceDir);
  for (const rule of AGENT_WORKSPACE_MARKERS) {
    for (const marker of rule.markers) {
      if (pathExistsSync(path.join(resolvedDir, marker))) {
        return {
          agent: rule.agent,
          source: "workspace_marker",
          matchedMarker: marker,
        };
      }
    }
  }

  return {
    agent: DEFAULT_AGENT,
    source: "default_fallback",
  };
}

/**
 * Собирает полный профиль рабочей директории проекта: абсолютный путь, имя проекта,
 * выбранного ИИ-агента с источником решения, теги технологического стека и статус графа `.context/`.
 */
export function inspectWorkspace(
  workspaceDir: string = process.cwd(),
  requestedAgent?: string,
  env: NodeJS.ProcessEnv = process.env,
): WorkspaceInspection {
  const resolvedWorkspacePath = path.resolve(workspaceDir);
  const projectName =
    path.basename(resolvedWorkspacePath) || GLOBAL_STACK_TAG;
  const resolvedAgent = resolveTargetAgent(
    resolvedWorkspacePath,
    requestedAgent,
    env,
  );
  const stackTags = detectStackTags(resolvedWorkspacePath);
  const contextGraph = inspectContextGraph(resolvedWorkspacePath);

  return {
    workspacePath: resolvedWorkspacePath,
    projectName,
    agent: resolvedAgent.agent,
    agentSource: resolvedAgent.source,
    matchedAgentMarker: resolvedAgent.matchedMarker,
    stackTags,
    hasContextGraph: contextGraph.hasContextGraph,
    contextGraphUpdatedAt: contextGraph.contextGraphUpdatedAt,
  };
}
