/**
 * Слой 1 (Host CLI Commands — Hook Slice): обработчик технической команды `hook`.
 * Изолирует выполнение жизненных хуков IDE (`SessionStart`, `PreToolUse`, `PostToolUse`,
 * `PreInvocation`, `Stop`) от TUI-оформления, сохраняя чистый JSON-поток в `stdout`.
 */

import {
  executeHookCommand,
  resolveCanonicalHookEvent,
} from "../hooks/index.ts";
import type { ParsedCliContext } from "./arg-parser.ts";

export { resolveCanonicalHookEvent };

/**
 * Делегирует выполнение жизненного хука в `executeHookCommand` и возвращает
 * код завершения `0` по контракту Fail-Open.
 */
export async function handleHookSubcommand(
  ctx: ParsedCliContext,
): Promise<number> {
  await executeHookCommand(ctx.subargs[0] ?? "", ctx.agent);
  return 0;
}
