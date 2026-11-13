"""Miscellaneous helper utilities."""

from pathlib import Path


def ensure_dir(path: Path) -> Path:
    """Create directory if missing; return the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path
