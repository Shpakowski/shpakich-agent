"""Комплексный набор юнит- и интеграционных тестов декомпозиции ядра и архивации `/compact` (SPEC-003).

Проверяет 5 уровней верификации:
1. Обратную совместимость всех публичных и внутренних реэкспортов (`src.core.main`, `src.core.decision.*`, `src.core.db`).
2. Полноту регистрации всех HTTP-маршрутов управления, жизненных хуков и шлюза `/mcp` в `create_app()`.
3. Регрессионную корректность существующих хуков (`PreToolUse`: block/rewrite/plan validation; `SessionStart`: `injectSteps` + `_syncFiles`).
4. Сквозной контур `SPEC-003` (`PreInvocation` -> парсинг `transcript.jsonl` -> Re-Hydration `.context/index.md` -> архивация в `CompactionArchiveStore` -> дедупликация в Redis -> `coop_search_memory(include_compactions=True)` и `coop_get_factory_status()`).
5. Валидацию целостности графа знаний `.context/` (`ContextGraphService(Path.cwd()).validate()`).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.db import (
    SCHEMA_DDL,
    CompactionArchiveRecord,
    CompactionArchiveStore,
)
from src.core.decision import (
    CompactionCheckpoint,
    TranscriptTailInspection,
    compute_checkpoint_hash,
    extract_compaction_summary,
    inspect_transcript_tail,
    inspect_transcript_tail_detailed,
)
from src.core.enums import AgentType, SessionMetaField
from src.core.graph import ContextGraphManager, ContextGraphService
from src.core.main import create_app


class FakeAsyncpgPool:
    """In-memory имитатор пула `asyncpg.Pool` для тестирования `CompactionArchiveStore` и `get_factory_status`."""

    def __init__(self) -> None:
        self.compaction_rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.hook_events: list[dict[str, Any]] = []
        self.insert_attempts: int = 0

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Эмулирует идемпотентную вставку `INSERT INTO session_compactions ... ON CONFLICT DO NOTHING`."""
        if "INSERT INTO session_compactions" in query:
            self.insert_attempts += 1
            conv_id = str(args[0])
            ws_path = str(args[1])
            agent_type = str(args[2])
            step_idx = int(args[3])
            ckpt_hash = str(args[4])
            summary_text = str(args[5])
            stack_tags = list(args[6])
            key = (conv_id, ckpt_hash)
            if key in self.compaction_rows:
                return None
            new_id = str(uuid.uuid4())
            record = {
                "id": new_id,
                "conversation_id": conv_id,
                "workspace_path": ws_path,
                "agent_type": agent_type,
                "step_idx": step_idx,
                "checkpoint_hash": ckpt_hash,
                "summary_text": summary_text,
                "stack_tags": stack_tags,
                "relevance": 0.912,
            }
            self.compaction_rows[key] = record
            return {"id": new_id}
        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        """Эмулирует чтение существующего `id` при конфликте и подсчёт строк `SELECT COUNT(*)`."""
        upper_q = query.upper()
        if "SELECT ID" in upper_q and "FROM SESSION_COMPACTIONS" in upper_q:
            key = (str(args[0]), str(args[1]))
            existing = self.compaction_rows.get(key)
            return existing["id"] if existing else None
        if "SELECT COUNT(*) FROM SESSION_COMPACTIONS" in upper_q:
            return len(self.compaction_rows)
        if "SELECT COUNT(*) FROM HOOK_EVENTS" in upper_q:
            return len(self.hook_events)
        if "SELECT COUNT(*)" in upper_q:
            return 0
        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Эмулирует гибридный и лексический поиск по таблице `session_compactions`."""
        if "FROM session_compactions" not in query:
            return []
        search_q = str(args[0]).lower()
        search_tags = set(args[1]) if len(args) > 1 and isinstance(args[1], list) else set()
        ws_filter = args[2]
        limit = int(args[-1])
        matched: list[dict[str, Any]] = []
        for row in self.compaction_rows.values():
            if ws_filter is not None and row["workspace_path"] != ws_filter:
                continue
            row_tags = set(row.get("stack_tags", []))
            tag_overlap = bool(search_tags & row_tags)
            if (
                search_q in row["summary_text"].lower()
                or any(tok in row["summary_text"].lower() for tok in search_q.split())
                or tag_overlap
            ):
                matched.append(dict(row))
        return matched[:limit]

    async def execute(self, query: str, *args: Any) -> str:
        """Эмулирует запись событий аудита в таблицу `hook_events`."""
        if "INSERT INTO hook_events" in query:
            self.hook_events.append(
                {
                    "conversation_id": args[0],
                    "workspace_path": args[1],
                    "event_type": args[2],
                }
            )
        return "INSERT 0 1"


class FakePostgresDatabase:
    """In-memory обёртка над `FakeAsyncpgPool`, совместимая с интерфейсом `PostgresDatabase`."""

    def __init__(self) -> None:
        self.pool = FakeAsyncpgPool()
        self.is_connected = True

    async def check_health(self) -> bool:
        """Возвращает статус доступности тестовой БД."""
        return self.is_connected


class FakeRedisSessionStore:
    """In-memory имитатор `RedisSessionStore` для проверки дедупликации чекпоинтов `/compact`."""

    def __init__(self) -> None:
        self.is_connected = True
        self.meta: dict[str, dict[str, str]] = {}

    async def check_health(self) -> bool:
        """Возвращает статус доступности тестового Redis."""
        return self.is_connected

    async def init_session(self, conversation_id: str, workspace_path: str) -> None:
        """Инициализирует метаданные сессии в памяти."""
        self.meta.setdefault(conversation_id, {})["workspace_path"] = workspace_path

    async def get_session_meta(self, conversation_id: str) -> dict[str, str]:
        """Возвращает копию словаря метаданных сессии."""
        return dict(self.meta.get(conversation_id, {}))

    async def update_session_meta(
        self,
        conversation_id: str,
        updates: dict[str, str],
    ) -> None:
        """Обновляет поля метаданных сессии в памяти."""
        self.meta.setdefault(conversation_id, {}).update(
            {str(k): str(v) for k, v in updates.items()}
        )

    async def get_last_compaction_checkpoint(
        self,
        conversation_id: str,
    ) -> tuple[str, int]:
        """Возвращает `(last_compact_hash, last_compact_step)` из in-memory метаданных."""
        session_meta = self.meta.get(conversation_id, {})
        raw_hash = session_meta.get(SessionMetaField.LAST_COMPACT_HASH.value, "")
        raw_step = session_meta.get(SessionMetaField.LAST_COMPACT_STEP.value, "0")
        return raw_hash, int(raw_step) if raw_step.isdigit() else 0

    async def record_compaction_checkpoint(
        self,
        conversation_id: str,
        checkpoint_hash: str,
        step_idx: int,
    ) -> None:
        """Записывает `last_compact_hash` и `last_compact_step` в in-memory метаданные сессии."""
        bucket = self.meta.setdefault(conversation_id, {})
        bucket[SessionMetaField.LAST_COMPACT_HASH.value] = checkpoint_hash
        bucket[SessionMetaField.LAST_COMPACT_STEP.value] = str(max(0, int(step_idx)))

    async def get_last_error(self, conversation_id: str) -> None:
        """Возвращает отсутствие активной ошибки в сессии."""
        _ = conversation_id

    async def get_cached_rules_bundle(self, stack_tags: Any) -> None:
        """Отключает кэш бандлов для чистого прогона компилятора."""
        _ = stack_tags

    async def cache_rules_bundle(self, stack_tags: Any, bundle: Any) -> None:
        """No-op сохранение бандла правил."""
        _ = (stack_tags, bundle)

    async def get_cached_decision(self, tool_name: str, command: str) -> None:
        """Отключает кэш решений для полной проверки тиров."""
        _ = (tool_name, command)

    async def cache_decision(
        self,
        tool_name: str,
        command: str,
        decision_payload: Any,
    ) -> None:
        """No-op кэширование вердикта."""
        _ = (tool_name, command, decision_payload)

    async def get_command_failure_count(
        self,
        conversation_id: str,
        tool_name: str,
        command: str,
    ) -> int:
        """Возвращает 0 сбоев команды по умолчанию."""
        _ = (conversation_id, tool_name, command)
        return 0


def test_level1_backward_compatible_imports() -> None:
    """Уровень 1: Проверяет 100% сохранность всех прежних импортов после декомпозиции."""
    import src.core.api as core_api
    import src.core.db as core_db
    import src.core.decision as core_decision
    import src.core.decision.engine as decision_engine_mod
    import src.core.decision.hook_dialects as hook_dialects_mod
    import src.core.main as core_main

    main_expected = (
        "ProjectRegistrationRequest",
        "ComponentPatchRequest",
        "ProviderPatchRequest",
        "ContextHealRequest",
        "_build_local_mcp_url",
        "_get_engine",
        "_get_script_dispatcher",
        "_get_compaction_store",
        "_resolve_project_context",
        "_resolve_project_stack_tags",
        "_build_plan_best_practices_hint",
        "_log_hook_event",
        "HARNESS_CONFIG_REL_PATH",
        "MAX_PLAN_PATTERNS_INJECTED",
        "MAX_PLAN_MEMORIES_INJECTED",
        "MAX_PLAN_COMPACTIONS_INJECTED",
        "PLAN_KEYWORDS",
        "UPSERT_PROJECT_SQL",
        "UPDATE_COMPONENT_STATUS_SQL",
        "create_app",
        "lifespan",
        "app",
    )
    for symbol in main_expected:
        assert hasattr(core_main, symbol), f"Missing symbol in src.core.main: {symbol}"

    for symbol in ("management_router", "hooks_router", "_build_plan_best_practices_hint"):
        assert hasattr(core_api, symbol), f"Missing symbol in src.core.api: {symbol}"

    for symbol in (
        "DecisionEngine",
        "DecisionTier",
        "DomainEvaluationSpec",
        "LocalDeterministicTier",
        "SystemOneDecisionTier",
        "AntigravityProbeDecisionTier",
        "build_default_domain_specs",
    ):
        assert hasattr(decision_engine_mod, symbol)
        assert hasattr(core_decision, symbol)

    for symbol in (
        "CompactionCheckpoint",
        "TranscriptTailInspection",
        "extract_compaction_summary",
        "compute_checkpoint_hash",
        "inspect_transcript_tail_detailed",
        "inspect_transcript_tail",
    ):
        assert hasattr(hook_dialects_mod, symbol)
        assert hasattr(core_decision, symbol)

    assert hasattr(core_db, "CompactionArchiveRecord")
    assert hasattr(core_db, "CompactionArchiveStore")
    assert "CREATE TABLE IF NOT EXISTS session_compactions" in SCHEMA_DDL


def test_level2_all_routes_registered() -> None:
    """Уровень 2: Проверяет наличие всех 14 управляющих/хуковых маршрутов и `/mcp` в `create_app()`."""
    app = create_app()
    route_methods_by_path: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path:
            route_methods_by_path.setdefault(path, set()).update(methods or set())
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            for sub_route in getattr(included_router, "routes", ()):
                sub_path = getattr(sub_route, "path", None)
                sub_methods = getattr(sub_route, "methods", None)
                if sub_path:
                    route_methods_by_path.setdefault(sub_path, set()).update(
                        sub_methods or set()
                    )

    expected_routes: list[tuple[str, str]] = [
        ("/health", "GET"),
        ("/api/context/heal", "POST"),
        ("/api/projects/register", "POST"),
        ("/api/harness/components", "GET"),
        ("/api/harness/components/{slug}", "PATCH"),
        ("/api/providers", "GET"),
        ("/api/providers", "POST"),
        ("/api/providers/{name}", "PATCH"),
        ("/api/providers/{name}", "DELETE"),
        ("/api/decisions/evaluate", "POST"),
        ("/api/hooks/session-start", "POST"),
        ("/api/hooks/pre-tool-use", "POST"),
        ("/api/hooks/post-tool-use", "POST"),
        ("/api/hooks/pre-invocation", "POST"),
        ("/api/hooks/stop", "POST"),
    ]
    for path, method in expected_routes:
        assert path in route_methods_by_path, f"Missing route path: {path}"
        assert method in route_methods_by_path[path], f"Missing {method} on {path}"

    assert "/mcp" in route_methods_by_path


@pytest.mark.asyncio
async def test_level3_regression_existing_hooks(tmp_path: Path) -> None:
    """Уровень 3: Регрессионный прогон `pre-tool-use` (block, rewrite, plan) и `session-start`."""
    app = create_app()
    context_dir = tmp_path / ".context"
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "index.md").write_text("# Test Context Index\n- [[mod-python-core]]\n", encoding="utf-8")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Блокировка деструктивной команды `rm -rf /`
        resp_block = await client.post(
            "/api/hooks/pre-tool-use",
            json={
                "conversationId": "conv-reg-1",
                "workspacePaths": [str(tmp_path)],
                "toolName": "run_command",
                "toolArgs": {"CommandLine": "rm -rf /"},
            },
        )
        assert resp_block.status_code == 200
        block_data = resp_block.json()
        assert block_data["decision"] == "deny"
        assert "ephemeralMessage" not in block_data

        # 2. Прозрачная перезапись `npm install` -> `bun install`
        resp_rewrite = await client.post(
            "/api/hooks/pre-tool-use",
            json={
                "conversationId": "conv-reg-1",
                "workspacePaths": [str(tmp_path)],
                "stack_tags": ["bun", "typescript"],
                "toolName": "run_command",
                "toolArgs": {"CommandLine": "npm install zod"},
            },
        )
        assert resp_rewrite.status_code == 200
        rewrite_data = resp_rewrite.json()
        assert rewrite_data["decision"] == "allow"
        assert rewrite_data["overwrite"]["CommandLine"] == "bun install zod"

        # 3. Валидация `implementation_plan.md` (отклонение слишком короткого плана без `.context/`)
        resp_plan_bad = await client.post(
            "/api/hooks/pre-tool-use",
            json={
                "conversationId": "conv-reg-1",
                "workspacePaths": [str(tmp_path)],
                "toolName": "write_file",
                "toolArgs": {
                    "Path": str(tmp_path / "implementation_plan.md"),
                    "Content": "Too short plan",
                },
            },
        )
        assert resp_plan_bad.status_code == 200
        assert resp_plan_bad.json()["decision"] == "deny"

        # 4. `session-start` возвращает `injectSteps` и `_syncFiles`
        resp_start = await client.post(
            "/api/hooks/session-start",
            json={
                "conversationId": "conv-reg-1",
                "workspacePaths": [str(tmp_path)],
            },
        )
        assert resp_start.status_code == 200
        start_data = resp_start.json()
        assert "injectSteps" in start_data
        assert len(start_data["injectSteps"]) >= 1
        assert "_syncFiles" in start_data
        assert len(start_data["_syncFiles"]) >= 1


@pytest.mark.asyncio
async def test_level4_spec003_end_to_end_compaction_archival_and_deduplication(
    tmp_path: Path,
) -> None:
    """Уровень 4: Сквозной сценарий `SPEC-003` (PreInvocation -> Archive -> Dedup -> Search -> Status)."""
    app = create_app()
    fake_db = FakePostgresDatabase()
    fake_redis = FakeRedisSessionStore()
    app.state.db = fake_db
    app.state.redis = fake_redis

    context_dir = tmp_path / ".context"
    context_dir.mkdir(parents=True, exist_ok=True)
    index_marker = "# Unique Context Graph Map Marker 2026"
    (context_dir / "index.md").write_text(index_marker, encoding="utf-8")

    summary_body = (
        "Implemented FastAPI router decomposition into src/core/api/ and added "
        "PostgreSQL session_compactions hybrid vector-lexical archival store."
    )
    transcript_file = tmp_path / "transcript.jsonl"
    transcript_lines = [
        json.dumps(
            {
                "type": "CHECKPOINT",
                "step_index": 42,
                "content": f"# Resuming from a compaction\n<summary>\n{summary_body}\n</summary>",
            }
        ),
        json.dumps(
            {
                "type": "USER_INPUT",
                "step_index": 43,
                "content": "Continue with /plan for next iteration",
            }
        ),
    ]
    transcript_file.write_text("\n".join(transcript_lines) + "\n", encoding="utf-8")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "conversationId": "conv-spec003-e2e",
            "workspacePaths": [str(tmp_path)],
            "transcriptPath": str(transcript_file),
            "stack_tags": ["python", "fastapi", "postgresql"],
        }

        # Первый вызов: должен выполнить Re-Hydration `.context/index.md`, сархивировать сводку в БД и записать хеш в Redis
        resp_first = await client.post("/api/hooks/pre-invocation", json=payload)
        assert resp_first.status_code == 200
        first_data = resp_first.json()
        inject_steps_1 = first_data.get("injectSteps", [])
        sys_messages_1 = [
            step["systemMessage"]["systemMessage"]
            for step in inject_steps_1
            if "systemMessage" in step
        ]
        eph_messages_1 = [
            step["ephemeralMessage"]
            for step in inject_steps_1
            if "ephemeralMessage" in step
        ]
        assert any("[Post-Compaction Re-Hydration]" in msg for msg in sys_messages_1)
        assert any(index_marker in msg for msg in sys_messages_1)
        assert any("[Prior Session Compactions]" in msg for msg in eph_messages_1)
        assert fake_db.pool.insert_attempts == 1
        assert len(fake_db.pool.compaction_rows) == 1

        expected_hash = compute_checkpoint_hash(summary_body)
        recorded_hash, recorded_step = await fake_redis.get_last_compaction_checkpoint(
            "conv-spec003-e2e"
        )
        assert recorded_hash == expected_hash
        assert recorded_step == 42

        # Повторный вызов на следующем шаге с тем же чекпоинтом в хвосте: должен дедуплицироваться!
        resp_second = await client.post("/api/hooks/pre-invocation", json=payload)
        assert resp_second.status_code == 200
        second_data = resp_second.json()
        inject_steps_2 = second_data.get("injectSteps", [])
        sys_messages_2 = [
            step["systemMessage"]["systemMessage"]
            for step in inject_steps_2
            if "systemMessage" in step
        ]
        assert not any("[Post-Compaction Re-Hydration]" in msg for msg in sys_messages_2)
        assert fake_db.pool.insert_attempts == 1
        assert len(fake_db.pool.compaction_rows) == 1

    # Проверка MCP-инструмента `coop_search_memory(..., include_compactions=True)`
    mcp_service = app.state.mcp_service
    search_res = await mcp_service.search_memory(
        query="FastAPI router decomposition session_compactions",
        stack_tags=["python", "fastapi"],
        include_compactions=True,
        workspace_path=str(tmp_path),
    )
    assert "compactions" in search_res
    assert search_res["compactions_count"] == 1
    assert search_res["compactions"][0]["checkpoint_hash"] == expected_hash
    assert search_res["compactions"][0]["step_idx"] == 42
    assert "FastAPI router decomposition" in search_res["compactions"][0]["summary_text"]

    # Проверка `coop_get_factory_status()`
    status_res = await mcp_service.get_factory_status()
    assert status_res["database_counts"]["session_compactions"] == 1


@pytest.mark.asyncio
async def test_compaction_store_edge_cases_and_conflict_idempotency() -> None:
    """Проверяет граничные случаи `CompactionArchiveStore` и `transcript.py`."""
    fake_db = FakePostgresDatabase()
    store = CompactionArchiveStore(db=fake_db, embedding_client=None)

    # Пустые входные строки безопасно возвращают None
    assert (
        await store.archive_compaction(
            conversation_id="",
            workspace_path="/tmp/ws",
            agent_type=AgentType.ANTIGRAVITY,
            step_idx=1,
            checkpoint_hash="abcd1234abcd1234",
            summary_text="valid text",
        )
        is None
    )

    # Первая запись возвращает `inserted=True`
    rec1: CompactionArchiveRecord | None = await store.archive_compaction(
        conversation_id="conv-idemp",
        workspace_path="/tmp/ws",
        agent_type=AgentType.CLAUDE_CODE,
        step_idx=10,
        checkpoint_hash="1122334455667788",
        summary_text="Solved asyncpg pool initialization race condition.",
        stack_tags=["Python", "AsyncPG"],
    )
    assert rec1 is not None
    assert rec1.inserted is True
    assert rec1.stack_tags == ["python", "asyncpg"]

    # Повторная запись с тем же `(conversation_id, checkpoint_hash)` возвращает `inserted=False` и тот же `id`
    rec2 = await store.archive_compaction(
        conversation_id="conv-idemp",
        workspace_path="/tmp/ws",
        agent_type=AgentType.CLAUDE_CODE,
        step_idx=10,
        checkpoint_hash="1122334455667788",
        summary_text="Solved asyncpg pool initialization race condition.",
    )
    assert rec2 is not None
    assert rec2.inserted is False
    assert rec2.id == rec1.id

    # Проверка инвариантности хеша к пробелам и переносам строк
    assert compute_checkpoint_hash("hello   world\nfoo") == compute_checkpoint_hash(
        "  hello world foo  "
    )
    assert extract_compaction_summary("<summary>   </summary>") is None
    assert inspect_transcript_tail(None) == (False, "")
    detailed: TranscriptTailInspection = inspect_transcript_tail_detailed(
        None,
        fallback_step_idx=7,
        raw_payload={"compaction_summary": "<summary>Extracted from payload</summary>"},
    )
    assert detailed.is_post_compact is True
    assert isinstance(detailed.checkpoint, CompactionCheckpoint)
    assert detailed.checkpoint.summary_text == "Extracted from payload"
    assert detailed.checkpoint.step_idx == 7


def test_level5_context_graph_validation() -> None:
    """Уровень 5: Проверяет валидность графа `.context/` (`0` фантомных файлов, `0` битых ссылок)."""
    workspace_root = Path.cwd()
    report = ContextGraphService(workspace_root).validate()
    assert report.is_valid is True, (
        f"Context graph invalid: missing_files={report.missing_files}, "
        f"broken_links={report.broken_links}, parse_errors={report.parse_errors}"
    )
    assert report.missing_files == {}
    assert report.broken_links == {}
    assert report.parse_errors == {}

    _, _, mgr_report = ContextGraphManager().build_and_validate(workspace_root)
    assert mgr_report.is_valid is True
