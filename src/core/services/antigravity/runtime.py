"""Рантайм-адаптер к пакету `google.antigravity` с ленивой загрузкой через `importlib`.

Отвечает за Layer 2 (Core Services): инкапсулирует создание `LocalAgentConfig`,
`CapabilitiesConfig` и декларативных политик `google.antigravity.hooks.policy`,
исключая статическую зависимость от бинарного колеса на этапе импорта.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
from typing import cast

from pydantic import BaseModel

from src.core.config import CoreSettings
from src.core.services.antigravity.models import (
    AntigravityAgentSessionProtocol,
    SandboxExecutionSpec,
)

logger = logging.getLogger("mcp_coop.core.services.antigravity.runtime")

_STREAM_INSPECTION_ERRORS = (
    OSError,
    RuntimeError,
    TimeoutError,
    ValueError,
    TypeError,
    AttributeError,
)


class DefaultAntigravityRuntime:
    """Рантайм-адаптер к пакету `google.antigravity` с ленивой загрузкой через `importlib`.

    Исключает статическую зависимость от бинарного колеса на этапе импорта модуля и
    обеспечивает прохождение `pyright` с нулём ошибок в базовом окружении без SDK.
    """

    def is_sdk_installed(self) -> bool:
        """Проверяет доступность модуля `google.antigravity` без его принудительной инициализации."""
        try:
            importlib.import_module("google.antigravity")
            return True
        except ImportError:
            return False

    async def execute(
        self,
        spec: SandboxExecutionSpec,
        settings: CoreSettings,
        response_schema: type[BaseModel] | None = None,
    ) -> tuple[str, list[str]]:
        """Спавнит изолированный `Agent(config)` в неинтерактивном окружении и возвращает ответ."""
        ag_mod = importlib.import_module("google.antigravity")
        ag_types = importlib.import_module("google.antigravity.types")
        ag_policy = importlib.import_module("google.antigravity.hooks.policy")

        agent_cls = ag_mod.Agent
        config_cls = ag_mod.LocalAgentConfig
        capabilities_cls = ag_mod.CapabilitiesConfig
        builtin_tools = ag_types.BuiltinTools
        allow_fn = ag_policy.allow
        deny_fn = ag_policy.deny

        if spec.read_only:
            enabled_tools = builtin_tools.read_only()
            policies = [
                allow_fn("view_file"),
                allow_fn("list_dir"),
                deny_fn("edit_file"),
                deny_fn("create_file"),
                deny_fn("run_command"),
            ]
        else:
            enabled_tools = [
                getattr(builtin_tools, "VIEW_FILE", "view_file"),
                getattr(builtin_tools, "LIST_DIR", "list_dir"),
                getattr(builtin_tools, "EDIT_FILE", "edit_file"),
                getattr(builtin_tools, "CREATE_FILE", "create_file"),
            ]
            policies = [
                allow_fn("view_file"),
                allow_fn("list_dir"),
                deny_fn("run_command"),
            ]

        capabilities = capabilities_cls(enabled_tools=enabled_tools)
        api_key = settings.antigravity_api_key.strip() if settings.antigravity_api_key else None
        project = settings.antigravity_project.strip() if settings.antigravity_project else None

        config_kwargs: dict[str, object] = {
            "model": settings.antigravity_model,
            "api_key": api_key,
            "vertex": settings.antigravity_vertex,
            "project": project,
            "location": settings.antigravity_location,
            "system_instructions": spec.system_instructions,
            "workspaces": [spec.workspace_path],
            "capabilities": capabilities,
            "policies": policies,
        }
        if spec.skills_paths:
            config_kwargs["skills_paths"] = spec.skills_paths
        if response_schema is not None:
            config_kwargs["response_schema"] = response_schema

        local_config = config_cls(**config_kwargs)

        os.environ.setdefault("CI", "true")
        os.environ.setdefault("TERM", "dumb")

        tool_calls_seen: list[str] = []
        timeout_s = max(spec.timeout_ms / 1000.0, 1.0)
        async with asyncio.timeout(timeout_s):
            session_cm = cast("AntigravityAgentSessionProtocol", agent_cls(local_config))
            async with session_cm as agent:
                response = await agent.chat(spec.prompt)
                try:
                    async for call in response.tool_calls:
                        call_name = str(call.name).strip()
                        if call_name:
                            tool_calls_seen.append(call_name)
                except _STREAM_INSPECTION_ERRORS as exc:
                    logger.debug("Tool call stream inspection skipped: %s", exc)

                raw_text = await response.text()
                return str(raw_text), tool_calls_seen


__all__ = ["DefaultAntigravityRuntime"]
