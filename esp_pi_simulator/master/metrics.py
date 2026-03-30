"""Metrics tracking utilities."""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict

from common.utils import dump_json


@dataclass
class Metrics:
    total_nodes: int = 0
    completed_nodes: int = 0
    total_chunks_received: int = 0
    per_node_ack_chunks: Dict[str, int] = None
    preempt_count: int = 0
    resume_count: int = 0
    fault_counts: int = 0
    average_wait_time_per_node: float = 0.0
    total_runtime_sec: float = 0.0


class MetricsTracker:
    """Collects metrics across the simulation run."""

    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.started_at = time.monotonic()
        self.metrics = Metrics(per_node_ack_chunks={})

    def increment_chunks(self, node_id: str) -> None:
        stats = self.metrics.per_node_ack_chunks
        stats[node_id] = stats.get(node_id, 0) + 1
        self.metrics.total_chunks_received += 1

    def mark_completed(self, node_id: str) -> None:
        self.metrics.completed_nodes += 1

    def add_preempt(self) -> None:
        self.metrics.preempt_count += 1

    def add_resume(self) -> None:
        self.metrics.resume_count += 1

    def add_fault(self) -> None:
        self.metrics.fault_counts += 1

    def finalize(self, avg_wait: float) -> Dict:
        self.metrics.total_runtime_sec = time.monotonic() - self.started_at
        self.metrics.average_wait_time_per_node = avg_wait
        dump_json(self.output_path, asdict(self.metrics))
        return asdict(self.metrics)
