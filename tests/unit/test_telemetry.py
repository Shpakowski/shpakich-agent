"""Набор юнит- и интеграционных тестов системы наблюдаемости и токен-телеметрии (`src/core/telemetry.py`).

Проверяет 5 ключевых контуров:
1. Мультиязычный эстиматор BPE-токенов (`estimate_tokens`) на кириллице, латинице и синтаксисе кода/JSON,
   а также расчёт процента экономии контекста (`compute_progressive_disclosure_savings_pct`).
2. Извлечение выходных токенов ответа модели и рассуждений (`extract_last_model_output_tokens`) из хвоста
   файла `transcript.jsonl`.
3. Фильтрацию дублирующего шума `uvicorn.access` (`QuietHooksAccessFilter`) и двухрежимное форматирование
   логов (`pretty` дерево и `json`).
4. Немутирующую запись `_telemetry` в таблицу `hook_events` через `_log_hook_event` и `_log_mcp_tool_event`.
5. Аналитический REST-эндпоинт `GET /api/visualizer/telemetry` и наличие элементов модального окна
   `Telemetry & Tokens` в SPA-шаблоне `GET /ui`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self, cast

import pytest
from fastapi.testclient import TestClient

from src.core.api.deps import _log_hook_event, _log_mcp_tool_event
from src.core.db.postgres import PostgresDatabase
from src.core.decision.hook_dialects import CanonicalHookInput, inspect_transcript_tail_detailed
from src.core.enums import AgentType, HookDialect, HookEventType
from src.core.main import create_app
from src.core.telemetry import (
    LOG_FORMAT_JSON,
    LOG_FORMAT_PRETTY,
    QuietHooksAccessFilter,
    build_and_log_hook_telemetry,
    build_and_log_mcp_telemetry,
    compute_progressive_disclosure_savings_pct,
    configure_core_logging,
    estimate_tokens,
    extract_last_llm_api_usage,
    extract_last_model_output_tokens,
)
from src.core.visualizer.models import VisualizerTelemetryResponse

SAMPLE_LATIN_TEXT: str = "FastAPI async endpoint with PostgreSQL pgvector hybrid search"
SAMPLE_CYRILLIC_TEXT: str = "Проверка подсчёта токенов для кириллического промпта в докере"
SAMPLE_WORKSPACE_PATH: str = "/Users/dev/project-alpha"
SAMPLE_CONVERSATION_ID: str = "conv-telemetry-001"


def test_estimate_tokens_and_savings_pct() -> None:
    """Проверяет детерминированную оценку токенов для пустых значений, латиницы, кириллицы и JSON."""
    assert estimate_tokens(None) == 0
    assert estimate_tokens("") == 0
    assert estimate_tokens({}) == 0
    assert estimate_tokens([]) == 0

    latin_tok = estimate_tokens(SAMPLE_LATIN_TEXT)
    cyrillic_tok = estimate_tokens(SAMPLE_CYRILLIC_TEXT)
    dict_tok = estimate_tokens({"CommandLine": "npm run typecheck", "WaitMsBeforeAsync": 5000})

    assert latin_tok > 5
    # Кириллица в BPE-токенизаторах требует больше токенов на ту же длину строки, чем латиница
    assert cyrillic_tok > latin_tok
    assert dict_tok > 8

    assert compute_progressive_disclosure_savings_pct(0) == 100.0
    assert compute_progressive_disclosure_savings_pct(420) == 90.0
    assert compute_progressive_disclosure_savings_pct(5000) == 0.0


def test_extract_last_model_output_tokens(tmp_path: Path) -> None:
    """Проверяет извлечение `(response_tokens, thinking_tokens)` последнего шага модели из `transcript.jsonl`."""
    transcript_file = tmp_path / "transcript.jsonl"
    steps = [
        {
            "step_index": 1,
            "type": "USER_INPUT",
            "content": "How can we improve Docker logs?",
        },
        {
            "step_index": 2,
            "type": "PLANNER_RESPONSE",
            "content": "We can add structured telemetry with token estimation and latency tracking.",
            "thinking": "Let's check routes_hooks.py and mcp/tools.py to see where hooks are logged.",
            "tool_calls": [{"name": "read_file", "args": {"Path": "/app/src/core/main.py"}}],
        },
    ]
    transcript_file.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in steps) + "\n",
        encoding="utf-8",
    )

    resp_tok, think_tok = extract_last_model_output_tokens(str(transcript_file))
    assert resp_tok > 10
    assert think_tok > 10
    assert extract_last_model_output_tokens(str(tmp_path / "missing.jsonl")) == (0, 0)

    # Проверка моста Host -> Docker через `transcript_tail` (когда файл недоступен внутри контейнера)
    docker_tail_lines = [
        json.dumps(
            {
                "step_index": 10,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": (
                    "<USER_REQUEST>\nокей, давай теперь починим логи в докере\n</USER_REQUEST>\n"
                    "<ADDITIONAL_METADATA>\nThe current local time is: 2026-10-27T18:08:06+03:00.\n</ADDITIONAL_METADATA>"
                ),
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "step_index": 11,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "input_tokens": 9676,
                "cache_read_tokens": 13269,
                "output_tokens": 492,
                "content": "Вот короткий план из 3 шагов.",
                "thinking": "Analyzing transcript bridge between host CLI and Docker Core.",
            },
            ensure_ascii=False,
        ),
    ]
    raw_payload_with_tail = {"transcript_tail": "\n".join(docker_tail_lines)}
    inspection = inspect_transcript_tail_detailed(
        "/Users/host/unmounted/transcript.jsonl",
        raw_payload=raw_payload_with_tail,
    )
    assert inspection.last_user_prompt == "окей, давай теперь починим логи в докере"

    api_usage = extract_last_llm_api_usage(
        "/Users/host/unmounted/transcript.jsonl",
        raw_payload=raw_payload_with_tail,
    )
    assert api_usage["output_tokens"] == 492
    assert api_usage["llm_input_tokens"] == 9676
    assert api_usage["llm_cache_read_tokens"] == 13269
    assert api_usage["resp_tokens"] + api_usage["think_tokens"] == 492


def test_quiet_hooks_access_filter() -> None:
    """Проверяет, что `QuietHooksAccessFilter` подавляет пинги `/health` и дубли `/api/hooks/*`."""
    log_filter = QuietHooksAccessFilter()

    health_rec = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='127.0.0.1:44112 - "GET /health HTTP/1.1" 200 OK',
        args=(),
        exc_info=None,
    )
    hook_rec = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='172.19.0.1:58378 - "POST /api/hooks/pre-invocation HTTP/1.1" 200 OK',
        args=(),
        exc_info=None,
    )
    ui_rec = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='127.0.0.1:58379 - "GET /ui HTTP/1.1" 200 OK',
        args=(),
        exc_info=None,
    )

    assert log_filter.filter(health_rec) is False
    assert log_filter.filter(hook_rec) is False
    assert log_filter.filter(ui_rec) is True


def test_build_and_log_telemetry_pretty_and_json(caplog: pytest.LogCaptureFixture) -> None:
    """Проверяет генерацию структуры `_telemetry` и форматирование лога в режимах `pretty` и `json`."""
    configure_core_logging(LOG_FORMAT_PRETTY, "INFO")
    with caplog.at_level(logging.INFO, logger="mcp_coop.core"):
        hook_tel = build_and_log_hook_telemetry(
            event_type="pre-invocation",
            agent_type="antigravity",
            workspace_path=SAMPLE_WORKSPACE_PATH,
            stack_tags=("python", "typescript"),
            duration_ms=14.6,
            prompt="давай добавим логирование токенов",
            ephemeral_message="[Dynamic Harness Rules]: universal-engineering-rules",
            rag_source="postgres",
            matched_rules=[("universal-engineering-rules", 0.84)],
            matched_skills=[("coop-orchestrator", 0.42, False)],
            executed_scripts=["inject-context-map"],
        )

    assert hook_tel["event_type"] == "pre-invocation"
    assert hook_tel["tokens"]["user_in"] > 0
    assert hook_tel["tokens"]["harness_injected"] > 0
    assert hook_tel["tokens"]["total_in"] == (
        hook_tel["tokens"]["user_in"] + hook_tel["tokens"]["harness_injected"]
    )
    assert "[PreInvocation]" in hook_tel["summary_line"]
    assert any("├─ rag (postgres)" in rec.message for rec in caplog.records)

    caplog.clear()
    configure_core_logging(LOG_FORMAT_JSON, "INFO")
    with caplog.at_level(logging.INFO, logger="mcp_coop.core"):
        mcp_tel = build_and_log_mcp_telemetry(
            tool_name="coop_search_memory",
            workspace_path=SAMPLE_WORKSPACE_PATH,
            args_payload={"query": "postgres backup"},
            result_payload={"count": 2, "results": [{"title": "pg_dump fix"}]},
            duration_ms=19.3,
            summary_note="hits=2",
        )
    assert mcp_tel["event_type"] == "mcp-tool"
    assert mcp_tel["tool_name"] == "coop_search_memory"
    parsed_json_log = json.loads(caplog.records[-1].message)
    assert parsed_json_log["mcp"]["method.name"] == "coop_search_memory"
    configure_core_logging(LOG_FORMAT_PRETTY, "INFO")


class _FakeTelemetryPool:
    """Мок-пул asyncpg для проверки записи и чтения `_telemetry` из таблицы `hook_events`."""

    def __init__(self) -> None:
        self.inserted_rows: list[tuple[Any, ...]] = []

    def acquire(self) -> Self:
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO hook_events" in query:
            self.inserted_rows.append(args)
        return "INSERT 0 1"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM hook_events" in query:
            now = datetime.now(tz=UTC)
            return [
                {
                    "id": 101,
                    "conversation_id": SAMPLE_CONVERSATION_ID,
                    "workspace_path": SAMPLE_WORKSPACE_PATH,
                    "event_type": "pre-invocation",
                    "tool_name": None,
                    "payload": json.dumps({"prompt": "test prompt"}),
                    "decision": json.dumps(
                        {
                            "_telemetry": {
                                "event_type": "pre-invocation",
                                "agent_type": "antigravity",
                                "duration_ms": 15.2,
                                "verdict": "allow",
                                "decided_by_tier": "default",
                                "tool_name": None,
                                "target": None,
                                "rules": [{"slug": "universal-engineering-rules", "relevance": 0.85}],
                                "skills": [{"slug": "coop-orchestrator", "relevance": 0.62, "mode": "BLUEPRINT"}],
                                "scripts": ["inject-context-map"],
                                "tokens": {
                                    "user_in": 45,
                                    "harness_injected": 280,
                                    "model_out": 0,
                                    "saved_pct": 93.3,
                                },
                                "summary_line": "[PreInvocation] 15.2ms │ tok: in=45 +inj=280",
                            }
                        }
                    ),
                    "created_at": now,
                },
                {
                    "id": 102,
                    "conversation_id": "mcp",
                    "workspace_path": SAMPLE_WORKSPACE_PATH,
                    "event_type": "mcp-tool",
                    "tool_name": "coop_search_memory",
                    "payload": json.dumps({"query": "logging"}),
                    "decision": json.dumps(
                        {
                            "_telemetry": {
                                "event_type": "mcp-tool",
                                "agent_type": "mcp",
                                "duration_ms": 11.4,
                                "verdict": "allow",
                                "tool_name": "coop_search_memory",
                                "target": "logging",
                                "rules": [],
                                "skills": [],
                                "scripts": [],
                                "tokens": {
                                    "user_in": 18,
                                    "harness_injected": 0,
                                    "model_out": 190,
                                    "saved_pct": 100.0,
                                },
                                "summary_line": "[MCP:coop_search_memory] 11.4ms │ hits=2",
                            }
                        }
                    ),
                    "created_at": now,
                },
            ]
        return []


class _FakePostgresDb:
    """Обёртка над `_FakeTelemetryPool`, совместимая с интерфейсом `PostgresDatabase`."""

    def __init__(self, pool: _FakeTelemetryPool) -> None:
        self._pool = pool

    @property
    def is_connected(self) -> bool:
        return True

    @property
    def pool(self) -> Any:
        return self._pool


@pytest.mark.asyncio
async def test_persist_and_query_telemetry_endpoint() -> None:
    """Проверяет сохранение `_telemetry` в БД и агрегацию метрик через `GET /api/visualizer/telemetry`."""
    fake_pool = _FakeTelemetryPool()
    fake_db = cast(PostgresDatabase, _FakePostgresDb(fake_pool))

    hook_in = CanonicalHookInput(
        event_type=HookEventType.PRE_TOOL_USE,
        agent_type=AgentType.ANTIGRAVITY,
        dialect=HookDialect.ANTIGRAVITY_PROTOJSON,
        conversation_id=SAMPLE_CONVERSATION_ID,
        workspace_path=SAMPLE_WORKSPACE_PATH,
        tool_name="run_command",
        tool_args={"CommandLine": "npm test"},
    )
    caller_response = {"decision": "allow"}
    sample_tel = {"duration_ms": 4.2, "verdict": "allow"}

    await _log_hook_event(fake_db, hook_in, caller_response, telemetry=sample_tel)
    # Убеждаемся, что ответ клиенту не был мутирован полем `_telemetry`
    assert "_telemetry" not in caller_response
    assert len(fake_pool.inserted_rows) == 1
    persisted_decision = json.loads(fake_pool.inserted_rows[0][5])
    assert persisted_decision["_telemetry"]["duration_ms"] == 4.2

    await _log_mcp_tool_event(
        fake_db,
        tool_name="coop_search_memory",
        workspace_path=SAMPLE_WORKSPACE_PATH,
        args_payload={"query": "test"},
        result_payload={"count": 1},
        telemetry={"duration_ms": 8.1, "summary_note": "hits=1"},
    )
    assert len(fake_pool.inserted_rows) == 2

    app = create_app()
    app.state.db = fake_db
    client = TestClient(app)

    resp = client.get("/api/visualizer/telemetry?since_minutes=60")
    assert resp.status_code == 200
    parsed = VisualizerTelemetryResponse.model_validate(resp.json())
    assert parsed.summary.total_events == 2
    assert parsed.summary.mcp_calls_count == 1
    assert parsed.summary.total_user_in_tokens == 63
    assert parsed.summary.total_harness_injected_tokens == 280
    assert parsed.summary.total_output_tokens == 190
    assert len(parsed.top_components) == 3

    ui_resp = client.get("/ui")
    assert ui_resp.status_code == 200
    assert "telemetry-btn" in ui_resp.text
    assert "telemetry-modal" in ui_resp.text
