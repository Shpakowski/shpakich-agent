/**
 * Слой 1 (Host CLI Materializer): стратегии неразрушающего слияния конфигурационных файлов.
 * Реализует идемпотентный deep-merge JSON (`mcp.json`, `settings.json`, `hooks.json`, `opencode.json`),
 * секционное слияние TOML (`.codex/config.toml`) и маркерное слияние управляемых блоков Markdown/TOML.
 */

import { resolveMcpEndpointUrl } from "../config/index.ts";
import {
  FACTORY_HOOK_SIGNATURE,
  type WriteStrategy,
} from "./manifest-store.ts";

export const MANAGED_MD_START = "<!-- MCP-COOP-FACTORY:START -->" as const;
export const MANAGED_MD_END = "<!-- MCP-COOP-FACTORY:END -->" as const;
export const MANAGED_TOML_START = "# --- MCP-COOP-FACTORY:START ---" as const;
export const MANAGED_TOML_END = "# --- MCP-COOP-FACTORY:END ---" as const;

const CODEX_MCP_SERVER_KEY = "harness_factory" as const;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Рекурсивно объединяет два JSON-объекта без потери пользовательских ключей верхнего и вложенного уровней.
 * Массивы строк дедуплицируются для сохранения порядка и идемпотентности.
 */
export function deepMergeObjects(
  base: Record<string, unknown>,
  incoming: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = { ...base };
  for (const [key, incVal] of Object.entries(incoming)) {
    const baseVal = base[key];
    if (isPlainObject(baseVal) && isPlainObject(incVal)) {
      result[key] = deepMergeObjects(baseVal, incVal);
    } else if (
      Array.isArray(baseVal) &&
      Array.isArray(incVal) &&
      baseVal.every((item) => typeof item === "string") &&
      incVal.every((item) => typeof item === "string")
    ) {
      result[key] = Array.from(new Set([...baseVal, ...incVal]));
    } else {
      result[key] = incVal;
    }
  }
  return result;
}

function isDisabledFactoryCommand(
  command: string,
  disabledSet: ReadonlySet<string>,
): boolean {
  for (const eventName of disabledSet) {
    if (
      command.includes(`hook ${eventName} `) ||
      command.endsWith(`hook ${eventName}`)
    ) {
      return true;
    }
  }
  return false;
}

function filterHookEntriesList(
  entries: readonly unknown[],
  stripAllFactory: boolean,
  disabledSet: ReadonlySet<string>,
): unknown[] {
  const kept: unknown[] = [];
  for (const entry of entries) {
    if (!isPlainObject(entry)) {
      kept.push(entry);
      continue;
    }
    const innerHooks = entry["hooks"];
    if (Array.isArray(innerHooks)) {
      const filteredInner = filterHookEntriesList(
        innerHooks,
        stripAllFactory,
        disabledSet,
      );
      if (filteredInner.length > 0) {
        kept.push({ ...entry, hooks: filteredInner });
      }
      continue;
    }
    const rawCmd = entry["command"];
    const cmd = typeof rawCmd === "string" ? rawCmd : "";
    if (cmd.includes(FACTORY_HOOK_SIGNATURE)) {
      if (stripAllFactory || isDisabledFactoryCommand(cmd, disabledSet)) {
        continue;
      }
    }
    kept.push(entry);
  }
  return kept;
}

/**
 * Фильтрует дерево хуков (`hooks` или `mcp-coop-agentic-factory`), удаляя отключённые пользователем
 * события из `disabledHooks` или полностью очищая старые записи фабрики перед перекомпиляцией.
 */
export function filterDisabledHooksFromPayload(
  payload: unknown,
  disabledHooks: readonly string[],
  stripAllFactory: boolean = false,
): unknown {
  const disabledSet = new Set(
    disabledHooks.map((item) => item.trim().toLowerCase()),
  );
  if (Array.isArray(payload)) {
    return filterHookEntriesList(payload, stripAllFactory, disabledSet);
  }
  if (!isPlainObject(payload)) {
    return payload;
  }

  const filteredMap: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(payload)) {
    if (Array.isArray(val)) {
      const kept = filterHookEntriesList(val, stripAllFactory, disabledSet);
      if (kept.length > 0) {
        filteredMap[key] = kept;
      }
    } else if (isPlainObject(val)) {
      filteredMap[key] = filterDisabledHooksFromPayload(
        val,
        disabledHooks,
        stripAllFactory,
      );
    } else {
      filteredMap[key] = val;
    }
  }
  return filteredMap;
}

