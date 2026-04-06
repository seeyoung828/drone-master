"""FastAPI 기반 마스터-슬레이브 시뮬레이터에서 공통으로 사용하는 요청/응답 모델 모음."""

from pydantic import BaseModel


class RegisterRequest(BaseModel):
    """슬레이브가 처음 마스터에 등록할 때 사용하는 요청 모델."""

    node_id: str
    total_chunks: int


class ReadyRequest(BaseModel):
    """슬레이브가 전송 준비 완료를 알릴 때 사용하는 요청 모델."""

    node_id: str


class ChunkRequest(BaseModel):
    """슬레이브가 더미 데이터를 청크 단위로 전송할 때 사용하는 요청 모델."""

    node_id: str
    chunk_index: int
    total_chunks: int
    payload: str


class CompleteRequest(BaseModel):
    """슬레이브가 모든 청크 전송을 끝낸 뒤 완료를 알릴 때 사용하는 요청 모델."""

    node_id: str


class SimpleResponse(BaseModel):
    """마스터가 공통적으로 반환하는 간단한 응답 모델."""

    ok: bool
    message: str


class PermissionResponse(BaseModel):
    """특정 슬레이브 노드가 지금 청크를 전송해도 되는지 알려주는 응답 모델."""

    allowed: bool
    active_node: str | None
    message: str


class StatusResponse(BaseModel):
    """마스터가 현재 시뮬레이터 상태를 요약해서 반환할 때 사용하는 응답 모델."""

    total_nodes: int
    registered_nodes: int
    ready_nodes: int
    completed_nodes: int
    nodes: dict[str, dict]