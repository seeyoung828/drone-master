"""Session persistence and state tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

from common.utils import dump_json, load_json


@dataclass
class SessionState:
    node_id: str
    session_id: str
    total_chunks: int
    last_ack_chunk: int = -1
    status: str = "REGISTERED"
    completed: bool = False
    slot_chunks: int = 0
    preemptions: int = 0
    resumes: int = 0
    fault_counts: int = 0
    wait_started: float = 0.0


class SessionManager:
    """In-memory session store with JSON persistence."""

    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir
        self.master_file = sessions_dir / "master_sessions.json"
        self.sessions: Dict[str, SessionState] = {}
        self._load()

    def _load(self) -> None:
        data = load_json(self.master_file, default={}) or {}
        for node_id, payload in data.items():
            self.sessions[node_id] = SessionState(**payload)

    def _persist(self) -> None:
        dump_json(self.master_file, {nid: asdict(state) for nid, state in self.sessions.items()})

    def register(self, node_id: str, total_chunks: int) -> SessionState:
        session = SessionState(
            node_id=node_id,
            session_id=f"{node_id}-{int(time.time()*1000)}",
            total_chunks=total_chunks,
            status="READY",
            wait_started=time.time(),
        )
        self.sessions[node_id] = session
        self._persist()
        return session

    def get(self, node_id: str) -> Optional[SessionState]:
        return self.sessions.get(node_id)

    def mark_ready(self, node_id: str) -> None:
        session = self.sessions[node_id]
        session.status = "READY"
        session.slot_chunks = 0
        session.wait_started = time.time()
        self._persist()

    def mark_active(self, node_id: str) -> None:
        session = self.sessions[node_id]
        session.status = "ACTIVE"
        session.slot_chunks = 0
        session.resumes += 1
        session.wait_started = 0.0
        self._persist()

    def record_ack(self, node_id: str, chunk_index: int) -> SessionState:
        session = self.sessions[node_id]
        session.last_ack_chunk = chunk_index
        session.slot_chunks += 1
        if session.last_ack_chunk + 1 >= session.total_chunks:
            session.completed = True
            session.status = "COMPLETED"
        self._persist()
        return session

    def mark_preempted(self, node_id: str) -> None:
        session = self.sessions[node_id]
        session.preemptions += 1
        session.status = "PAUSED"
        session.slot_chunks = 0
        session.wait_started = time.time()
        self._persist()

    def mark_fault(self, node_id: str) -> None:
        session = self.sessions[node_id]
        session.fault_counts += 1
        self._persist()

    def eligible_nodes(self) -> List[str]:
        return [nid for nid, s in self.sessions.items() if (not s.completed) and s.status in {"READY", "PAUSED"}]

    def mark_completed(self, node_id: str) -> None:
        session = self.sessions[node_id]
        session.completed = True
        session.status = "COMPLETED"
        self._persist()

    def summary(self) -> Dict[str, Dict]:
        return {nid: asdict(state) for nid, state in self.sessions.items()}
