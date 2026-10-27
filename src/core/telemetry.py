"""Модуль структурной наблюдаемости, токен-телеметрии и форматирования логов (Layer 2: Core Telemetry).

Реализует стандарты наблюдаемости ИИ-агентов 2025–2026 (OpenTelemetry GenAI & MCP Semantic Conventions):
1. Быстрый детерминированный эстиматор BPE-токенов (`estimate_tokens`) без внешних сетевых словарей
   (`ADR-001`), учитывающий плотность токенизации для латиницы, кириллицы и синтаксиса кода/JSON.
2. Извлечение выходных токенов ответа модели и рассуждений (`response_tokens`, `thinking_tokens`)
   из хвоста JSONL-транскрипта (`extract_last_model_output_tokens`) на фазе `Stop`.
3. Расчёт экономии контекста за счёт двухступенчатого Progressive Disclosure (`saved_pct` относительно
   статической загрузки полного каталога из 40 компонентов `Harness`).
4. Двухрежимный вывод логов в консоль Docker (`LOG_FORMAT=pretty|json`):
   - В режиме `pretty` (по умолчанию) первая строка содержит компактную `logfmt`-шапку, видимую
     в свёрнутом списке Docker Desktop, а подстроки `├─ / └─` раскрывают детали при клике на `⌄`.
   - Фильтр `QuietHooksAccessFilter` подавляет дублирующие строки `POST /api/hooks/*` и `GET /health`
     от `uvicorn.access`, оставляя поток логов чистым.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

LOG_FORMAT_PRETTY: str = "pretty"
LOG_FORMAT_JSON: str = "json"
DEFAULT_LOG_LEVEL: str = "INFO"

# Базовый эталонный объём полного статического каталога (40 компонентов Harness) в токенах
# для расчёта процента экономии контекста (Progressive Disclosure Context Savings %).
BASELINE_STATIC_CATALOG_TOKENS: int = 4_200

# Коэффициенты плотности символов на 1 BPE-токен (калибровка под o200k_base / Gemini tokenizer)
LATIN_CHARS_PER_TOKEN: float = 4.0
CYRILLIC_CHARS_PER_TOKEN: float = 1.85
PUNCT_CHARS_PER_TOKEN: float = 1.45

TRANSCRIPT_TAIL_READ_BYTES: int = 65_536
TRANSCRIPT_SCAN_LINES: int = 20
PROMPT_PREVIEW_MAX_CHARS: int = 80
TARGET_PREVIEW_MAX_CHARS: int = 72
REASON_PREVIEW_MAX_CHARS: int = 90

PLANNER_STEP_TYPES: frozenset[str] = frozenset(
    {"PLANNER_RESPONSE", "ASSISTANT", "MODEL", "ASSISTANT_MESSAGE"}
)

CYRILLIC_PATTERN: re.Pattern[str] = re.compile(r"[\u0400-\u04ff]")
LATIN_ALNUM_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z0-9_]")
CODE_PUNCT_PATTERN: re.Pattern[str] = re.compile(r"[{}[\](),.:;+\-*/=<>!@#$%^&|~`'\"\\]")

SUPPRESSED_ACCESS_SUBSTRINGS: tuple[str, ...] = (
    '"GET /health',
    '"POST /api/hooks/',
    '"POST /api/sync/',
)

logger = logging.getLogger("mcp_coop.core")


class QuietHooksAccessFilter(logging.Filter):
    """Фильтр для `uvicorn.access`, подавляющий технические пинги `/health` и дубли роутов хуков."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(marker in msg for marker in SUPPRESSED_ACCESS_SUBSTRINGS)


