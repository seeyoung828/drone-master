# 📋 다중 노드 세션 충돌 분석 보고서 (Multi-Queue Session Collision Report)

**분석 대상:** `drone_test_log.txt`, `db_manager.py`, `drone_master.py`
**에러 유형:** `FileNotFoundError: [Errno 2] No such file or directory`
**발생 시점:** 다중 노드(Sensor_01, Sensor_02)가 동일한 `Data_ID`로 전송을 완료하는 시점

---

## 1. 에러 현상 및 로그 분석

### 1.1 로그 요약
- **상황:** `Sensor_01`과 `Sensor_02`가 모두 `Data_ID: 10152245`로 세션을 준비함.
- **성공:** `Sensor_01` (혹은 02)의 데이터가 먼저 완료되어 `10152245.jpg`로 저장됨.
- **실패:** 직후 다른 노드의 `COMPLETE` 패킷을 처리하던 중 `verify_and_finalize` 함수에서 `10152245.tmp` 파일을 찾지 못해 크래시 발생.

### 1.2 발생 원인 (Root Cause)
1.  **파일명 식별자 부족:** 현재 `DroneDB`는 임시 파일명을 `{data_id}.tmp`로 생성함. 
2.  **ID 중복:** 센서 노드들이 동일한 시간대에 이미지를 촬영하거나 테스트용 동일 파일을 읽을 경우 `Data_ID`(타임스탬프 기반)가 중복됨.
3.  **파일 레이스 컨디션 (Race Condition):**
    - 노드 A가 먼저 완료 -> `10152245.tmp`를 `10152245.jpg`로 **Rename**.
    - 노드 B가 완료 보고 -> 마스터는 `10152245.tmp`를 열려고 시도하지만, 이미 Rename되어 존재하지 않음.
4.  **SQL 쿼리 모호성:** `db_manager.py`의 `verify_and_finalize` 내부 쿼리가 `s_id`를 무시하고 `WHERE data_id = ?`만 사용하여, 다른 노드의 세션 정보를 잘못 참조할 위험이 있음.

---

## 2. 코드 결함 상세

### 2.1 `db_manager.py` - 파일 경로 생성 로직
```python
# 현재 로직: s_id가 누락되어 동일 Data_ID 시 덮어쓰기 발생
file_path = os.path.join(self.storage_dir, f"{data_id}.tmp")
```

### 2.2 `db_manager.py` - 검증 쿼리
```python
# 현재 로직: 여러 노드가 동일 Data_ID 사용 시 어떤 노드의 세션인지 구분 불가
cursor.execute("SELECT total_chunks, received_count FROM image_sessions WHERE data_id = ?", (data_id,))
```

---

## 3. 해결 방안 (Solution)

### 3.1 파일 시스템 구조 변경
- 임시 파일 및 최종 파일명에 `S_ID`를 접두어로 추가하여 노드별 격리 공간을 확보함.
- 형식: `{s_id}_{data_id}.tmp` 및 `{s_id}_{data_id}.jpg`

### 3.2 DB 접근 로직 강화
- 모든 조회(`SELECT`), 업데이트(`UPDATE`), 검증(`verify`) 쿼리에 `s_id`와 `data_id`를 함께 조건(WHERE)으로 사용하도록 수정함.

### 3.3 예외 처리 추가
- `verify_and_finalize` 호출 시 파일이 이미 존재하지 않거나 처리된 경우를 대비한 `try-except` 및 파일 존재 여부 체크(`os.path.exists`) 로직을 강화함.

---

## 4. 기대 효과
- 다수의 센서 노드가 동시에 동일한 이름의 이미지 파일을 보내더라도 데이터 섞임(Corruption) 없이 개별적으로 수집 가능.
- 시스템 크래시 방지 및 가동 중단 시간(Downtime) 최소화.
