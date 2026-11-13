"""LangChain tool wrappers that call the Phase 1 bridge executor."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from app.bridge.executor import (
    ExecutorValidationError,
    ToolNotFoundError,
    execute_tool,
)
from app.bridge.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# Explicit risky tools (modify files, send messages, or control the system).
RISKY_TOOLS: set[str] = {
    "delete_file",
    "write_file",
    "send_message",
    "send_email",
    "whatsapp_call",
    "shutdown",
    "shutdown_jarvis",
    "run_command",
    "execute_command",
    "kill_process",
    "format_disk",
    "uninstall_app",
}

# Name substrings that also mark a tool as risky when not in the set above.
_RISKY_NAME_KEYWORDS = (
    "delete",
    "remove",
    "unlink",
    "send_",
    "write_",
    "shutdown",
    "kill_",
    "format_",
    "uninstall",
    "overwrite",
)


def is_risky_tool(tool_name: str) -> bool:
    """Return True if the tool should require human confirmation before running."""
    name = (tool_name or "").strip()
    if not name:
        return False
    if name in RISKY_TOOLS:
        return True
    lower = name.lower()
    return any(keyword in lower for keyword in _RISKY_NAME_KEYWORDS)


def _safe_model_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"Tool_{cleaned}"
    return f"{cleaned}_Args"


def _args_schema_for(tool_name: str, parameters: dict[str, Any]) -> type[BaseModel]:
    """Build a lightweight Pydantic args model from Mark-LVII-style parameters."""
    props = (parameters or {}).get("properties") or {}
    fields: dict[str, Any] = {}
    for key, schema in props.items():
        if not isinstance(key, str):
            continue
        desc = ""
        if isinstance(schema, dict):
            desc = str(schema.get("description") or "")
        fields[key] = (str, Field(default="", description=desc))

    if not fields:
        fields["note"] = (
            str,
            Field(default="", description="Optional note (unused by most tools)"),
        )

    return create_model(_safe_model_name(tool_name), **fields)  # type: ignore[call-overload]


def _make_bridge_tool(spec: dict[str, Any], registry: ToolRegistry) -> StructuredTool:
    """Wrap one registry entry so LangGraph can call execute_tool."""
    tool_name = spec["name"]
    risky = is_risky_tool(tool_name)
    base_description = spec.get("description") or f"Bridge tool '{tool_name}'"
    description = base_description
    if risky:
        description = f"[RISKY — needs confirmation] {base_description}"

    parameters = spec.get("parameters") or {}
    args_schema = _args_schema_for(tool_name, parameters)

    async def _run(**kwargs: Any) -> str:
        arguments = {k: v for k, v in kwargs.items() if v not in ("", None)}
        try:
            outcome = await execute_tool(tool_name, arguments, registry=registry)
        except (ExecutorValidationError, ToolNotFoundError) as exc:
            logger.warning("Bridge tool %s failed: %s", tool_name, exc)
            return json.dumps({"success": False, "tool_name": tool_name, "error": str(exc)})
        return json.dumps(outcome)

    tool = StructuredTool.from_function(
        name=tool_name,
        description=description,
        coroutine=_run,
        args_schema=args_schema,
    )
    # Extra flag for callers that inspect metadata.
    tool.metadata = {**(tool.metadata or {}), "risky": risky}
    return tool


def build_langchain_tools(registry: ToolRegistry) -> list[StructuredTool]:
    """Create one LangChain tool per discovered Mark-LVII / bridge tool."""
    tools: list[StructuredTool] = []
    for spec in registry.list_tools():
        name = spec.get("name")
        if not name:
            continue
        tool = _make_bridge_tool(spec, registry)
        tools.append(tool)
        logger.info(
            "Agent tool registered: %s (risky=%s)",
            name,
            is_risky_tool(name),
        )
    return tools
