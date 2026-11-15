"""Discovery checks against the cloned Mark-LVII tree.

The clone lives at ``<workspace>/mark_lvii/mark_lvii`` (one extra folder).
The bridge sample folder ``web-agent-bridge/mark_lvii`` is not this repo.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.bridge.executor import execute_tool
from app.bridge.tool_registry import ToolRegistry

# web-agent-bridge/tests/ -> workspace root -> mark_lvii/mark_lvii
CLONE = Path(__file__).resolve().parents[2] / "mark_lvii" / "mark_lvii"


pytestmark = pytest.mark.skipif(
    not (CLONE / "actions").is_dir(),
    reason="Mark-LVII clone is not present next to web-agent-bridge",
)


def test_registry_loads_without_errors() -> None:
    registry = ToolRegistry(CLONE)
    registry.load()
    tools = registry.list_tools()
    assert tools
    assert all(tool.get("name") and tool.get("source") for tool in tools)


def test_at_least_five_tools_discovered() -> None:
    registry = ToolRegistry(CLONE)
    registry.load()
    names = {tool["name"] for tool in registry.list_tools()}
    assert len(names) >= 5
    assert {"web_search", "weather_report", "desktop_control"} <= names


def test_simulated_dry_run_of_simple_tools() -> None:
    """Allowlisted tools are marked real; other desktop actions stay simulated."""
    from app.bridge.executor import allows_real_execution

    assert allows_real_execution("weather_report", {"city": "Mumbai"}) is True
    assert allows_real_execution("web_search", {"query": "python"}) is True
    assert allows_real_execution("desktop_control", {"action": "list"}) is True
    assert allows_real_execution("desktop_control", {"action": "clean"}) is False
    assert allows_real_execution("send_message", {"text": "hi"}) is False
    assert allows_real_execution(
        "browser_control",
        {"action": "new_tab", "url": "https://www.google.com/search?q=griezmann"},
    ) is True
    assert allows_real_execution(
        "browser_control", {"action": "search", "query": "griezmann"}
    ) is True
    assert allows_real_execution(
        "browser_control", {"action": "click", "text": "Sign in"}
    ) is False
    assert allows_real_execution(
        "browser_control", {"action": "new_tab", "url": "file:///C:/Windows"}
    ) is False

    registry = ToolRegistry(CLONE)
    registry.load()

    async def _run() -> None:
        blocked = await execute_tool(
            "desktop_control",
            {"action": "clean"},
            registry=registry,
        )
        assert blocked["success"] is True
        assert blocked["result"]["simulated"] is True

    asyncio.run(_run())
