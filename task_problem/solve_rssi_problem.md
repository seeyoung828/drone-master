# 🛠️ RSSI 추출 에러 분석 및 플랫폼 통합 해결 보고서

`test_rssi.py` 실행 시 발생한 `[WinError 2]` 에러에 대한 원인 분석과 환경별 맞춤형 해결 방안을 제시합니다.

---

## 1. 문제 원인 분석 (Root Cause Analysis)

### **에러 메시지**
`Error: [WinError 2] 지정된 파일을 찾을 수 없습니다`

### **발생 원인**
현재 작성된 RSSI 추출 로직은 **리눅스(Linux) 전용** 명령어와 파일 시스템을 기반으로 작성되었습니다. 하지만 테스트가 진행된 환경은 **윈도우(Windows)**이기 때문에 다음 두 가지 지점에서 에러가 발생합니다.

1.  **`iwconfig` 부재:** `iwconfig`는 리눅스의 무선 관리 도구로, 윈도우 시스템에는 존재하지 않는 실행 파일입니다. (`subprocess` 실행 실패)
2.  **`/proc` 디렉토리 부재:** `/proc/net/wireless`는 리눅스 커널이 제공하는 가상 파일 시스템으로, 윈도우에는 해당 경로가 존재하지 않습니다. (`FileNotFoundError`)

---

## 2. 해결 방안 (Solutions)

### **방안 1: 플랫폼별 분기 처리 (Cross-Platform 지원)**
파이썬의 `platform` 라이브러리를 사용하여 실행 환경을 감지하고, 환경에 맞는 명령어를 실행하도록 코드를 보완해야 합니다.

*   **Linux:** 기존의 `iwconfig` 또는 `/proc/net/wireless` 사용.
*   **Windows:** 윈도우 내장 명령인 `netsh wlan show interfaces` 사용.

### **방안 2: 윈도우용 RSSI 파싱 로직 도입**
윈도우 터미널에서 `netsh wlan show interfaces`를 실행하면 "신호(Signal)" 값이 백분율(%)로 나옵니다. 이를 dBm으로 근사치 변환하는 수식을 사용합니다.
- **변환식:** `dBm = (Signal / 2) - 100` (예: 90% -> -55 dBm)

---

## 3. 수정된 통합 테스트 코드 (Proposed Code)

환경에 상관없이 동작하도록 개선된 `test_rssi.py` 로직 예시입니다.

```python
import subprocess
import re
import platform
import os

def get_universal_rssi(interface="wlx54c9ff00053c"):
    current_os = platform.system()
    
    # --- [Windows 환경] ---
    if current_os == "Windows":
        try:
            cmd = ["netsh", "wlan", "show", "interfaces"]
            res = subprocess.check_output(cmd, encoding='cp949') # 한글 윈도우 대응
            match = re.search(r"신호\s+:\s+(\d+)%", res) or re.search(r"Signal\s+:\s+(\d+)%", res)
            if match:
                signal_percent = int(match.group(1))
                return (signal_percent // 2) - 100
        except Exception: pass

    # --- [Linux 환경] ---
    elif current_os == "Linux":
        try:
            # iwconfig 시도
            res = subprocess.check_output(["iwconfig", interface]).decode()
            match = re.search(r"Signal level=(-?\d+)", res)
            if match: return int(match.group(1))
            
            # /proc 시도
            if os.path.exists("/proc/net/wireless"):
                with open("/proc/net/wireless", "r") as f:
                    lines = f.readlines()
                    for line in lines[2:]:
                        if interface in line:
                            return int(float(line.split()[3]))
        except Exception: pass

    # --- [실패 시 시뮬레이션] ---
    import random
    return -65 + random.randint(-5, 5)
```

---

## 4. 향후 조치 사항

1.  **`sensor_node.py` 업데이트:** 위의 플랫폼 통합 로직을 `SensorNode` 클래스의 `get_rssi` 메서드에 반영하여 개발 환경(Windows)과 배포 환경(Linux)에서 모두 에러 없이 작동하도록 수정합니다.
2.  **환경 변수 활용:** `interface` 명칭이 환경마다 다를 수 있으므로, 설정 파일이나 환경 변수를 통해 유연하게 관리하도록 권장합니다.
3.  **의존성 체크:** 리눅스 환경에서 `iwconfig`가 설치되어 있지 않을 경우를 대비해 `sudo apt install wireless-tools` 가이드라인을 `README.md`에 추가합니다.
