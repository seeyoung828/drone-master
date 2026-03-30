"""Persist slave-side state."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from common.utils import dump_json, load_json
from .state_machine import SlaveState


@dataclass
class SlaveSession:
    node_id: str
    session_id: str
    total_chunks: int
    last_ack_chunk: int = -1
    state: SlaveState = SlaveState.INIT


class SlaveSessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state: Optional[SlaveSession] = None
        self._load()

    def _load(self) -> None:
        data = load_json(self.path)
        if data:
            data["state"] = SlaveState(data["state"])
            self.state = SlaveSession(**data)

    def _persist(self) -> None:
        if self.state:
            payload = asdict(self.state)
            payload["state"] = self.state.state.value
            dump_json(self.path, payload)

    def initialize(self, node_id: str, session_id: str, total_chunks: int) -> None:
        self.state = SlaveSession(node_id=node_id, session_id=session_id, total_chunks=total_chunks)
        self._persist()

    def update_ack(self, chunk_index: int) -> None:
        if not self.state:
            return
        self.state.last_ack_chunk = chunk_index
        self._persist()

    def set_state(self, new_state: SlaveState) -> None:
        if not self.state:
            return
        self.state.state = new_state
        self._persist()
