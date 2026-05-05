# 🛠️ REVOKE 이후 전송 재개(Resume) 및 큐 보존 해결 방안 (solve_explicit_checksum_problem_2.md)

`explicit_checksum_problem_2.md`에서 분석된 "명시적 중단 후 이미지 자동 폐기" 문제를 해결하고, 대용량 이미지도 여러 슬롯에 걸쳐 안정적으로 수집될 수 있도록 **"State-Preserving Transmission (상태 보존 전송)"** 방식을 도입합니다.

---

## 1. 노드 전송 로직 근본 개선 (`sensor_node.py`)

### 1.1 큐(Queue) 관리 방식 변경 (Pop-on-Success)
- **기존:** 루프 시작 시 무조건 `pop(0)` 하여 이미지를 꺼냄. 실패해도 복구 불가능.
- **개선:** 이미지가 **완전히 성공(`is_finished == True`)한 직후**에만 큐에서 제거하고 `sent/` 폴더로 이동함.
- **동작:** `REVOKE` 등으로 중단된 경우, 다음 루프에서 다시 `_prepare_next_image()`가 호출되더라도 현재 이미지가 큐의 맨 앞에 유지되어 전송을 재개함.

### 1.2 단일 이미지 루프 탈출 조건 정교화
- **정상 종료 (`is_finished = True`):** 마스터로부터 `target_idx >= total_chunks`를 확인받은 경우. 이 경우에만 `COMPLETE` 송신 및 파일 정리 수행.
- **일시 중단 (`is_finished = False`):** `REVOKE`, `TIMEOUT`, `SESSION_MISMATCH` 등으로 루프를 탈출한 경우. 파일은 그대로 두고 다음 `BEACON` 주기부터 다시 시도함.

---

## 2. 세부 구현 방안

### 2.1 `run()` 함수 구조 재설계
```python
def run(self):
    while True:
        self._scan_images()
        if not self.image_queue:
            time.sleep(5); continue
        
        # [v3.2] pop(0) 대신 0번 인덱스 참조만 수행하는 prepare_next 도입 가능
        # 또는 현재 이미지 상태를 멤버 변수로 완벽히 관리
        if not self.current_image_path:
            self._prepare_next_image() 

        is_finished = False
        while True: # 전송 시도 루프
            self.send_beacon()
            # ... 수신 대기 ...
            if msg[0] == "GRANT":
                # ... 전송 수행 ...
                if target_idx >= self.total_chunks:
                    is_finished = True; break
            elif msg[0] == "REVOKE":
                print("[Pause] 전송이 일시 중단되었습니다."); break
            elif msg[0] == "ERROR":
                break # 에러 시 루프 탈출 후 비콘부터 다시 시작
        
        if is_finished:
            self.send_complete()
            self._finalize_current_image() # 성공 시에만 파일 이동 및 변수 초기화
```

### 2.2 `Data_ID` 유지 및 Resume 보장
- 중단 후 다시 `BEACON`을 보낼 때, 기존과 동일한 `Data_ID`와 마지막으로 성공한 `current_idx`를 사용합니다.
- 마스터는 DB를 통해 이미 받은 조각을 알고 있으므로, `GRANT` 시 유실된 부분부터 다시 요청하게 되어 자연스럽게 Resume이 이루어집니다.

---

## 3. 프로토콜 및 마스터 정합성 (`protocol_spec.md`)
- **REVOKE의 의미 재정의:** "이미지 폐기"가 아닌 "현재 슬롯 소유권 반납(Yield)"으로 명확히 정의합니다.
- **상태 동기화:** 마스터는 `REVOKE`를 보낸 후에도 해당 세션 정보를 DB에 유지하며, 노드가 다시 동일 `Data_ID`로 `BEACON`을 보내면 중단된 지점부터 `GRANT`를 발행합니다.

---

## 4. 기대 효과
1.  **대용량 파일 전송 보장:** 5.0초(`SLOT_TIME_LIMIT`) 이상의 전송 시간이 필요한 고해상도 이미지도 2~3번의 슬롯을 거쳐 완벽하게 수집 가능.
2.  **안정성 극대화:** 네트워크 불안정으로 인한 일시적 단절 상황에서도 데이터 손실 없이 수집 재개.
3.  **공정성 유지:** 마스터는 언제든 `REVOKE`를 통해 채널을 회수할 수 있으며, 노드는 불이익 없이 다음 차례를 기다릴 수 있음.

---

## 5. 결론
이번 개선은 노드를 **"Stateless(단발성)"** 전송 방식에서 **"Stateful(상태 보존형)"** 전송 방식으로 전환하는 작업입니다. 이는 드론 수집 시스템의 신뢰성을 완성하는 마지막 핵심 단계입니다.
