# ESP PI Simulator 기술 개요

## 1. 목적과 범위

`esp_pi_simulator/` 디렉터리는 Raspberry Pi 기반 이동형 AP가 ESP 센서 노드로부터 데이터를 회수하는 과정을 로컬에서 재현하기 위한 테스트 하네스입니다. 실제 무선 환경 없이도 프로토콜 시퀀스, 슬롯 기반 스케줄러, 세션 복원 로직을 빠르게 검증할 수 있도록 구성되어 있습니다.

## 2. 구성 요소 맵

| 구성 요소          | 핵심 역할                                                    | 비고                                         |
| ------------------ | ------------------------------------------------------------ | -------------------------------------------- |
| `common.py`        | TCP 스트림 상에서 줄 단위 JSON 메시지를 송수신하는 공용 헬퍼 | `ensure_ascii=False`로 한글 메시지도 지원    |
| `config.py`        | 네트워크·청크·슬롯·DB 파라미터의 단일 소스                   | ESP와 PI가 동일 상수를 사용하도록 중앙집중화 |
| `esp_node.py`      | ESP32 노드를 모사하는 asyncio 서버                           | 헨드셰이크→데이터 전송→COMPLETE 알림 구현    |
| `pi_controller.py` | Raspberry Pi 컨트롤러 시뮬레이터                             | 라운드 로빈 슬롯 스케줄링 + SQLite 상태 관리 |
| `init_db.py`       | `data/sessions.db` 스키마 초기화                             | `transfers`, `contact_logs` 두 테이블 생성   |
| `run_esps.sh`      | 10개의 ESP 노드를 병렬로 기동                                | 각 노드별 포트·총 청크 수 설정               |
| `data/`            | 런타임 SQLite 파일 저장 위치                                 | Git 제외 대상 │                              |

## 3. ESP 노드 동작 (`esp_node.py`)

1. **리스닝**: `asyncio.start_server`로 `HOST`/`port`에서 연결을 수신하며, 인스턴스마다 `node_id`, `data_id`, `total_chunks`를 보유합니다.
2. **프로토콜 시퀀스**: Pi로부터 `HELLO`를 받으면 `HELLO_ACK → NODE_INFO → DATA_INFO`를 순차 전송합니다. 메시지 스키마는 `docs/protocol_spec.md`와 동일합니다.
3. **데이터 스트림**: `REQUEST_TRANSFER(start_chunk, max_chunks)`를 받은 뒤, `make_chunk_payload`가 생성한 고정 길이 페이로드를 포함한 `CHUNK` 메시지를 순차 송신합니다.
4. **ACK 처리**: 각 청크마다 `recv_json`으로 ACK/STOP을 확인하며, 순번 불일치 시 `ERROR`를 리턴하고 루프를 종료합니다.
5. **완료 알림**: `current >= total_chunks`에 도달하면 `COMPLETE` 메시지로 세션 종료를 알립니다.

## 4. Raspberry Pi 컨트롤러 동작 (`pi_controller.py`)

1. **DB 추상화**: `transfers(node_id, data_id)`에는 `total_chunks`, `last_received_chunk`, `state`(PAUSED/COMPLETED)를 저장하고, `contact_logs`에는 슬롯 단위 수신 통계를 기록합니다.
2. **슬롯 제어**: `SLOT_MAX_CHUNKS=5`, `SLOT_MAX_SECONDS=2.0`의 하이브리드 제한을 적용하여 특정 노드가 채널을 독점하지 못하게 합니다.
3. **연결 플로우**: `visit_esp(i)`가 포트 `BASE_PORT + i - 1`로 접속 → 헨드셰이크 검증 → DB 기준 `start_chunk` 계산 → `REQUEST_TRANSFER` 발송 → CHUNK 수신 시마다 ACK 전송 및 DB 업데이트.
4. **STOP 조건**: 시간 초과, 청크 한도 도달, COMPLETE 이벤트, ESP 오류 시 각각 STOP을 송신하고 종료 사유를 `contact_logs`에 남깁니다.
5. **라운드 로빈 루프**: `main()`은 무한 라운드를 돌면서 `ESP_COUNT` 노드를 방문하고, `all_completed()`가 true일 때 종료합니다.

## 5. 실행 절차

1. `cd esp_pi_simulator && python3 init_db.py` → SQLite 스키마 초기화
2. `bash run_esps.sh` → 포트 9001~9010에 ESP 노드 10개 기동
3. 별도 터미널에서 `python3 pi_controller.py` → 컨트롤러 구동
4. 진행 상태 확인: `sqlite3 data/sessions.db` 접속 후 `SELECT * FROM transfers;` 실행
