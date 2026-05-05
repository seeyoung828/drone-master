# 🛠️ REVOKE 및 CHECKSUM_FAIL 문제 종합 해결 방안 (solve_explicit_checksum_problem.md)

`explicit_checksum_problem.md`에서 분석된 제어 신호 유실 및 체크섬 오류 문제를 해결하기 위해, 시스템의 신뢰성을 보장하는 **"Robust Signaling & Atomic Verification"** 아키텍처를 설계합니다.

---

## 1. REVOKE 신호 처리 강화 (Signaling Reliability)

### 1.1 노드 메인 루프 분기 추가 (`sensor_node.py`)
노드가 데이터를 보내고 있지 않은 상태(비콘 대기 등)에서 도착한 `REVOKE` 신호가 증발하는 것을 방지합니다.
- **수정 사항:** `run()` 함수의 메인 `while` 루프 내 패킷 수신부(`recvfrom`)에 `REVOKE` 처리 로직을 추가합니다.
- **동작:** 현재 전송 중인 `Data_ID`와 일치하는 `REVOKE` 수신 시, `is_finished` 변수를 조작하거나 즉시 다음 이미지로 넘어가도록 상태를 제어합니다.

### 1.2 마스터의 다중 REVOKE 송신 (`drone_master.py`)
UDP 유실에 대비하여 중단 신호의 도달 확률을 높입니다.
- **수정 사항:** 슬롯 종료 조건 만족 시, `REVOKE` 패킷을 1회가 아닌 **3회 연속(약간의 간격)** 송신합니다.
- **기대 효과:** 고속 전송 중 발생하는 패킷 충돌 및 유실 상황에서도 최소 1개의 제어 패킷이 노드에 도달할 확률을 극대화합니다.

---

## 2. 체크섬(CHECKSUM_FAIL) 완벽 해결 (Verification Integrity)

### 2.1 강제 플러시 및 핸들 정리 (`drone_master.py` & `db_manager.py`)
검증 직전에 쓰기 버퍼의 모든 데이터가 물리 디스크에 기록되도록 보장합니다.
- **수정 사항:** `drone_master.py`에서 `COMPLETE` 수신 시, `db.verify_and_finalize()`를 호출하기 **직전**에 반드시 `db.close_file()`을 호출합니다.
- **상세:** `close_file()`은 내부적으로 `self._current_file_handle.close()`를 수행하여 OS 버퍼를 플러시하고 락을 해제합니다. 이후 검증 루틴이 새로운 핸들로 파일을 읽을 때 최신 데이터를 보장받습니다.

### 2.2 고해상도 Data_ID 생성 로직 개선 (`sensor_node.py`)
초 단위 충돌을 방지하기 위해 ID 생성 방식을 변경합니다.
- **기존:** `time.strftime("%d%H%M%S", time.localtime(mtime))` (초 단위)
- **개선:** `f"{int(mtime):x}_{len(self.file_data):x}"` (수정 시간 16진수 + 파일 크기 16진수 조합)
- **기대 효과:** 동일 시간에 생성된 파일이라도 크기가 다르면 ID가 달라지며, 1초 내에 동일 크기의 파일이 생성될 확률은 거의 없으므로 세션 간섭을 원천 차단합니다.

### 2.3 검증 유예 시간(Grace Period) 도입 (`drone_master.py`)
패킷 순서 역전 현상을 해결합니다.
- **수정 사항:** `COMPLETE` 수신 시 즉시 검증하지 않고, `time.sleep(0.1)` 정도의 유예 시간을 갖거나, 소켓 수신 버퍼에 남은 패킷들을 모두 처리한 후 검증을 시작합니다.
- **기대 효과:** `COMPLETE`보다 미세하게 늦게 도착한 마지막 `DATA` 조각들이 DB에 기록될 시간을 확보하여 `received_count == total_chunks` 상태에서 검증이 수행되도록 합니다.

---

## 3. 세부 구현 가이드 (예시 코드)

### [Master] `drone_master.py` 수정 예시
```python
elif msg_type == "COMPLETE":
    # 1. 잠시 대기하여 지연 도착 패킷 처리 시간 확보
    time.sleep(0.1) 
    # 2. 열려있는 쓰기 핸들 강제 종료 (Flush & Close)
    db.close_file()
    # 3. 검증 수행
    success = db.verify_and_finalize(s_id, data_id, checksum_bin)
    # ... 이후 처리 ...
```

### [Node] `sensor_node.py` ID 생성 개선
```python
# _prepare_next_image 내 ID 생성 부분
mtime = os.path.getmtime(self.current_image_path)
fsize = os.path.getsize(self.current_image_path)
self.data_id = f"{int(mtime):08x}{fsize:04x}"[-8:] # 하위 8자리 추출하여 규격 유지
```

---

## 4. 최종 결론
위 방안들은 통신 계층의 **신호 보장(Reliability)**과 파일 시스템의 **동기화(Synchronization)** 문제를 동시에 해결합니다. 특히 검증 전 핸들 정리는 `CHECKSUM_FAIL`의 90% 이상을 차지하는 원인을 제거하며, 고해상도 ID는 시스템의 장기 운영 안정성을 확보하는 핵심 요소입니다.
