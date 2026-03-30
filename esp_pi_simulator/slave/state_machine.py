"""State machine helpers for slave nodes."""

from __future__ import annotations

from enum import Enum


class SlaveState(str, Enum):
    INIT = "INIT"
    REGISTERED = "REGISTERED"
    READY = "READY"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    BACKOFF = "BACKOFF"
    COMPLETED = "COMPLETED"
