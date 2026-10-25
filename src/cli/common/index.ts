/**
 * Слой 1 (Host CLI Common Foundation): публичный фасад (Barrel) пакета `src/cli/common/`.
 * Экспортирует номинальные типы, монаду `Result<T, E>`, атомарные операции ФС,
 * функции SHA-256 хеширования, парсер YAML frontmatter и утилиты запуска процессов.
 */

export {
  asContentHash,
  asCoreBaseUrl,
  asPortNumber,
  asSha256Hex,
  assertUnreachable,
  asWorkspacePath,
  type Brand,
  type ContentHash,
  type CoreBaseUrl,
  err,
  type ErrResult,
  isJsonObject,
  type JsonArray,
  type JsonObject,
  type JsonPrimitive,
  type JsonValue,
  type NonEmptyArray,
  ok,
  type OkResult,
  type PortNumber,
  type Result,
  type Sha256Hex,
  toError,
  tryAsync,
  trySync,
  type WorkspacePath,
} from "./types.ts";

export {
  type AtomicWriteOptions,
  isSafeRelativeWorkspacePath,
  pathExists,
  pathExistsSync,
  readFileUtf8IfExists,
  readFileUtf8IfExistsSync,
  removeEmptyParentDirs,
  removeEmptyParentDirsSync,
  requireSafeWorkspacePath,
  resolveSafeWorkspacePath,
  writeFileAtomic,
  writeFileAtomicSync,
} from "./fs-atomic.ts";

export {
  computeNormalizedContentHash,
  computeSha256Hex,
  normalizeLineEndings,
} from "./hashing.ts";

export {
  extractFrontmatterList,
  extractFrontmatterScalar,
  extractTagsFromSlug,
   type ParsedFrontmatter,
  parseMarkdownFrontmatter,
} from "./frontmatter.ts";

export {
  openUrlInBrowser,
  runSubprocess,
  type SubprocessOptions,
  type SubprocessResult,
} from "./process.ts";
