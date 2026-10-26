"""Транспортный шлюз `MCPServer` (`Streamable HTTP /mcp`, Layer 2: MCP Server).

Обеспечивает:
- Декларацию 8 инструментов `MCPToolName` с семантическими аннотациями `ToolAnnotations`
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`), `title` и
  строгими JSON Schema (`Draft-07`, `additionalProperties: false`, `description`, `enum`, `required`)
  по спецификации MCP 2025-03-26+.
- Нативную интеграцию с официальным SDK `mcp>=2.0` (`mcp.server.mcpserver.MCPServer`)
  и отказоустойчивым обработчиком JSON-RPC 2.0 `Streamable HTTP`.
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
from src.core.enums import (
    ComponentType,
    ContextEntityClass,
    ContextEntityStatus,
    MCPToolName,
    ScopeType,
)
from src.core.harness.compiler import MCP_HTTP_PATH
from src.core.mcp.tools import (
    PROVIDER_ACTION_LIST,
    PROVIDER_ACTION_UPDATE,
    CoopMCPToolService,
)

logger = logging.getLogger("mcp_coop.core.mcp.server")

MCP_SERVER_NAME: str = "mcp-coop-agentic-factory"
MCP_PROTOCOL_VERSION: str = "2025-03-26"
JSONRPC_VERSION: str = "2.0"
JSON_SCHEMA_DRAFT_07_URI: str = "http://json-schema.org/draft-07/schema#"

JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600
JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_INVALID_PARAMS: int = -32602
JSONRPC_INTERNAL_ERROR: int = -32603

COMPONENT_TYPE_VALUES: list[str] = [item.value for item in ComponentType]
SCOPE_TYPE_VALUES: list[str] = [item.value for item in ScopeType]
CONTEXT_ENTITY_CLASS_VALUES: list[str] = [item.value for item in ContextEntityClass]
CONTEXT_ENTITY_STATUS_VALUES: list[str] = [item.value for item in ContextEntityStatus]
PROVIDER_ACTION_VALUES: list[str] = [PROVIDER_ACTION_LIST, PROVIDER_ACTION_UPDATE]

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
        "$schema": JSON_SCHEMA_DRAFT_07_URI,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query or error message to match against memories.",
            },
            "stack_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional technology stack tags to filter relevant memories (e.g., ['python', 'fastapi']).",
            },
            "limit": {
                "type": "integer",
                "default": 5,
                "description": "Maximum number of top-ranked memory records to return.",
            },
            "include_compactions": {
                "type": "boolean",
                "default": False,
                "description": "Whether to also search archived post-compaction session summaries.",
            },
            "workspace_path": {
                "type": "string",
                "default": "",
                "description": "Optional workspace directory path used when filtering session compactions.",
            },
        },
        "required": ["query"],
    },
    MCPToolName.LEARN_MEMORY: {
        "$schema": JSON_SCHEMA_DRAFT_07_URI,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {
                "type": "string",
                "description": "Concise title summarizing the resolved issue or architectural lesson.",
            },
            "error_symptom": {
                "type": "string",
                "description": "Exact error symptom, stack trace pattern, or failure condition.",
            },
            "solution": {
                "type": "string",
                "description": "Verified root cause explanation and step-by-step fix.",
            },
            "stack_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Technology stack tags associated with this memory (defaults to ['global']).",
            },
            "scope": {
                "type": "string",
                "enum": SCOPE_TYPE_VALUES,
                "default": ScopeType.GLOBAL.value,
                "description": "Visibility scope of the memory ('global' or 'domain').",
            },
            "source_project_path": {
                "type": "string",
                "description": "Optional absolute path of the workspace where the fix was discovered.",
            },
        },
        "required": ["title", "error_symptom", "solution"],
    },
    MCPToolName.PROPOSE_HARNESS_EDIT: {
        "$schema": JSON_SCHEMA_DRAFT_07_URI,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "component_type": {
                "type": "string",
                "enum": COMPONENT_TYPE_VALUES,
                "description": "Target Harness component category (system_prompt, guardrail, skill, tool_guidance, script, subagent, middleware).",
            },
            "slug": {
                "type": "string",
                "description": "Unique kebab-case identifier for the component.",
            },
            "title": {
                "type": "string",
                "description": "Human-readable title of the Harness component.",
            },
            "description": {
                "type": "string",
                "description": "Summary of when and why this component should be applied.",
            },
            "content": {
                "type": "string",
                "description": "Full Markdown body or executable script content of the component.",
            },
            "scope": {
                "type": "string",
                "enum": SCOPE_TYPE_VALUES,
                "default": ScopeType.GLOBAL.value,
                "description": "Applicability scope ('global' or 'domain').",
            },
            "stack_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Stack tags governing automatic activation of this component.",
            },
            "metadata": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
                "description": "Optional structured metadata (e.g., execution_mode, hook trigger, guardrail rules).",
            },
            "source_project_path": {
                "type": "string",
                "description": "Optional workspace path where the component was authored.",
            },
        },
        "required": ["component_type", "slug", "title", "description", "content"],
    },
    MCPToolName.PUBLISH_SKILL: {
        "$schema": JSON_SCHEMA_DRAFT_07_URI,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "slug": {
                "type": "string",
                "description": "Unique kebab-case skill identifier.",
            },
            "description": {
                "type": "string",
                "description": "Trigger description explaining when the agent should activate this skill.",
            },
            "instructions_md": {
                "type": "string",
                "description": "Full Markdown instructions (SKILL.md body) for executing the skill.",
            },
            "stack_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Technology stack tags associated with the skill.",
            },
            "helper_scripts": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional mapping of relative script filenames to script contents.",
            },
            "source_project_path": {
                "type": "string",
                "description": "Optional workspace path from which the skill is published.",
            },
        },
        "required": ["slug", "description", "instructions_md"],
    },
    MCPToolName.QUERY_CONTEXT_GRAPH: {
        "$schema": JSON_SCHEMA_DRAFT_07_URI,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_path": {
                "type": "string",
                "description": "Absolute path to the project workspace containing the .context/ directory.",
            },
            "node_id": {
                "type": "string",
                "default": "",
                "description": "Optional target node ID (e.g., 'mod-python-core') to center the subgraph query; empty returns the full graph.",
            },
            "depth": {
                "type": "integer",
                "default": 2,
                "description": "Neighborhood traversal depth around node_id.",
            },
        },
        "required": ["workspace_path"],
    },
    MCPToolName.UPSERT_CONTEXT_NODE: {
        "$schema": JSON_SCHEMA_DRAFT_07_URI,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_path": {
                "type": "string",
                "description": "Absolute path to the project workspace containing .context/.",
            },
            "entity_class": {
                "type": "string",
                "enum": CONTEXT_ENTITY_CLASS_VALUES,
                "description": "Ontology class of the context node ('Module', 'Contract', or 'Decision').",
            },
            "node_id": {
                "type": "string",
                "description": "Canonical node identifier (e.g., 'mod-python-core', 'contract-mcp-and-http-api').",
            },
            "name": {
                "type": "string",
                "description": "Human-readable display title of the context node.",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of relative repository file paths governed or implemented by this node.",
            },
            "body_md": {
                "type": "string",
                "description": "Markdown body describing the architecture, contracts, and data flow of the node.",
            },
            "trace_note": {
                "type": "string",
                "description": "Audit log entry appended to the 'AI Traces' section of the node.",
            },
            "stack": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of stack tags for this node.",
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of node IDs that this node depends on.",
            },
            "implements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of Contract node IDs implemented by this node.",
            },
            "governed_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of Decision (ADR) node IDs governing this node.",
            },
            "status": {
                "type": "string",
                "enum": CONTEXT_ENTITY_STATUS_VALUES,
                "default": ContextEntityStatus.ACTIVE.value,
                "description": "Lifecycle status of the node ('Active', 'Draft', or 'Deprecated').",
            },
            "conversation_id": {
                "type": "string",
                "default": "",
                "description": "Optional active session conversation ID used to mark context sync in Redis.",
            },
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
        "$schema": JSON_SCHEMA_DRAFT_07_URI,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_path": {
                "type": "string",
                "default": "",
                "description": "Optional workspace path to include local .context/ graph validation in the status report.",
            },
        },
    },
    MCPToolName.MANAGE_DECISION_PROVIDERS: {
        "$schema": JSON_SCHEMA_DRAFT_07_URI,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": PROVIDER_ACTION_VALUES,
                "default": PROVIDER_ACTION_LIST,
                "description": "Operation to perform on Tier 2 providers ('list' or 'update').",
            },
            "name": {
                "type": "string",
                "default": "",
                "description": "Provider name (required when action is 'update').",
            },
            "priority": {
                "type": "integer",
                "description": "Optional new routing priority (lower number = higher priority).",
            },
            "enabled": {
                "type": "boolean",
                "description": "Optional flag to enable or disable the provider at runtime.",
            },
            "reset_circuit": {
                "type": "boolean",
                "default": False,
                "description": "Whether to reset the provider's circuit breaker state.",
            },
        },
    },
}


class CoopMCPGateway:
    """Адаптер протокола `MCPServer` (`Streamable HTTP /mcp`) с регистрацией 8 аннотированных инструментов."""

    def __init__(self, app: FastAPI, service: CoopMCPToolService) -> None:
        self._app: FastAPI = app
        self._service: CoopMCPToolService = service
        self._fastmcp: Any = self._try_build_fastmcp()

    @property
    def has_native_fastmcp(self) -> bool:
        """Возвращает `True`, если в окружении инициализирован `MCPServer` (`mcp>=2.0`)."""
        return self._fastmcp is not None

    def _try_build_fastmcp(self) -> Any:
        """Создаёт `MCPServer` (`mcp>=2.0`), регистрирует 8 инструментов с `ToolAnnotations` и синхронизирует `TOOL_INPUT_SCHEMAS`."""
        try:
            mcpserver_mod = importlib.import_module("mcp.server.mcpserver")
            types_mod = importlib.import_module("mcp.types")
        except ImportError:
            return None

        mcpserver_cls = getattr(mcpserver_mod, "MCPServer", None)
        annotations_cls = getattr(types_mod, "ToolAnnotations", None)
        if mcpserver_cls is None:
            return None

        mcp_server = mcpserver_cls(
            name=MCP_SERVER_NAME,
            version=__version__,
        )

        method_map = self._tool_handler_map()
        for tool_enum, handler in method_map.items():
            raw_ann = TOOL_ANNOTATIONS[tool_enum]
            ann_obj: Any = (
                annotations_cls.model_validate(raw_ann)
                if annotations_cls is not None
                else raw_ann
            )
            mcp_server.add_tool(
                handler,
                name=tool_enum.value,
                title=str(raw_ann.get("title") or tool_enum.value),
                description=TOOL_DESCRIPTIONS[tool_enum],
                annotations=ann_obj,
            )

            # Принудительно синхронизируем каноническую JSON Schema Draft-07 без `anyOf: null`
            tool_manager = getattr(mcp_server, "_tool_manager", None)
            tools_dict = getattr(tool_manager, "_tools", None)
            if isinstance(tools_dict, dict) and tool_enum.value in tools_dict:
                tool_entry = tools_dict[tool_enum.value]
                if hasattr(tool_entry, "parameters"):
                    tool_entry.parameters = dict(TOOL_INPUT_SCHEMAS[tool_enum])

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
        """Формирует список спецификаций всех 8 инструментов в формате ответа `tools/list` (MCP 2025-03-26+)."""
        return [
            {
                "name": tool_enum.value,
                "title": str(TOOL_ANNOTATIONS[tool_enum].get("title") or tool_enum.value),
                "description": TOOL_DESCRIPTIONS[tool_enum],
                "inputSchema": dict(TOOL_INPUT_SCHEMAS[tool_enum]),
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
        clean_args = {
            k: v for k, v in (arguments or {}).items() if v is not None
        }
        return await handler(**clean_args)

    async def handle_jsonrpc(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Обрабатывает одиночное JSON-RPC 2.0 сообщение протокола MCP (`Streamable HTTP`)."""
        req_id = payload.get("id")
        method = str(payload.get("method", "")).strip()
        params = payload.get("params")
        params_dict: dict[str, Any] = dict(params) if isinstance(params, dict) else {}

        if method.startswith("notifications/") or req_id is None and not method:
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
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
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

        if method == "resources/list":
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "result": {"resources": []},
            }

        if method == "resources/templates/list":
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "result": {"resourceTemplates": []},
            }

        if method == "prompts/list":
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "result": {"prompts": []},
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
        """Управляет жизненным циклом MCP-шлюза внутри `lifespan` приложения FastAPI."""
        yield

    def mount(self, app: FastAPI) -> None:
        """Регистрирует эндпоинт `/mcp` (`Streamable HTTP` POST/GET/DELETE) в маршрутизаторе FastAPI."""

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

            if isinstance(body, list):
                if not body:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "jsonrpc": JSONRPC_VERSION,
                            "id": None,
                            "error": {
                                "code": JSONRPC_INVALID_REQUEST,
                                "message": "Empty JSON-RPC batch",
                            },
                        },
                    )
                replies: list[dict[str, Any]] = []
                for item in body:
                    if isinstance(item, dict):
                        item_reply = await self.handle_jsonrpc(item)
                        if item_reply is not None:
                            replies.append(item_reply)
                if not replies:
                    return Response(status_code=202)
                return JSONResponse(content=replies)

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
    "JSON_SCHEMA_DRAFT_07_URI",
    "MCP_PROTOCOL_VERSION",
    "MCP_SERVER_NAME",
    "TOOL_ANNOTATIONS",
    "TOOL_DESCRIPTIONS",
    "TOOL_INPUT_SCHEMAS",
    "CoopMCPGateway",
]
