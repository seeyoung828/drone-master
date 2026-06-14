# 🚨 에러 패킷 처리 및 복구 로직 명세 (Error Handling & Recovery)

본 문서는 `project_progress.md`에서 지적된 "단순 타임아웃 위주의 에러 처리"를 개선하고, `protocol_spec.md`의 `ERROR` 패킷을 활용하여 시스템의 신뢰성을 높이기 위한 구체적인 구현 가이드를 제공한다.

---

## 1. 현재 시스템의 한계 (Current Limitations)

1.  **CRC 불일치 시 무한 루프 위험**: 모든 조각을 수집했으나 체크섬이 다를 경우, `get_next_missing_idx`는 마지막 인덱스를 반환하여 더 이상 요청할 조각이 없다고 판단함. 슬레이브는 전송이 끝났다고 생각하고 `COMPLETE`만 반복 송신할 위험이 있음.
2.  **명시적 에러 피드백 부재**: 마스터가 수집 중 문제(파일 쓰기 오류, 세션 충돌 등)를 발견해도 슬레이브에게 알리지 않고 단순히 무시함.
3.  **재시도(Retry) 로직 미비**: `GRANT` 송신 후 응답이 없을 때에 대한 카운터 기반 재시도 로직이 `drone_master.py`에 구현되어 있지 않음.

---

## 2. 에러 패킷 구조 (ERROR Packet Structure)

`ERROR | S_ID | Data_ID | 0 | 0 | 0 | Error_Code`

- **Error_Code 정의**:
    - `TIMEOUT`: `GRANT` 후 응답 없음 (3회 재시도 초과)
    - `CHECKSUM_FAIL`: `COMPLETE` 수신 후 CRC32 검증 실패
    - `LIVELOCK_PREVENT`: 동일 구간 반복 요청 또는 데이터 진행 중단
    - `SESSION_MISMATCH`: 동일 `S_ID`에서 예기치 못한 `Data_ID` 변경 감지

---

## 3. 상세 개선 사항 (Implementation Steps)

### 3.1 마스터 (Drone Master) 개선

#### A. CRC32 검증 실패 처리 (`CHECKSUM_FAIL`)
- **현재**: `db.verify_and_finalize`가 `False`를 반환하면 단순히 `current_target = None` 처리.
- **개선**:
    1.  슬레이브에게 `ERROR|...|CHECKSUM_FAIL` 패킷을 송신한다.
    2.  DB의 `received_mask`와 `received_count`를 초기화하여 재수집을 유도하거나, 특정 구간을 다시 요청하도록 `status`를 `PAUSED`로 변경한다.
    3.  해당 노드의 점수를 낮추어 다른 노드에게 기회를 준다.

#### B. GRANT 재시도 로직 (`TIMEOUT`)
- **개선**:
    1.  `sensors_mem`에 `retry_count` 필드를 추가한다.
    2.  `GRANT`를 보낼 때마다 해당 노드의 `last_grant_time`을 기록한다.
    3.  0.3초 이내에 `DATA` 패킷이 오지 않으면 `retry_count`를 증가시키고 다시 `GRANT`를 보낸다.
    4.  `retry_count >= 3`이 되면 슬레이브에게 `ERROR|...|TIMEOUT`을 보내고(도달하지 못할 수 있으나 기록용), 해당 노드를 스케줄링에서 일시 제외한다.

#### C. 데이터 정합성 오류 (`SESSION_MISMATCH`)
- **개선**:
    1.  `prepare_session` 시 `Purge`가 발생하면, 슬레이브에게 `ERROR|...|SESSION_MISMATCH`를 보내어 슬레이브의 전송 상태를 강제로 리셋시킨다.

---

### 3.2 슬레이브 (Sensor Node) 개선

#### A. ERROR 패킷 수신 루틴 추가
- **개선**:
    1.  `run()` 루프의 `recvfrom` 섹션에서 `msg[0] == "ERROR"` 조건을 추가한다.
    2.  `CHECKSUM_FAIL` 수신 시: `current_idx = 0`, `max_sent_idx = -1`로 리셋하여 처음부터 다시 전송 준비를 한다.
    3.  `TIMEOUT` 수신 시: 전송을 중단하고 다시 `BEACON` 모드로 돌아가 대기한다.
    4.  `SESSION_MISMATCH` 수신 시: 현재 `Data_ID`를 새로 생성하거나(재촬영), `current_idx`를 0으로 초기화한다.

---

## 4. 예외 상황별 시퀀스 (Error Sequences)

### 4.1 체크섬 실패 시 복구 (Checksum Recovery)
1.  **Master**: `COMPLETE` 수신 -> CRC32 계산 -> **불일치 발견**.
2.  **Master**: `ERROR | SENSOR_01 | 04071420 | ... | CHECKSUM_FAIL` 송신.
3.  **Slave**: `ERROR` 패킷 수신 -> 에러 코드 확인 -> 내부 `current_idx` 0으로 리셋.
4.  **Master**: 해당 세션의 `received_mask` 초기화.
5.  **Master**: 다음 스케줄링 타임에 `GRANT | ... | Start_Idx: 0` 송신.

### 4.2 타임아웃 발생 시 (Timeout Management)
1.  **Master**: `GRANT` 송신 -> 0.3초 대기 -> 응답 없음 (1회).
2.  **Master**: `GRANT` 재송신 -> 0.3초 대기 -> 응답 없음 (2회).
3.  **Master**: `GRANT` 재송신 -> 0.3초 대기 -> 응답 없음 (3회).
4.  **Master**: `current_target = None`, DB에 `TIMEOUT` 상태 기록, 다음 노드로 전환.

---

## 5. 코드 수정 가이드 (Code Modification Tips)

### `drone_master.py`
```python
# Retry 관리 구조체 예시
sensors_mem[s_id] = {
    ...,
    'retry_count': 0,
    'last_grant_time': 0
}

# 에러 송신 함수
def send_error(s_id, data_id, addr, error_code):
    err_msg = f"ERROR|{s_id}|{data_id}|0|0|0|{error_code}"
    sock.sendto(err_msg.encode(), addr)
```

### `sensor_node.py`
```python
# 메시지 처리 루프
if msg[0] == "ERROR":
    error_code = msg[6]
    if error_code == "CHECKSUM_FAIL":
        self.current_idx = 0  # 처음부터 다시 시작
    elif error_code == "TIMEOUT":
        # 대기 상태로 전환
        pass
```

---

## 6. 결론

에러 패킷 처리는 단순한 로그 기록을 넘어, **마스터와 슬레이브 간의 상태 동기화(State Synchronization)**를 위한 핵심 도구이다. 위 로직을 반영함으로써 UDP 환경에서의 불확실성을 최소화하고 데이터 수집의 완결성을 보장할 수 있다.
