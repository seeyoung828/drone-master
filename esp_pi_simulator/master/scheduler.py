"""Scheduling policies for node rotation."""

from __future__ import annotations

from collections import deque
from typing import Deque, Iterable, Optional


class RoundRobinScheduler:
    """Simple round-robin scheduler with preemption support."""

    def __init__(self) -> None:
        self.queue: Deque[str] = deque()
        self.current: Optional[str] = None

    def register(self, node_id: str) -> None:
        if node_id not in self.queue:
            self.queue.append(node_id)

    def mark_completed(self, node_id: str) -> None:
        self.queue = deque(n for n in self.queue if n != node_id)
        if self.current == node_id:
            self.current = None

    def release_current(self) -> None:
        self.current = None

    def next_node(self, eligible: Iterable[str]) -> Optional[str]:
        eligible_set = set(eligible)
        if not eligible_set:
            self.current = None
            return None

        for _ in range(len(self.queue)):
            node = self.queue.popleft()
            self.queue.append(node)
            if node in eligible_set:
                self.current = node
                return node

        self.current = None
        return None

    def current_node(self) -> Optional[str]:
        return self.current
