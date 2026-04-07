# 📡 드론-센서 노드 통신 프로토콜 명세서 (v2.1)

---

## 1. 문서 목적

본 문서는 드론(Master)과 고정형 센서 노드(Slave) 간의 이미지 데이터 수집을 위한 **UDP 기반 비동기 통신 규격**을 정의한다.

본 규격은 `scheduling_policy.md`의 **하이브리드 슬롯 제한 및 Aging 스케줄링 정책**을 기술적으로 구현하는 것을 목표로 한다.

---

## 2. 통신 계층 및 방식

- **L4 프로토콜**: UDP (User Datagram Protocol)
- **통신 모델**: Master-Slave 구조
  - Master: 드론
  - Slave: 센서 노드
- **포트 번호**: 5005 (기본값)
- **인코딩**
  - 헤더: UTF-8 String
  - 페이로드: Binary
- **구분자**
  - 파이프(`|`)
  - 헤더의 마지막 필드 뒤에 반드시 구분자를 붙여 바이너리 데이터와의 경계를 명확히.

---

## 3. 메시지 프레임 구조

모든 패킷은 다음 구조를 가진다:
[텍스트 헤더] | [바이너리 데이터]

---

### 3.1 공통 헤더 형식

Type | S_ID | Data_ID | Total_Chunks | Current_Idx | RSSI | Last_Flag | [Payload]
| 필드 | 설명 |
| ------------ | -------------------------------------------------- |
| Type | 메시지 유형 (BEACON, GRANT, DATA, COMPLETE, ERROR) |
| S_ID | 센서 노드 식별자 (예: SENSOR_01) |
| Data_ID | 타임스탬프 기반의 8자리 문자열 (예: 04071420) |
| Total_Chunks | 전체 이미지 조각 수 |
| Current_Idx | 현재 전송/요청 조각 인덱스 (0부터 시작) |
| RSSI | 상대방 신호 세기 (dBm, 정수) |
| Last_Flag | 현재 송신하는 `DATA` 패킷이 파일의 마지막 조각인 경우 `1`, 그 외에는 `0`으로 설정한다. |

---

## 4. 메시지 정의

| 메시지 Type | 송신측 | 목적 | 주요 필드 설명 |
| BEACON | Slave | 존재 알림 | 현재 보유한 Data_ID와 Total_Chunks 보고 |
| GRANT | Master | 권한 부여 | 수집 시작점(Start_Idx)과 허용량(Count) 지정 |
| DATA | Slave | 조각 전송 | 1024B 바이너리 페이로드 포함 |
| COMPLETE | Slave | 전송 완료 | 전체 이미지 파일의 CRC32 체크섬 포함 |
| ERROR | 공통 | 오류 보고 | TIMEOUT, CHECKSUM_FAIL 등의 코드 포함 |

### 4.1 BEACON (Slave → Master)

센서 노드가 자신의 상태를 알리기 위해 주기적으로 전송하는 메시지

- **주기**: 0.1초
- **전송 방식**: Broadcast 또는 Unicast

BEACON | S_ID | Data_ID | Total_Chunks | Current_Idx | RSSI

- **목적**
  - 스케줄링 점수(Score) 계산을 위한 기초 데이터 제공

---

### 4.2 GRANT (Master → Slave)

드론이 특정 노드에게 전송 권한과 범위를 부여

GRANT | S_ID | Data_ID | Count | Start_Idx | RSSI

| 필드      | 설명                            |
| --------- | ------------------------------- |
| Start_Idx | `max_idx + 1` (암시적 ACK 역할) |
| Count     | 전송할 청크 수 (동적 가변)      |

---

### 4.3 DATA (Slave → Master)

이미지 조각 데이터 전송 메시지
DATA | S_ID | Data_ID | Total_Chunks | Current_Idx | RSSI | Last_Flag |[Binary_Payload]

헤더의 마지막에 반드시 파이프(|)가 붙는다

- **Payload Size**: 1024 Bytes (고정)

---

### 4.4 COMPLETE (Master ↔ Slave)

전체 데이터 전송 및 검증 완료 알림
COMPLETE | S_ID | Data_ID | 0 | 0 | 0 | CRC32_Checksum

- COMPLETE 메시지의 CRC32 체크섬은 4바이트 바이너리이며, **Network Byte Order(Big Endian)**를 따른다.

---

### 4.5 ERROR (공통)

오류 발생 시 전송
ERROR | S_ID | Data_ID | 0 | 0 | 0 | Error_Code

- **Error Codes**
  - TIMEOUT
  - SIGNAL_LOSS
  - CHECKSUM_FAIL
  - LIVELOCK_PREVENT

---

## 5. 통신 시퀀스 (Sequence Diagram)

### 5.1 정상 수집 시나리오

1. **Discovery**
   - Slave → BEACON 주기적 송신

2. **Scheduling**
   - Master → Score 계산 후 GRANT 송신

3. **Transfer**
   - Slave → Start_Idx부터 Count만큼 DATA 전송

4. **Implicit ACK**
   - 다음 GRANT의 Start_Idx 증가로 수신 확인

---

### 5.2 슬롯 종료 및 재개 (Hybrid Slot Control)

- Master는 다음 조건 시 슬롯 종료
  - 시간 제한 ($T$)
  - 청크 제한 ($N$)

