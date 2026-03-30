"""Slave node simulation for the ESP-PI project."""

from __future__ import annotations

import random
import string
from pathlib import Path
from typing import Dict

import httpx

from common.faults import FaultInjector
from common.logger import get_logger
from common.protocol import ChunkPayload
from common.utils import compute_checksum, sleep_seconds
from .session_store import SlaveSessionStore
from .state_machine import SlaveState


class SlaveNode:
    """Simulates an ESP node sending chunks to the master."""

    def __init__(self, node_id: str, config: Dict, session_dir: Path) -> None:
        self.node_id = node_id
        self.config = config
        self.logger = get_logger(f"slave.{node_id}")
        self.total_chunks = config["per_node_total_chunks"][node_id]
        self.quota = int(config.get("quota_chunks_per_turn", 4))
        self.chunk_size = int(config.get("chunk_size", 128))
        self.master_url = f"http://{config['master_host']}:{config['master_port']}"
        self.store = SlaveSessionStore(session_dir / f"{node_id}.json")
        self.faults = FaultInjector(config)
        self.http = httpx.AsyncClient()

    async def run(self) -> None:
        self.logger.info("[SLAVE][%s] starting", self.node_id)
        try:
            await self.register()
            await self.ready()
            while True:
                if not self.store.state or self.store.state.state == SlaveState.COMPLETED:
                    break
                permission = await self.check_permission()
                if permission["allowed"]:
                    await self.send_chunks(permission)
                else:
                    await sleep_seconds(self.config.get("tick_delay_sec", 0.5))
        finally:
            await self.http.aclose()
            self.logger.info("[SLAVE][%s] shutting down", self.node_id)

    async def register(self) -> None:
        payload = {"node_id": self.node_id, "total_chunks": self.total_chunks}
        resp = await self.http.post(f"{self.master_url}/register", json=payload)
        resp.raise_for_status()
        data = resp.json()
        session_id = data["session_id"]
        self.store.initialize(self.node_id, session_id, self.total_chunks)
        self.store.set_state(SlaveState.REGISTERED)
        self.logger.info("[SLAVE][%s] registered session_id=%s", self.node_id, session_id)

    async def ready(self) -> None:
        resp = await self.http.post(f"{self.master_url}/ready", json={"node_id": self.node_id})
        resp.raise_for_status()
        self.store.set_state(SlaveState.READY)

    async def check_permission(self) -> Dict:
        resp = await self.http.get(f"{self.master_url}/permission/{self.node_id}")
        resp.raise_for_status()
        return resp.json()

    async def send_chunks(self, permission: Dict) -> None:
        resume_from = permission["resume_from"]
        quota = permission["quota"]
        sent = 0
        self.store.set_state(SlaveState.ACTIVE)
        while sent < quota and (resume_from + sent) < self.total_chunks:
            chunk_index = resume_from + sent
            fault_delay = self.faults.maybe_network_delay()
            if fault_delay:
                self.logger.warning("[FAULT][%s] network_delay before chunk %d: %.2fs", self.node_id, chunk_index, fault_delay)
                await sleep_seconds(fault_delay)
            if self.faults.should_drop_chunk():
                self.logger.warning("[FAULT][%s] chunk_drop injected for chunk %d", self.node_id, chunk_index)
                await self.report_fault("chunk_drop", f"chunk {chunk_index} dropped")
                sent += 1
                continue
            payload = self.make_chunk(chunk_index)
            try:
                response = await self.http.post(f"{self.master_url}/chunk", json=payload.__dict__)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as exc:
                self.logger.error("[SLAVE][%s] chunk %d send error: %s", self.node_id, chunk_index, exc)
                await sleep_seconds(0.5)
                continue

            if not data.get("ok"):
                self.logger.warning("[MASTER][%s] chunk %d not accepted: %s", self.node_id, chunk_index, data.get("message"))
                await sleep_seconds(0.5)
                break

            self.logger.info("[ACK][%s] chunk %d acked", self.node_id, chunk_index)
            self.store.update_ack(chunk_index)
            sent += 1

            if data.get("should_stop"):
                self.logger.info("[STOP][%s] quota stop signaled", self.node_id)
                break

            disconnect_delay = self.faults.maybe_disconnect()
            if disconnect_delay:
                self.logger.warning("[FAULT][%s] temporary_disconnect at chunk %d", self.node_id, chunk_index)
                await self.report_fault("temporary_disconnect", f"chunk {chunk_index}")
                self.store.set_state(SlaveState.BACKOFF)
                await sleep_seconds(disconnect_delay)
                self.logger.info("[RECOVERY][%s] reconnecting after %.2fs", self.node_id, disconnect_delay)
                await self.ready()
                return

        if self.store.state.last_ack_chunk + 1 >= self.total_chunks:
            await self.complete()
        else:
            self.store.set_state(SlaveState.PAUSED)

    async def report_fault(self, fault_type: str, detail: str) -> None:
        try:
            await self.http.post(f"{self.master_url}/fault", json={"node_id": self.node_id, "fault_type": fault_type, "detail": detail})
        except httpx.HTTPError:
            self.logger.error("[SLAVE][%s] fault report failed", self.node_id)

    async def complete(self) -> None:
        await self.http.post(f"{self.master_url}/complete", json={"node_id": self.node_id})
        self.store.set_state(SlaveState.COMPLETED)
        self.logger.info("[SLAVE][%s] transfer complete", self.node_id)

    def make_chunk(self, chunk_index: int) -> ChunkPayload:
        payload = self.generate_payload(chunk_index)
        checksum = compute_checksum(payload)
        session_id = self.store.state.session_id if self.store.state else "unknown"
        return ChunkPayload(
            node_id=self.node_id,
            session_id=session_id,
            chunk_index=chunk_index,
            total_chunks=self.total_chunks,
            payload=payload,
            checksum=checksum,
        )

    def generate_payload(self, chunk_index: int) -> str:
        base = f"{self.node_id}-chunk-{chunk_index}-"
        random_part = "".join(random.choices(string.ascii_letters + string.digits, k=max(4, self.chunk_size - len(base))))
        return (base + random_part)[: self.chunk_size]
