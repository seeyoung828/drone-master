# 📋 시스템 분석 및 에러 리포트 (Analysis Report)

**분석 대상:** `error.txt`, `drone_master.py`, `sensor_node.py`, `db_manager.py`
**작성 일자:** 2026년 4월 27일

## 1. 에러 로그(`error.txt`) 분석 결과

### 1.1 `SESSION_MISMATCH` 발생 (이미지 전송 초기)
- **현상:** 새로운 이미지(`10.jpg`) 전송 시작 시 마스터로부터 `SESSION_MISMATCH` 에러 수신.
- **원인:** 
    - `task/error_packet.md` 명세에는 `prepare_session` 시 세션 충돌 시 송신하도록 되어 있으나, 현재 `drone_master.py` 구현에는 해당 송신 로직이 누락됨. (로그에 기록된 것으로 보아 실행 중인 코드와 소스 코드 간의 버전 불일치 또는 누락 확인 필요)
    - 센서 노드가 이미지 전환 시 `Data_ID`를 바꾸어 비콘을 보내는 순간, 마스터가 이전 세션의 `GRANT`를 보냈을 경우 발생 가능.

### 1.2 `TIMEOUT` 에러 및 프로그램 종료
- **현상:** `10.jpg` 전송 시 `TIMEOUT` 에러 수신 후 `sensor_node.py`가 `TimeoutError: timed out`으로 크래시 발생.
- **원인:**
    - **Tight Timeout:** 마스터의 재시도 임계값(0.3s)이 너무 짧아 40개의 청크(약 0.2s 소요) 전송 시 약간의 네트워크 지연만으로도 타임아웃이 발생함.
    - **Penalty System:** 마스터는 `TIMEOUT` 송신 후 해당 노드를 30초간 스케줄링에서 제외(`timeout_until`)하지만, 센서 노드는 백오프 없이 1초 간격으로 계속 비콘을 송신함.
    - **Socket Crash:** `self.sock.sendto`에서 발생한 `TimeoutError`는 주로 물리적인 네트워크 연결 끊김(WiFi 연결 해제 등)이나 ARP 해석 실패 시 Windows 환경에서 발생함.

---

## 2. 코드 레벨의 주요 문제점 (Code Defects)

### 2.1 [Master] 데이터 이어받기 동기화 누락 (Resumption Bug)
- **위치:** `drone_master.py` L108-L117
- **내용:** `db.prepare_session`이 `False`(기존 세션)를 반환할 경우, DB에서 현재 진행도를 가져와 `sensors_mem[s_id]['curr']`를 업데이트하는 로직이 없음.
- **결과:** 센서가 재부팅되어 `curr=0`으로 비콘을 보내면 마스터도 0부터 다시 요청하게 되어 '이어받기' 기능이 정상 동작하지 않음.

### 2.2 [Master/Protocol] 명세와 구현의 불일치
- **내용:** `task/error_packet.md`에 정의된 `SESSION_MISMATCH`, `LIVELOCK_PREVENT` 송신 로직이 `drone_master.py`에 구현되어 있지 않음.
- **결과:** 예외 상황 발생 시 센서 노드에 명확한 피드백을 주지 못해 상태 동기화가 깨질 수 있음.

### 2.3 [Sensor] 에러 수신 후 처리 로직 미흡
- **위치:** `sensor_node.py` L141-L151
- **내용:** `ERROR` 패킷 수신 시 단순히 프린트만 하고 루프를 계속 진행함.
- **결과:** 특히 `TIMEOUT` 수신 시 마스터가 30초간 무시함에도 불구하고 계속 비콘을 보내 리소스를 낭비하고, 네트워크 불안정 시 소켓 에러로 프로그램이 종료됨.

---

## 3. 개선 제안 (Recommendations)

### 3.1 마스터(Drone Master) 수정
1.  **이어받기 로직 보완:** `BEACON` 처리 루틴에서 세션이 신규든 기존이든 관계없이 DB의 최신 인덱스로 `sensors_mem`을 동기화해야 함.
2.  **타임아웃 임계값 상향:** RSSI 환경에 따라 0.3초를 0.5초~1.0초로 완만하게 조정하여 불필요한 재시도 방지.
3.  **에러 송신 보완:** 명세에 따라 세션 ID가 바뀌거나 데이터 무결성에 문제가 생겼을 때 `SESSION_MISMATCH`를 명시적으로 송신하도록 수정.

### 3.2 센서(Sensor Node) 수정
1.  **지수 백오프(Exponential Back-off) 도입:** `TIMEOUT` 수신 시 즉시 비콘 송신 주기를 늘려 네트워크 안정화를 기다리도록 수정.
2.  **네트워크 예외 처리:** `sendto` 및 `recvfrom` 호출부를 `try-except Exception`으로 감싸고, 소켓 에러 발생 시 재연결 시도 로직 추가.

### 3.3 DB 매니저 수정
1.  **세션 관리 강화:** `prepare_session` 호출 시 해당 노드의 다른 활성 세션이 있다면 정리(Purge)하는 로직을 강화하여 데이터 정합성 유지.
