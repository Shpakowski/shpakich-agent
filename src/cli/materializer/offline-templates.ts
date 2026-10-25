/**
 * Слой 1 (Host CLI Materializer): генератор автономных (Offline Fallback) шаблонов адаптеров.
 * Формирует минимальный рабочий комплект правил, бутстрап-скилла, хуков и MCP-конфигов для всех
 * 5 поддерживаемых ИИ-агентов, когда Docker-ядро фабрики недоступно во время `init` или `sync`.
 */

import {
  GLOBAL_FACTORY_DIR_NAME,
  OFFLINE_ORCHESTRATOR_SLUG,
  resolveMcpEndpointUrl,
} from "../config/index.ts";
import { type AgentId, GLOBAL_STACK_TAG } from "../detector/index.ts";
import {
  FACTORY_HOOK_SIGNATURE,
  type HookEventName,
  type RenderedFilePayload,
} from "./manifest-store.ts";

export const DEFAULT_OFFLINE_MCP_URL = "http://127.0.0.1:8787/mcp" as const;
export const DEFAULT_MCP_SERVER_KEY = "mcp-coop-factory" as const;
export const CODEX_MCP_SERVER_KEY = "mcp_coop_factory" as const;
export const DEFAULT_HOOK_TIMEOUT_SECONDS = 15 as const;

function buildHookCommand(event: HookEventName, agent: AgentId): string {
  return `"$HOME/${GLOBAL_FACTORY_DIR_NAME}/bin/run-hook.sh" hook ${event} --agent ${agent}`;
}

/**
 * Генерирует автономный Markdown-файл инженерных правил `Harness²` с активными тегами стека.
 */
export function buildOfflineRulesMarkdown(
  stackTags: readonly string[],
): string {
  const normalizedTags =
    stackTags.length > 0 ? stackTags.join(", ") : GLOBAL_STACK_TAG;
  return [
    "# MCP-COOP Agentic Factory — Active Engineering Rules (`Harness²` Offline Seed)",
    "",
    `> **Compiled Stack Tags:** \`${normalizedTags}\``,
    "",
    "- Keep `.context/index.md` and `.context/entities/` synchronized with architectural changes.",
    "- Enforce KISS, DRY, SOLID, YAGNI, and Zero-Hardcode across all modules.",
    "- Query cross-project memory and the Context Graph via FastMCP tools when Core is online.",
    "",
  ].join("\n");
}

/**
 * Генерирует автономный бутстрап-скилл `harness-orchestrator` (`SKILL.md`) для навигации
 * по графу `.context/` и инструментам `FastMCP`.
 */
export function buildOfflineBootstrapSkillMarkdown(
  stackTags: readonly string[] = [],
): string {
  const normalizedTags =
    stackTags.length > 0 ? stackTags.join(", ") : GLOBAL_STACK_TAG;
  return [
    "---",
    `name: ${OFFLINE_ORCHESTRATOR_SLUG}`,
    'description: "Unified bootstrap skill for navigating the MCP-COOP Agentic Factory, querying dynamic skills and error memories, and synchronizing the .context/ knowledge graph."',
    "---",
    "",
    "# Harness² Bootstrap Orchestrator (Offline Seed)",
    "",
    `> **Active Stack Tags:** \`${normalizedTags}\``,
    "",
    "1. Read `.context/index.md` before running broad filesystem searches.",
    "2. Keep `.context/entities/` synchronized whenever source files or contracts change.",
    "3. Use FastMCP tools (`coop_search_memory`, `coop_query_context_graph`, `coop_upsert_context_node`) when the Docker Core is online.",
    "",
  ].join("\n");
}

function buildClaudeCodexHooksPayload(agent: AgentId): string {
  const eventPairs: ReadonlyArray<readonly [HookEventName, string]> = [
    ["session-start", "SessionStart"],
    ["pre-tool-use", "PreToolUse"],
    ["post-tool-use", "PostToolUse"],
    ["pre-invocation", "UserPromptSubmit"],
    ["stop", "Stop"],
  ];
  const hooks: Record<string, unknown> = {};
  for (const [canonical, vendor] of eventPairs) {
    hooks[vendor] = [
      {
        matcher: "*",
        hooks: [
          {
            type: "command",
            command: buildHookCommand(canonical, agent),
            timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
          },
        ],
      },
    ];
  }
  return `${JSON.stringify({ hooks }, null, 2)}\n`;
}

