"""Protocol-level dataclasses used by master/slave."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChunkPayload:
    node_id: str
    session_id: str
    chunk_index: int
    total_chunks: int
    payload: str
    checksum: str


@dataclass
class AckResponse:
    ok: bool
    acked_chunk: int
    should_stop: bool
    message: str

