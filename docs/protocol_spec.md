# 프로토콜 명세서

## 1. 문서 목적

본 문서는 Raspberry Pi가 이동형 수집 노드 역할을 수행하고, 여러 개의 ESP 모듈이 고정된 센서 노드로 배치된 환경에서 데이터를 수집하기 위한 통신 프로토콜을 정의한다.

본 프로토콜의 목적은 다음과 같다.

1. Raspberry Pi가 여러 ESP와 순차적으로 통신할 수 있어야 한다.
2. 한 번의 접촉에서 전송이 끝나지 않아도 이후 재접촉 시 이어받을 수 있어야 한다.
3. 특정 ESP가 장시간 통신을 점유하지 않도록 슬롯 종료가 가능해야 한다.
4. 메시지 구조가 명확하여 디버깅과 구현이 쉬워야 한다.

---

## 2. 시스템 역할

### 2.1 Raspberry Pi
Raspberry Pi는 마스터(master) 역할을 수행한다.

주요 역할:
- ESP 탐색
- 연결 시작
- 노드 정보 확인
- 전송 시작 위치 결정
- 청크 수신
- ACK 전송
- STOP 전송
- 세션 상태 저장

### 2.2 ESP
ESP는 슬레이브(slave) 역할을 수행한다.

주요 역할:
- 연결 요청 수락
- 노드 정보 제공
- 데이터 정보 제공
- 요청된 시작 위치부터 청크 전송
- ACK 확인
- STOP 수신 시 전송 중단

---

## 3. 기본 원칙

### 3.1 통신 방식
본 프로토콜은 Wi-Fi 기반 TCP 통신을 전제로 한다.

### 3.2 제어 주체
세션 제어는 Raspberry Pi가 수행한다.  
ESP는 Raspberry Pi의 요청에 따라 응답한다.

### 3.3 전송 단위
모든 데이터는 청크 단위로 전송한다.

### 3.4 수신 성공 기준
Raspberry Pi가 청크를 정상 저장하고 ACK를 보낸 경우에만 해당 청크를 수신 성공으로 간주한다.

### 3.5 복원 기준
세션 복원은 마지막으로 ACK된 청크 번호를 기준으로 한다.

---

## 4. 메시지 형식

초기 구현에서는 JSON 문자열 1개 = 메시지 1개 구조를 사용한다.

모든 메시지는 UTF-8 인코딩된 JSON 문자열로 전송한다.

### 4.1 공통 필드
메시지에 따라 아래 필드를 포함할 수 있다.

- `type`: 메시지 종류
- `node_id`: ESP 식별자
- `data_id`: 데이터 식별자
- `timestamp`: 메시지 생성 시각
- `seq`: 선택적 메시지 순번

초기 구현에서는 `type`, `node_id`, `data_id`를 중심으로 사용한다.

---

## 5. 메시지 정의

## 5.1 HELLO

### 목적
Raspberry Pi가 ESP와의 세션 시작을 알림

### 송신자
Raspberry Pi

### 수신자
ESP

### 필드
- `type`
- `pi_id`

### 예시
`{"type":"HELLO","pi_id":"pi_01"}`

---

## 5.2 HELLO_ACK

### 목적
ESP가 HELLO를 정상 수신했음을 응답

### 송신자
ESP

### 수신자
Raspberry Pi

### 필드
- `type`
- `node_id`

### 예시
`{"type":"HELLO_ACK","node_id":"esp_03"}`

---

## 5.3 NODE_INFO

### 목적
ESP의 기본 정보를 제공

### 송신자
ESP

### 수신자
Raspberry Pi

### 필드
- `type`
- `node_id`
- `status`

### 선택 필드
- `firmware_version`

### 예시
`{"type":"NODE_INFO","node_id":"esp_03","status":"READY"}`

---

## 5.4 DATA_INFO

### 목적
ESP가 현재 전송 가능한 데이터의 메타정보를 제공

### 송신자
ESP

