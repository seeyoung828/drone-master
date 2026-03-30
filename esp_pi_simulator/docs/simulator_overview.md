# ESP-PI 시뮬레이터 쉽게 이해하기

## 1. 무엇을 보여주는 시뮬레이터인가요?
- 드론에 탑재된 Raspberry Pi가 이동형 AP가 되어 고정된 ESP 노드 데이터를 수집하는 상황을 하나의 PC에서 재현합니다.
- 마스터: FastAPI 서버 (`master/app.py`). 슬레이브: httpx 기반 비동기 클라이언트 (`slave/slave.py`).
- 실행 명령 한 번 (`python esp_pi_simulator/run_simulation.py`)으로 마스터와 여러 슬레이브가 동시에 동작하면서 로그를 남깁니다.

## 2. 스케줄링 방식 (Round Robin + 슬롯 quota)
1. 모든 슬레이브가 `/register` → `/ready` 를 호출하면 세션 매니저가 `READY` 상태로 저장합니다.
2. 스케줄러는 `eligible` 노드 목록을 돌면서 차례대로 하나씩 활성화합니다.
3. 한 슬롯에서 최대 `quota_chunks_per_turn`(기본 4개)만 전송할 수 있고, 초과하면 `[STOP][node_x] quota reached` 로그와 함께 강제로 중단합니다.
4. STOP 후에는 다른 노드가 차례를 가져가고, 자기 차례가 다시 오면 `resume_from` 포인터(마지막 ACK + 1)부터 이어서 전송합니다.
5. `per_node_total_chunks` 를 채우면 `[MASTER][node_x] transfer complete`와 함께 스케줄 대상에서 제외됩니다.

## 3. 장애 주입 모델
| 장애 유형 | 언제 발생? | 로그 예시 | 처리 방식 |
| --- | --- | --- | --- |
| `network_delay` | 청크 전송 직전 | `[FAULT][node_5] network_delay before chunk 4: 1.10s` | 해당 시간만큼 `asyncio.sleep`, 이후 정상 진행 |
| `temporary_disconnect` | 청크를 보낸 직후 확률적으로 | `[FAULT][node_2] temporary_disconnect at chunk 8` → `[RECOVERY][node_2] reconnecting after 1.2s` | 상태를 `BACKOFF`로 바꾸고 `/fault` 알림 → 지정된 backoff 이후 `/ready` 재진입 |
| `chunk_drop` | 전송하려는 특정 청크 | `[FAULT][node_5] chunk_drop injected for chunk 4` | 마스터가 `[MASTER][node_5] unexpected chunk 5 (expected 4)` 로그로 감지 → 슬레이브는 STOP 후 다시 `resume_from` 위치에서 재전송 |

`config.json > faults` 섹션에서 확률과 지연 범위를 즉시 조정할 수 있습니다.

## 4. 로그 해석 가이드
| Prefix | 의미 | 설명 |
| --- | --- | --- |
| `[MASTER]` | 마스터 이벤트 | 서버 시작, 예상치 못한 메시지, 완료 알림 등 핵심 관리 이벤트 |
| `[SLAVE]` | 슬레이브 라이프사이클 | 노드 시작/등록/준비/종료, 청크 재생성 정보 |
| `[SCHED]` | 스케줄러 결정 | 이번 슬롯에서 활성화된 노드 (`selected node_x`) |
| `[ACK]` | 청크 수신 확인 | `[ACK][node_3] chunk 12 acknowledged` 처럼 마스터와 슬레이브 모두에서 표시 |
| `[STOP]` | 슬롯 선점 종료 | quota 도달, STOP 신호 전송, 슬레이브에서 `quota stop signaled` 로그 |
| `[RESUME]` | (향후 확장) 재개 이벤트 | 현재 버전에선 `SessionManager.mark_active` 호출 시 내부 카운터만 증가하며, Prefix는 필요 시 추가 가능 |
| `[FAULT]` | 장애 주입 | network_delay / temporary_disconnect / chunk_drop 발생 로그 |
| `[RECOVERY]` | 장애 복구 | temporary_disconnect 이후 백오프가 끝나 다시 `/ready` 하는 시점 |
| `[METRIC]` | 실행 요약 | 모든 노드 완료 후 `summary` JSON 출력 |

## 5. 요약
- 공정한 슬롯 스케줄링: quota 기반 STOP/Resume으로 특정 노드가 독점하지 못하게 함.
- 세션 복원: `sessions/*.json`에 마지막 ACK가 저장되어 중단/재시작에도 이어받을 수 있음.
- 장애 상황 재현: `config.json`을 조정하며 다양한 무선 품질 이슈를 영상으로 시연 가능.
- 로그 기반 데모: `[MASTER]`, `[FAULT]`, `[STOP]`, `[RECOVERY]` 등 Prefix로 흐름을 즉시 파악할 수 있어 발표용으로 적합.