function mergeHookEventMap(
  existingMap: Record<string, unknown>,
  incomingMap: Record<string, unknown>,
  disabledSet: ReadonlySet<string>,
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  const allKeys = new Set([
    ...Object.keys(existingMap),
    ...Object.keys(incomingMap),
  ]);

  for (const key of allKeys) {
    const existingVal = existingMap[key];
    const incomingVal = incomingMap[key];
    if (!Array.isArray(existingVal) && !Array.isArray(incomingVal)) {
      result[key] = incomingVal !== undefined ? incomingVal : existingVal;
      continue;
    }
    const preservedExisting = Array.isArray(existingVal)
      ? filterHookEntriesList(existingVal, true, disabledSet)
      : [];
    const allowedIncoming = Array.isArray(incomingVal)
      ? filterHookEntriesList(incomingVal, false, disabledSet)
      : [];
    const mergedEntries = [...preservedExisting, ...allowedIncoming];
    if (mergedEntries.length > 0) {
      result[key] = mergedEntries;
    }
  }
  return result;
}

/**
 * Идемпотентно объединяет существующий и новый JSON-конфиги (`mcp.json`, `settings.json`, `hooks.json`,
 * `opencode.json`). Сохраняет пользовательские MCP-серверы и кастомные хуки, заменяя только записи
 * с сигнатурой `mcp-coop-agentic-factory` и исключая события из `disabledHooks`.
 */
export function mergeJsonContent(
  existingRaw: string,
  incomingRaw: string,
  disabledHooks: readonly string[] = [],
): string {
  let incomingObj: Record<string, unknown>;
  try {
    const parsedIncoming = JSON.parse(incomingRaw) as unknown;
    if (!isPlainObject(parsedIncoming)) {
      return incomingRaw;
    }
    incomingObj = parsedIncoming;
  } catch {
    return incomingRaw;
  }

  let existingObj: Record<string, unknown> = {};
  if (existingRaw.trim().length > 0) {
    try {
      const parsedExisting = JSON.parse(existingRaw) as unknown;
      if (isPlainObject(parsedExisting)) {
        existingObj = parsedExisting;
      }
    } catch {
      existingObj = {};
    }
  }

  const merged: Record<string, unknown> = { ...existingObj };
  const disabledSet = new Set(
    disabledHooks.map((item) => item.trim().toLowerCase()),
  );

  for (const [key, incValue] of Object.entries(incomingObj)) {
    const exValue = existingObj[key];

    if ((key === "mcpServers" || key === "mcp") && isPlainObject(incValue)) {
      merged[key] = isPlainObject(exValue)
        ? { ...exValue, ...incValue }
        : { ...incValue };
    } else if (key === "instructions" && Array.isArray(incValue)) {
      const exList = Array.isArray(exValue) ? exValue : [];
      const combined = new Set<string>();
      for (const item of [...exList, ...incValue]) {
        if (typeof item === "string" && item.trim().length > 0) {
          combined.add(item);
        }
      }
      merged[key] = Array.from(combined);
    } else if (
      (key === "hooks" || key === FACTORY_HOOK_SIGNATURE) &&
      isPlainObject(incValue)
    ) {
      merged[key] = mergeHookEventMap(
        isPlainObject(exValue) ? exValue : {},
        incValue,
        disabledSet,
      );
    } else if (isPlainObject(exValue) && isPlainObject(incValue)) {
      merged[key] = deepMergeObjects(exValue, incValue);
    } else {
      merged[key] = incValue;
    }
  }

  return `${JSON.stringify(merged, null, 2)}\n`;
}

/**
 * Объединяет текстовый файл по управляемым маркерам `startMarker` / `endMarker`.
 * Если блок маркеров уже присутствует в `existingRaw`, обновляет только его внутреннее содержимое,
 * иначе дописывает управляемый блок в конец файла с сохранением пользовательского текста.
 */
export function mergeSectionContent(
  existingRaw: string,
  incomingRaw: string,
  startMarker: string = MANAGED_MD_START,
  endMarker: string = MANAGED_MD_END,
): string {
  const trimmedIncoming = incomingRaw.trim();
  const managedBlock =
    trimmedIncoming.startsWith(startMarker) &&
    trimmedIncoming.endsWith(endMarker)
      ? trimmedIncoming
      : `${startMarker}\n${trimmedIncoming}\n${endMarker}`;

  if (existingRaw.trim().length === 0) {
    return `${managedBlock}\n`;
  }

  const startIdx = existingRaw.indexOf(startMarker);
  const endIdx = existingRaw.indexOf(endMarker);
  if (startIdx >= 0 && endIdx > startIdx) {
    const before = existingRaw.slice(0, startIdx);
    const after = existingRaw.slice(endIdx + endMarker.length);
    return `${before}${managedBlock}${after.endsWith("\n") ? after : `${after}\n`}`;
  }

  return `${existingRaw.trimEnd()}\n\n${managedBlock}\n`;
}

interface TomlSection {
  header: string;
  lines: string[];
}

