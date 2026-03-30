"""Logging helpers for unified console/file output."""

from __future__ import annotations

import logging
from logging import Logger
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"


def init_logging(log_dir: Path) -> Path:
    """Initialize console and file logging."""

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "runtime.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

    root.addHandler(console)
    root.addHandler(file_handler)

    root.info("[MASTER] logger initialized -> %s", log_path)
    return log_path


def get_logger(name: Optional[str] = None) -> Logger:
    """Return a named logger."""

    return logging.getLogger(name)
