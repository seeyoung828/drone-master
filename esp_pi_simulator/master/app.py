"""FastAPI master application for the simulator."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.logger import get_logger
from common.protocol import AckResponse, ChunkPayload
from .metrics import MetricsTracker
from .scheduler import RoundRobinScheduler
from .session_manager import SessionManager


class RegisterPayload(BaseModel):
    node_id: str
    total_chunks: int


class ReadyPayload(BaseModel):
    node_id: str


class MasterState:
    def __init__(self, session_dir: Path, metrics_path: Path, quota: int) -> None:
        self.logger = get_logger("master")
        self.sessions = SessionManager(session_dir)
        self.scheduler = RoundRobinScheduler()
        self.metrics = MetricsTracker(metrics_path)
        self.quota = quota
        self.current_slot_start = time.monotonic()

    def register_node(self, node_id: str, total_chunks: int) -> Dict[str, str]:
        session = self.sessions.register(node_id, total_chunks)
        self.scheduler.register(node_id)
        self.metrics.metrics.total_nodes = len(self.sessions.sessions)
        self.logger.info("[SLAVE][%s] registered with total_chunks=%d", node_id, total_chunks)
        return {"session_id": session.session_id, "resume_from": session.last_ack_chunk + 1}

    def ready_node(self, node_id: str) -> Dict:
        session = self.sessions.get(node_id)
        if not session:
            raise HTTPException(status_code=404, detail="node not registered")
        self.sessions.mark_ready(node_id)
        self.logger.info("[SLAVE][%s] ready state", node_id)
        return {"resume_from": session.last_ack_chunk + 1}

    def permission(self, node_id: str) -> Dict:
        session = self.sessions.get(node_id)
        if not session:
            raise HTTPException(status_code=404, detail="node not registered")

        current = self.scheduler.current_node()
        eligible = self.sessions.eligible_nodes()
        if current not in eligible:
            chosen = self.scheduler.next_node(eligible)
            if chosen:
                self.sessions.mark_active(chosen)
                self.logger.info("[SCHED] selected %s", chosen)
                self.current_slot_start = time.monotonic()
                self.metrics.add_resume()
        else:
            chosen = current

        allowed = chosen == node_id
        return {
            "allowed": allowed,
            "current_node": chosen,
            "resume_from": session.last_ack_chunk + 1,
            "quota": self.quota,
        }

    def record_chunk(self, chunk: ChunkPayload) -> AckResponse:
        session = self.sessions.get(chunk.node_id)
        if not session:
            raise HTTPException(status_code=404, detail="node not registered")

        if chunk.chunk_index != session.last_ack_chunk + 1:
            self.logger.warning(
                "[MASTER][%s] unexpected chunk %d (expected %d)",
                chunk.node_id,
                chunk.chunk_index,
                session.last_ack_chunk + 1,
            )
            return AckResponse(False, session.last_ack_chunk, False, "unexpected chunk")

        session = self.sessions.record_ack(chunk.node_id, chunk.chunk_index)
        self.logger.info("[ACK][%s] chunk %d acknowledged", chunk.node_id, chunk.chunk_index)
        self.metrics.increment_chunks(chunk.node_id)

        should_stop = session.slot_chunks >= self.quota and not session.completed
        if should_stop:
            self.logger.info("[STOP][%s] quota reached, preempting", chunk.node_id)
            self.sessions.mark_preempted(chunk.node_id)
            self.scheduler.release_current()
            self.metrics.add_preempt()

        if session.completed:
            self.logger.info("[MASTER][%s] transfer complete", chunk.node_id)
            self.sessions.mark_completed(chunk.node_id)
            self.scheduler.mark_completed(chunk.node_id)
            self.metrics.mark_completed(chunk.node_id)

        return AckResponse(True, chunk.chunk_index, should_stop, "ACK")

    def report_fault(self, node_id: str, fault_type: str, detail: str) -> None:
        self.logger.warning("[FAULT][%s] %s -> %s", node_id, fault_type, detail)
        self.sessions.mark_fault(node_id)
        self.metrics.add_fault()

    def finalize(self) -> Dict:
        waits = []
        for session in self.sessions.sessions.values():
            if session.wait_started > 0:
                waits.append(time.monotonic() - session.wait_started)
        avg_wait = sum(waits) / len(waits) if waits else 0.0
        summary = self.metrics.finalize(avg_wait)
        summary["sessions"] = self.sessions.summary()
        return summary


def build_master_app(state: MasterState) -> FastAPI:
    app = FastAPI()

    @app.post("/register")
    def register(payload: RegisterPayload):
        return state.register_node(payload.node_id, payload.total_chunks)

    @app.post("/ready")
    def ready(payload: ReadyPayload):
        return state.ready_node(payload.node_id)

    @app.get("/permission/{node_id}")
    def permission(node_id: str):
        return state.permission(node_id)

    @app.post("/chunk")
    def chunk(payload: Dict):
        chunk_payload = ChunkPayload(**payload)
        ack = state.record_chunk(chunk_payload)
        return ack.__dict__

    @app.post("/complete")
    def complete(payload: Dict[str, str]):
        session = state.sessions.get(payload["node_id"])
        if session:
            state.sessions.mark_completed(payload["node_id"])
            state.scheduler.mark_completed(payload["node_id"])
            state.metrics.mark_completed(payload["node_id"])
        return {"ok": True}

    @app.post("/fault")
    def fault(payload: Dict[str, str]):
        state.report_fault(payload["node_id"], payload["fault_type"], payload.get("detail", ""))
        return {"ok": True}

    @app.get("/status")
    def status():
        return state.sessions.summary()

    @app.get("/metrics")
    def metrics():
        return state.metrics.metrics.__dict__

    return app
