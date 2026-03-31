

"""슬레이브 노드의 등록, 준비 완료, 청크 수신, 완료 보고를 처리하는 가장 기본적인 FastAPI 마스터 서버."""

from fastapi import FastAPI

from common.protocol import (
    ChunkRequest,
    CompleteRequest,
    ReadyRequest,
    RegisterRequest,
    SimpleResponse,
    StatusResponse,
)

app = FastAPI(title="ESP-Pi Simulator Master")

# 노드별 현재 상태를 메모리에 저장하는 딕셔너리
nodes: dict[str, dict] = {}

# 노드별로 수신한 청크 내용을 저장하는 딕셔너리
received_chunks: dict[str, list[dict]] = {}


@app.get("/")
def root() -> dict:
    """서버가 정상 실행 중인지 확인하는 기본 엔드포인트."""

    return {"message": "master server is running"}


@app.post("/register", response_model=SimpleResponse)
def register_node(request: RegisterRequest) -> SimpleResponse:
    """슬레이브 노드를 등록하고 초기 상태를 저장한다."""

    nodes[request.node_id] = {
        "registered": True,
        "ready": False,
        "completed": False,
        "total_chunks": request.total_chunks,
        "last_chunk_index": -1,
    }
    received_chunks[request.node_id] = []

    print(f"[MASTER] {request.node_id} 등록 완료 (총 청크 수: {request.total_chunks})")

    return SimpleResponse(ok=True, message=f"{request.node_id} registered")


@app.post("/ready", response_model=SimpleResponse)
def ready_node(request: ReadyRequest) -> SimpleResponse:
    """등록된 슬레이브 노드를 준비 완료 상태로 변경한다."""

    if request.node_id not in nodes:
        return SimpleResponse(ok=False, message=f"{request.node_id} not registered")

    nodes[request.node_id]["ready"] = True
    print(f"[MASTER] {request.node_id} 준비 완료")

    return SimpleResponse(ok=True, message=f"{request.node_id} ready")


@app.post("/chunk", response_model=SimpleResponse)
def receive_chunk(request: ChunkRequest) -> SimpleResponse:
    """슬레이브가 보낸 청크를 저장하고 마지막 수신 청크 번호를 갱신한다."""

    if request.node_id not in nodes:
        return SimpleResponse(ok=False, message=f"{request.node_id} not registered")

    chunk_info = {
        "chunk_index": request.chunk_index,
        "payload": request.payload,
    }
    received_chunks[request.node_id].append(chunk_info)
    nodes[request.node_id]["last_chunk_index"] = request.chunk_index

    print(f"[MASTER] {request.node_id}의 chunk {request.chunk_index} 수신 완료")

    return SimpleResponse(ok=True, message=f"chunk {request.chunk_index} received")


@app.post("/complete", response_model=SimpleResponse)
def complete_node(request: CompleteRequest) -> SimpleResponse:
    """슬레이브 노드의 전송 완료 상태를 기록한다."""

    if request.node_id not in nodes:
        return SimpleResponse(ok=False, message=f"{request.node_id} not registered")

    nodes[request.node_id]["completed"] = True
    print(f"[MASTER] {request.node_id} 전송 완료")

    return SimpleResponse(ok=True, message=f"{request.node_id} completed")


@app.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    """현재 마스터가 알고 있는 전체 노드 상태를 요약해서 반환한다."""

    total_nodes = len(nodes)
    registered_nodes = sum(1 for node in nodes.values() if node["registered"])
    ready_nodes = sum(1 for node in nodes.values() if node["ready"])
    completed_nodes = sum(1 for node in nodes.values() if node["completed"])

    return StatusResponse(
        total_nodes=total_nodes,
        registered_nodes=registered_nodes,
        ready_nodes=ready_nodes,
        completed_nodes=completed_nodes,
        nodes=nodes,
    )