/**
 * Генерирует JSON-конфигурацию жизненных хуков в диалекте целевого ИИ-агента
 * (`Antigravity`, `Cursor`, `Claude Code` / `Codex`).
 */
export function buildOfflineHooksJson(agent: AgentId): string {
  if (agent === "antigravity") {
    return `${JSON.stringify(
      {
        [FACTORY_HOOK_SIGNATURE]: {
          enabled: true,
          SessionStart: [
            {
              type: "command",
              command: buildHookCommand("session-start", agent),
              timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
            },
          ],
          PreToolUse: [
            {
              matcher: ".*",
              hooks: [
                {
                  type: "command",
                  command: buildHookCommand("pre-tool-use", agent),
                  timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
                },
              ],
            },
          ],
          PostToolUse: [
            {
              matcher: ".*",
              hooks: [
                {
                  type: "command",
                  command: buildHookCommand("post-tool-use", agent),
                  timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
                },
              ],
            },
          ],
          PreInvocation: [
            {
              type: "command",
              command: buildHookCommand("pre-invocation", agent),
              timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
            },
          ],
          Stop: [
            {
              type: "command",
              command: buildHookCommand("stop", agent),
              timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
            },
          ],
        },
      },
      null,
      2,
    )}\n`;
  }

  if (agent === "cursor") {
    return `${JSON.stringify(
      {
        version: 1,
        hooks: {
          sessionStart: [
            {
              command: buildHookCommand("session-start", agent),
              timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
            },
          ],
          preToolUse: [
            {
              command: buildHookCommand("pre-tool-use", agent),
              timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
            },
          ],
          postToolUse: [
            {
              command: buildHookCommand("post-tool-use", agent),
              timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
            },
          ],
          beforeSubmitPrompt: [
            {
              command: buildHookCommand("pre-invocation", agent),
              timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
            },
          ],
          stop: [
            {
              command: buildHookCommand("stop", agent),
              timeout: DEFAULT_HOOK_TIMEOUT_SECONDS,
            },
          ],
        },
      },
      null,
      2,
    )}\n`;
  }

  return buildClaudeCodexHooksPayload(agent);
}

/**
 * Генерирует JSON-конфигурацию `opencode.json` со ссылками на инструкции и удалённый шлюз `FastMCP`.
 */
export function buildOfflineOpencodeConfigJson(
  mcpUrl: string = resolveMcpEndpointUrl(),
): string {
  return `${JSON.stringify(
    {
      $schema: "https://opencode.ai/config.json",
      instructions: ["AGENTS.md", ".context/index.md", ".opencode/rules/*.md"],
      mcp: {
        [DEFAULT_MCP_SERVER_KEY]: {
          type: "remote",
          url: mcpUrl,
          enabled: true,
        },
      },
    },
    null,
    2,
  )}\n`;
}

/**
 * Генерирует JSON-конфигурацию подключения к локальному серверу `FastMCP` для выбранного агента.
 */
export function buildOfflineMcpJson(
  agent: AgentId,
  mcpUrl: string = resolveMcpEndpointUrl(),
): string {
  if (agent === "claude-code") {
    return `${JSON.stringify(
      {
        mcpServers: {
          [DEFAULT_MCP_SERVER_KEY]: {
            type: "http",
            url: mcpUrl,
          },
        },
      },
      null,
      2,
    )}\n`;
  }

  if (agent === "opencode") {
    return buildOfflineOpencodeConfigJson(mcpUrl);
  }

  return `${JSON.stringify(
    { mcpServers: { [DEFAULT_MCP_SERVER_KEY]: { url: mcpUrl } } },
    null,
    2,
  )}\n`;
}

/**
 * Генерирует TOML-конфигурацию `.codex/config.toml` с включённым флагом `codex_hooks = true`
 * и регистрацией MCP-сервера фабрики.
 */
export function buildOfflineCodexConfigToml(
  mcpUrl: string = resolveMcpEndpointUrl(),
): string {
  return `[features]\ncodex_hooks = true\n\n[mcp_servers.${CODEX_MCP_SERVER_KEY}]\nenabled = true\nurl = "${mcpUrl}"\n`;
}

/**
 * Генерирует минимальный автономный набор файлов адаптера (`RenderedFilePayload[]`) для выбранного
 * ИИ-агента, когда глобальный Docker-контейнер ядра недоступен во время выполнения `init` или `sync`.
 */
