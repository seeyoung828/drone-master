# ✅ 루프 무한 대기 문제 해결 가이드 (Loop Error Solve)

이 문서는 `loop_error.md`에서 분석된 **이미지 부재 시 연결 종료 감지 불능 문제**를 해결하기 위한 구체적인 방법론과 수정 절차를 다룹니다.

---

## 1. 해결 전략 (Solution Strategy)

### 1.1 대기 모드와 연결 모니터링의 통합
이미지가 없는 상태(Waiting)를 단순한 `sleep` 루프가 아닌, **네트워크 가용성을 확인하는 상태**로 정의합니다. 이미지가 없더라도 드론 AP와의 통신이 유효한지 주기적으로 확인합니다.

### 1.2 "IDLE 비콘" 메커니즘 도입
전송할 데이터가 없을 때 전용 `Data_ID` (예: `IDLE`) 또는 `None` 상태의 비콘을 송신하여 소켓 레벨의 네트워크 에러를 강제로 발생시킵니다. 이를 통해 연결 끊김을 즉시 인지할 수 있습니다.

---

## 2. 구체적인 수정 절차 (Step-by-Step Implementation)

### Step 1: `send_beacon` 메서드 고도화
이미지가 없는 경우에도 안전하게 비콘을 보낼 수 있도록 필드값을 조정합니다.

- `data_id`가 `None`인 경우 `"IDLE"` 문자열 송신.
- `total_chunks`와 `current_idx`를 `0`으로 설정.

### Step 2: `run()` 메서드 메인 루프 구조 개편
기존의 `continue` 기반 대기 루프를 제거하고, 모든 상태에서 비콘 송신을 시도하도록 통합합니다.

1. **상태 체크:** 큐를 스캔하고 전송할 이미지를 준비합니다.
2. **연결 확인 (통합 비콘):** 이미지가 있든 없든 `send_beacon()`을 호출합니다.
3. **네트워크 에러 처리:** 비콘 송신 실패 시 "연결 끊김" 로그를 출력하고 재접속 대기 모드(Back-off)로 진입합니다.
4. **이미지 부재 시 대기:** 연결은 정상이지만 이미지가 없는 경우에만 짧게 대기 후 루프를 반복합니다.

### Step 3: 마스터(Pi)측 대응 (Idempotency)
마스터가 `"IDLE"` 상태의 비콘을 받았을 때 세션을 생성하지 않고 센서의 실시간 상태(RSSI)만 갱신하도록 `db.prepare_session` 또는 마스터 로직을 보완합니다.

---

## 3. 코드 수정 예상 (Draft)

### `server_udp/esp/sensor_node.py`
```python
def send_beacon(self):
    # data_id가 없으면 IDLE 세션으로 표시
    d_id = self.data_id if self.data_id else "IDLE"
    header = f"BEACON|{self.s_id}|{d_id}|{self.total_chunks}|{self.current_idx}|0|"
    try:
        self.sock.sendto(header.encode(), (DRONE_IP, DRONE_PORT))
        return True
    except Exception as e:
        # 연결 종료 시 여기서 에러 발생
        return False

def run(self):
    while True:
        self._scan_images()
        # 이미지 준비 로직...
        
        # [해결 핵심] 이미지가 없어도 비콘을 통해 연결 확인
        if not self.send_beacon():
            print("[Network Error] 드론 AP와 연결되지 않았습니다. 재접속 대기 중...")
            time.sleep(5)
            continue
            
        if not self.current_image_path:
            print("--- [Waiting] 전송할 이미지가 없습니다. (연결 정상) ---")
            time.sleep(5)
            continue
```

---

## 4. 검증 시나리오 (Verification)

1. **정상 대기:** 이미지가 없을 때 "연결 정상" 로그가 출력되는지 확인.
2. **연결 강제 종료:** Wi-Fi를 껐을 때 즉시 "[Network Error]" 로그가 출력되며 대기하는지 확인.
3. **복구 테스트:** 연결이 복구되었을 때 다시 "연결 정상" 또는 전송 시작 상태로 돌아오는지 확인.

## 5. 최종 결론
이 방안은 기존의 수동적인 대기 방식을 **능동적인 상태 감시 방식**으로 전환함으로써, `loop_error.md`에서 제기된 문제를 완벽하게 해결할 수 있습니다.