- 상태 저장
  - `last_idx`
  - `last_contact_time`

- 재접촉 시
  - Aging 반영 후 우선순위 상승
  - `last_idx + 1`부터 재개

---

## 6. 파라미터 규격 (Fixed for Test)

| 항목                | 값         | 비고         |
| ------------------- | ---------- | ------------ |
| Chunk Size          | 1024 Bytes | UDP MTU 고려 |
| Max Slot Time ($T$) | 5.0 sec    | 정책 v2.1    |
| Max Chunks ($N$)    | 20 (Base)  | 10~40 가변   |
| Retry Limit         | 3          | 이후 TIMEOUT |
| Aging Threshold     | 60 sec     | 최대 가산점  |

---

## 7. 상태 정의 및 동기화 (State Management)

정책 문서와의 일치성을 위해 시스템 상태를 **'드론의 동작 상태'**와 **'데이터 세션 상태'**로 분리하여 관리한다.

### 7.1 Master(드론) 프로그램 동작 상태

드론 소프트웨어의 메인 루프에서 제어하는 상태

- SCANNING: 비콘을 수신하며 각 노드의 스케줄링 점수를 계산하는 상태.

- REQUESTING: 특정 노드에 GRANT를 보낸 후 첫 DATA 패킷을 기다리는 상태.

- RECEIVING: 슬롯 종료 전까지 DATA 패킷을 연속적으로 수신하여 저장하는 상태.

- VERIFYING: COMPLETE 수신 후 .tmp 파일의 무결성을 검사하는 상태.

---

### 7.2 데이터 세션 관리 상태 (DB/Memory 저장)

각 S_ID + Data_ID 조합별로 기록되는 논리적 수집 진척도이다.

- IDLE: 비콘만 확인되었고 수집 이력이 없는 상태.

- COLLECTING: 현재 드론이 이 세션을 활발히 수집 중인 상태.

- PAUSED: 슬롯 제한(시간/개수)으로 인해 중단되었으나 이어받기가 필요한 상태.

- COMPLETED: 체크섬 검증이 완료되어 .jpg 파일로 확정된 최종 상태.

- TIMEOUT: 재시도 횟수 초과로 인해 통신이 일시 불가능한 상태.

---

### 8. 데이터 수집 및 파일 처리 시퀀스 (상세)

1. 세션 초기화 및 파일 생성

- 드론(Master)은 수신된 Data_ID가 기존 수집 이력에 존재하지 않는 신규 데이터일 경우, 즉시 Data_ID.tmp 파일을 생성하여 수집 세션을 초기화한다. 만약 동일한 Data_ID 내에서 Total_Chunks가 변경되는 비정상 동작이 감지될 경우, 데이터의 변형으로 간주하여 기존 .tmp 파일을 즉시 삭제(Purge)하고 세션을 초기화하고고 새롭게 수집을 시작한다.

2. 비동기 데이터 기록

- 수신된 DATA 메시지의 Current_Idx를 기반으로, 파일 시스템 내의 절대 위치(Idx \* 1024)를 계산하여 바이너리 데이터를 직접 기록한다.
- 또한, 수신된 UDP 패킷에서 헤더 길이를 뺀 실제 페이로드 크기만큼만 파일에 기록한다
- 이때 드론은 Livelock 방지를 위해 수신된 인덱스가 기존에 기록된 max_idx보다 큰 경우에만 파일 쓰기를 수행하며 세션 정보를 갱신한다.

3. 원자적 전송 보장 및 임시 저장

- 모든 데이터 청크가 완전히 수집될 때까지 해당 파일은 .tmp 확장자를 유지한다. 이는 불완전한 데이터가 완성된 이미지로 오인되어 상위 애플리케이션에서 처리되는 것을 방지하기 위한 격리 조치이다.

4. 종료 판단

- `Last_Flag == 1`이 포함된 패킷을 수신하면 드론은 해당 슬롯을 즉시 종료하고 `VERIFYING` 상태로 전환한다.

5. 무결성 검증 및 전송 확정

- 데이터 수집이 종료되면 Slave로부터 전달된 4바이트 바이너리 형태의 CRC32 체크섬과 드론이 수집된 임시 파일을 통해 계산한 체크섬 값을 대조한다. 두 값이 완벽히 일치할 경우에만 원자적(Atomic) 연산을 통해 확장자를 .jpg로 변경하며, 이를 통해 데이터의 완결성을 최종적으로 확정한다.

---

## 9. 에러 제어 및 재시도 규칙

### 9.1 재시도 (Retry)

- GRANT 후 0.3초 내 응답 없을 시 재시도
- 최대 3회 수행 후 TIMEOUT 처리

---

### 9.2 역행 방지 (Livelock Prevention)

- **Master**
  - `idx > max_idx`인 경우만 저장

- **Slave**
  - 다음 로직 유지
    current_idx = max(start_from, max_sent_idx + 1)

---

### 9.3 동일 Data_ID, 다른 Total_Chunks

- 동일 Data_ID에서 Total_Chunks가 변경되면 기존 세션을 파기하고 새로 시작한다

---

### 9.4 무결성 검증

- 전체 수집 완료 후 CRC32 수행
- COMPLETE 패킷의 Checksum과 비교

---
