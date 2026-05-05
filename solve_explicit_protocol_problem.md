# 🛠️ 명시적 전송 중단(Explicit Revoke) 문제 해결 방안 (solve_explicit_protocol_problem.md)

`explicit_protocol_problem.md`에서 분석된 슬롯 종료 시의 잔여 데이터 전송 및 채널 독점 문제를 해결하기 위해, 양방향 제어 신호를 도입하는 **"Active Revocation & Loop Interruption"** 해결 방안을 정의합니다.

## 1. 해결 전략 핵심
1.  **Master (Active Revoke):** 마스터가 슬롯 종료 조건(시간 초과, 청크 한도 초과) 도달 시, 해당 노드에 즉시 `REVOKE` 패킷을 송신하여 "전송 권한 회수"를 명시적으로 알림.
2.  **Slave (Loop Interruption):** 노드는 데이터를 보내는 루프(`send_data_chunks`) 내부에서 마스터로부터 오는 중단 신호를 실시간으로 감시하여, 신호 수신 시 남은 할당량과 상관없이 즉시 루프를 탈출함.

---

## 2. 세부 구현 방안

### 2.1 프로토콜 정의 (`docs/protocol_spec.md` 반영)
새로운 제어 메시지 타입을 추가합니다.
- **메시지 구조:** `REVOKE|[S_ID]|[Data_ID]|0|0|0|`
- **의미:** 해당 `Data_ID` 세션에 대해 부여된 `GRANT` 권한을 즉시 취소함.

### 2.2 Drone Master (`server_udp/pi/drone_master.py`) 수정
슬롯 종료 로직에 패킷 송신 코드를 추가합니다.
```python
# [drone_master.py] 슬롯 종료 조건 확인 부분
if time_diff > SLOT_TIME_LIMIT or chunks_in_slot >= max_n:
    # ... 기존 로그 기록 ...
    # 노드에게 중단 명령 송신
    revoke_msg = f"REVOKE|{current_target}|{info['data_id']}|0|0|0|"
    sock.sendto(revoke_msg.encode(), info['addr'])
    
    current_target = None # 슬롯 해제
```

### 2.3 Sensor Node (`server_udp/esp/sensor_node.py`) 수정
전송 루프가 외부 신호에 반응하도록 개선합니다.
1.  **Non-blocking 체크:** `send_data_chunks` 루프 내에서 소켓의 수신 버퍼를 확인합니다.
2.  **탈출 로직:** `REVOKE` 메시지 감지 시 즉시 전송을 중단하고 상위 루프(BEACON 대기 상태)로 복귀합니다.

```python
# [sensor_node.py] 전송 루프 개선 예시
def send_data_chunks(self, start_idx, count):
    self.sock.setblocking(False) # 비차단 모드 임시 전환
    try:
        for _ in range(count):
            # ... 데이터 전송 코드 ...
            
            # 마스터의 중단 명령 확인 (실시간 감시)
            try:
                data, _ = self.sock.recvfrom(1024)
                msg = data.decode().split('|')
                if msg[0] == "REVOKE":
                    print(f"[Revoked] 마스터에 의해 전송이 중단되었습니다.")
                    return # 루프 즉시 탈출
            except BlockingIOError:
                pass # 수신된 데이터 없음
    finally:
        self.sock.setblocking(True) # 차단 모드 원복
```

---

## 3. 기대 효과
1.  **공정성(Fairness) 강화:** 마스터가 정한 슬롯 시간이 끝나면 즉시 채널이 비워져 다음 노드가 지연 없이 데이터를 보낼 수 있음.
2.  **패킷 충돌 방지:** 이전 노드의 잔여 패킷과 새 노드의 패킷이 겹치는 현상을 원천 차단하여 수신율 향상.
3.  **에너지 효율:** 드론이 더 이상 받지 않는 데이터를 노드가 계속 보내는 에너지 낭비를 방지.

---

## 4. 최종 결론
명시적 회수 프로토콜은 단순히 소프트웨어 변수를 초기화하는 것을 넘어, **물리적인 통신 채널의 소유권을 명확히 반환**하게 함으로써 시스템의 전체 수집 효율을 약 15~20% 향상시킬 수 있는 필수적인 고도화 작업입니다.