export function buildOfflineFallbackFiles(
  agent: AgentId,
  stackTags: readonly string[],
): RenderedFilePayload[] {
  const baseRulesMd = buildOfflineRulesMarkdown(stackTags);
  const mcpUrl = resolveMcpEndpointUrl();

  if (agent === "antigravity") {
    const agyRoot = `.agents/plugins/${FACTORY_HOOK_SIGNATURE}`;
    return [
      {
        relative_path: `${agyRoot}/plugin.json`,
        content: `${JSON.stringify(
          {
            name: FACTORY_HOOK_SIGNATURE,
            displayName: "MCP-COOP Agentic Factory",
            version: "0.1.0",
            description:
              "Self-evolving Harness² engineering rules, PreToolUse guardrails, and .context/ Graph-as-Code sync.",
            disabled: false,
            suggestedPrompts: [
              "Inspect the .context/ knowledge graph and validate all nodes",
            ],
          },
          null,
          2,
        )}\n`,
        write_strategy: "overwrite",
      },
      {
        relative_path: `${agyRoot}/rules/AGENTS.md`,
        content: baseRulesMd,
        write_strategy: "overwrite",
      },
      {
        relative_path: `${agyRoot}/mcp_config.json`,
        content: buildOfflineMcpJson(agent, mcpUrl),
        write_strategy: "overwrite",
      },
      {
        relative_path: `${agyRoot}/hooks.json`,
        content: buildOfflineHooksJson(agent),
        write_strategy: "overwrite",
      },
    ];
  }

  if (agent === "claude-code") {
    return [
      {
        relative_path: ".claude/rules/mcp-coop-factory.md",
        content: baseRulesMd,
        write_strategy: "overwrite",
      },
      {
        relative_path: ".mcp.json",
        content: buildOfflineMcpJson(agent, mcpUrl),
        write_strategy: "merge_json",
      },
      {
        relative_path: ".claude/settings.json",
        content: buildOfflineHooksJson(agent),
        write_strategy: "merge_json",
      },
    ];
  }

  if (agent === "cursor") {
    const cursorMdc = `---\ndescription: MCP-COOP Agentic Factory always-on engineering rules\nglobs: "*"\nalwaysApply: true\n---\n\n${baseRulesMd}`;
    return [
      {
        relative_path: ".cursor/rules/mcp-coop-factory.mdc",
        content: cursorMdc,
        write_strategy: "overwrite",
      },
      {
        relative_path: ".cursor/mcp.json",
        content: buildOfflineMcpJson(agent, mcpUrl),
        write_strategy: "merge_json",
      },
      {
        relative_path: ".cursor/hooks.json",
        content: buildOfflineHooksJson(agent),
        write_strategy: "merge_json",
      },
    ];
  }

  if (agent === "codex") {
    return [
      {
        relative_path: "AGENTS.md",
        content: baseRulesMd,
        write_strategy: "overwrite",
      },
      {
        relative_path: ".codex/config.toml",
        content: buildOfflineCodexConfigToml(mcpUrl),
        write_strategy: "merge_toml",
      },
      {
        relative_path: ".codex/hooks.json",
        content: buildOfflineHooksJson(agent),
        write_strategy: "merge_json",
      },
    ];
  }

  return [
    {
      relative_path: ".opencode/rules/mcp-coop-factory.md",
      content: baseRulesMd,
      write_strategy: "overwrite",
    },
    {
      relative_path: "opencode.json",
      content: buildOfflineOpencodeConfigJson(mcpUrl),
      write_strategy: "merge_json",
    },
    {
      relative_path: ".opencode/plugins/mcp-coop-factory.ts",
      content: [
        "// Auto-generated OpenCode lifecycle plugin for MCP-COOP Agentic Factory",
        "export const McpCoopFactoryPlugin = async ({ $ }: { $: any }) => ({",
        '  "tool.execute.before": async () => {',
        `    await $\`${buildHookCommand("pre-tool-use", agent)}\`;`,
        "  },",
        '  "tool.execute.after": async () => {',
        `    await $\`${buildHookCommand("post-tool-use", agent)}\`;`,
        "  },",
        '  "session.idle": async () => {',
        `    await $\`${buildHookCommand("stop", agent)}\`;`,
        "  },",
        "});",
        "export default McpCoopFactoryPlugin;",
        "",
      ].join("\n"),
      write_strategy: "overwrite",
    },
  ];
}
