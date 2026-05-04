# 📡 드론-센서 노드 통신 프로토콜 명세서 (v3.0)

---

## 1. 문서 목적

본 문서는 드론(Master)과 고정형 센서 노드(Slave) 간의 이미지 데이터 수집을 위한 **UDP 기반 상태 기반(Stateful) 비동기 통신 규격**을 정의한다.

v3.0 사양은 **전송 지연 제거**를 위해 노드 측의 RSSI 전송을 폐지하고 **Master-Side RSSI Tracking** 방식을 채택하였다.

---

## 2. 통신 계층 및 방식 (동일)

- **L4 프로토콜**: UDP (User Datagram Protocol)
- **통신 모델**: Master-Slave 구조
- **포트 번호**: 5005
- **인코딩**: 헤더(UTF-8), 페이로드(Binary)
- **구분자**: 파이프(`|`)

---

## 3. 메시지 프레임 구조 (v3.0 수정)

모든 패킷은 다음 구조를 가진다:
`[Type]|[S_ID]|[Data_ID]|[Total_Chunks]|[Current_Idx]|[Last_Flag]|[Payload]`

| 필드명 | 설명 | 예시 |
| :--- | :--- | :--- |
| **Type** | 패킷 종류 (BEACON, GRANT, DATA, COMPLETE, ERROR) | DATA |
| **S_ID** | 센서 노드 고유 식별자 | Sensor_01 |
| **Data_ID** | 현재 전송 중인 이미지 세션 ID | 04123045 |
| **Total_Chunks** | 전체 데이터 조각 수 | 248 |
| **Current_Idx** | 현재 조각의 인덱스 (0부터 시작) | 12 |
| **Last_Flag** | 마지막 조각 여부 (0: 중간, 1: 마지막) | 0 |
| **Payload** | 바이너리 데이터 (DATA 타입에만 존재) | [Binary] |

> **참고:** v3.0부터 노드는 RSSI 값을 패킷에 포함하지 않으며, 드론(Master)이 수신 시 직접 측정한다.

---

## 4. 메시지 정의

### 4.4 COMPLETE (Master ↔ Slave)
- **Master의 검증 결과 피드백**: 현재 프로토콜에서는 슬레이브가 `COMPLETE`를 보낸 후 마스터의 `GRANT`가 2초 이상 없거나 새로운 `Data_ID`에 대한 `GRANT`가 오면 성공으로 간주하고 다음 이미지로 전환한다. (검증 실패 시 마스터는 즉시 `ERROR|CHECKSUM_FAIL`을 송신해야 함)

### 4.5 ERROR (공통)
- **Error Codes**
  - `TIMEOUT`: `GRANT` 후 응답 없음 (3회 재시도 초과)
  - `CHECKSUM_FAIL`: 수집 완료 후 CRC32 불일치 (세션 초기화 유도)
  - `SESSION_MISMATCH`: (v2.6 완화) 이전 세션이 비정상 종료된 상태에서 새로운 `Data_ID` 요청 시 발생 가능.
  - `LIVELOCK_PREVENT`: 동일 인덱스 반복 수신 등으로 인한 진행 중단 방지.

---

## 5. 통신 시퀀스

### 5.3 다중 이미지 전환 (Multi-Image Transition)
1. **Current Image**: Slave가 `COMPLETE` 송신.
2. **Verification**: Master는 CRC32 검증 후 `{S_ID}_{Data_ID}.jpg`로 저장.
3. **Next Image**: Slave는 2초 대기 후 큐에서 다음 파일을 꺼내 새로운 `Data_ID`로 `BEACON` 송신.
4. **Session Update**: Master는 `S_ID`는 같으나 `Data_ID`가 바뀐 것을 감지하고 신규 세션 준비(`prepare_session`).

---

## 8. 데이터 수집 및 파일 처리 시퀀스 (v2.6 업데이트)

### 8.1 세션 초기화 및 파일 격리
- **파일명 규칙**: 다중 노드 충돌 방지를 위해 모든 파일은 `{S_ID}_{Data_ID}` 형식을 사용한다.
  - 임시 파일: `{S_ID}_{Data_ID}.tmp`
  - 확정 파일: `{S_ID}_{Data_ID}.jpg`
- **Purge 정책**: 동일 `S_ID`에서 `Data_ID`가 변경되면 마스터는 이전 세션을 자동으로 정리(또는 보존)하고 신규 파일을 생성한다.

### 8.2 비동기 데이터 기록 (Seek-Write)
- 수신된 `Current_Idx`를 기반으로 파일의 `Idx * 4096` 위치에 직접 기록한다. (v2.7: CHUNK_SIZE 4KB)
- **무결성 추적**: DB의 `received_mask` (BLOB)를 통해 조각 단위 수신 여부를 관리하며, 누락된 조각(Hole)은 다음 `GRANT`의 `Start_Idx`를 통해 우선 요청한다.

---

## 9. 에러 제어 및 재시도 규칙

### 9.1 재시도 (Retry)
- `GRANT` 후 **1.0초**(v2.6 조정) 내 응답 없을 시 재시도.
- 최대 3회 수행 후 해당 노드에 30초간 패널티(스케줄링 제외) 부여.

### 9.4 무결성 검증 실패 시
- 마스터는 `ERROR|CHECKSUM_FAIL`을 슬레이브에게 알리고, DB 세션을 리셋하여 0번 조각부터 다시 수집을 유도한다.

