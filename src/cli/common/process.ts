/**
 * Слой 1 (Host CLI Common Foundation): асинхронный запуск дочерних процессов и системных команд.
 * Координирует выполнение подпроцессов с композицией дедлайнов через `AbortSignal.timeout` / `AbortSignal.any`,
 * передачей `stdin` и кроссплатформенным открытием URL в браузере пользователя.
 */

import { spawn } from "node:child_process";

const UTF8_ENCODING = "utf-8" as const;
const ABORT_ERROR_NAME = "AbortError" as const;
const EXIT_CODE_SPAWN_ERROR = 1 as const;
const EXIT_CODE_TIMED_OUT = 124 as const;
const EXIT_CODE_ABORTED = 130 as const;

const OPEN_BROWSER_DARWIN = "open" as const;
const OPEN_BROWSER_LINUX = "xdg-open" as const;
const OPEN_BROWSER_WIN32 = "cmd" as const;

/**
 * Параметры запуска дочернего процесса в `runSubprocess`.
 */
export interface SubprocessOptions {
  readonly cwd?: string | undefined;
  readonly env?: NodeJS.ProcessEnv | undefined;
  readonly timeoutMs?: number | undefined;
  readonly signal?: AbortSignal | undefined;
  readonly stdinData?: string | undefined;
  readonly inheritStdio?: boolean | undefined;
}

/**
 * Унифицированный результат завершения дочернего процесса.
 */
export interface SubprocessResult {
  readonly exitCode: number;
  readonly stdout: string;
  readonly stderr: string;
  readonly timedOut: boolean;
}

function buildEffectiveAbortSignal(options?: SubprocessOptions): {
  readonly effectiveSignal: AbortSignal | undefined;
  readonly timeoutSignal: AbortSignal | undefined;
} {
  const timeoutSignal =
    options?.timeoutMs !== undefined && options.timeoutMs > 0
      ? AbortSignal.timeout(options.timeoutMs)
      : undefined;

  if (options?.signal && timeoutSignal) {
    return {
      effectiveSignal: AbortSignal.any([options.signal, timeoutSignal]),
      timeoutSignal,
    };
  }

  return {
    effectiveSignal: options?.signal ?? timeoutSignal,
    timeoutSignal,
  };
}

/**
 * Запускает внешний процесс через `node:child_process.spawn` с поддержкой `AbortSignal`,
 * таймаутов и потоковой передачей `stdinData`, возвращая `SubprocessResult` без необработанных исключений.
 */
export async function runSubprocess(
  command: string,
  args: readonly string[],
  options?: SubprocessOptions,
): Promise<SubprocessResult> {
  const { promise, resolve } = Promise.withResolvers<SubprocessResult>();
  const { effectiveSignal, timeoutSignal } = buildEffectiveAbortSignal(options);

  if (effectiveSignal?.aborted) {
    const timedOut = Boolean(timeoutSignal?.aborted);
    resolve({
      exitCode: timedOut ? EXIT_CODE_TIMED_OUT : EXIT_CODE_ABORTED,
      stdout: "",
      stderr: timedOut ? "Subprocess timed out before start" : "Subprocess aborted before start",
      timedOut,
    });
    return promise;
  }

  const stdoutChunks: Buffer[] = [];
  const stderrChunks: Buffer[] = [];
  let settled = false;

  const finalize = (exitCode: number, fallbackStderr?: string): void => {
    if (settled) {
      return;
    }
    settled = true;
    const stdout = Buffer.concat(stdoutChunks).toString(UTF8_ENCODING);
    const rawStderr = Buffer.concat(stderrChunks).toString(UTF8_ENCODING);
    const stderr =
      rawStderr.length > 0 ? rawStderr : (fallbackStderr ?? "");
    const timedOut = Boolean(timeoutSignal?.aborted);
    resolve({
      exitCode: timedOut && exitCode === 0 ? EXIT_CODE_TIMED_OUT : exitCode,
      stdout,
      stderr,
      timedOut,
    });
  };

  try {
    const child = spawn(command, [...args], {
      ...(options?.cwd !== undefined ? { cwd: options.cwd } : {}),
      ...(options?.env !== undefined ? { env: options.env } : {}),
      ...(effectiveSignal !== undefined ? { signal: effectiveSignal } : {}),
      stdio: options?.inheritStdio ? "inherit" : ["pipe", "pipe", "pipe"],
    });

    if (!options?.inheritStdio) {
      child.stdout?.on("data", (chunk: Buffer | string) => {
        stdoutChunks.push(
          typeof chunk === "string" ? Buffer.from(chunk, UTF8_ENCODING) : chunk,
        );
      });
      child.stderr?.on("data", (chunk: Buffer | string) => {
        stderrChunks.push(
          typeof chunk === "string" ? Buffer.from(chunk, UTF8_ENCODING) : chunk,
        );
      });

      if (child.stdin) {
        child.stdin.on("error", () => {});
        if (options?.stdinData !== undefined) {
          child.stdin.end(options.stdinData, UTF8_ENCODING);
        } else {
          child.stdin.end();
        }
      }
    }

    child.on("error", (err: Error) => {
      if (err.name === ABORT_ERROR_NAME) {
        const timedOut = Boolean(timeoutSignal?.aborted);
        finalize(
          timedOut ? EXIT_CODE_TIMED_OUT : EXIT_CODE_ABORTED,
          err.message,
        );
        return;
      }
      finalize(EXIT_CODE_SPAWN_ERROR, err.message);
    });

    child.on("close", (code: number | null) => {
      if (effectiveSignal?.aborted) {
        const timedOut = Boolean(timeoutSignal?.aborted);
        finalize(timedOut ? EXIT_CODE_TIMED_OUT : EXIT_CODE_ABORTED);
        return;
      }
      finalize(code ?? EXIT_CODE_SPAWN_ERROR);
    });
  } catch (cause: unknown) {
    const message = cause instanceof Error ? cause.message : String(cause);
    finalize(EXIT_CODE_SPAWN_ERROR, message);
  }

  return promise;
}

function resolveBrowserOpenCommand(targetUrl: string): {
  readonly command: string;
  readonly args: readonly string[];
} {
  if (process.platform === "darwin") {
    return { command: OPEN_BROWSER_DARWIN, args: [targetUrl] };
  }
  if (process.platform === "win32") {
    return {
      command: OPEN_BROWSER_WIN32,
      args: ["/c", "start", "", targetUrl],
    };
  }
  return { command: OPEN_BROWSER_LINUX, args: [targetUrl] };
}

/**
 * Открывает указанный URL в системном браузере по умолчанию (`open` на macOS, `cmd /c start` на Windows,
 * `xdg-open` на Linux) в отвязанном (`detached`) фоновом процессе без блокировки CLI.
 */
export async function openUrlInBrowser(url: string): Promise<boolean> {
  const trimmedUrl = url.trim();
  if (trimmedUrl.length === 0) {
    return false;
  }

  try {
    const { command, args } = resolveBrowserOpenCommand(trimmedUrl);
    const child = spawn(command, [...args], {
      detached: true,
      stdio: "ignore",
    });
    child.on("error", () => {});
    child.unref();
    return true;
  } catch {
    return false;
  }
}
