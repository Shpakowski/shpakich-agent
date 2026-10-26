"""Транспортный шлюз `FastMCP` (`Streamable HTTP /mcp`, Layer 2: MCP Server).

Обеспечивает:
- Декларацию 8 инструментов `MCPToolName` с семантическими аннотациями `ToolAnnotations`
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) по спецификации MCP 2025-03-26+.
- Бесшовную работу через официальный SDK `FastMCP` (`stateless_http=True, json_response=True`) внутри
  Python 3.12 контейнера и встроенный совместимый обработчик JSON-RPC 2.0 `Streamable HTTP` для
  хостового окружения и легковесных интеграционных тестов.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from src.core import __version__
from src.core.enums import MCPToolName, ScopeType
from src.core.harness.compiler import MCP_HTTP_PATH
from src.core.mcp.tools import PROVIDER_ACTION_LIST, CoopMCPToolService

logger = logging.getLogger("mcp_coop.core.mcp.server")

MCP_SERVER_NAME: str = "mcp-coop-agentic-factory"
MCP_PROTOCOL_VERSION: str = "2025-03-26"
JSONRPC_VERSION: str = "2.0"

JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600
JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_INVALID_PARAMS: int = -32602
JSONRPC_INTERNAL_ERROR: int = -32603

TOOL_ANNOTATIONS: dict[MCPToolName, dict[str, Any]] = {
    MCPToolName.SEARCH_MEMORY: {
        "title": "Search Factory Hybrid Memory",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    MCPToolName.LEARN_MEMORY: {
        "title": "Record Error Solution to Long-Term Memory",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    MCPToolName.PROPOSE_HARNESS_EDIT: {
        "title": "Propose & Probe Harness Rule/Guardrail",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    MCPToolName.PUBLISH_SKILL: {
        "title": "Publish Reusable Agent Skill (SKILL.md)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    MCPToolName.QUERY_CONTEXT_GRAPH: {
        "title": "Query Local .context/ Knowledge Subgraph",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    MCPToolName.UPSERT_CONTEXT_NODE: {
        "title": "Upsert & Validate .context/ Graph Node",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    MCPToolName.GET_FACTORY_STATUS: {
        "title": "Get Factory Core & Context Graph Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    MCPToolName.MANAGE_DECISION_PROVIDERS: {
        "title": "Manage Tier 2 Decision API Providers",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

TOOL_DESCRIPTIONS: dict[MCPToolName, str] = {
    MCPToolName.SEARCH_MEMORY: (
        "Search long-term error recovery memories and optional post-compaction session summaries using 3-way hybrid ranking (pgvector + tsvector + pg_trgm)."
    ),
    MCPToolName.LEARN_MEMORY: (
        "Store a verified error symptom and solution into long-term PostgreSQL memory with EmbeddingGemma 2 vectorization."
    ),
    MCPToolName.PROPOSE_HARNESS_EDIT: (
        "Propose and evaluate a Harness component (system_prompt, guardrail, tool_guidance, subagent, skill) via Propose -> Probe -> Compose."
    ),
    MCPToolName.PUBLISH_SKILL: (
        "Publish a reusable modular skill (SKILL.md + optional helper scripts) into the Harness registry."
    ),
    MCPToolName.QUERY_CONTEXT_GRAPH: (
        "Query the local .context/ NetworkX directed graph around a target node_id up to radius depth."
    ),
    MCPToolName.UPSERT_CONTEXT_NODE: (
        "Create or update a .context/entities/ Markdown node with No Phantom Nodes validation and AI Traces logging."
    ),
    MCPToolName.GET_FACTORY_STATUS: (
        "Inspect health and metrics of PostgreSQL, Redis, EmbeddingClient, Tier 2 Decision providers, and .context/ graph."
    ),
    MCPToolName.MANAGE_DECISION_PROVIDERS: (
        "List or update Tier 2 Decision API provider priority, enabled state, or circuit breaker without exposing API keys."
    ),
}

TOOL_INPUT_SCHEMAS: dict[MCPToolName, dict[str, Any]] = {
    MCPToolName.SEARCH_MEMORY: {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "stack_tags": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "default": 5},
            "include_compactions": {"type": "boolean", "default": False},
            "workspace_path": {"type": "string", "default": ""},
        },
        "required": ["query"],
    },
    MCPToolName.LEARN_MEMORY: {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "error_symptom": {"type": "string"},
            "solution": {"type": "string"},
            "stack_tags": {"type": "array", "items": {"type": "string"}},
            "scope": {"type": "string", "default": ScopeType.GLOBAL.value},
            "source_project_path": {"type": "string"},
        },
        "required": ["title", "error_symptom", "solution"],
    },
    MCPToolName.PROPOSE_HARNESS_EDIT: {
        "type": "object",
        "properties": {
            "component_type": {"type": "string"},
            "slug": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "content": {"type": "string"},
            "scope": {"type": "string", "default": ScopeType.GLOBAL.value},
            "stack_tags": {"type": "array", "items": {"type": "string"}},
            "metadata": {"type": "object"},
            "source_project_path": {"type": "string"},
        },
        "required": ["component_type", "slug", "title", "description", "content"],
    },
    MCPToolName.PUBLISH_SKILL: {
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "description": {"type": "string"},
            "instructions_md": {"type": "string"},
            "stack_tags": {"type": "array", "items": {"type": "string"}},
            "helper_scripts": {"type": "object"},
            "source_project_path": {"type": "string"},
        },
        "required": ["slug", "description", "instructions_md"],
    },
    MCPToolName.QUERY_CONTEXT_GRAPH: {
        "type": "object",
        "properties": {
            "workspace_path": {"type": "string"},
            "node_id": {"type": "string", "default": ""},
            "depth": {"type": "integer", "default": 2},
        },
        "required": ["workspace_path"],
    },
    MCPToolName.UPSERT_CONTEXT_NODE: {
        "type": "object",
        "properties": {
            "workspace_path": {"type": "string"},
            "entity_class": {"type": "string"},
            "node_id": {"type": "string"},
            "name": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}},
            "body_md": {"type": "string"},
            "trace_note": {"type": "string"},
            "stack": {"type": "array", "items": {"type": "string"}},
            "depends_on": {"type": "array", "items": {"type": "string"}},
            "implements": {"type": "array", "items": {"type": "string"}},
            "governed_by": {"type": "array", "items": {"type": "string"}},
            "status": {"type": "string", "default": "Active"},
            "conversation_id": {"type": "string", "default": ""},
        },
        "required": [
            "workspace_path",
            "entity_class",
            "node_id",
            "name",
            "files",
            "body_md",
            "trace_note",
        ],
    },
    MCPToolName.GET_FACTORY_STATUS: {
        "type": "object",
        "properties": {
            "workspace_path": {"type": "string", "default": ""},
        },
    },
    MCPToolName.MANAGE_DECISION_PROVIDERS: {
        "type": "object",
        "properties": {
            "action": {"type": "string", "default": PROVIDER_ACTION_LIST},
            "name": {"type": "string", "default": ""},
            "priority": {"type": "integer"},
            "enabled": {"type": "boolean"},
            "reset_circuit": {"type": "boolean", "default": False},
        },
    },
}


class CoopMCPGateway:
    """Адаптер протокола `FastMCP` (`Streamable HTTP /mcp`) с регистрацией 8 аннотированных инструментов."""

    def __init__(self, app: FastAPI, service: CoopMCPToolService) -> None:
        self._app: FastAPI = app
        self._service: CoopMCPToolService = service
        self._fastmcp: Any = self._try_build_fastmcp()

    @property
    def has_native_fastmcp(self) -> bool:
        """Возвращает `True`, если в окружении установлен пакет `mcp.server.fastmcp` (`mcp>=1.9.0`)."""
        return self._fastmcp is not None

    def _try_build_fastmcp(self) -> Any:
        """Динамически создаёт `FastMCP` и регистрирует 8 инструментов с `ToolAnnotations` при наличии SDK."""
        try:
            fastmcp_mod = importlib.import_module("mcp.server.fastmcp")
            types_mod = importlib.import_module("mcp.types")
        except ImportError:
            return None

        fastmcp_cls = getattr(fastmcp_mod, "FastMCP", None)
        annotations_cls = getattr(types_mod, "ToolAnnotations", None)
        if fastmcp_cls is None:
            return None

        mcp_server = fastmcp_cls(
            name=MCP_SERVER_NAME,
            stateless_http=True,
            json_response=True,
            streamable_http_path=MCP_HTTP_PATH,
        )

        method_map = self._tool_handler_map()
        for tool_enum, handler in method_map.items():
            raw_ann = TOOL_ANNOTATIONS[tool_enum]
            ann_obj = (
                annotations_cls(**raw_ann) if annotations_cls is not None else raw_ann
            )
            decorator = mcp_server.tool(
                name=tool_enum.value,
                description=TOOL_DESCRIPTIONS[tool_enum],
                annotations=ann_obj,
            )
            decorator(handler)

        return mcp_server

    def _tool_handler_map(self) -> dict[MCPToolName, Any]:
        """Возвращает отображение канонических имён `MCPToolName` на асинхронные методы `CoopMCPToolService`."""
        return {
            MCPToolName.SEARCH_MEMORY: self._service.search_memory,
            MCPToolName.LEARN_MEMORY: self._service.learn_memory,
            MCPToolName.PROPOSE_HARNESS_EDIT: self._service.propose_harness_edit,
            MCPToolName.PUBLISH_SKILL: self._service.publish_skill,
            MCPToolName.QUERY_CONTEXT_GRAPH: self._service.query_context_graph,
            MCPToolName.UPSERT_CONTEXT_NODE: self._service.upsert_context_node,
            MCPToolName.GET_FACTORY_STATUS: self._service.get_factory_status,
            MCPToolName.MANAGE_DECISION_PROVIDERS: self._service.manage_decision_providers,
        }

    def list_tools_payload(self) -> list[dict[str, Any]]:
        """Формирует список спецификаций всех 8 инструментов в формате ответа `tools/list`."""
        return [
            {
                "name": tool_enum.value,
                "description": TOOL_DESCRIPTIONS[tool_enum],
                "inputSchema": TOOL_INPUT_SCHEMAS[tool_enum],
                "annotations": dict(TOOL_ANNOTATIONS[tool_enum]),
            }
            for tool_enum in MCPToolName
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Вызывает инструмент `MCPToolName` по имени и возвращает его структурированный словарь."""
        try:
            tool_enum = MCPToolName(name)
        except ValueError as exc:
            raise KeyError(f"Unknown MCP tool '{name}'") from exc

        handler = self._tool_handler_map()[tool_enum]
        return await handler(**(arguments or {}))

    async def handle_jsonrpc(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Обрабатывает одиночное JSON-RPC 2.0 сообщение протокола MCP (`Streamable HTTP`)."""
        req_id = payload.get("id")
        method = str(payload.get("method", "")).strip()
        params = payload.get("params")
        params_dict: dict[str, Any] = dict(params) if isinstance(params, dict) else {}

        if method.startswith("notifications/"):
            return None

        if method == "initialize":
            protocol_ver = str(
                params_dict.get("protocolVersion") or MCP_PROTOCOL_VERSION
            )
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "result": {
                    "protocolVersion": protocol_ver,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": MCP_SERVER_NAME,
                        "version": __version__,
                    },
                },
            }

        if method == "ping":
            return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": {}}

        if method == "tools/list":
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "result": {"tools": self.list_tools_payload()},
            }

        if method == "tools/call":
            tool_name = str(params_dict.get("name", "")).strip()
            raw_args = params_dict.get("arguments")
            tool_args = dict(raw_args) if isinstance(raw_args, dict) else {}
            try:
                result_data = await self.call_tool(tool_name, tool_args)
                serialized = json.dumps(result_data, ensure_ascii=False)
                return {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": serialized}],
                        "structuredContent": result_data,
                        "isError": False,
                    },
                }
            except KeyError as exc:
                return {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": req_id,
                    "error": {
                        "code": JSONRPC_INVALID_PARAMS,
                        "message": str(exc),
                    },
                }
            except Exception as exc:  # noqa: BLE001
                error_payload = {"error": str(exc), "tool": tool_name}
                return {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(error_payload, ensure_ascii=False),
                            }
                        ],
                        "structuredContent": error_payload,
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "error": {
                "code": JSONRPC_METHOD_NOT_FOUND,
                "message": f"Method '{method}' not found",
            },
        }

    @asynccontextmanager
    async def run_lifespan(self) -> AsyncIterator[None]:
        """Запускает `session_manager.run()` нативного `FastMCP` внутри `lifespan` приложения FastAPI."""
        if self._fastmcp is not None and hasattr(self._fastmcp, "session_manager"):
            async with self._fastmcp.session_manager.run():
                yield
            return
        yield

    def mount(self, app: FastAPI) -> None:
        """Регистрирует эндпоинт `/mcp` (`Streamable HTTP`) в маршрутизаторе FastAPI."""
        if self._fastmcp is not None and hasattr(self._fastmcp, "streamable_http_app"):
            mcp_asgi = self._fastmcp.streamable_http_app()
            for route in getattr(mcp_asgi, "routes", ()):
                app.router.routes.append(route)
            return

        @app.post(MCP_HTTP_PATH, tags=["mcp"])
        async def mcp_post_endpoint(request: Request) -> Response:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                return JSONResponse(
                    status_code=400,
                    content={
                        "jsonrpc": JSONRPC_VERSION,
                        "id": None,
                        "error": {
                            "code": JSONRPC_PARSE_ERROR,
                            "message": "Invalid JSON payload",
                        },
                    },
                )

            if not isinstance(body, dict):
                return JSONResponse(
                    status_code=400,
                    content={
                        "jsonrpc": JSONRPC_VERSION,
                        "id": None,
                        "error": {
                            "code": JSONRPC_INVALID_REQUEST,
                            "message": "Expected JSON-RPC object",
                        },
                    },
                )

            reply = await self.handle_jsonrpc(body)
            if reply is None:
                return Response(status_code=202)
            return JSONResponse(content=reply)

        @app.get(MCP_HTTP_PATH, tags=["mcp"])
        async def mcp_get_endpoint() -> JSONResponse:
            return JSONResponse(
                content={
                    "server": MCP_SERVER_NAME,
                    "version": __version__,
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "transport": "streamable-http",
                    "tools": [t.value for t in MCPToolName],
                }
            )

        @app.delete(MCP_HTTP_PATH, tags=["mcp"])
        async def mcp_delete_endpoint() -> Response:
            return Response(status_code=204)


__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MCP_SERVER_NAME",
    "TOOL_ANNOTATIONS",
    "TOOL_DESCRIPTIONS",
    "TOOL_INPUT_SCHEMAS",
    "CoopMCPGateway",
]
