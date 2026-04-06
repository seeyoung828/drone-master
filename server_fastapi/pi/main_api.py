# main_api.py
import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from config import DB_PATH
import os

app = FastAPI(title="Drone Data Receiver (Data Mule)")

# 1. 데이터 모델 정의 (센서가 보낼 형식)
class ChunkData(BaseModel):
    node_id: str
    data_id: str
    chunk_index: int
    total_chunks: int
    payload: str

# 2. DB 업데이트 함수 (이전 pi_controller 로직 활용)
def upsert_transfer(node_id, data_id, total_chunks, last_received_chunk, state):
    """받은 조각 정보를 DB에 저장 (이어받기 핵심)"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO transfers (node_id, data_id, total_chunks, last_received_chunk, state, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(node_id, data_id) DO UPDATE SET
            total_chunks = excluded.total_chunks,
            last_received_chunk = excluded.last_received_chunk,
            state = excluded.state,
            updated_at = CURRENT_TIMESTAMP
    """, (node_id, data_id, total_chunks, last_received_chunk, state))
    conn.commit()
    conn.close()

# 3. 데이터 수신 엔드포인트
@app.post("/upload")
async def receive_chunk(item: ChunkData):
    try:
        # 상태 계산
        state = "COMPLETED" if item.chunk_index + 1 >= item.total_chunks else "PAUSED"
        
        # DB에 저장
        upsert_transfer(item.node_id, item.data_id, item.total_chunks, item.chunk_index, state)
        
        print(f"[DRONE] Received {item.node_id} - Chunk {item.chunk_index}/{item.total_chunks}")
        
        # 센서에게 ACK 응답
        return {"status": "ACK", "chunk_index": item.chunk_index}
        
    except Exception as e:
        print(f"[DRONE] Error processing chunk: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 4. 상태 조회 엔드포인트 (모니터링용)
@app.get("/status")
async def get_status():
    """모든 노드의 수집 현황(%)을 반환"""
    if not os.path.exists(DB_PATH):
        return []
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT node_id, last_received_chunk + 1 as current, total_chunks, state, updated_at FROM transfers")
    rows = cur.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        node = dict(row)
        progress = (node['current'] / node['total_chunks'] * 100) if node['total_chunks'] > 0 else 0
        node['progress_percent'] = round(progress, 2)
        results.append(node)
    
    return results