### 수신자
Raspberry Pi

### 필드
- `type`
- `node_id`
- `data_id`
- `total_chunks`

### 선택 필드
- `remaining_chunks`
- `data_size`
- `version`
- `checksum`

### 예시
`{"type":"DATA_INFO","node_id":"esp_03","data_id":"log_01","total_chunks":120}`

---

## 5.5 REQUEST_TRANSFER

### 목적
Raspberry Pi가 특정 데이터의 전송 시작 또는 재개를 요청

### 송신자
Raspberry Pi

### 수신자
ESP

### 필드
- `type`
- `node_id`
- `data_id`
- `start_chunk`

### 선택 필드
- `max_chunks`
- `slot_time_ms`

### 예시
`{"type":"REQUEST_TRANSFER","node_id":"esp_03","data_id":"log_01","start_chunk":24,"max_chunks":12}`

---

## 5.6 CHUNK

### 목적
ESP가 실제 데이터 청크를 전송

### 송신자
ESP

### 수신자
Raspberry Pi

### 필드
- `type`
- `node_id`
- `data_id`
- `chunk_index`
- `payload`

### 선택 필드
- `payload_size`
- `chunk_checksum`

### 예시
`{"type":"CHUNK","node_id":"esp_03","data_id":"log_01","chunk_index":24,"payload":"..."}`

---

## 5.7 ACK

### 목적
Raspberry Pi가 청크를 정상 수신했음을 응답

### 송신자
Raspberry Pi

### 수신자
ESP

### 필드
- `type`
- `node_id`
- `data_id`
- `chunk_index`

### 예시
`{"type":"ACK","node_id":"esp_03","data_id":"log_01","chunk_index":24}`

---

## 5.8 STOP

### 목적
현재 슬롯 종료로 인해 전송을 일시 중단하도록 지시

### 송신자
Raspberry Pi

### 수신자
ESP

### 필드
- `type`
- `reason`

### 선택 필드
- `last_acked_chunk`

### 예시
`{"type":"STOP","reason":"slot_end","last_acked_chunk":35}`

---

## 5.9 COMPLETE

### 목적
전체 데이터 전송 완료를 알림

### 송신자
Raspberry Pi 또는 ESP

### 수신자
상대 노드

### 필드
- `type`
- `node_id`
- `data_id`

### 예시
`{"type":"COMPLETE","node_id":"esp_03","data_id":"log_01"}`

---

## 5.10 ERROR

### 목적
오류 상황을 알림

### 송신자
양측 모두 가능

### 수신자
상대 노드

### 필드
- `type`
- `code`
- `message`

### 예시
`{"type":"ERROR","code":"INVALID_DATA_ID","message":"unknown data id"}`

### 대표 오류 코드 예시
- `INVALID_DATA_ID`
- `INVALID_CHUNK_INDEX`
- `BUSY`
- `CHECKSUM_FAIL`
- `TIMEOUT`
- `INTERNAL_ERROR`

---

## 6. 전체 통신 절차

## 6.1 초기 접속 절차
1. Raspberry Pi가 ESP에 TCP 연결
2. Raspberry Pi → ESP: `HELLO`
3. ESP → Raspberry Pi: `HELLO_ACK`
4. ESP → Raspberry Pi: `NODE_INFO`
5. ESP → Raspberry Pi: `DATA_INFO`

---

## 6.2 전송 시작 절차
1. Raspberry Pi가 DB에서 `last_received_chunk` 조회
2. 시작 청크 번호 결정
3. Raspberry Pi → ESP: `REQUEST_TRANSFER`
4. ESP가 지정된 청크부터 `CHUNK` 전송
5. Raspberry Pi가 각 청크마다 `ACK` 전송

---

## 6.3 슬롯 종료 절차
다음 중 하나가 발생하면 현재 슬롯 종료:
- 최대 시간 T 도달
- 최대 청크 수 N 도달
- 링크 오류
- 전체 완료

