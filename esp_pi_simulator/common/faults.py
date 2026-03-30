"""Fault injection helper."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict


@dataclass
class FaultConfig:
    enabled: bool
    network_delay_prob: float
    temporary_disconnect_prob: float
    chunk_drop_prob: float
    min_delay_sec: float
    max_delay_sec: float
    disconnect_backoff_sec_min: float
    disconnect_backoff_sec_max: float


class FaultInjector:
    """Decides whether to inject a fault for a given event."""

    def __init__(self, config: Dict):
        fc = config.get("faults", {})
        self.conf = FaultConfig(
            enabled=bool(fc.get("enabled", True)),
            network_delay_prob=float(fc.get("network_delay_prob", 0.0)),
            temporary_disconnect_prob=float(fc.get("temporary_disconnect_prob", 0.0)),
            chunk_drop_prob=float(fc.get("chunk_drop_prob", 0.0)),
            min_delay_sec=float(fc.get("min_delay_sec", 0.1)),
            max_delay_sec=float(fc.get("max_delay_sec", 1.5)),
            disconnect_backoff_sec_min=float(fc.get("disconnect_backoff_sec_min", 0.5)),
            disconnect_backoff_sec_max=float(fc.get("disconnect_backoff_sec_max", 2.0)),
        )

    def _rand_bool(self, prob: float) -> bool:
        return self.conf.enabled and random.random() < prob

    def maybe_network_delay(self) -> float:
        if not self._rand_bool(self.conf.network_delay_prob):
            return 0.0
        return random.uniform(self.conf.min_delay_sec, self.conf.max_delay_sec)

    def maybe_disconnect(self) -> float:
        if not self._rand_bool(self.conf.temporary_disconnect_prob):
            return 0.0
        return random.uniform(
            self.conf.disconnect_backoff_sec_min,
            self.conf.disconnect_backoff_sec_max,
        )

    def should_drop_chunk(self) -> bool:
        return self._rand_bool(self.conf.chunk_drop_prob)
