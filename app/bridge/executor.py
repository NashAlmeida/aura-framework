"""Safely executes a Mark-LVII tool call.

Safe allowlist runs the real Mark-LVII handler:
  weather_report, web_search,
  desktop_control when action=list,
  browser_control when action is go_to, search, or new_tab
  and the target is an http(s) address or a search query,
  file_controller when action is list, disk_usage, or info
  and the path is a home shortcut (desktop, downloads, documents, …),
  file_controller create_file/write only for a new .py, .txt, or .md
  file inside the project cache/ folder.
Every other tool, including browser click/type/close, overwrites,
deletes, and writes outside cache/, stays simulated or blocked.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.api.websocket import manager

if TYPE_CHECKING:
    from app.bridge.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

MAX_ARGUMENTS_BYTES = 50_000
_UNSAFE_NAME_MARKERS = ("..", "/", "\\")

# Tools that may call Mark-LVII for real. desktop_control and browser_control
# are special-cased so only their read-or-open actions run.
REAL_TOOL_NAMES = frozenset({"weather_report", "web_search"})
BROWSER_OPEN_ACTIONS = frozenset({"go_to", "search", "new_tab"})
FILE_READ_ACTIONS = frozenset({"list", "disk_usage", "info"})
FILE_SHORTCUTS = frozenset(
    {"", "desktop", "downloads", "documents", "pictures", "music", "videos", "home"}
)
CACHE_DIR = Path(__file__).resolve().parents[2] / "cache"
CACHE_EXTENSIONS = frozenset({".py", ".txt", ".md"})
CACHE_WRITE_ACTIONS = frozenset({"create_file", "write"})
FILE_CHANGE_ACTIONS = CACHE_WRITE_ACTIONS | frozenset(
    {"create_folder", "delete", "move", "copy", "rename", "organize_desktop"}
)
MAX_CACHE_FILE_BYTES = 100_000
REAL_TIMEOUT_SECONDS = 25


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


def _http_url(value: Any) -> bool:
    url = str(value or "").strip().lower()
    return url.startswith("https://") or url.startswith("http://")


def _file_shortcut(value: Any) -> bool:
    """Allow only named home folders, not absolute paths or traversal."""
    path = str(value or "").strip().strip("\"'")
    if any(mark in path for mark in ("..", "/", "\\", ":")):
        return False
    return path.lower() in FILE_SHORTCUTS


def cache_create_path(arguments: dict[str, Any] | None) -> Path | None:
    """A new file inside cache/, or None when the write is not allowed.

    The name must be a single .py, .txt, or .md file. The path must be cache/
    itself. Existing files are refused so a create cannot overwrite.
    """
    arguments = arguments or {}
    action = str(arguments.get("action") or "").strip().lower()
    if action not in CACHE_WRITE_ACTIONS:
        return None
    name = str(arguments.get("name") or arguments.get("filename") or "").strip().strip("\"'")
    raw_path = str(arguments.get("path") or "cache").strip().strip("\"'")
    if not name or any(mark in name for mark in ("..", "/", "\\", ":")):
        return None
    if any(mark in raw_path for mark in ("..", ":")):
        return None
    suffix = Path(name).suffix.lower()
    if suffix not in CACHE_EXTENSIONS or not Path(name).stem:
        return None
    normalized = raw_path.replace("\\", "/").strip("/")
    if normalized not in {"", "cache", f"cache/{name}"}:
        return None
    root = CACHE_DIR.resolve()
    dest = (root / name).resolve()
    if root not in dest.parents:
        return None
    if dest.exists():
        return None
    return dest


def file_action_needs_confirmation(tool_name: str, arguments: dict[str, Any] | None) -> bool:
    """Writes outside cache/, overwrites, and deletes need a confirmation pause."""
    if tool_name != "file_controller":
        return False
    action = str((arguments or {}).get("action") or "").strip().lower()
    if action not in FILE_CHANGE_ACTIONS:
        return False
    return not allows_real_execution(tool_name, arguments)


def allows_real_execution(tool_name: str, arguments: dict[str, Any] | None) -> bool:
    """True only for the safe allowlist."""
    arguments = arguments or {}
    if tool_name in REAL_TOOL_NAMES:
        return True
    if tool_name == "desktop_control":
        return str(arguments.get("action") or "").strip().lower() == "list"
    if tool_name == "browser_control":
        action = str(arguments.get("action") or "").strip().lower()
        if action not in BROWSER_OPEN_ACTIONS:
            return False
        if action == "search":
            return bool(str(arguments.get("query") or "").strip())
        return _http_url(arguments.get("url"))
    if tool_name == "file_controller":
        action = str(arguments.get("action") or "").strip().lower()
        if action in CACHE_WRITE_ACTIONS:
            return cache_create_path(arguments) is not None
        if action not in FILE_READ_ACTIONS or not _file_shortcut(arguments.get("path")):
            return False
        if action == "info":
            name = str(arguments.get("name") or "").strip()
            if not name or any(mark in name for mark in ("/", "\\", ":", "..")):
                return False
        return True
    return False


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


def _load_handler(registry: "ToolRegistry", source: str):
    """Import one Mark-LVII file and return its TOOL handler."""
    path = (registry.mark_lvii_path / source).resolve()
    root = registry.mark_lvii_path.resolve()
    if not path.is_file() or root not in path.parents:
        raise FileNotFoundError(f"Mark-LVII source not found: {source}")

    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    module_name = "aura_lvii_" + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not import {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    tool = getattr(module, "TOOL", None)
    handler = tool.get("handler") if isinstance(tool, dict) else None
    if not callable(handler):
        handler = getattr(module, "run", None)
    if not callable(handler):
        raise RuntimeError(f"{source} has no callable TOOL handler")
    return handler


def _invoke_handler(handler, arguments: dict[str, Any]) -> str:
    """Call a Mark-LVII handler with only the arguments it declares."""
    sig = inspect.signature(handler)
    names = list(sig.parameters)
    if names and names[0] == "parameters":
        extra = {
            key: None
            for key in ("player", "speak", "response", "session_memory")
            if key in sig.parameters
        }
        result = handler(arguments, **extra)
    elif "parameters" in sig.parameters:
        result = handler(parameters=arguments)
    else:
        result = handler(arguments)
    text = result if isinstance(result, str) else str(result)
    if len(text) > 4000:
        text = text[:4000] + "…"
    return text


def _write_cache_file(arguments: dict[str, Any]) -> str:
    """Create one new text file inside cache/. Refuses anything else."""
    dest = cache_create_path(arguments)
    if dest is None:
        raise RuntimeError("refusing to write outside cache/ or to overwrite a file")
    content = arguments.get("content")
    text = "" if content is None else str(content)
    if len(text.encode("utf-8")) > MAX_CACHE_FILE_BYTES:
        raise RuntimeError("cache file content is too large")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    relative = f"cache/{dest.name}"
    logger.info("cache file created: %s", relative)
    return f"Created {relative}"


def _run_real(registry: "ToolRegistry", tool: dict[str, Any], arguments: dict[str, Any]) -> str:
    handler = _load_handler(registry, tool.get("source") or "")
    return _invoke_handler(handler, arguments)


async def execute_tool(
    tool_name: str,
    arguments: dict,
    *,
    registry: "ToolRegistry",
) -> dict:
    """Run an allowlisted tool for real, or simulate everything else."""
    arguments = arguments or {}

    _validate_tool_name(tool_name)
    _validate_arguments(arguments)
    tool_name = tool_name.strip()

    tool = registry.get_tool(tool_name)
    if tool is None:
        logger.info("execute_tool: tool=%s mode=missing success=%s", tool_name, False)
        raise ToolNotFoundError(tool_name)

    real = allows_real_execution(tool_name, arguments)
    mode = "real" if real else "simulated"
    await manager.broadcast(
        {
            "type": "tool_started",
            "tool_name": tool_name,
            "mode": mode,
            "timestamp": _utc_now_iso(),
        }
    )

    success = True
    try:
        if real:
            logger.info("execute_tool: tool=%s mode=real starting", tool_name)
            if tool_name == "browser_control":
                logger.info(
                    "browser tool running for real: action=%s url=%s query=%s browser=%s",
                    arguments.get("action"),
                    arguments.get("url") or "",
                    arguments.get("query") or "",
                    arguments.get("browser") or "default",
                )
            elif tool_name == "file_controller":
                logger.info(
                    "file tool running for real: action=%s path=%s",
                    arguments.get("action"),
                    arguments.get("path") or "default",
                )
            try:
                cache_write = (
                    tool_name == "file_controller"
                    and str(arguments.get("action") or "").strip().lower() in CACHE_WRITE_ACTIONS
                )
                if cache_write:
                    output = await asyncio.wait_for(
                        asyncio.to_thread(_write_cache_file, arguments),
                        timeout=REAL_TIMEOUT_SECONDS,
                    )
                else:
                    output = await asyncio.wait_for(
                        asyncio.to_thread(_run_real, registry, tool, arguments),
                        timeout=REAL_TIMEOUT_SECONDS,
                    )
            except Exception as exc:
                success = False
                logger.exception("execute_tool: tool=%s mode=real success=%s", tool_name, False)
                response = {
                    "success": False,
                    "tool_name": tool_name,
                    "result": {
                        "arguments": arguments,
                        "source": tool.get("source", ""),
                        "simulated": False,
                        "error": str(exc),
                    },
                    "message": f"real execution failed: {exc}",
                }
            else:
                logger.info("execute_tool: tool=%s mode=real success=%s", tool_name, True)
                response = {
                    "success": True,
                    "tool_name": tool_name,
                    "result": {
                        "arguments": arguments,
                        "source": tool.get("source", ""),
                        "simulated": False,
                        "output": output,
                    },
                    "message": "real execution",
                }
        else:
            reason = "simulated execution — not on the safe allowlist"
            if tool_name == "desktop_control":
                reason = "simulated execution — desktop_control is real only for action=list"
            elif tool_name == "browser_control":
                reason = (
                    "simulated execution — browser_control is real only for "
                    "go_to, search, and new_tab with an http(s) address"
                )
            elif tool_name == "file_controller":
                action = str(arguments.get("action") or "").strip().lower()
                if action in CACHE_WRITE_ACTIONS and cache_create_path(arguments) is None:
                    name = str(arguments.get("name") or "")
                    dest_name = Path(name).name
                    exists = bool(dest_name) and (CACHE_DIR / dest_name).is_file()
                    if exists and Path(dest_name).suffix.lower() in CACHE_EXTENSIONS:
                        reason = f"blocked — refusing to overwrite cache/{dest_name}"
                    else:
                        reason = (
                            "blocked — writes are only allowed for a new "
                            ".py, .txt, or .md file inside cache/"
                        )
                    success = False
                elif action in FILE_CHANGE_ACTIONS:
                    reason = "blocked — deletes and writes outside cache/ are not run"
                    success = False
                else:
                    reason = (
                        "simulated execution — file_controller is real only for "
                        "list, disk_usage, info, and new files inside cache/"
                    )
            blocked = success is False and reason.startswith("blocked")
            if blocked:
                mode = "blocked"
            logger.info(
                "execute_tool: tool=%s mode=%s success=%s",
                tool_name,
                "blocked" if blocked else "simulated",
                success,
            )
            response = {
                "success": success,
                "tool_name": tool_name,
                "result": {
                    "arguments": arguments,
                    "source": tool.get("source", ""),
                    "simulated": not blocked,
                    "blocked": blocked,
                },
                "message": reason,
            }
    except Exception:
        success = False
        raise
    finally:
        await manager.broadcast(
            {
                "type": "tool_finished",
                "tool_name": tool_name,
                "mode": mode,
                "success": success,
                "timestamp": _utc_now_iso(),
            }
        )

    return response
