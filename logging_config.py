from __future__ import annotations

import logging
import os
import sys

from observability import LOGGER, configure_logging


class ColorFormatter(logging.Formatter):
    _LEVEL_COLORS = {
        logging.DEBUG: "\033[2;37m",
        logging.INFO: "\033[36m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
    }
    _RESET = "\033[0m"
    _DIM = "\033[2m"

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, "%H:%M:%S")
        color = self._LEVEL_COLORS.get(record.levelno, "")
        level = f"[{record.levelname:<7}]"
        fields = getattr(record, "fields", None)
        suffix = " " + " ".join(f"{key}={value}" for key, value in fields.items()) if fields else ""
        return f"{self._DIM}{timestamp}{self._RESET} {color}{level}{self._RESET} {self._DIM}{record.name}{self._RESET} {record.getMessage()}{suffix}"


def setup_logging() -> None:
    """Colorized console logging when attached to a real terminal; unchanged JSON pipeline otherwise (production/CloudWatch parsing depends on it)."""
    root = logging.getLogger()
    if any(getattr(handler, "_leadlens_console", False) for handler in root.handlers):
        return
    color_disabled = os.getenv("LOG_COLOR", "1").strip().lower() in ("0", "false", "no")
    if not sys.stdout.isatty() or color_disabled:
        configure_logging()
        return
    import colorama

    colorama.init()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler._leadlens_console = True
    handler.setFormatter(ColorFormatter())
    root.addHandler(handler)
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    LOGGER.propagate = True
