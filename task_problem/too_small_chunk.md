# Too Small Chunk 문제 분석 및 해결 방안

## 1. 문제 현상 분석
`test_result.txt` 로그 분석 결과, 다음과 같은 심각한 성능 저하 현상이 관찰되었습니다.

```
[Slot End] Sensor_01 종료 (Idle Timeout, Chunks: 1)
[Slot End] Sensor_01 종료 (Idle Timeout, Chunks: 3)
[Slot End] Sensor_01 종료 (Idle Timeout, Chunks: 1)
```

*   **증상**: 하나의 슬롯(Slot)에서 수신되는 청크(Chunk)의 개수가 1~3개에 불과하며, 대부분 `Idle Timeout`으로 인해 슬롯이 강제 종료됨.
*   **결과**: 이미지 한 장을 전송하는 데 수백 번의 슬롯 할당과 권한 요청(GRANT) 과정이 반복되어 전송 시간이 비정상적으로 길어짐.

## 2. 원인 분석 (Root Cause Analysis)

### 2.1 너무 작은 Chunk Size와 DB 오버헤드
현재 `CHUNK_SIZE`는 **1024 bytes (1KB)**로 설정되어 있습니다. 1MB 이미지를 보낼 경우 1024개의 패킷이 생성됩니다.
`db_manager.py`의 `save_fragment` 함수는 각 청크를 수신할 때마다 다음 작업을 수행합니다:
1.  파일 열기 (`open`)
2.  파일 위치 이동 및 쓰기 (`seek`, `write`)
3.  파일 닫기
4.  SQLite `UPDATE` 쿼리 실행 (수신 마스크 업데이트)
5.  **`self.conn.commit()` 실행**

SD 카드를 사용하는 Raspberry Pi 환경에서 매번 `commit()`을 호출하는 것은 매우 느린 작업입니다. 청크 크기가 작을수록 이 오버헤드는 기하급수적으로 증가합니다.

### 2.2 스케줄러의 타임아웃 로직 결함 (`drone_master.py`)
`drone_master.py`의 메인 루프에서 `last_grant_time`을 업데이트하는 방식에 문제가 있습니다.

```python
while True:
    now = time.time()  # 루프 시작 시점의 시간
    ...
    while True: # 패킷 수신 루프
        try:
            data, addr = sock.recvfrom(2048)
            ...
            elif msg_type == "DATA":
                if s_id in sensors_mem:
                    sensors_mem[s_id].update({
                        'last_grant_time': now, # 루프 시작 시점의 'stale'한 시간 사용
                        ...
                    })
                db.save_fragment(...) # 여기서 시간이 오래 걸림
```

*   `db.save_fragment`가 0.5초 이상 걸리면, 다음 `IDLE_TIMEOUT` 체크 시 `time.time() - last_grant_time`은 이미 0.5초를 초과하게 됩니다.
*   스케줄러는 데이터를 방금 받았음에도 불구하고, 처리 시간이 길어져 "데이터가 오지 않았다"고 판단하고 `Idle Timeout`으로 슬롯을 종료해 버립니다.

### 2.3 하드코딩된 값
`db_manager.py` 내부에 `f.seek(idx * 1024)`와 같이 `CHUNK_SIZE`가 하드코딩되어 있어, 단순히 설정값만 변경할 경우 데이터가 오염되는 문제가 있습니다.

## 3. 해결 방안 (Solution)

### 3.1 Chunk Size 상향 조정
`CHUNK_SIZE`를 **4096 (4KB)** 또는 그 이상으로 상향 조정합니다. 이는 DB I/O 횟수를 1/4로 줄여 직접적인 성능 향상을 가져옵니다. (단, UDP 패킷 파편화와 네트워크 안정성을 고려하여 적절한 값을 선택해야 합니다.)

### 3.2 DB 처리 최적화
*   `save_fragment`에서 매번 호출되는 `commit()`을 제거하고, 슬롯이 종료될 때나 일정 주기마다 한 번에 커밋하도록 변경합니다.
*   자주 열고 닫는 파일 핸들을 캐싱하거나, 더 효율적인 I/O 방식을 도입합니다.

### 3.3 타임아웃 로직 수정
`last_grant_time`을 업데이트할 때 `now`가 아닌 `time.time()`을 직접 사용하여, 패킷 처리 시간이 타임아웃 계산에 영향을 주지 않도록 합니다.

### 3.4 상수 통일
모든 파일(`utility.py`, `sensor_node.py`, `db_manager.py`)에서 동일한 `CHUNK_SIZE` 상수를 사용하도록 수정합니다.

## 4. 해결 단계 (Action Plan)
1.  **상수 정의**: `common/utility.py`에 `CHUNK_SIZE = 4096` 정의.
2.  **DB 코드 수정**: `db_manager.py`에서 하드코딩된 `1024`를 `CHUNK_SIZE`로 교체하고 `commit()` 부하 줄이기.
3.  **마스터 수정**: `drone_master.py`의 `last_grant_time` 업데이트 로직 수정.
4.  **노드 수정**: `sensor_node.py`의 `CHUNK_SIZE`를 `common/utility.py`를 참조하도록 수정.
5.  **검증**: 다시 테스트를 수행하여 `[Slot End]` 로그에서 한 슬롯당 처리되는 `Chunks` 수가 증가했는지 확인.
