/**
 * Слой 1 (Host CLI TUI Presentation): пошаговый прогресс-логгер конвейеров `init` и `sync`.
 * Управляет живым спиннером `@clack/prompts` в интерактивном терминале (TTY) и автоматически
 * деградирует в дискретные лог-сообщения `log.step` при запуске в CI/headless окружении.
 */

import { log, spinner } from "@clack/prompts";
import process from "node:process";
import { styleText } from "node:util";

/**
 * Контракт управления индикатором прогресса длительных стадий конвейера (`docker compose`, `/health`, компиляция бандла).
 */
export interface PipelineProgress {
  readonly start: (initialMessage: string) => void;
  readonly update: (phaseMessage: string) => void;
  readonly stop: (finalMessage: string) => void;
}

export type PipelineProgressController = PipelineProgress;

/**
 * Создаёт контроллер живого прогресс-спиннера `@clack/prompts` для длительных операций
 * с безопасным фолбэком на построчный вывод при отсутствии интерактивного `stdout.isTTY`.
 */
export function createPipelineProgress(): PipelineProgress {
  const isTty = Boolean(process.stdout.isTTY);
  const activeSpinner = isTty ? spinner() : null;
  let started = false;

  return {
    start(initialMessage: string): void {
      if (activeSpinner) {
        activeSpinner.start(styleText("cyan", initialMessage));
        started = true;
      } else {
        log.step(styleText("cyan", initialMessage));
      }
    },
    update(phaseMessage: string): void {
      if (activeSpinner && started) {
        activeSpinner.message(styleText("cyan", phaseMessage));
      }
    },
    stop(finalMessage: string): void {
      if (activeSpinner && started) {
        activeSpinner.stop(finalMessage);
        started = false;
      } else {
        log.step(finalMessage);
      }
    },
  };
}
