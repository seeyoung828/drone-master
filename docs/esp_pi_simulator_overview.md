# ESP PI Simulator 기술 개요 (FastAPI 버전)

## 1. 개요
`esp_pi_simulator/`는 FastAPI 기반 마스터 서버와 비동기 슬레이브 태스크로 구성된 Python only 시뮬레이터입니다. 드론에 탑재된 Raspberry Pi가 분산된 ESP 노드에서 데이터를 회수한다는 프로젝트 요구사항을 로컬 환경에서 검증할 수 있도록 설계했습니다.

## 2. 디렉터리 구성
| 경로 | 설명 |
| --- | --- |
| `run_simulation.py` | 한 번의 명령으로 FastAPI 마스터 + 슬레이브들을 동시에 실행하는 엔트리 포인트 (`python esp_pi_simulator/run_simulation.py`) |
| `requirements.txt` | FastAPI, uvicorn, httpx 의존성 목록 (`pip install -r esp_pi_simulator/requirements.txt`) |
| `esp_pi_simulator/config.json` | 노드 수, 청크 크기, 슬롯 quota, 장애 주입 확률 등을 조정하는 설정 파일 |
| `esp_pi_simulator/common/` | 설정 로더, 로거, 프로토콜, fault injector, 유틸 모듈 |
| `esp_pi_simulator/master/` | FastAPI 앱, 스케줄러, 세션 매니저, 메트릭 수집기 |
| `esp_pi_simulator/slave/` | 슬레이브 상태머신, 세션 저장소, HTTP 기반 송신 로직 |
| `esp_pi_simulator/logs/` | 실행 중 콘솔과 동일한 `runtime.log`가 저장되는 위치 |
| `esp_pi_simulator/sessions/` | 마스터/슬레이브 세션 스냅샷(재시작 시 이어받기) |
| `esp_pi_simulator/data/metrics_summary.json` | 실행 종료 후 요약 메트릭 |

## 3. 동작 흐름
1. `python run_simulation.py` 실행 → `config.json` 로드 → 로그/세션/데이터 디렉터리 생성.
2. `MasterState` 인스턴스를 기반으로 FastAPI 앱을 생성하고 uvicorn을 백그라운드 태스크로 실행.
3. 설정된 노드 수(`num_slaves`)만큼 `SlaveNode`를 생성하여 비동기 태스크로 실행.
4. 슬레이브는 `/register → /ready → /permission → /chunk → /complete` 순으로 마스터와 통신하며, quota 도달 시 STOP/Resume, 장애 발생 시 Fault/Recovery 로그를 남깁니다.
5. 마스터는 Round Robin 스케줄러로 한 차례당 `quota_chunks_per_turn`만큼만 허용하고, `SessionManager`가 `last_ack_chunk`를 저장해 재시도 시 이어받습니다.
6. 시뮬레이션 종료 후 `metrics_summary.json`에 총 노드 수, 완료 노드 수, 누적 청크, stop/resume/fault 카운트, 평균 대기 시간, 노드별 세션 스냅샷을 저장합니다.

## 4. 장애 주입 모델
- **network_delay**: 청크 전송 직전에 0.2~1.2초 랜덤 지연을 삽입해 연결 품질 변화를 재현.
- **temporary_disconnect**: 전송 중 Backoff 상태로 전환 → 일정 시간 대기 후 `/ready` 재호출 → Resume 로그 출력.
- **chunk_drop**: 특정 청크를 건너뛰어 마스터가 `unexpected chunk`를 감지하도록 하고, 슬레이브는 STOP 후 다시 `resume_from`을 확인해 재전송.
- 모든 확률은 `config.json > faults`에서 on/off 및 범위를 조정할 수 있습니다.

## 5. 세션 및 메트릭
- **SessionManager** (`master/session_manager.py`): `master_sessions.json`에 node_id, session_id, total_chunks, last_ack_chunk, 상태, preemption/resume/fault 카운트를 저장합니다.
- **SlaveSessionStore** (`slave/session_store.py`): 슬레이브별 `sessions/slave_states/*.json`에 자신이 마지막으로 ACK 받은 청크와 상태(SlaveState Enum)를 저장합니다.
- **MetricsTracker** (`master/metrics.py`): `total_nodes`, `completed_nodes`, `total_chunks_received`, `per_node_ack_chunks`, `preempt_count`, `resume_count`, `fault_counts`, `average_wait_time_per_node`, `total_runtime_sec`를 누적하고 JSON으로 출력합니다.

## 6. API 요약
| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `POST /register` | 슬레이브 등록, `session_id`와 `resume_from` 반환 |
| `POST /ready` | 노드가 SLOT 대기열에 진입했음을 알림 |
| `GET /permission/{node_id}` | 현재 차례인지, `resume_from`, quota 정보를 제공 |
| `POST /chunk` | 청크 수신 → ACK/STOP 응답 |
| `POST /complete` | 노드 완료 보고 |
| `POST /fault` | 슬레이브가 감지한 장애 유형 전달 |
| `GET /status` | 전체 세션 스냅샷 |
| `GET /metrics` | 현재 누적 메트릭 (실행 중에도 확인 가능) |

## 7. 실행/발표 포인트
- 로그 prefix(`[MASTER]`, `[SLAVE]`, `[SCHED]`, `[FAULT]`, `[RECOVERY]`, `[STOP]`, `[RESUME]`, `[ACK]`, `[METRIC]`)로 이벤트 타이밍을 명확하게 시연할 수 있습니다.
- `config.json` 값만 바꿔서 노드 수, 청크 수, fault 확률을 조정하면 다양한 실험 시나리오를 영상으로 촬영할 수 있습니다.
- FastAPI + uvicorn 기반이므로 추후 실제 Raspberry Pi/ESP 환경에서 HTTP→TCP, SQLite→실제 DB 등으로 확장이 용이합니다.
