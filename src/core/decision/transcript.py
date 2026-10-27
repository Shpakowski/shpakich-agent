"""Парсер хвоста JSONL-транскриптов и извлечения сводок `/compact` (Layer 2: Decision Engine).

Отвечает за неблокирующее чтение последних 64 КБ `transcript.jsonl`, детекцию маркеров
выхода из сжатия контекста (`CHECKPOINT`, `# Resuming from a compaction`, `<summary>`),
нормализацию текста сводки и вычисление детерминированного SHA-256 хеша для идемпотентной
архивации в таблицу `session_compactions` PostgreSQL (`SPEC-003`).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

TRANSCRIPT_TAIL_MAX_BYTES: int = 65_536
COMPACT_RESUMING_MARKER: str = "# Resuming from a compaction"
COMPACT_STEP_TYPE: str = "CHECKPOINT"
USER_INPUT_STEP_TYPE: str = "USER_INPUT"
MAX_COMPACTION_SUMMARY_CHARS: int = 32_000
CHECKPOINT_HASH_LENGTH: int = 16

TAIL_SCAN_MAX_LINES: int = 16
SUMMARY_XML_OPEN_TAG: str = "<summary>"
SUMMARY_XML_CLOSE_TAG: str = "</summary>"
PAYLOAD_SUMMARY_KEYS: tuple[str, ...] = (
    "summary",
    "compaction_summary",
    "compactionSummary",
)
STEP_CONTENT_KEYS: tuple[str, ...] = (
    "content",
    "text",
    "prompt",
    "user_prompt",
)
NESTED_MESSAGE_KEYS: tuple[str, ...] = (
    "message",
    "checkpoint",
    "data",
)
PAYLOAD_PROMPT_KEYS: tuple[str, ...] = (
    "prompt",
    "user_prompt",
    "lastUserInput",
    "last_user_input",
)
TRANSCRIPT_TAIL_KEYS: tuple[str, ...] = (
    "transcript_tail",
    "transcriptTail",
)
STEP_INDEX_KEYS: tuple[str, ...] = (
    "step_index",
    "step_idx",
    "stepIdx",
    "step",
)

USER_REQUEST_TAG_REGEX: re.Pattern[str] = re.compile(
    r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>",
    re.IGNORECASE | re.DOTALL,
)

SUMMARY_TAG_REGEX: re.Pattern[str] = re.compile(
    r"<summary>\s*(.*?)\s*</summary>",
    re.IGNORECASE | re.DOTALL,
)
OPEN_SUMMARY_SPLIT_REGEX: re.Pattern[str] = re.compile(
    r"<summary>",
    re.IGNORECASE,
)
LEADING_SUMMARY_TAG_REGEX: re.Pattern[str] = re.compile(
    r"^\s*<summary>\s*",
    re.IGNORECASE,
)
TRAILING_SUMMARY_TAG_REGEX: re.Pattern[str] = re.compile(
    r"\s*</summary>\s*$",
    re.IGNORECASE,
)
JSON_STRING_LITERAL_REGEX: re.Pattern[str] = re.compile(
    r'"((?:\\.|[^"\\])*)"',
)
TRUNCATED_JSON_KEY_REGEX: re.Pattern[str] = re.compile(
    r'"(?:type|content|summary|compaction_summary|compactionSummary|step_idx|stepIdx|step_index|step|message|text)"\s*:',
)
TRUNCATED_JSON_STEP_IDX_REGEX: re.Pattern[str] = re.compile(
    r'"(?:step_index|step_idx|stepIdx|step)"\s*:\s*(\d+)',
)


class CompactionCheckpoint(BaseModel):
    """Иммутабельный слепок извлечённой сводки сжатия контекста (`/compact`) для архивации."""

    model_config = ConfigDict(frozen=True)

    summary_text: str
    checkpoint_hash: str
    step_idx: int = 0
    source_marker: str = COMPACT_STEP_TYPE

    @property
    def step_index(self) -> int:
        """Псевдоним `step_idx` для совместимости с SQL-схемой `session_compactions`."""
        return self.step_idx


class TranscriptTailInspection(BaseModel):
    """Результат детальной инспекции хвоста JSONL-транскрипта и входного пэйлоада хука."""

    model_config = ConfigDict(frozen=True)

    is_post_compact: bool = False
    last_user_prompt: str = ""
    checkpoint: CompactionCheckpoint | None = None


def extract_compaction_summary(raw_text: str) -> str | None:
    """Извлекает сводку из `<summary>...</summary>` или после `# Resuming from a compaction`."""
    if not raw_text or not raw_text.strip():
        return None

    matches = SUMMARY_TAG_REGEX.findall(raw_text)
    for match in reversed(matches):
        cleaned_match = match.strip()
        if cleaned_match:
            return cleaned_match[:MAX_COMPACTION_SUMMARY_CHARS]

    if COMPACT_RESUMING_MARKER in raw_text:
        after_marker = raw_text.rsplit(COMPACT_RESUMING_MARKER, 1)[-1]
        if OPEN_SUMMARY_SPLIT_REGEX.search(after_marker):
            after_marker = OPEN_SUMMARY_SPLIT_REGEX.split(after_marker)[-1]
        cleaned_after = LEADING_SUMMARY_TAG_REGEX.sub("", after_marker)
        cleaned_after = TRAILING_SUMMARY_TAG_REGEX.sub("", cleaned_after).strip()
        if cleaned_after:
            return cleaned_after[:MAX_COMPACTION_SUMMARY_CHARS]

    return None


def compute_checkpoint_hash(summary_text: str) -> str:
    """Вычисляет 16-символьный SHA-256 дайджест по нормализованному тексту сводки."""
    normalized = " ".join(summary_text.split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:CHECKPOINT_HASH_LENGTH]


def _extract_step_idx(mapping: Mapping[str, Any], fallback_step_idx: int) -> int:
    """Извлекает неотрицательный индекс шага из словаря JSONL-строки или пэйлоада с фолбэком."""
    for key in STEP_INDEX_KEYS:
        raw_val = mapping.get(key)
        if isinstance(raw_val, bool) or raw_val is None:
            continue
        if isinstance(raw_val, (int, float)):
            return max(0, int(raw_val))
        if isinstance(raw_val, str) and raw_val.strip().isdigit():
            return max(0, int(raw_val.strip()))
    return max(0, fallback_step_idx)


def _extract_content_value(val: Any) -> str:
    """Извлекает строку из одиночного поля или списка блоков контента (`str` / `Mapping`)."""
    if isinstance(val, str) and val.strip():
        return val
    if isinstance(val, list):
        parts: list[str] = []
        for item in val:
            if isinstance(item, str) and item.strip():
                parts.append(item)
            elif isinstance(item, Mapping):
                for key in STEP_CONTENT_KEYS:
                    sub_val = item.get(key)
                    if isinstance(sub_val, str) and sub_val.strip():
                        parts.append(sub_val)
                        break
        joined = "\n".join(p for p in parts if p.strip())
        if joined.strip():
            return joined
    return ""


def _extract_json_step_content(obj: Mapping[str, Any]) -> str:
    """Извлекает текстовое содержимое шага из различных диалектов JSONL-транскрипта."""
    for key in STEP_CONTENT_KEYS:
        extracted = _extract_content_value(obj.get(key))
        if extracted:
            return extracted

    for nested_key in NESTED_MESSAGE_KEYS:
        nested_obj = obj.get(nested_key)
        if isinstance(nested_obj, Mapping):
            for key in STEP_CONTENT_KEYS:
                extracted = _extract_content_value(nested_obj.get(key))
                if extracted:
                    return extracted

    for key in PAYLOAD_SUMMARY_KEYS:
        extracted = _extract_content_value(obj.get(key))
        if extracted:
            return extracted

    return ""


def _extract_plain_or_tagged_summary(raw_value: str) -> tuple[bool, str | None]:
    """Разбирает явное поле сводки, отсекая пустые теги `<summary></summary>` и голые маркеры."""
    stripped = raw_value.strip()
    if not stripped:
        return False, None

    lowered = stripped.lower()
    has_marker_or_tag = (
        COMPACT_RESUMING_MARKER in stripped
        or SUMMARY_XML_OPEN_TAG in lowered
        or SUMMARY_XML_CLOSE_TAG in lowered
    )
    extracted = extract_compaction_summary(stripped)
    if extracted:
        return True, extracted
    if has_marker_or_tag:
        return COMPACT_RESUMING_MARKER in stripped, None
    return True, stripped[:MAX_COMPACTION_SUMMARY_CHARS]


def _extract_explicit_json_summary(obj: Mapping[str, Any]) -> tuple[bool, str | None, str]:
    """Проверяет явные поля сводки (`summary`, `compaction_summary`, `compactionSummary`) в JSON-шаге."""
    candidates: list[Mapping[str, Any]] = [obj]
    for nested_key in NESTED_MESSAGE_KEYS:
        nested_obj = obj.get(nested_key)
        if isinstance(nested_obj, Mapping):
            candidates.append(nested_obj)

    for mapping in candidates:
        for key in PAYLOAD_SUMMARY_KEYS:
            raw_val = mapping.get(key)
            if isinstance(raw_val, str) and raw_val.strip():
                is_compact, summary_text = _extract_plain_or_tagged_summary(raw_val)
                if is_compact or summary_text is not None:
                    return is_compact, summary_text, key
    return False, None, ""


def _looks_like_truncated_json(line: str) -> bool:
    """Определяет, является ли невалидная строка обрезанной JSONL-записью, а не сырым Markdown-текстом."""
    if TRUNCATED_JSON_KEY_REGEX.search(line) is not None:
        return True
    if (line.startswith("{") or line.endswith("}")) and '":' in line:
        return True
    return "\\n" in line and (
        COMPACT_RESUMING_MARKER in line or SUMMARY_XML_OPEN_TAG in line.lower()
    )


def _unescape_json_fragment(fragment: str) -> str:
    """Декодирует базовые escape-последовательности JSON в обрезанном строковом литерале."""
    return (
        fragment.replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _try_extract_from_truncated_json_line(
    line: str,
    fallback_step_idx: int,
) -> tuple[bool, CompactionCheckpoint | None]:
    """Безопасно извлекает сводку из обрезанной JSONL-строки без захвата синтаксиса JSON."""
    line_lower = line.lower()
    has_marker = (
        COMPACT_RESUMING_MARKER in line
        or SUMMARY_XML_OPEN_TAG in line_lower
        or SUMMARY_XML_CLOSE_TAG in line_lower
        or f'"{COMPACT_STEP_TYPE}"' in line
    )
    if not has_marker:
        return False, None

    step_match = TRUNCATED_JSON_STEP_IDX_REGEX.search(line)
    step_idx = int(step_match.group(1)) if step_match is not None else fallback_step_idx

    decoded_literals: list[str] = []
    for match in JSON_STRING_LITERAL_REGEX.finditer(line):
        try:
            val = json.loads(match.group(0))
            if isinstance(val, str) and val.strip():
                decoded_literals.append(val)
        except ValueError:
            continue

    is_post_compact = COMPACT_RESUMING_MARKER in line or f'"{COMPACT_STEP_TYPE}"' in line
    for literal in reversed(decoded_literals):
        lit_lower = literal.lower()
        if COMPACT_RESUMING_MARKER in literal or SUMMARY_XML_OPEN_TAG in lit_lower:
            is_post_compact = True
        extracted = extract_compaction_summary(literal)
        if extracted:
            source_marker = (
                COMPACT_STEP_TYPE
                if f'"{COMPACT_STEP_TYPE}"' in line
                else (
                    COMPACT_RESUMING_MARKER
                    if COMPACT_RESUMING_MARKER in literal
                    else SUMMARY_XML_OPEN_TAG
                )
            )
            return True, CompactionCheckpoint(
                summary_text=extracted,
                checkpoint_hash=compute_checkpoint_hash(extracted),
                step_idx=step_idx,
                source_marker=source_marker,
            )

    if SUMMARY_XML_OPEN_TAG in line_lower and SUMMARY_XML_CLOSE_TAG in line_lower:
        unescaped_line = _unescape_json_fragment(line)
        extracted_tag = extract_compaction_summary(unescaped_line)
        if extracted_tag:
            return True, CompactionCheckpoint(
                summary_text=extracted_tag,
                checkpoint_hash=compute_checkpoint_hash(extracted_tag),
                step_idx=step_idx,
                source_marker=SUMMARY_XML_OPEN_TAG,
            )

    return is_post_compact, None


def _clean_user_prompt(raw_content: str) -> str:
    """Извлекает чистый текст запроса из `<USER_REQUEST>...</USER_REQUEST>`, отсекая `<ADDITIONAL_METADATA>`."""
    stripped = raw_content.strip()
    if not stripped:
        return ""
    matches = USER_REQUEST_TAG_REGEX.findall(stripped)
    for match in reversed(matches):
        cleaned = match.strip()
        if cleaned:
            return cleaned
    return stripped


def inspect_transcript_tail_detailed(
    transcript_path: str | None,
    *,
    fallback_step_idx: int = 0,
    raw_payload: Mapping[str, Any] | None = None,
) -> TranscriptTailInspection:
    """Читает последние 64 КБ транскрипта (с диска или из `transcript_tail`) и пэйлоад для извлечения `CompactionCheckpoint`."""
    is_post_compact = False
    last_user_prompt = ""
    checkpoint: CompactionCheckpoint | None = None
    safe_fallback_step = max(0, fallback_step_idx)

    tail_text = ""
    if transcript_path:
        path = Path(transcript_path)
        if path.is_file():
            try:
                file_size = path.stat().st_size
                offset = max(0, file_size - TRANSCRIPT_TAIL_MAX_BYTES)
                with path.open("rb") as fh:
                    if offset > 0:
                        fh.seek(offset)
                    tail_text = fh.read().decode("utf-8", errors="ignore")
            except OSError:
                tail_text = ""

    if not tail_text and isinstance(raw_payload, Mapping):
        for tail_key in TRANSCRIPT_TAIL_KEYS:
            candidate_tail = raw_payload.get(tail_key)
            if isinstance(candidate_tail, str) and candidate_tail.strip():
                tail_text = candidate_tail
                break

    if tail_text:
        lines = [ln.strip() for ln in tail_text.splitlines() if ln.strip()]
        tail_start_idx = max(0, len(lines) - TAIL_SCAN_MAX_LINES)
        raw_non_json_lines: list[tuple[int, str]] = []
        last_checkpoint_line_idx = -1

        for idx, line in enumerate(lines):
            try:
                obj = json.loads(line)
            except ValueError:
                if _looks_like_truncated_json(line):
                    if idx >= tail_start_idx:
                        trunc_compact, trunc_ckpt = _try_extract_from_truncated_json_line(
                            line,
                            safe_fallback_step,
                        )
                        if trunc_compact:
                            is_post_compact = True
                        if trunc_ckpt is not None:
                            checkpoint = trunc_ckpt
                            last_checkpoint_line_idx = idx
                else:
                    raw_non_json_lines.append((idx, line))
                continue

            if not isinstance(obj, Mapping):
                continue

            if idx < tail_start_idx:
                continue

            step_type = str(obj.get("type") or "").strip()
            content_str = _extract_json_step_content(obj)
            line_step_idx = _extract_step_idx(obj, safe_fallback_step)
            explicit_compact, explicit_summary, explicit_key = (
                _extract_explicit_json_summary(obj)
            )

            has_compact_type = step_type == COMPACT_STEP_TYPE
            has_resuming_marker = COMPACT_RESUMING_MARKER in content_str
            extracted_summary = explicit_summary or extract_compaction_summary(content_str)

            if (
                has_compact_type
                or has_resuming_marker
                or explicit_compact
                or extracted_summary is not None
            ):
                is_post_compact = True
                summary_candidate = extracted_summary
                if summary_candidate is None and has_compact_type:
                    plain_checkpoint = content_str.replace(
                        COMPACT_RESUMING_MARKER, ""
                    ).strip()
                    if plain_checkpoint:
                        summary_candidate = plain_checkpoint[
                            :MAX_COMPACTION_SUMMARY_CHARS
                        ]

                if summary_candidate:
                    source_marker = (
                        COMPACT_STEP_TYPE
                        if has_compact_type
                        else (
                            explicit_key
                            if explicit_summary is not None and explicit_key
                            else (
                                COMPACT_RESUMING_MARKER
                                if has_resuming_marker
                                else SUMMARY_XML_OPEN_TAG
                            )
                        )
                    )
                    checkpoint = CompactionCheckpoint(
                        summary_text=summary_candidate,
                        checkpoint_hash=compute_checkpoint_hash(summary_candidate),
                        step_idx=line_step_idx,
                        source_marker=source_marker,
                    )
                    last_checkpoint_line_idx = idx
                elif checkpoint is None:
                    fallback_marker = f"compact_step_{line_step_idx}_{idx}"
                    checkpoint = CompactionCheckpoint(
                        summary_text="",
                        checkpoint_hash=compute_checkpoint_hash(fallback_marker),
                        step_idx=line_step_idx,
                        source_marker=COMPACT_STEP_TYPE,
                    )
                    last_checkpoint_line_idx = idx

            if step_type == USER_INPUT_STEP_TYPE and content_str.strip():
                last_user_prompt = _clean_user_prompt(content_str)

        if raw_non_json_lines:
            raw_non_json_block = "\n".join(ln for _, ln in raw_non_json_lines)
            block_lower = raw_non_json_block.lower()
            if (
                COMPACT_RESUMING_MARKER in raw_non_json_block
                or SUMMARY_XML_OPEN_TAG in block_lower
                or SUMMARY_XML_CLOSE_TAG in block_lower
            ):
                is_post_compact = True
                if (
                    checkpoint is None
                    or raw_non_json_lines[-1][0] > last_checkpoint_line_idx
                ):
                    multiline_summary = extract_compaction_summary(raw_non_json_block)
                    if multiline_summary:
                        checkpoint = CompactionCheckpoint(
                            summary_text=multiline_summary,
                            checkpoint_hash=compute_checkpoint_hash(multiline_summary),
                            step_idx=safe_fallback_step,
                            source_marker=(
                                COMPACT_RESUMING_MARKER
                                if COMPACT_RESUMING_MARKER in raw_non_json_block
                                else SUMMARY_XML_OPEN_TAG
                            ),
                        )

    if isinstance(raw_payload, Mapping):
        payload_step_idx = _extract_step_idx(raw_payload, safe_fallback_step)
        for key in PAYLOAD_SUMMARY_KEYS:
            raw_summary = raw_payload.get(key)
            if isinstance(raw_summary, str) and raw_summary.strip():
                is_compact_field, extracted = _extract_plain_or_tagged_summary(raw_summary)
                if is_compact_field:
                    is_post_compact = True
                if extracted:
                    is_post_compact = True
                    checkpoint = CompactionCheckpoint(
                        summary_text=extracted,
                        checkpoint_hash=compute_checkpoint_hash(extracted),
                        step_idx=payload_step_idx,
                        source_marker=key,
                    )
                    break

        if checkpoint is None:
            raw_prompt = ""
            for prompt_key in PAYLOAD_PROMPT_KEYS:
                candidate_prompt = raw_payload.get(prompt_key)
                if isinstance(candidate_prompt, str) and candidate_prompt.strip():
                    raw_prompt = candidate_prompt.strip()
                    break
            if raw_prompt and (
                COMPACT_RESUMING_MARKER in raw_prompt
                or SUMMARY_XML_OPEN_TAG in raw_prompt.lower()
            ):
                if COMPACT_RESUMING_MARKER in raw_prompt:
                    is_post_compact = True
                extracted_from_prompt = extract_compaction_summary(raw_prompt)
                if extracted_from_prompt:
                    is_post_compact = True
                    checkpoint = CompactionCheckpoint(
                        summary_text=extracted_from_prompt,
                        checkpoint_hash=compute_checkpoint_hash(extracted_from_prompt),
                        step_idx=payload_step_idx,
                        source_marker=(
                            COMPACT_RESUMING_MARKER
                            if COMPACT_RESUMING_MARKER in raw_prompt
                            else SUMMARY_XML_OPEN_TAG
                        ),
                    )

    return TranscriptTailInspection(
        is_post_compact=is_post_compact,
        last_user_prompt=last_user_prompt,
        checkpoint=checkpoint,
    )


def inspect_transcript_tail(transcript_path: str | None) -> tuple[bool, str]:
    """Обратно-совместимая обёртка, возвращающая `(is_post_compact, last_user_prompt)`."""
    inspection = inspect_transcript_tail_detailed(transcript_path)
    return inspection.is_post_compact, inspection.last_user_prompt


__all__ = [
    "CHECKPOINT_HASH_LENGTH",
    "COMPACT_RESUMING_MARKER",
    "COMPACT_STEP_TYPE",
    "MAX_COMPACTION_SUMMARY_CHARS",
    "TRANSCRIPT_TAIL_MAX_BYTES",
    "USER_INPUT_STEP_TYPE",
    "CompactionCheckpoint",
    "TranscriptTailInspection",
    "compute_checkpoint_hash",
    "extract_compaction_summary",
    "inspect_transcript_tail",
    "inspect_transcript_tail_detailed",
]