def configure_core_logging(
    log_format: str = LOG_FORMAT_PRETTY,
    log_level: str = DEFAULT_LOG_LEVEL,
) -> None:
    """Настраивает логгер `mcp_coop.core` на уровень `INFO` и подключает фильтр к `uvicorn.access`.

    Зачем:
    - В Docker Desktop колонка даты уже рендерится слева самим контейнером, поэтому форматтер
      выводит сразу `INFO: [Event] ...`, не тратя горизонтальное место в свёрнутой строке.
    - Убирает дублирующие сырые строки `172.19.0.1 - "POST /api/hooks/..." 200 OK`.
    """
    level_num = getattr(logging, log_level.strip().upper(), logging.INFO)
    core_logger = logging.getLogger("mcp_coop")
    core_logger.setLevel(level_num)

    if not core_logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level_num)
        formatter = logging.Formatter("%(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        core_logger.addHandler(handler)
        core_logger.propagate = False
    else:
        for existing_handler in core_logger.handlers:
            existing_handler.setLevel(level_num)

    access_logger = logging.getLogger("uvicorn.access")
    has_quiet_filter = any(
        isinstance(f, QuietHooksAccessFilter) for f in access_logger.filters
    )
    if not has_quiet_filter:
        access_logger.addFilter(QuietHooksAccessFilter())

    os.environ["MCP_COOP_ACTIVE_LOG_FORMAT"] = (
        LOG_FORMAT_JSON
        if log_format.strip().lower() == LOG_FORMAT_JSON
        else LOG_FORMAT_PRETTY
    )


def _get_active_log_format() -> str:
    """Возвращает активный режим форматирования логов (`pretty` или `json`)."""
    raw = os.environ.get("MCP_COOP_ACTIVE_LOG_FORMAT") or os.environ.get(
        "MCP_COOP_LOG_FORMAT", LOG_FORMAT_PRETTY
    )
    return LOG_FORMAT_JSON if raw.strip().lower() == LOG_FORMAT_JSON else LOG_FORMAT_PRETTY


def estimate_tokens(value: Any) -> int:
    """Вычисляет детерминированную оценку количества BPE-токенов для строки, словаря или списка.

    Как работает:
    - Сериализует сложные объекты в компактный UTF-8 текст.
    - Раздельно оценивает кириллицу (~1.85 симв/токен), латиницу/цифры (~4.0 симв/токен)
      и синтаксическую пунктуацию кода/JSON (~1.45 симв/токен).
    """
    if value is None:
        return 0
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (Mapping, Sequence)) and not isinstance(value, (bytes, bytearray)):
        if not value:
            return 0
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value).strip()

    if not text:
        return 0

    cyrillic_count = len(CYRILLIC_PATTERN.findall(text))
    latin_count = len(LATIN_ALNUM_PATTERN.findall(text))
    punct_count = len(CODE_PUNCT_PATTERN.findall(text))
    remaining_chars = max(0, len(text) - cyrillic_count - latin_count - punct_count)

    estimated = (
        (cyrillic_count / CYRILLIC_CHARS_PER_TOKEN)
        + (latin_count / LATIN_CHARS_PER_TOKEN)
        + (punct_count / PUNCT_CHARS_PER_TOKEN)
        + (remaining_chars / LATIN_CHARS_PER_TOKEN)
    )
    return max(1, math.ceil(estimated))


def compute_progressive_disclosure_savings_pct(
    injected_tokens: int,
    baseline_tokens: int = BASELINE_STATIC_CATALOG_TOKENS,
) -> float:
    """Вычисляет процент экономии контекстного окна относительно статического каталога (4200 tok)."""
    if baseline_tokens <= 0:
        return 0.0
    if injected_tokens <= 0:
        return 100.0
    if injected_tokens >= baseline_tokens:
        return 0.0
    saved_ratio = (baseline_tokens - injected_tokens) / float(baseline_tokens)
    return round(max(0.0, min(100.0, saved_ratio * 100.0)), 1)


def _resolve_transcript_tail_text(
    transcript_path: str | None,
    *,
    transcript_tail: str | None = None,
    raw_payload: Mapping[str, Any] | None = None,
) -> str:
    """Возвращает хвост `transcript.jsonl` с локального диска или из проброшенного `transcript_tail`."""
    if transcript_path:
        path = Path(transcript_path)
        if path.is_file():
            try:
                file_size = path.stat().st_size
                offset = max(0, file_size - TRANSCRIPT_TAIL_READ_BYTES)
                with path.open("rb") as fh:
                    if offset > 0:
                        fh.seek(offset)
                    tail_text = fh.read().decode("utf-8", errors="ignore")
                if tail_text.strip():
                    return tail_text
            except OSError:
                pass

    if isinstance(transcript_tail, str) and transcript_tail.strip():
        return transcript_tail

    if isinstance(raw_payload, Mapping):
        for key in ("transcript_tail", "transcriptTail"):
            candidate = raw_payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate

    return ""


