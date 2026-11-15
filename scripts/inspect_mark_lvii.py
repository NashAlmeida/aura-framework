"""Inspect the cloned Mark-LVII tree and probe imports. Prints a text report."""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

from app.bridge.tool_registry import ToolRegistry

ROOT = Path(r"D:\final year proejct\mark_lvii\mark_lvii")


def main() -> int:
    reg = ToolRegistry(ROOT)
    reg.load()
    tools = reg.list_tools()
    print(f"COUNT {len(tools)}")
    for tool in tools:
        desc = " ".join((tool.get("description") or "").split())
        placeholder = desc.startswith("Discovered from")
        print(f"TOOL {tool['name']} | {tool['source']} | placeholder={placeholder}")
        print(f"DESC {desc[:220]}")

    print("--- IMPORTS ---")
    sys.path.insert(0, str(ROOT))
    for folder in ("actions", "plugins"):
        directory = ROOT / folder
        if not directory.is_dir():
            print(f"MISSING {folder}")
            continue
        for path in sorted(directory.glob("*.py")):
            rel = f"{folder}/{path.name}"
            if path.name.startswith("_"):
                print(f"SKIP {rel}")
                continue
            module_name = f"{folder}.{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    print(f"FAIL {rel} ImportError: could not build spec")
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                has = hasattr(module, "TOOL") or hasattr(module, "PLUGIN")
                print(f"OK {rel} metadata={has}")
            except Exception as exc:
                print(f"FAIL {rel} {type(exc).__name__}: {exc}")
                traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
