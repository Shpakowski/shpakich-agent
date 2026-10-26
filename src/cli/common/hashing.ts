/**
 * Слой 1 (Host CLI Common Foundation): криптографическая нормализация текста и расчёт SHA-256.
 * Обеспечивает кроссплатформенную идентичность хешей между CLI-инжектором и Python-ядром
 * при 3-Way Hash синхронизации графа `.context/` и дедупликации компонентов `Harness`.
 */

import crypto from "node:crypto";

import {
  asContentHash,
  asSha256Hex,
  type ContentHash,
  type Sha256Hex,
} from "./types.ts";

const SHA256_ALGORITHM = "sha256" as const;
const UTF8_ENCODING = "utf-8" as const;
const HEX_DIGEST = "hex" as const;

/**
 * Приводит переводы строк (`\r\n` и одиночные `\r`) к каноническому LF (`\n`).
 */
export function normalizeLineEndings(content: string): string {
  return content.replace(/\r\n?/g, "\n");
}

/**
 * Вычисляет 64-символьный SHA-256 дайджест в шестнадцатеричном формате для строки UTF-8.
 */
export function computeSha256Hex(content: string): Sha256Hex {
  const digest = crypto
    .createHash(SHA256_ALGORITHM)
    .update(content, UTF8_ENCODING)
    .digest(HEX_DIGEST);
  return asSha256Hex(digest);
}

/**
 * Вычисляет канонический SHA-256 хеш нормализованного текста (`\r\n` -> `\n` + `.trim()`),
 * один в один совместимый с контрактом `workspace_sync.ts` и Python `compute_normalized_content_hash`.
 */
export function computeNormalizedContentHash(content: string): ContentHash {
  const normalized = normalizeLineEndings(content).trim();
  return asContentHash(computeSha256Hex(normalized));
}