def _safe_non_negative_int(value: Any) -> int:
    """Безопасно приводит значение счётчика токенов к неотрицательному `int`."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
    return 0


def extract_last_llm_api_usage(
    transcript_path: str | None,
    *,
    transcript_tail: str | None = None,
    raw_payload: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Извлекает аппаратные и расчётные токены последнего шага `PLANNER_RESPONSE` из транскрипта."""
    empty_usage = {
        "resp_tokens": 0,
        "think_tokens": 0,
        "output_tokens": 0,
        "llm_input_tokens": 0,
        "llm_cache_read_tokens": 0,
    }
    tail_text = _resolve_transcript_tail_text(
        transcript_path,
        transcript_tail=transcript_tail,
        raw_payload=raw_payload,
    )
    if not tail_text:
        return empty_usage

    lines = [ln.strip() for ln in tail_text.splitlines() if ln.strip()]
    for line in reversed(lines[-TRANSCRIPT_SCAN_LINES:]):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, Mapping):
            continue

        step_type = str(obj.get("type") or obj.get("role") or "").strip().upper()
        source_val = str(obj.get("source") or "").strip().upper()
        if step_type in PLANNER_STEP_TYPES or (
            source_val == "MODEL" and step_type != "GENERIC"
        ):
            content_val = obj.get("content") or obj.get("text") or obj.get("message") or ""
            thinking_val = obj.get("thinking") or obj.get("reasoning") or ""
            tool_calls_val = obj.get("tool_calls") or obj.get("toolCalls") or ""
            est_resp = estimate_tokens(content_val) + estimate_tokens(tool_calls_val)
            est_think = estimate_tokens(thinking_val)

            api_input = _safe_non_negative_int(obj.get("input_tokens"))
            api_cache_read = _safe_non_negative_int(obj.get("cache_read_tokens"))
            api_output = _safe_non_negative_int(obj.get("output_tokens"))

            if api_output > 0:
                if est_think > 0 and est_resp > 0:
                    think_tokens = min(est_think, max(1, api_output - 1))
                    resp_tokens = max(1, api_output - think_tokens)
                elif est_think > 0:
                    think_tokens = api_output
                    resp_tokens = 0
                else:
                    resp_tokens = api_output
                    think_tokens = 0
            else:
                resp_tokens = est_resp
                think_tokens = est_think

            total_out = resp_tokens + think_tokens
            if total_out > 0 or api_input > 0 or api_cache_read > 0:
                return {
                    "resp_tokens": resp_tokens,
                    "think_tokens": think_tokens,
                    "output_tokens": total_out,
                    "llm_input_tokens": api_input,
                    "llm_cache_read_tokens": api_cache_read,
                }

    return empty_usage


def extract_last_model_output_tokens(
    transcript_path: str | None,
    *,
    transcript_tail: str | None = None,
    raw_payload: Mapping[str, Any] | None = None,
) -> tuple[int, int]:
    """Извлекает выходные токены `(response_tokens, thinking_tokens)` последнего хода LLM из транскрипта."""
    usage = extract_last_llm_api_usage(
        transcript_path,
        transcript_tail=transcript_tail,
        raw_payload=raw_payload,
    )
    return usage["resp_tokens"], usage["think_tokens"]


def _truncate_one_line(text: str, max_chars: int = PROMPT_PREVIEW_MAX_CHARS) -> str:
    """Схлопывает переводы строк и аккуратно обрезает строку до `max_chars` символов."""
    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return clean
    return f"{clean[: max_chars - 3]}..."


def extract_tool_target_preview(tool_name: str, tool_args: Mapping[str, Any]) -> str:
    """Извлекает человекочитаемую цель вызова инструмента (команду, путь файла или поисковый запрос)."""
    if not tool_args:
        return tool_name or "-"
    for key in (
        "CommandLine",
        "command",
        "cmd",
        "Path",
        "path",
        "file_path",
        "TargetFile",
        "query",
        "Query",
        "Pattern",
        "Slug",
        "slug",
        "node_id",
    ):
        val = tool_args.get(key)
        if isinstance(val, str) and val.strip():
            return _truncate_one_line(val.strip(), TARGET_PREVIEW_MAX_CHARS)
    return _truncate_one_line(json.dumps(dict(tool_args), ensure_ascii=False), TARGET_PREVIEW_MAX_CHARS)


