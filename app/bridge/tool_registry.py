"""Discovers and registers Mark-LVII tools/actions.

Scans `mark_lvii/actions/` and `mark_lvii/plugins/` once at startup,
extracts module-level TOOL / PLUGIN dicts (AST, no import), and caches
the results for GET /tools.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Mark-LVII actions use TOOL; plugins use PLUGIN. Both share name/description/parameters.
_META_NAMES = ("TOOL", "PLUGIN")
_SCAN_DIRS = ("actions", "plugins")


class ToolRegistry:
    """In-memory registry of available Mark-LVII tools."""

    def __init__(self, mark_lvii_path: Path | None = None) -> None:
        self.mark_lvii_path = Path(mark_lvii_path or settings.mark_lvii_path)
        self._tools: list[dict[str, Any]] = []
        self._loaded = False

    def load(self) -> None:
        """Scan Mark-LVII once and cache discovered tools."""
        if self._loaded:
            return

        root = self.mark_lvii_path.resolve()
        if not root.is_dir():
            logger.warning(
                "ToolRegistry: mark_lvii path does not exist (%s) — returning empty tool list",
                root,
            )
            self._tools = []
            self._loaded = True
            return

        tools: list[dict[str, Any]] = []
        for folder in _SCAN_DIRS:
            directory = root / folder
            if not directory.is_dir():
                logger.info("ToolRegistry: %s/ not found — skipping", folder)
                continue
            tools.extend(self._scan_directory(directory, folder))

        if not tools:
            logger.warning(
                "ToolRegistry: no tools found under %s "
                "(mark_lvii may be empty — place the Mark-LVII fork there)",
                root,
            )

        self._tools = tools
        self._loaded = True
        logger.info("ToolRegistry: loaded %d tool(s)", len(self._tools))

    def list_tools(self) -> list[dict[str, Any]]:
        """Return cached tool descriptors (scan happens only in load())."""
        if not self._loaded:
            self.load()
        return list(self._tools)

    def get_tool(self, name: str) -> dict[str, Any] | None:
        for tool in self._tools:
            if tool.get("name") == name:
                return tool
        return None

    def _scan_directory(self, directory: Path, folder: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.py")):
            if path.name == "__init__.py" or path.name.startswith("_"):
                continue
            tool = self._extract_tool(path, folder)
            found.append(tool)
            logger.debug("ToolRegistry: discovered %s from %s", tool["name"], tool["source"])
        return found

    def _extract_tool(self, path: Path, folder: str) -> dict[str, Any]:
        """Build a tool entry from a .py file; fall back to filename if no TOOL/PLUGIN."""
        source = f"{folder}/{path.name}"
        fallback = {
            "name": path.stem,
            "description": f"Discovered from {source} (no TOOL/PLUGIN metadata).",
            "source": source,
            "parameters": {},
        }

        meta = self._parse_meta_dict(path)
        if meta is None:
            return fallback

        name = meta.get("name")
        if not isinstance(name, str) or not name.strip():
            name = path.stem

        description = meta.get("description")
        if not isinstance(description, str) or not description.strip():
            description = f"Tool '{name}' from {source}"

        parameters = meta.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}

        return {
            "name": name.strip(),
            "description": description.strip(),
            "source": source,
            "parameters": parameters,
        }

    def _parse_meta_dict(self, path: Path) -> dict[str, Any] | None:
        """AST-parse a module for TOOL or PLUGIN assignment; never imports the file."""
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as exc:
            logger.warning("ToolRegistry: could not parse %s: %s", path.name, exc)
            return None

        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _META_NAMES:
                    value = self._safe_literal(node.value)
                    if isinstance(value, dict):
                        return value
        return None

    @staticmethod
    def _safe_literal(node: ast.AST) -> Any:
        """literal_eval a dict while skipping non-literal values (e.g. handler callables)."""
        if isinstance(node, ast.Dict):
            out: dict[str, Any] = {}
            for key_node, value_node in zip(node.keys, node.values):
                if key_node is None:
                    continue
                try:
                    key = ast.literal_eval(key_node)
                except (ValueError, TypeError):
                    continue
                if not isinstance(key, str):
                    continue
                # Skip callables / names like handler=web_search — not JSON-serializable metadata.
                if isinstance(value_node, (ast.Name, ast.Attribute, ast.Call, ast.Lambda)):
                    continue
                try:
                    out[key] = ast.literal_eval(value_node)
                except (ValueError, TypeError):
                    continue
            return out
        try:
            return ast.literal_eval(node)
        except (ValueError, TypeError):
            return None
