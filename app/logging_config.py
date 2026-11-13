"""Logging setup for the bridge."""

import logging
import sys

from app.config import settings


def setup_logging() -> None:
    """Configure root logger from settings.LOG_LEVEL."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
