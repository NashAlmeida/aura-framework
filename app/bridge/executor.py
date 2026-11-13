"""Safely executes a Mark-LVII tool call.

Phase 1: look up the tool in the registry and *simulate* execution.
Real Mark-LVII imports/runs come later (deps may be missing).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from app.api.websocket import manager

if TYPE_CHECKING:
    from app.bridge.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# Reject argument payloads larger than this (serialized JSON size in bytes).
MAX_ARGUMENTS_BYTES = 50_000

# Characters / sequences that must never appear in a tool name (path traversal).
_UNSAFE_NAME_MARKERS = ("..", "/", "\\")


class ExecutorValidationError(Exception):
    """Raised when a tool call fails basic safety checks (bad name, huge args)."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ToolNotFoundError(Exception):
    """Raised when the requested tool is not in the registry."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.message = f"Tool not found: {tool_name}"
        super().__init__(self.message)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_tool_name(tool_name: str) -> None:
    """Reject empty names and anything that looks like a path."""
    if not tool_name or not tool_name.strip():
        raise ExecutorValidationError("tool_name must be a non-empty string")

    name = tool_name.strip()
    for marker in _UNSAFE_NAME_MARKERS:
        if marker in name:
            raise ExecutorValidationError(
                f"tool_name contains forbidden path characters ({marker!r})"
            )


def _validate_arguments(arguments: dict[str, Any]) -> None:
    """Reject oversized argument payloads."""
    if not isinstance(arguments, dict):
        raise ExecutorValidationError("arguments must be a JSON object")

    try:
        encoded = json.dumps(arguments, default=str)
    except (TypeError, ValueError) as exc:
        raise ExecutorValidationError(f"arguments are not JSON-serializable: {exc}") from exc

    if len(encoded.encode("utf-8")) > MAX_ARGUMENTS_BYTES:
        raise ExecutorValidationError(
            f"arguments exceed size limit of {MAX_ARGUMENTS_BYTES} bytes"
        )


async def execute_tool(
    tool_name: str,
    arguments: dict,
    *,
    registry: "ToolRegistry",
) -> dict:
    """Look up a tool and return a simulated execution result.

    Does **not** import or run Mark-LVII code yet.
    Broadcasts `tool_started` / `tool_finished` over the WebSocket manager.

    Returns:
        {
          "success": true,
          "tool_name": "...",
          "result": { "arguments": {...}, "source": "..." },
          "message": "simulated execution — Mark-LVII integration pending"
        }

    Raises:
        ExecutorValidationError: unsafe tool_name or oversized arguments
        ToolNotFoundError: tool is not registered
    """
    arguments = arguments or {}

    # --- safety gates (always run before registry lookup) ---
    _validate_tool_name(tool_name)
    _validate_arguments(arguments)
    tool_name = tool_name.strip()

    tool = registry.get_tool(tool_name)
    if tool is None:
        logger.info("execute_tool: tool=%s success=%s", tool_name, False)
        raise ToolNotFoundError(tool_name)

    # Notify live clients that work is starting.
    await manager.broadcast(
        {
            "type": "tool_started",
            "tool_name": tool_name,
            "timestamp": _utc_now_iso(),
        }
    )

    success = True
    try:
        # Simulated result — real dispatch into Mark-LVII comes in a later phase.
        result = {
            "arguments": arguments,
            "source": tool.get("source", ""),
            "simulated": True,
        }
        response = {
            "success": True,
            "tool_name": tool_name,
            "result": result,
            "message": "simulated execution — Mark-LVII integration pending",
        }
    except Exception:
        success = False
        raise
    finally:
        await manager.broadcast(
            {
                "type": "tool_finished",
                "tool_name": tool_name,
                "success": success,
                "timestamp": _utc_now_iso(),
            }
        )

    logger.info("execute_tool: tool=%s success=%s", tool_name, True)
    return response
