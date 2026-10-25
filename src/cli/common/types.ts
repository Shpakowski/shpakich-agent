/**
 * Слой 1 (Host CLI Common Foundation): базовые типовые примитивы TypeScript.
 * Определяет номинальные Branded-типы с нулевым рантайм-оверхедом, монаду `Result<T, E>`
 * для явной обработки ошибок без нетипизированных исключений и рекурсивные JSON-контракты.
 */

declare const BRAND_SYMBOL: unique symbol;

/**
 * Номинальный фантомный тип для защиты примитивов (`string`, `number`) от случайного смешивания
 * на этапе компиляции без выделения памяти в рантайме (`--erasableSyntaxOnly`).
 */
export type Brand<T, B extends string> = T & {
  readonly [BRAND_SYMBOL]: B;
};

export type WorkspacePath = Brand<string, "WorkspacePath">;
export type Sha256Hex = Brand<string, "Sha256Hex">;
export type ContentHash = Brand<string, "ContentHash">;
export type CoreBaseUrl = Brand<string, "CoreBaseUrl">;
export type PortNumber = Brand<number, "PortNumber">;

const MIN_TCP_PORT = 1 as const;
const MAX_TCP_PORT = 65535 as const;

/**
 * Маркирует строку пути рабочей директории номинальным брендом `WorkspacePath`.
 */
export function asWorkspacePath(raw: string): WorkspacePath {
  return raw as WorkspacePath;
}

/**
 * Маркирует 64-символьный шестнадцатеричный дайджест SHA-256 брендом `Sha256Hex`.
 */
export function asSha256Hex(raw: string): Sha256Hex {
  return raw as Sha256Hex;
}

/**
 * Маркирует нормализованный хеш содержимого файла или компонента брендом `ContentHash`.
 */
export function asContentHash(raw: string): ContentHash {
  return raw as ContentHash;
}

/**
 * Маркирует базовый HTTP URL аналитического ядра фабрики брендом `CoreBaseUrl`.
 */
export function asCoreBaseUrl(raw: string): CoreBaseUrl {
  return raw as CoreBaseUrl;
}

/**
 * Валидирует диапазон TCP-порта (`1..65535`) и маркирует число брендом `PortNumber`.
 */
export function asPortNumber(raw: number): PortNumber {
  if (!Number.isInteger(raw) || raw < MIN_TCP_PORT || raw > MAX_TCP_PORT) {
    throw new RangeError(
      `Invalid TCP port number: ${String(raw)}. Expected integer in range ${String(MIN_TCP_PORT)}..${String(MAX_TCP_PORT)}.`,
    );
  }
  return raw as PortNumber;
}

/**
 * Успешная ветвь дискриминированного объединения `Result<T, E>`.
 */
export interface OkResult<T> {
  readonly ok: true;
  readonly value: T;
}

/**
 * Ошибочная ветвь дискриминированного объединения `Result<T, E>`.
 */
export interface ErrResult<E> {
  readonly ok: false;
  readonly error: E;
}

/**
 * Монада явного результата операции для безопасного возврата ошибок через границы модулей.
 */
export type Result<T, E = Error> = OkResult<T> | ErrResult<E>;

/**
 * Создаёт успешный результат `OkResult<T>`.
 */
export function ok<T>(value: T): OkResult<T> {
  return { ok: true, value };
}

/**
 * Создаёт ошибочный результат `ErrResult<E>`.
 */
export function err<E>(error: E): ErrResult<E> {
  return { ok: false, error };
}

/**
 * Преобразует перехваченное значение `unknown` из блока `catch` в экземпляр `Error`
 * с сохранением исходного стек-трейса и сообщения.
 */
export function toError(cause: unknown): Error {
  if (cause instanceof Error) {
    return cause;
  }
  if (typeof cause === "string") {
    return new Error(cause);
  }
  try {
    return new Error(JSON.stringify(cause));
  } catch {
    return new Error(String(cause));
  }
}

/**
 * Выполняет асинхронную функцию и упаковывает её результат либо перехваченное исключение в `Result<T, Error>`.
 */
export async function tryAsync<T>(
  fn: () => Promise<T>,
): Promise<Result<T, Error>> {
  try {
    const value = await fn();
    return ok(value);
  } catch (cause: unknown) {
    return err(toError(cause));
  }
}

/**
 * Выполняет синхронную функцию и упаковывает её результат либо перехваченное исключение в `Result<T, Error>`.
 */
export function trySync<T>(fn: () => T): Result<T, Error> {
  try {
    return ok(fn());
  } catch (cause: unknown) {
    return err(toError(cause));
  }
}

/**
 * Гарантирует исчерпывающую проверку всех веток дискриминированного объединения (`switch`) на этапе компиляции.
 */
export function assertUnreachable(value: never): never {
  throw new Error(
    `Unhandled discriminated union member: ${JSON.stringify(value)}`,
  );
}

/**
 * Кортеж фиксированной минимальной длины, гарантирующий наличие хотя бы одного элемента на уровне типов.
 */
export type NonEmptyArray<T> = readonly [T, ...T[]];

export type JsonPrimitive = string | number | boolean | null;
export type JsonArray = readonly JsonValue[];
export type JsonObject = { readonly [key: string]: JsonValue };
export type JsonValue = JsonPrimitive | JsonObject | JsonArray;

/**
 * Проверяет, что значение `unknown` является обычным словарём (`Record<string, unknown>`), а не `null` или массивом.
 */
export function isJsonObject(
  value: unknown,
): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
