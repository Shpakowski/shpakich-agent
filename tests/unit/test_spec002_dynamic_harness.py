"""Комплексный набор юнит- и интеграционных тестов динамического харнеса и 3-Way Sync (SPEC-002).

Проверяет 4 архитектурных контура:
1. Минимальный Bootstrap (`HarnessCompiler.build_canonical_bundle`) и двухступенчатый Progressive Disclosure (`select_dynamic_components_for_prompt`).
2. Двусторонний инжест пользовательских компонентов (`WorkspaceSyncEngine.ingest_components`) с `Content-Hash Gate` и `Anti-Self-Ingest`.
3. Структурный AST-мержер (`merge_context_node_markdown`) и 3-Way Hash синхронизацию узлов `.context/` (`WorkspaceSyncEngine.sync_context_nodes`).
4. Кластеризацию и Lineage-слияние дубликатов (`HarnessConsolidator`) и HTTP-эндпоинты `/api/sync/*`, `/api/harness/consolidate`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.core.enums import (
    ComponentStatus,
    ComponentType,
    ContextSyncAction,
    ScriptExecutionMode,
)
from src.core.graph.markdown_merge import (
    compute_normalized_content_hash,
    merge_context_node_markdown,
)
from src.core.graph.sync_engine import (
    INGEST_ACTION_CREATED,
    INGEST_ACTION_SKIPPED_SYSTEM,
    INGEST_ACTION_UNCHANGED,
    INGEST_ACTION_UPDATED,
    ContextNodeSyncInput,
    WorkspaceComponentCandidate,
    WorkspaceSyncEngine,
)
from src.core.harness.compiler import HarnessCompiler
from src.core.harness.consolidator import HarnessConsolidator
from src.core.harness.dynamic_rag import (
    RAG_SOURCE_POSTGRES,
    select_dynamic_components_for_prompt,
)
from src.core.harness.seed import HARNESS_ORCHESTRATOR_SLUG
from src.core.main import create_app

STEWARD_SLUG: str = "context-graph-steward"
DESTRUCTIVE_GUARDRAIL_SLUG: str = "block-destructive-commands"
BUN_GUARDRAIL_SLUG: str = "rewrite-npm-to-bun"
TEST_WORKSPACE_PATH: str = "/tmp/spec002-workspace"
FAKE_PROJECT_UUID: str = "00000000-0000-0000-0000-000000000001"


class FakeSpec002Pool:
    """In-memory имитатор пула `asyncpg.Pool` для тестирования `dynamic_rag`, `sync_engine` и `consolidator`."""

    def __init__(self) -> None:
        self.components: dict[str, dict[str, Any]] = {}
        self.context_nodes: dict[tuple[str, str], dict[str, Any]] = {}
        self.rag_rows: list[dict[str, Any]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Эмулирует выборки кандидатов RAG, узлов `.context/` и кандидатов консолидации."""
        if "AS relevance" in query and "FROM harness_components" in query:
            return list(self.rag_rows)
        if "FROM project_context_nodes" in query:
            ws = str(args[0])
            return [dict(r) for (r_ws, _), r in sorted(self.context_nodes.items()) if r_ws == ws]
        if "FROM harness_components" in query and "ORDER BY score DESC, usage_count DESC" in query:
            status_f, type_f, exclude_mode = str(args[0]), set(args[1] or ()), str(args[2])
            tag_f = set(args[3]) if args[3] else None
            matched: list[dict[str, Any]] = []
            for row in self.components.values():
                if row.get("status") != status_f or row.get("component_type") not in type_f:
                    continue
                meta = row.get("metadata") or {}
                if isinstance(meta, str):
                    meta = json.loads(meta)
                if str(meta.get("execution_mode", "semantic")) == exclude_mode:
                    continue
                if tag_f and not (set(row.get("stack_tags") or ["global"]) & tag_f):
                    continue
                matched.append(dict(row))
            return matched
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Эмулирует проверку `content_hash` и UPSERT компонента в `harness_components`."""
        if "SELECT id, content_hash, version FROM harness_components" in query:
            row = self.components.get(str(args[0]))
            return dict(row) if row is not None else None
        if "INSERT INTO harness_components" in query and "RETURNING version" in query:
            slug = str(args[0])
            existing = self.components.get(slug)
            next_ver = (int(existing["version"]) + 1) if existing else 1
            self.components[slug] = {
                "id": slug, "slug": slug, "component_type": str(args[1]), "scope": str(args[2]),
                "stack_tags": list(args[3]), "title": str(args[4]), "description": str(args[5]),
                "content": str(args[6]), "content_hash": str(args[7]), "metadata": json.loads(str(args[8])),
                "status": str(args[9]), "score": float(args[10]), "source_project_path": str(args[11]),
                "embedding": args[12], "usage_count": 0, "version": next_ver,
            }
            return {"version": next_ver}
        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        """Эмулирует разрешение `project_id` по пути рабочей директории."""
        _ = args
        return FAKE_PROJECT_UUID if "SELECT id FROM projects" in query else None

    async def execute(self, query: str, *args: Any) -> str:
        """Эмулирует запись узлов `.context/` и транзакционное обновление/архивацию кластеров."""
        if "INSERT INTO project_context_nodes" in query:
            ws, rel_path = str(args[1]), str(args[2])
            self.context_nodes[(ws, rel_path)] = {
                "project_id": args[0], "workspace_path": ws, "relative_path": rel_path,
                "node_id": str(args[3]), "entity_class": str(args[4]), "content": str(args[5]),
                "content_hash": str(args[6]), "base_hash": str(args[7]),
            }
            return "INSERT 0 1"
        if "UPDATE project_context_nodes SET base_hash" in query:
            key = (str(args[1]), str(args[2]))
            if key in self.context_nodes:
                self.context_nodes[key]["base_hash"] = str(args[0])
            return "UPDATE 1"
        if "SET title = $2, description = $3" in query:
            slug = str(args[0])
            if slug in self.components:
                comp = self.components[slug]
                comp.update({
                    "title": str(args[1]), "description": str(args[2]), "content": str(args[3]),
                    "content_hash": str(args[4]), "stack_tags": list(args[5]),
                    "metadata": json.loads(str(args[6])), "score": float(args[8]),
                    "version": int(comp.get("version", 1)) + 1,
                })
            return "UPDATE 1"
        if "SET status = $2, metadata = COALESCE" in query:
            dup_slugs, new_status, extra_meta = list(args[0]), str(args[1]), json.loads(str(args[2]))
            for dup_slug in dup_slugs:
                if dup_slug in self.components:
                    self.components[dup_slug]["status"] = new_status
                    self.components[dup_slug]["metadata"] = {
                        **dict(self.components[dup_slug].get("metadata") or {}),
                        **extra_meta,
                    }
            return f"UPDATE {len(dup_slugs)}"
        return "OK"


class FakeSpec002Database:
    """Обёртка над `FakeSpec002Pool`, совместимая с контрактом `PostgresDatabase`."""

    def __init__(self) -> None:
        self.pool = FakeSpec002Pool()
        self.is_connected = True


@pytest.mark.asyncio
async def test_contour1_minimal_bootstrap_and_progressive_disclosure() -> None:
    """Контур 1: Проверяет фильтрацию `execution_mode='always'` в бандле и 2 порога Progressive Disclosure."""
    compiler = HarnessCompiler()
    bundle = await compiler.build_canonical_bundle(stack_tags=["bun", "python"])

    skill_slugs = {skill.slug for skill in bundle.skills}
    assert HARNESS_ORCHESTRATOR_SLUG in skill_slugs
    assert STEWARD_SLUG not in skill_slugs

    guardrail_slugs = {gr.slug for gr in bundle.guardrails}
    assert DESTRUCTIVE_GUARDRAIL_SLUG in guardrail_slugs
    assert BUN_GUARDRAIL_SLUG in guardrail_slugs
    assert "### Guardrail: Bun Package Manager Enforcement" not in bundle.rules_markdown

    forced_bundle = await compiler.build_canonical_bundle(
        stack_tags=["python"],
        profile_overrides={"enabled_components": [STEWARD_SLUG]},
    )
    assert STEWARD_SLUG in {skill.slug for skill in forced_bundle.skills}

    fake_db = FakeSpec002Database()
    fake_db.pool.rag_rows = [
        {
            "slug": STEWARD_SLUG, "component_type": ComponentType.SKILL.value,
            "title": "Context Graph Steward", "description": "Synchronize .context/ nodes and AI Traces.",
            "content": "# Full Steward Blueprint\nValidate all wiki links.",
            "metadata": {"execution_mode": ScriptExecutionMode.SEMANTIC.value},
            "score": 1.0, "relevance": 0.62,
        },
        {
            "slug": "fastapi-pagination-skill", "component_type": ComponentType.SKILL.value,
            "title": "FastAPI Cursor Pagination", "description": "Keyset pagination blueprint for asyncpg.",
            "content": "# FastAPI Pagination\nUse keyset cursors.",
            "metadata": {"execution_mode": ScriptExecutionMode.SEMANTIC.value},
            "score": 1.0, "relevance": 0.31,
        },
        {
            "slug": "asyncpg-pool-rule", "component_type": ComponentType.TOOL_GUIDANCE.value,
            "title": "AsyncPG Pool Tuning", "description": "Connection pool sizing.",
            "content": "Always configure max_inactive_connection_lifetime.",
            "metadata": {"execution_mode": ScriptExecutionMode.SEMANTIC.value},
            "score": 1.0, "relevance": 0.41,
        },
    ]

    rag_res = await select_dynamic_components_for_prompt(
        prompt="Update .context/ graph and check asyncpg pool",
        stack_tags=["python", "asyncpg"],
        db=fake_db,  # type: ignore[arg-type]
    )
    assert rag_res.source == RAG_SOURCE_POSTGRES
    assert len(rag_res.rules) == 1 and rag_res.rules[0].slug == "asyncpg-pool-rule"
    by_slug = {s.slug: s for s in rag_res.skills}
    assert by_slug[STEWARD_SLUG].is_full_blueprint is True
    assert by_slug["fastapi-pagination-skill"].is_full_blueprint is False

    hint_text = rag_res.format_ephemeral_hint()
    assert hint_text is not None
    assert "[Dynamic Harness² Rules]:" in hint_text
    assert f"[Active Skill Blueprint: {STEWARD_SLUG}]:" in hint_text
    assert "[Available Skill Hint]: fastapi-pagination-skill —" in hint_text


@pytest.mark.asyncio
async def test_contour2_workspace_ingest_content_hash_gate() -> None:
    """Контур 2: Проверяет защиту `skipped_system`, `Content-Hash Gate` (`unchanged`) и версионирование при инжесте."""
    fake_db = FakeSpec002Database()
    sync_engine = WorkspaceSyncEngine(db=fake_db)  # type: ignore[arg-type]

    custom_v1 = "# Custom Pytest Skill\nAlways run pytest with strict markers and fixtures."
    candidates = [
        WorkspaceComponentCandidate(
            slug=HARNESS_ORCHESTRATOR_SLUG, component_type=ComponentType.SKILL,
            title="System Orchestrator", content="# System bootstrap skill content over twenty chars",
        ),
        WorkspaceComponentCandidate(
            slug="custom-pytest-skill", component_type=ComponentType.SKILL,
            title="Custom Pytest Skill", description="Pytest guidelines",
            content=custom_v1, stack_tags=["python", "pytest"],
        ),
    ]

    report1 = await sync_engine.ingest_components(TEST_WORKSPACE_PATH, ["python"], candidates)
    assert report1.created == 1 and report1.unchanged == 0
    actions1 = {item.slug: item.action for item in report1.items}
    assert actions1[HARNESS_ORCHESTRATOR_SLUG] == INGEST_ACTION_SKIPPED_SYSTEM
    assert actions1["custom-pytest-skill"] == INGEST_ACTION_CREATED
    assert fake_db.pool.components["custom-pytest-skill"]["metadata"]["execution_mode"] == ScriptExecutionMode.SEMANTIC.value

    report2 = await sync_engine.ingest_components(TEST_WORKSPACE_PATH, ["python"], [candidates[1]])
    assert report2.unchanged == 1 and report2.items[0].action == INGEST_ACTION_UNCHANGED

    modified_cand = WorkspaceComponentCandidate(
        slug="custom-pytest-skill", component_type=ComponentType.SKILL,
        title="Custom Pytest Skill v2", description="Updated pytest guidelines",
        content=custom_v1 + "\nUse async fixtures for database tests.", stack_tags=["python", "pytest"],
    )
    report3 = await sync_engine.ingest_components(TEST_WORKSPACE_PATH, ["python"], [modified_cand])
    assert report3.updated == 1 and report3.items[0].action == INGEST_ACTION_UPDATED
    assert report3.items[0].version == 2


@pytest.mark.asyncio
async def test_contour3_ast_markdown_merge_and_3way_context_sync() -> None:
    """Контур 3: Проверяет AST-мерж YAML/AI Traces и матрицу 3-Way Hash Sync `(local_hash, db_hash, base_hash)`."""
    local_md = (
        "---\ntype: Entity\nclass: Module\nid: mod-demo\nname: Demo Module\nstatus: Active\n"
        "stack:\n  - python\nfiles:\n  - src/a.py\ndepends_on:\n  - '[[mod-base]]'\n"
        "implements:\n  - '[[contract-a]]'\ngoverned_by:\n  - '[[adr-001]]'\n---\n\n"
        "# Demo Module Updated Locally\n\n## Логи контекста (AI Traces)\n"
        "- 2026-10-25: Local trace entry B.\n- 2026-10-20: Shared initial trace.\n"
    )
    db_md = (
        "---\ntype: Entity\nclass: Module\nid: mod-demo\nname: Demo Module\nstatus: Active\n"
        "stack:\n  - python\n  - fastapi\nfiles:\n  - src/a.py\n  - src/b.py\n"
        "depends_on:\n  - '[[mod-base]]'\n  - '[[mod-extra]]'\nimplements:\n  - '[[contract-b]]'\n"
        "governed_by:\n  - '[[adr-002]]'\n---\n\n# Demo Module DB Body\n\n## Логи контекста (AI Traces)\n"
        "- 2026-10-22: Server trace entry A.\n- 2026-10-20: Shared initial trace.\n"
    )
    merged = merge_context_node_markdown(local_md, db_md, prefer_local_body=True)
    assert "src/a.py" in merged and "src/b.py" in merged and "fastapi" in merged
    assert "[[mod-extra]]" in merged and "[[contract-b]]" in merged and "[[adr-002]]" in merged
    assert "# Demo Module Updated Locally" in merged
    assert merged.index("2026-10-20:") < merged.index("2026-10-22:") < merged.index("2026-10-25:")
    assert merged.count("Shared initial trace.") == 1

    fake_db = FakeSpec002Database()
    sync_engine = WorkspaceSyncEngine(db=fake_db)  # type: ignore[arg-type]
    base_hash = compute_normalized_content_hash(db_md)
    updated_db_md = db_md.replace("# Demo Module DB Body", "# Demo Module Newer Server Body")
    updated_db_hash = compute_normalized_content_hash(updated_db_md)

    fake_db.pool.context_nodes = {
        (TEST_WORKSPACE_PATH, "entities/modules/mod-pull.md"): {
            "project_id": "p1", "workspace_path": TEST_WORKSPACE_PATH, "relative_path": "entities/modules/mod-pull.md",
            "node_id": "mod-pull", "entity_class": "Module", "content": updated_db_md,
            "content_hash": updated_db_hash, "base_hash": base_hash,
        },
        (TEST_WORKSPACE_PATH, "entities/modules/mod-conflict.md"): {
            "project_id": "p1", "workspace_path": TEST_WORKSPACE_PATH, "relative_path": "entities/modules/mod-conflict.md",
            "node_id": "mod-demo", "entity_class": "Module", "content": db_md,
            "content_hash": base_hash, "base_hash": "old-ancestor-hash",
        },
        (TEST_WORKSPACE_PATH, "entities/modules/mod-missing-local.md"): {
            "project_id": "p1", "workspace_path": TEST_WORKSPACE_PATH, "relative_path": "entities/modules/mod-missing-local.md",
            "node_id": "mod-missing-local", "entity_class": "Module", "content": db_md,
            "content_hash": base_hash, "base_hash": base_hash,
        },
    }

    sync_report = await sync_engine.sync_context_nodes(
        TEST_WORKSPACE_PATH,
        [
            ContextNodeSyncInput(relative_path="entities/modules/mod-push.md", node_id="mod-push", entity_class="Module", content=local_md),
            ContextNodeSyncInput(relative_path="entities/modules/mod-pull.md", node_id="mod-pull", entity_class="Module", content=db_md, content_hash=base_hash, base_hash=base_hash),
            ContextNodeSyncInput(relative_path="entities/modules/mod-conflict.md", node_id="mod-demo", entity_class="Module", content=local_md, base_hash="old-ancestor-hash"),
        ],
    )
    assert sync_report.pushed_to_db == 1 and sync_report.pulled_from_db == 2 and sync_report.merged == 1
    by_path = {item.relative_path: item for item in sync_report.items}
    assert by_path["entities/modules/mod-push.md"].action == ContextSyncAction.PUSHED_TO_DB
    assert by_path["entities/modules/mod-pull.md"].action == ContextSyncAction.PULLED_FROM_DB
    assert by_path["entities/modules/mod-pull.md"].content == updated_db_md
    assert by_path["entities/modules/mod-conflict.md"].action == ContextSyncAction.MERGED
    assert by_path["entities/modules/mod-missing-local.md"].action == ContextSyncAction.PULLED_FROM_DB


@pytest.mark.asyncio
async def test_contour4_consolidator_and_fastapi_sync_endpoints() -> None:
    """Контур 4: Проверяет `HarnessConsolidator` (`dry_run` + транзакция) и маршруты `/api/sync/*`, `/api/harness/consolidate`."""
    fake_db = FakeSpec002Database()
    fake_db.pool.components = {
        "fastapi-auth-jwt": {
            "slug": "fastapi-auth-jwt", "component_type": ComponentType.SKILL.value, "scope": "domain",
            "stack_tags": ["python", "fastapi"], "title": "FastAPI JWT Authentication Blueprint",
            "description": "Validate OAuth2 bearer JWT tokens in FastAPI dependencies.",
            "content": "## Overview\nUse OAuth2PasswordBearer.\n\n## Security Rules\n- Verify token exp claim.",
            "metadata": {"execution_mode": ScriptExecutionMode.SEMANTIC.value},
            "status": ComponentStatus.COMPOSED.value, "score": 1.2, "usage_count": 5, "version": 1, "embedding": [0.9, 0.1, 0.0],
        },
        "fastapi-jwt-security": {
            "slug": "fastapi-jwt-security", "component_type": ComponentType.SKILL.value, "scope": "domain",
            "stack_tags": ["fastapi", "pydantic"], "title": "FastAPI JWT Bearer Security Guide",
            "description": "Validate OAuth2 bearer JWT tokens in FastAPI endpoints.",
            "content": "## Overview\nUse OAuth2PasswordBearer.\n\n## Security Rules\n- Reject alg none tokens.\n\n## Testing\n- Mock decode key.",
            "metadata": {"execution_mode": ScriptExecutionMode.SEMANTIC.value},
            "status": ComponentStatus.COMPOSED.value, "score": 1.0, "usage_count": 2, "version": 1, "embedding": [0.89, 0.11, 0.0],
        },
    }

    consolidator = HarnessConsolidator(db=fake_db)  # type: ignore[arg-type]
    dry_report = await consolidator.consolidate(dry_run=True)
    assert dry_report.clusters_found == 1 and dry_report.components_archived == 0
    assert fake_db.pool.components["fastapi-jwt-security"]["status"] == ComponentStatus.COMPOSED.value

    live_report = await consolidator.consolidate(dry_run=False)
    assert live_report.clusters_found == 1 and live_report.components_archived == 1
    cluster = live_report.clusters[0]
    assert cluster.canonical_slug == "fastapi-auth-jwt" and cluster.duplicate_slugs == ("fastapi-jwt-security",)
    assert set(cluster.merged_stack_tags) == {"fastapi", "pydantic", "python"}
    assert "Reject alg none tokens" in cluster.synthesized_content and "## Testing" in cluster.synthesized_content

    assert fake_db.pool.components["fastapi-auth-jwt"]["version"] == 2
    assert fake_db.pool.components["fastapi-auth-jwt"]["metadata"]["merged_from"] == ["fastapi-jwt-security"]
    assert fake_db.pool.components["fastapi-jwt-security"]["status"] == ComponentStatus.ARCHIVED.value
    assert fake_db.pool.components["fastapi-jwt-security"]["metadata"]["merged_into"] == "fastapi-auth-jwt"

    app = create_app()
    app.state.db = fake_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp_ingest = await client.post("/api/sync/ingest", json={
            "workspace_path": TEST_WORKSPACE_PATH, "stack_tags": ["python"],
            "components": [{"slug": "api-test-rule", "component_type": "system_prompt", "title": "API Test Rule", "description": "Rule via HTTP ingest", "content": "Always validate HTTP payloads with strict Pydantic models."}],
        })
        assert resp_ingest.status_code == 200 and resp_ingest.json()["created"] == 1

        resp_ctx = await client.post("/api/sync/context", json={
            "workspace_path": TEST_WORKSPACE_PATH,
            "nodes": [{"relative_path": "index.md", "node_id": "index", "entity_class": "GraphIndex", "content": "# Context Index\n"}],
        })
        assert resp_ctx.status_code == 200 and resp_ctx.json()["pushed_to_db"] == 1

        resp_cons = await client.post("/api/harness/consolidate", json={"dry_run": True})
        assert resp_cons.status_code == 200 and resp_cons.json()["status"] == "ok"