def build_and_log_hook_telemetry(
    *,
    event_type: str,
    agent_type: str,
    workspace_path: str,
    stack_tags: Sequence[str],
    duration_ms: float,
    verdict: str = "allow",
    decided_by_tier: str = "default",
    reason: str | None = None,
    tool_name: str = "",
    tool_args: Mapping[str, Any] | None = None,
    prompt: str = "",
    system_message: str | None = None,
    ephemeral_message: str | None = None,
    rag_source: str = "",
    matched_rules: Sequence[tuple[str, float]] = (),
    matched_skills: Sequence[tuple[str, float, bool]] = (),
    executed_scripts: Sequence[str] = (),
    state_updates: Mapping[str, str] | None = None,
    compiled_files_count: int = 0,
    is_plan_mode: bool = False,
    is_post_compact: bool = False,
    transcript_path: str | None = None,
    raw_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Формирует OTel-совместимый словарь `_telemetry` для жизненного хука и выводит лог в Docker."""
    rounded_ms = round(max(0.1, duration_ms), 1)
    user_in_tokens = estimate_tokens(prompt) if prompt else estimate_tokens(tool_args)
    sys_tokens = estimate_tokens(system_message)
    eph_tokens = estimate_tokens(ephemeral_message)
    harness_injected_tokens = sys_tokens + eph_tokens
    total_in_tokens = user_in_tokens + harness_injected_tokens

    llm_usage = extract_last_llm_api_usage(
        transcript_path,
        raw_payload=raw_payload,
    )
    resp_out_tokens = llm_usage["resp_tokens"]
    think_out_tokens = llm_usage["think_tokens"]
    model_out_tokens = llm_usage["output_tokens"]
    llm_input_tokens = llm_usage["llm_input_tokens"]
    llm_cache_read_tokens = llm_usage["llm_cache_read_tokens"]
    saved_pct = compute_progressive_disclosure_savings_pct(harness_injected_tokens)

    rules_list = [
        {"slug": slug, "relevance": round(rel, 3)} for slug, rel in matched_rules
    ]
    skills_list = [
        {
            "slug": slug,
            "relevance": round(rel, 3),
            "mode": "BLUEPRINT" if is_full else "HINT",
        }
        for slug, rel, is_full in matched_skills
    ]
    scripts_list = list(executed_scripts)
    target_preview = extract_tool_target_preview(tool_name, tool_args or {}) if tool_name else ""

    # Формируем свёрнутую шапку (первая строка для Docker Desktop) и детальное дерево `├─ / └─`
    label_map = {
        "session-start": "SessionStart",
        "pre-invocation": "PreInvocation",
        "pre-tool-use": "PreToolUse",
        "post-tool-use": "PostToolUse",
        "stop": "Stop",
    }
    event_label = label_map.get(event_type, event_type)
    verdict_upper = verdict.upper()

    rules_short = ",".join(r["slug"] for r in rules_list) if rules_list else "-"
    skills_short = (
        ",".join(f"{s['slug']}:{s['mode']}" for s in skills_list) if skills_list else "-"
    )
    scripts_short = ",".join(scripts_list) if scripts_list else "-"

    if event_type == "pre-invocation":
        summary_line = (
            f"[{event_label}] {rounded_ms}ms │ tok: in={user_in_tokens} "
            f"+inj={harness_injected_tokens} Σ={total_in_tokens} (saved {saved_pct}%) │ "
            f"rules=[{rules_short}] skills=[{skills_short}]"
        )
    elif event_type in ("pre-tool-use", "post-tool-use"):
        summary_line = (
            f"[{event_label}] {rounded_ms}ms │ {verdict_upper} ({decided_by_tier}) │ "
            f"tool={tool_name or '-'} target=\"{target_preview}\" │ "
            f"tok: args={user_in_tokens} +inj={harness_injected_tokens} │ scripts=[{scripts_short}]"
        )
    elif event_type == "stop":
        summary_line = (
            f"[{event_label}] {rounded_ms}ms │ {verdict_upper} ({decided_by_tier}) │ "
            f"scripts=[{scripts_short}] │ tok: model_out={model_out_tokens} "
            f"(resp={resp_out_tokens}, think={think_out_tokens}) +inj={harness_injected_tokens}"
        )
    else:
        summary_line = (
            f"[{event_label}] {rounded_ms}ms │ agent={agent_type} tags={list(stack_tags)} │ "
            f"files={compiled_files_count} scripts=[{scripts_short}] │ tok: +inj={harness_injected_tokens}"
        )

    tree_lines: list[str] = [
        f"  ├─ agent: {agent_type} │ tags: [{', '.join(stack_tags)}]"
        + (f" │ workspace: {Path(workspace_path).name}" if workspace_path else "")
    ]
    if prompt:
        tree_lines.append(
            f"  ├─ prompt ({user_in_tokens} tok): \"{_truncate_one_line(prompt)}\""
            + (" [PLAN MODE]" if is_plan_mode else "")
            + (" [POST-COMPACT]" if is_post_compact else "")
        )
    if tool_name:
        tree_lines.append(
            f"  ├─ tool: {tool_name} │ target: \"{target_preview}\" │ verdict: {verdict_upper} ({decided_by_tier})"
            + (f" │ reason: \"{_truncate_one_line(reason, REASON_PREVIEW_MAX_CHARS)}\"" if reason else "")
        )
    if rules_list or skills_list or rag_source:
        rules_detail = (
            ", ".join(f"{r['slug']}({r['relevance']:.2f})" for r in rules_list) or "none"
        )
        skills_detail = (
            ", ".join(f"{s['slug']}:{s['mode']}({s['relevance']:.2f})" for s in skills_list)
            or "none"
        )
        tree_lines.append(
            f"  ├─ rag ({rag_source or 'n/a'}): rules=[{rules_detail}] │ skills=[{skills_detail}]"
        )
    if scripts_list or state_updates:
        state_str = (
            f" │ state={dict(state_updates)}" if state_updates else ""
        )
        tree_lines.append(
            f"  ├─ scripts: [{', '.join(scripts_list) if scripts_list else 'none'}]{state_str}"
        )
    llm_ctx_suffix = (
        f" │ llm_api(in={llm_input_tokens}, cache={llm_cache_read_tokens}, out={model_out_tokens})"
        if (llm_input_tokens > 0 or llm_cache_read_tokens > 0)
        else ""
    )
    tree_lines.append(
        f"  └─ tokens: user_in={user_in_tokens} + harness_inj={harness_injected_tokens} "
        f"(sys={sys_tokens}, eph={eph_tokens}) => total_in={total_in_tokens} tok "
        f"│ model_out={model_out_tokens} tok{llm_ctx_suffix} │ saved={saved_pct}%"
    )

    telemetry_dict: dict[str, Any] = {
        "event_type": event_type,
        "agent_type": agent_type,
        "duration_ms": rounded_ms,
        "verdict": verdict,
        "decided_by_tier": decided_by_tier,
        "reason": reason,
        "tool_name": tool_name or None,
        "target": target_preview or None,
        "rag_source": rag_source or None,
        "is_plan_mode": is_plan_mode,
        "is_post_compact": is_post_compact,
        "compiled_files_count": compiled_files_count,
        "rules": rules_list,
        "skills": skills_list,
        "scripts": scripts_list,
        "tokens": {
            "user_in": user_in_tokens,
            "harness_sys": sys_tokens,
            "harness_eph": eph_tokens,
            "harness_injected": harness_injected_tokens,
            "total_in": total_in_tokens,
            "model_out": model_out_tokens,
            "model_resp_out": resp_out_tokens,
            "model_think_out": think_out_tokens,
            "llm_input_tokens": llm_input_tokens,
            "llm_cache_read_tokens": llm_cache_read_tokens,
            "saved_pct": saved_pct,
        },
        "gen_ai": {
            "operation.name": event_type,
            "usage.input_tokens": total_in_tokens,
            "usage.output_tokens": model_out_tokens,
        },
        "summary_line": summary_line,
    }

    if _get_active_log_format() == LOG_FORMAT_JSON:
        logger.info(json.dumps(telemetry_dict, ensure_ascii=False))
    else:
        logger.info("%s\n%s", summary_line, "\n".join(tree_lines))

    return telemetry_dict


def build_and_log_mcp_telemetry(
    *,
    tool_name: str,
    workspace_path: str,
    args_payload: Mapping[str, Any],
    result_payload: Mapping[str, Any],
    duration_ms: float,
    summary_note: str = "",
) -> dict[str, Any]:
    """Формирует телеметрию вызова MCP-инструмента (`req_in -> resp_out` токенов) и выводит в Docker-лог."""
    rounded_ms = round(max(0.1, duration_ms), 1)
    req_in_tokens = estimate_tokens(args_payload)
    resp_out_tokens = estimate_tokens(result_payload)
    target_preview = extract_tool_target_preview(tool_name, args_payload)

    note_part = f" │ {summary_note}" if summary_note else ""
    summary_line = (
        f"[MCP:{tool_name}] {rounded_ms}ms{note_part} │ "
        f"tok: in={req_in_tokens} -> out={resp_out_tokens} │ target=\"{target_preview}\""
    )
    tree_lines = [
        f"  ├─ mcp.tool: {tool_name}"
        + (f" │ workspace: {Path(workspace_path).name}" if workspace_path else ""),
        f"  ├─ args ({req_in_tokens} tok): \"{target_preview}\"",
        f"  └─ tokens: req_in={req_in_tokens} tok -> resp_out={resp_out_tokens} tok"
        + (f" │ result: {summary_note}" if summary_note else ""),
    ]

    telemetry_dict: dict[str, Any] = {
        "event_type": "mcp-tool",
        "agent_type": "mcp",
        "duration_ms": rounded_ms,
        "verdict": "allow",
        "decided_by_tier": "fastmcp_gateway",
        "tool_name": tool_name,
        "target": target_preview,
        "summary_note": summary_note,
        "rules": [],
        "skills": [],
        "scripts": [],
        "tokens": {
            "user_in": req_in_tokens,
            "harness_sys": 0,
            "harness_eph": 0,
            "harness_injected": 0,
            "total_in": req_in_tokens,
            "model_out": resp_out_tokens,
            "model_resp_out": resp_out_tokens,
            "model_think_out": 0,
            "saved_pct": 100.0,
        },
        "mcp": {
            "method.name": tool_name,
            "input_tokens": req_in_tokens,
            "output_tokens": resp_out_tokens,
        },
        "summary_line": summary_line,
    }

    if _get_active_log_format() == LOG_FORMAT_JSON:
        logger.info(json.dumps(telemetry_dict, ensure_ascii=False))
    else:
        logger.info("%s\n%s", summary_line, "\n".join(tree_lines))

    return telemetry_dict


def log_sync_operation(
    *,
    operation: str,
    workspace_path: str,
    duration_ms: float,
    details: Mapping[str, Any],
) -> None:
    """Выводит в лог Docker информативную сводку по операциям `/api/sync/context` и `/api/sync/ingest`."""
    rounded_ms = round(max(0.1, duration_ms), 1)
    kv_str = " ".join(f"{k}={v}" for k, v in details.items())
    ws_name = Path(workspace_path).name if workspace_path else "global"
    summary_line = f"[Sync:{operation}] {rounded_ms}ms │ workspace={ws_name} │ {kv_str}"
    if _get_active_log_format() == LOG_FORMAT_JSON:
        logger.info(
            json.dumps(
                {
                    "event_type": f"sync-{operation}",
                    "workspace_path": workspace_path,
                    "duration_ms": rounded_ms,
                    **dict(details),
                },
                ensure_ascii=False,
            )
        )
    else:
        logger.info(summary_line)


__all__ = [
    "BASELINE_STATIC_CATALOG_TOKENS",
    "DEFAULT_LOG_LEVEL",
    "LOG_FORMAT_JSON",
    "LOG_FORMAT_PRETTY",
    "QuietHooksAccessFilter",
    "build_and_log_hook_telemetry",
    "build_and_log_mcp_telemetry",
    "compute_progressive_disclosure_savings_pct",
    "configure_core_logging",
    "estimate_tokens",
    "extract_last_llm_api_usage",
    "extract_last_model_output_tokens",
    "extract_tool_target_preview",
    "log_sync_operation",
]
