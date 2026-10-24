"""Пакет локального MCP-шлюза (`FastMCP Streamable HTTP /mcp`, Layer 2).

Экспортирует прикладной сервис 8 инструментов `CoopMCPToolService` и транспортный адаптер
`CoopMCPGateway` с семантическими аннотациями `TOOL_ANNOTATIONS`.
"""

from __future__ import annotations

from src.core.mcp.server import (
    MCP_PROTOCOL_VERSION,
    MCP_SERVER_NAME,
    TOOL_ANNOTATIONS,
    TOOL_DESCRIPTIONS,
    TOOL_INPUT_SCHEMAS,
    CoopMCPGateway,
)
from src.core.mcp.tools import (
    PROVIDER_ACTION_LIST,
    PROVIDER_ACTION_UPDATE,
    UPSERT_HARNESS_COMPONENT_SQL,
    CoopMCPToolService,
)

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MCP_SERVER_NAME",
    "PROVIDER_ACTION_LIST",
    "PROVIDER_ACTION_UPDATE",
    "TOOL_ANNOTATIONS",
    "TOOL_DESCRIPTIONS",
    "TOOL_INPUT_SCHEMAS",
    "UPSERT_HARNESS_COMPONENT_SQL",
    "CoopMCPGateway",
    "CoopMCPToolService",
]