종료 시:
1. Raspberry Pi가 마지막 ACK 청크 저장
2. Raspberry Pi → ESP: `STOP`
3. 연결 종료 또는 다음 노드로 이동

---

## 6.4 재접촉 절차
1. Raspberry Pi가 동일 ESP에 다시 접속
2. `HELLO` / `HELLO_ACK`
3. `NODE_INFO` / `DATA_INFO`
4. Raspberry Pi가 이전 세션 조회
5. `last_received_chunk + 1` 계산
6. 해당 위치부터 다시 `REQUEST_TRANSFER`

---

## 7. 청크 처리 규칙

### 7.1 청크 번호
모든 청크는 0부터 시작하는 정수 인덱스를 가진다.

### 7.2 수신 성공 기준
Raspberry Pi가 청크를 저장하고 ACK를 보냈을 때만 성공으로 인정한다.

### 7.3 중복 청크
이미 ACK 완료한 청크가 다시 도착하면 중복 청크로 간주한다.  
필요 시 ACK만 재전송하고 데이터는 다시 저장하지 않는다.

### 7.4 순서 오류
현재 기대하는 청크보다 큰 번호의 청크가 먼저 오면 순서 오류로 간주한다.  
초기 구현에서는 순차 전송만 허용한다.

---

## 8. 타임아웃 및 재전송 규칙

### 8.1 CHUNK 타임아웃
ESP가 일정 시간 내 다음 청크를 보내지 않으면 Raspberry Pi는 링크 이상으로 판단할 수 있다.

### 8.2 ACK 타임아웃
ESP는 CHUNK 전송 후 일정 시간 내 ACK를 받지 못하면 재전송한다.

### 8.3 재전송 횟수
초기 구현에서는 최대 3회 재전송을 권장한다.

### 8.4 재전송 초과
재전송 횟수를 초과하면 현재 슬롯을 종료하고 다음 접촉에서 이어받는다.

---

## 9. 슬롯 제어 규칙

Raspberry Pi는 다음 값을 유지해야 한다.

- 현재 슬롯 시작 시각
- 현재 슬롯 수신 청크 수
- 최대 슬롯 시간 T
- 최대 청크 수 N

다음 중 하나를 만족하면 STOP 전송:
- 현재 시각 - 슬롯 시작 시각 ≥ T
- 현재 슬롯 수신 청크 수 ≥ N
- 전체 데이터 수신 완료

---

## 10. 데이터 일관성 규칙

### 10.1 data_id 변경
이전 세션과 다른 `data_id`가 보고되면 새로운 데이터 세션으로 간주한다.

### 10.2 total_chunks 변경
동일 `data_id`인데 `total_chunks`가 변경되면 기존 세션을 무효화하고 새 세션으로 본다.

### 10.3 checksum
초기 구현에서는 선택 사항으로 두고, 필요 시 전체 데이터 완료 후 검증한다.

---

## 11. 상태 정의

각 `node_id + data_id` 조합은 다음 상태를 가진다.

- `DISCOVERED`
- `CONNECTED`
- `TRANSFERRING`
- `PAUSED`
- `RESUMING`
- `COMPLETED`
- `FAILED`

---

## 12. 권장 초기 파라미터

- 청크 크기: 256B
- 최대 슬롯 시간 T: 4초
- 최대 청크 수 N: 12
- ACK 타임아웃: 500ms ~ 1초
- 재전송 횟수: 3회

---

## 13. 요약

본 프로토콜은 Raspberry Pi가 여러 ESP와 공정하게 통신하며 데이터를 수집할 수 있도록 설계되었다.  
Raspberry Pi는 세션을 시작하고, 데이터 정보를 확인한 뒤, 마지막으로 ACK한 청크 다음 위치부터 전송을 요청한다.  
데이터는 청크 단위로 순차 전송되며, 슬롯 기반 제한에 따라 중간 종료 후 이후 접촉에서 이어받을 수 있다.
