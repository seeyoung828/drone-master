# 📶 실시간 RSSI 연동 구현 상세 명세서 (Live RSSI Integration Guide)

본 문서는 `sensor_node.py`에서 고정값(-65)으로 처리되고 있는 RSSI(신호 세기)를 무선 인터페이스로부터 실시간으로 추출하여 하이브리드 공정성 스케줄러에 반영하기 위한 기술적 절차를 정의한다.

---

## 1. 개요 (Overview)

드론 기반 데이터 수집 시스템에서 RSSI는 스케줄링 점수 계산의 30%를 차지하는 핵심 지표이다. 정확한 실시간 RSSI 연동은 드론이 통신 품질이 가장 좋은 노드를 우선 선택하게 하여 전체 수집 처리량(Throughput)을 극대화한다.

---

## 2. 플랫폼별 구현 (Linux / Windows)

리눅스와 윈도우 환경 모두에서 무선 인터페이스의 RSSI를 가져오는 최적의 방법을 제시한다.

### 윈도우(Windows) 환경: `netsh` 명령어 활용

윈도우 내장 명령인 `netsh wlan show interfaces`를 실행하여 정보를 추출한다.

**Python 구현 코드:**

```python
import subprocess
import re
import platform

def get_live_rssi_windows():
    try:
        cmd = ["netsh", "wlan", "show", "interfaces"]
        # 한글 윈도우의 경우 cp949 인코딩 사용
        res = subprocess.check_output(cmd, encoding='cp949')
        
        # 1순위: 드라이버에서 RSSI를 직접 제공하는 경우
        match = re.search(r"Rssi\s*:\s*(-?\d+)", res, re.IGNORECASE)
        if match:
            return int(match.group(1))
            
        # 2순위: Signal(백분율)만 제공하는 경우 dBm으로 변환 (fallback)
        match = re.search(r"Signal\s*:\s*(\d+)%", res, re.IGNORECASE)
        if match:
            signal_percent = int(match.group(1))
            return (signal_percent // 2) - 100
    except Exception as e:
        print(f"[RSSI Error] Windows extraction failed: {e}")
    return -100
```

### 리눅스(Linux) 환경: `iwconfig` 또는 `/proc` 활용

### 방법 A: `iwconfig` 명령어 파싱 (가장 일반적)

`wireless-tools` 패키지가 설치된 환경에서 사용한다.

- **명령어 예시:** `iwconfig wlx54c9ff00053c | grep "Signal level"`
- **출력 샘플:** `Link Quality=70/70  Signal level=-30 dBm`

**Python 구현 코드:**

```python
import subprocess
import re

def get_live_rssi(interface="wlx54c9ff00053c"):
    try:
        # iwconfig 결과 캡처
        cmd = ["iwconfig", interface]
        result = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8")

        # 'Signal level=-XX dBm' 패턴 추출
        match = re.search(r"Signal level=(-?\d+)\s+dBm", result)
        if match:
            return int(match.group(1))
    except Exception as e:
        print(f"[RSSI Error] Failed to get RSSI via iwconfig: {e}")
    return -100 # 실패 시 최저값 반환
```

### 방법 B: `/proc/net/wireless` 직접 읽기 (가장 효율적)

외부 프로세스 실행 없이 시스템 파일을 직접 읽으므로 CPU 부하가 적고 속도가 빠르다.

**Python 구현 코드:**

```python
def get_live_rssi_proc(interface="wlx54c9ff00053c"):
    try:
        with open("/proc/net/wireless", "r") as f:
            lines = f.readlines()
            for line in lines[2:]: # 헤더 2줄 제외
                if interface in line:
                    parts = line.split()
                    # 4번째 필드(status 3)가 대개 Signal Level (dBm)
                    return int(float(parts[3]))
    except Exception as e:
        pass
    return -100
```

---

## 3. ESP32-CAM (C++/Arduino) 구현

향후 C++ 포팅 시 적용할 ESP32 내장 함수 기반 구현이다.

```cpp
#include <WiFi.h>

int getLiveRSSI() {
    int rssi = WiFi.RSSI();

    // 연결이 끊겼거나 오류 시 -100 반환
    if (rssi == 0 || rssi < -100) {
        return -100;
    }
    return rssi;
}

// 사용 예시
void loop() {
    int current_rssi = getLiveRSSI();
    // 비콘 또는 데이터 헤더에 current_rssi 포함하여 전송
    delay(100);
}
```

---

## 4. `sensor_node.py` 통합 단계 (Step-by-Step)

### 1단계: 메서드 교체

기존 `get_rssi(self)` 메서드를 위에서 정의한 실시간 추출 로직으로 교체한다.

### 2단계: 예외 처리 및 가상 RSSI

무선 랜카드가 없거나 드론 AP에 연결되지 않은 개발 환경에서의 테스트를 위해 환경 변수나 설정 파일을 통한 '가상 모드'를 지원하도록 설계한다.

```python
def get_rssi(self):
    # 1. 실제 RSSI 시도
    rssi = get_live_rssi("wlx54c9ff00053c")

    # 2. 실패 시(시뮬레이션 모드) 더미 값에 랜덤 변동 부여하여 스케줄러 테스트
    if rssi == -100:
        import random
        return -65 + random.randint(-5, 5)

    return rssi
```

### 3단계: 전송 주기와 동기화

RSSI는 매우 빠르게 변하므로 매 `BEACON` 송신 시마다 새로 측정하도록 `send_beacon()` 내부에서 호출한다.

---

## 5. 성능 최적화 가이드

- **캐싱(Caching):** RSSI 측정은 수 ms의 지연을 발생시킬 수 있다. 데이터 전송(`DATA` 패킷) 루프 중에는 매 패킷마다 측정하기보다 10개 패킷 단위로 측정하여 헤더에 반영하는 것이 효율적이다.
- **이동 평균(Moving Average):** 급격한 신호 변동으로 인한 스케줄링 핑퐁(Ping-pong) 현상을 방지하기 위해 최근 3~5개 측정값의 평균을 사용하는 것을 권장한다.

---

## 6. 결론

실시간 RSSI 연동이 완료되면 `project_progress.md`의 `Intelligent Scheduler` 기능이 100% 활성화된다. 이를 통해 드론은 멀어지는 노드를 즉시 감지하여 전송을 중단하고, 다가오는 노드에게 최적의 시점에 권한을 부여할 수 있게 된다.
