"""Изоляция файловой песочницы `/tmp`, защита от Prompt Injection и парсер JSON-ответов.

Отвечает за Layer 2 (Core Services): безопасно копирует срез рабочей директории проекта
во временную песочницу (жёстко отсекая симлинки и выход за пределы корня), упаковывает
недоверенные диффы/манифесты в одноразовые XML-конверты и извлекает Pydantic v2 схемы.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ValidationError

from src.core.graph.context_graph import CONTEXT_DIR_NAME

logger = logging.getLogger("mcp_coop.core.services.antigravity.sandbox")

# Параметры безопасности файловой песочницы в /tmp
SANDBOX_DIR_PREFIX: str = "mcp_coop_agy_"
MAX_SANDBOX_FILE_BYTES: int = 262_144
MAX_SANDBOX_SOURCE_FILES: int = 40

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
_IGNORED_SANDBOX_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
    }
)


def extract_structured_model[TModel: BaseModel](
    raw_text: str,
    model_cls: type[TModel],
) -> TModel:
    """Извлекает и валидирует Pydantic v2 схему из ответа агента в 3 ступени.

    Сначала пробует прямой JSON, затем ищет последний fenced-блок ```json ... ```,
    а на третьем шаге сканирует текст через `raw_decode` для извлечения сбалансированного объекта `{...}`.
    """
    cleaned = raw_text.strip()
    if not cleaned:
        raise ValueError("Agent response text is empty")

    try:
        return model_cls.model_validate_json(cleaned)
    except (ValidationError, ValueError) as exc:
        logger.debug("Direct JSON validation failed for %s: %s", model_cls.__name__, exc)

    fenced_matches = list(_FENCED_JSON_RE.finditer(cleaned))
    for match in reversed(fenced_matches):
        candidate = match.group(1).strip()
        try:
            return model_cls.model_validate_json(candidate)
        except (ValidationError, ValueError) as exc:
            logger.debug("Fenced JSON validation failed for %s: %s", model_cls.__name__, exc)

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(cleaned):
        if ch != "{":
            continue
        try:
            parsed_obj, _ = decoder.raw_decode(cleaned[idx:])
            if isinstance(parsed_obj, dict):
                return model_cls.model_validate(parsed_obj)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("Balanced JSON decode failed at offset %d: %s", idx, exc)

    raise ValueError(f"Failed to extract valid {model_cls.__name__} JSON from agent output")


def wrap_untrusted_payload(label: str, content: str) -> tuple[str, str]:
    """Оборачивает недоверенный контент (дифф или манифест) в одноразовый XML-конверт против Prompt Injection."""
    nonce = secrets.token_hex(6)
    tag_name = f"untrusted_{label}_{nonce}"
    sanitized_body = content.replace(f"</{tag_name}>", "[escaped_closing_tag]")
    envelope = f"<{tag_name}>\n{sanitized_body}\n</{tag_name}>"
    return tag_name, envelope


def stage_isolated_workspace(
    source_workspace: Path,
    sandbox_root: Path,
    referenced_files: Sequence[str] = (),
) -> Path:
    """Копирует `.context/` и безопасный срез файлов проекта в песочницу, жёстко отсекая симлинки."""
    resolved_source = source_workspace.expanduser().resolve()
    resolved_sandbox = sandbox_root.expanduser().resolve()

    def _safe_copy_file(rel_path: Path) -> None:
        src_file = resolved_source / rel_path
        if src_file.is_symlink() or not src_file.is_file():
            return
        try:
            real_src = src_file.resolve()
            if not real_src.is_relative_to(resolved_source):
                return
            if real_src.stat().st_size > MAX_SANDBOX_FILE_BYTES:
                return
        except OSError:
            return

        dst_file = resolved_sandbox / rel_path
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(real_src, dst_file, follow_symlinks=False)

    context_dir = resolved_source / CONTEXT_DIR_NAME
    if context_dir.exists() and not context_dir.is_symlink():
        for item in context_dir.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            _safe_copy_file(item.relative_to(resolved_source))

    copied_count = 0
    for rel_str in referenced_files:
        clean_rel = rel_str.strip().lstrip("/")
        if not clean_rel or ".." in Path(clean_rel).parts:
            continue
        _safe_copy_file(Path(clean_rel))
        copied_count += 1
        if copied_count >= MAX_SANDBOX_SOURCE_FILES:
            break

    if copied_count == 0 and resolved_source.is_dir():
        for candidate in sorted(resolved_source.rglob("*")):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            rel_parts = candidate.relative_to(resolved_source).parts
            if any(part in _IGNORED_SANDBOX_DIRS for part in rel_parts):
                continue
            if rel_parts and rel_parts[0] == CONTEXT_DIR_NAME:
                continue
            _safe_copy_file(candidate.relative_to(resolved_source))
            copied_count += 1
            if copied_count >= MAX_SANDBOX_SOURCE_FILES:
                break

    return resolved_sandbox


__all__ = [
    "MAX_SANDBOX_FILE_BYTES",
    "MAX_SANDBOX_SOURCE_FILES",
    "SANDBOX_DIR_PREFIX",
    "extract_structured_model",
    "stage_isolated_workspace",
    "wrap_untrusted_payload",
]
