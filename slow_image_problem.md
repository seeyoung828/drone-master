# 🚨 이미지 전송 속도 저하 및 Chunks 수집 부족 문제 분석 보고서

## 1. 개요
최근 RSSI를 실시간으로 측정하여 패킷에 포함하도록 코드를 수정한 이후, 라즈베리파이(Master)에서 이미지를 수신하는 속도가 현저히 느려지는 현상이 발생함. `test_result.txt` 로그 확인 결과, 대부분의 슬롯이 `Idle Timeout`으로 종료되며 한 슬롯당 수집되는 청크(Chunks)가 0개 또는 1개에 불과함.

## 2. 문제 원인 분석 (Root Cause Analysis)

### 2.1 Sensor Node (Windows)의 RSSI 측정 병목
- **코드 위치:** `server_udp/esp/sensor_node.py` 내 `get_rssi()` 함수
- **문제 지점:** Windows 환경에서 RSSI를 얻기 위해 `subprocess.check_output(["netsh", "wlan", "show", "interfaces"])` 명령을 실행함.
- **분석 결과:**
    - `subprocess`를 통해 외부 명령어를 호출하는 작업은 Windows에서 매우 무거운 작업이며, 실행 시마다 약 **0.5초 ~ 1.0초**의 시간이 소요됨.
    - `send_data_chunks()` 함수 내에서 **매 청크(4KB)를 보낼 때마다** `get_rssi()`를 호출하고 있음.
    - 결과적으로 청크 하나를 보내는 데 걸리는 시간이 네트워크 전송 시간보다 RSSI 측정 시간에 의해 결정됨 (심각한 지연 발생).

### 2.2 Drone Master (Raspberry Pi)의 타임아웃 임계치 미달
- **코드 위치:** `server_udp/pi/drone_master.py` 내 `IDLE_TIMEOUT = 0.5`
- **문제 지점:** 마스터는 `GRANT`를 보낸 후 또는 마지막 데이터를 받은 후 **0.5초** 동안 다음 데이터가 오지 않으면 `Idle Timeout`으로 판단하고 슬롯을 종료함.
- **분석 결과:**
    - 노드에서 RSSI를 측정하는 데 이미 0.5초 이상을 소비하므로, 마스터 입장에서는 노드가 응답하지 않는 것으로 간주함.
    - 이로 인해 겨우 1개의 청크를 받거나, 아예 받기도 전에 슬롯이 강제 종료되는 현상이 반복됨.

## 3. 요약 및 영향
| 항목 | 내용 |
| :--- | :--- |
| **주요 증상** | 슬롯당 Chunks 수집량 0~1개, 로그에 `Idle Timeout` 빈번하게 발생 |
| **직접적 원인** | Windows `netsh` 명령어 호출 지연 (> 0.5s) |
| **상호작용** | 노드의 응답 지연이 마스터의 `IDLE_TIMEOUT` 설정을 초과함 |
| **최종 영향** | 전체 이미지 전송 효율 급감, 사실상 통신 마비 상태 |

## 4. 해결 방안 제안

### [해결책 1] Sensor Node: RSSI 캐싱 도입 (권장)
매 청크마다 RSSI를 새로 측정하지 않고, 일정 시간(예: 1초) 동안은 이전에 측정된 값을 재사용하도록 수정.
```python
# 예시: 1초 이내 요청 시 캐시값 반환
if time.time() - self.last_rssi_time < 1.0:
    return self.last_rssi
```

### [해결책 2] Drone Master: IDLE_TIMEOUT 상향 조정
Windows 환경 및 DB I/O 지연을 고려하여 마스터의 대기 시간을 기존 0.5초에서 **1.2초 ~ 1.5초** 수준으로 완화.

### [해결책 3] 전송 로직 분리
데이터 전송 루프 밖에서 RSSI를 한 번만 측정하거나, 별도의 스레드에서 주기적으로 갱신하도록 구조 개선.