function parseTomlSections(raw: string): TomlSection[] {
  const sections: TomlSection[] = [{ header: "", lines: [] }];
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  for (const line of lines) {
    const match = /^\s*\[([^\]]+)\]\s*$/.exec(line);
    if (match && match[1]) {
      sections.push({ header: match[1].trim(), lines: [line] });
    } else {
      const current = sections[sections.length - 1];
      if (current) {
        current.lines.push(line);
      }
    }
  }
  return sections;
}

/**
 * Секционный слиятель для `.codex/config.toml`. Гарантирует установку `codex_hooks = true`
 * в секции `[features]` и обновляет блок `[mcp_servers.mcp_coop_factory]`, сохраняя все
 * остальные пользовательские секции и комментарии.
 */
export function mergeTomlContent(
  existingRaw: string,
  incomingRaw: string,
): string {
  if (existingRaw.trim().length === 0) {
    return `${incomingRaw.trim()}\n`;
  }

  if (
    existingRaw.includes(MANAGED_TOML_START) &&
    incomingRaw.includes(MANAGED_TOML_START)
  ) {
    return mergeSectionContent(
      existingRaw,
      incomingRaw,
      MANAGED_TOML_START,
      MANAGED_TOML_END,
    );
  }

  const existingSections = parseTomlSections(existingRaw);
  const incomingSections = parseTomlSections(incomingRaw);
  const incomingMcpSection = incomingSections.find(
    (sec) => sec.header === `mcp_servers.${CODEX_MCP_SERVER_KEY}`,
  );

  let hasFeatures = false;
  for (const sec of existingSections) {
    if (sec.header === "features") {
      hasFeatures = true;
      let foundKey = false;
      sec.lines = sec.lines.map((line) => {
        if (/^\s*codex_hooks\s*=/.test(line)) {
          foundKey = true;
          return "codex_hooks = true";
        }
        return line;
      });
      if (!foundKey) {
        sec.lines.push("codex_hooks = true");
      }
    }
  }

  if (!hasFeatures) {
    existingSections.push({
      header: "features",
      lines: ["[features]", "codex_hooks = true"],
    });
  }

  const targetMcpHeader = `mcp_servers.${CODEX_MCP_SERVER_KEY}`;
  const replacementMcpLines = incomingMcpSection
    ? incomingMcpSection.lines.filter((line, idx, arr) =>
        idx < arr.length - 1 ? true : line.trim().length > 0,
      )
    : [
        `[${targetMcpHeader}]`,
        "enabled = true",
        `url = "${resolveMcpEndpointUrl()}"`,
      ];

  const existingMcpIdx = existingSections.findIndex(
    (sec) => sec.header === targetMcpHeader,
  );
  if (existingMcpIdx >= 0) {
    existingSections[existingMcpIdx] = {
      header: targetMcpHeader,
      lines: replacementMcpLines,
    };
  } else {
    existingSections.push({
      header: targetMcpHeader,
      lines: replacementMcpLines,
    });
  }

  const renderedBlocks = existingSections
    .map((sec) => sec.lines.join("\n").trim())
    .filter((block) => block.length > 0);

  return `${renderedBlocks.join("\n\n")}\n`;
}

/**
 * Применяет выбранную стратегию записи (`WriteStrategy`) к паре `(existingRaw, incomingRaw)`
 * и вычисляет итоговое содержимое вместе с флагом необходимости записи на диск.
 */
export function applyWriteStrategy(
  strategy: WriteStrategy,
  existingRaw: string | null,
  incomingRaw: string,
  options?: {
    relativePath?: string | undefined;
    disabledHooks?: readonly string[] | undefined;
  },
): { content: string; shouldWrite: boolean } {
  const disabledHooks = options?.disabledHooks ?? [];
  const relativePath = options?.relativePath ?? "";

  if (strategy === "create_if_missing" && existingRaw !== null) {
    return { content: existingRaw, shouldWrite: false };
  }

  let nextContent = incomingRaw;

  if (strategy === "merge_json" || strategy === "json_deep_merge") {
    nextContent = mergeJsonContent(
      existingRaw ?? "",
      incomingRaw,
      disabledHooks,
    );
  } else if (strategy === "merge_toml" || strategy === "toml_section_merge") {
    nextContent = mergeTomlContent(existingRaw ?? "", incomingRaw);
  } else if (strategy === "markdown_section_merge") {
    nextContent = mergeSectionContent(
      existingRaw ?? "",
      incomingRaw,
      MANAGED_MD_START,
      MANAGED_MD_END,
    );
  } else if (
    strategy === "overwrite" &&
    relativePath.endsWith("hooks.json") &&
    disabledHooks.length > 0
  ) {
    nextContent = mergeJsonContent("", incomingRaw, disabledHooks);
  }

  if (existingRaw !== null && existingRaw === nextContent) {
    return { content: nextContent, shouldWrite: false };
  }

  return { content: nextContent, shouldWrite: true };
}
