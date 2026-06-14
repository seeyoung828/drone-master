# 📶 실시간 RSSI 연동 확인 및 검증 가이드

본 문서는 센서 노드(`sensor_node.py`)에서 추출하는 RSSI 값이 실제 무선 환경의 신호 세기와 일치하는지 확인하는 테스트 절차를 안내합니다.

---

## 1. 사전 준비 사항

- **대상 기기:** 무선 랜카드가 장착된 리눅스 환경 (Raspberry Pi, Jetson, Linux Laptop 등)
- **인터페이스 확인:** 현재 코드의 기본값은 `wlx54c9ff00053c`입니다. 사용 중인 인터페이스 명칭이 다를 경우(예: `wlp2s0`) 코드의 `interface` 변수를 수정해야 합니다.
- **필수 도구:** `wireless-tools` (iwconfig)

---

## 2. 시스템 도구와 비교 검증 (Cross-Check)

노드를 실행하기 전, OS에서 제공하는 값과 노드가 읽어오는 값이 일치하는지 확인합니다.

### 방법 A: `iwconfig` 활용

터미널에서 다음 명령어를 실행하여 현재 신호 세기를 확인합니다.

```bash
iwconfig wlx54c9ff00053c | grep "Signal level"
# 출력 예: Link Quality=70/70  Signal level=-45 dBm
```

### 방법 B: `/proc/net/wireless` 직접 확인

코드가 참조하는 시스템 파일을 직접 열어 확인합니다.

```bash
watch -n 1 cat /proc/net/wireless
```

- `level` 항목 아래의 숫자가 현재 dBm 값입니다.

---

## 3. 노드 실행을 통한 로그 확인

센서 노드를 실행하여 실제 전송 패킷에 담기는 RSSI 로그를 모니터링합니다.

1. **노드 실행:**
   ```bash
   python sensor_node.py S01
   ```
2. **로그 분석:**
   - 현재 구현된 로직은 **최근 5개 측정값의 이동 평균(Moving Average)**을 사용하므로, `iwconfig` 값보다 변화가 완만하게 나타나야 정상입니다.
   - 만약 무선 랜카드가 없거나 드론 AP에 연결되지 않았다면, `-65` 근처에서 랜덤하게 변하는 **시뮬레이션 모드** 값이 출력됩니다.

---

## 4. 독립 테스트 스크립트로 검증

시스템 환경에서 추출 로직만 따로 떼어내어 즉시 확인할 수 있는 간단한 파이썬 스크립트입니다.

```python
import subprocess
import re
import time

def test_raw_rssi(interface="wlx54c9ff00053c"):
    try:
        # 1. iwconfig 방식 테스트
        res = subprocess.check_output(["iwconfig", interface]).decode()
        match = re.search(r"Signal level=(-?\d+)", res)
        iw_val = match.group(1) if match else "N/A"

        # 2. /proc/net/wireless 방식 테스트
        with open("/proc/net/wireless", "r") as f:
            lines = f.readlines()
            proc_val = "N/A"
            for line in lines[2:]:
                if interface in line:
                    proc_val = line.split()[3]

        print(f"[{time.strftime('%H:%M:%S')}] iwconfig: {iw_val} dBm | /proc: {proc_val} dBm")
    except Exception as e:
        print(f"Error: {e}")

print(f"--- RSSI Extraction Test ---")
for _ in range(10):
    test_raw_rssi()
    time.sleep(1)
```

---

## 5. 거리 변화에 따른 동적 반응 확인

실제 하이브리드 스케줄러가 RSSI에 반응하는지 확인하려면 다음 시나리오를 수행합니다.

1. **근거리 전송:** 드론 AP 근처(-40dBm 이상)에서 `GRANT` 시 `N_limit`이 **40**으로 설정되는지 마스터 로그 확인.
2. **원거리 이동:** 기기를 멀리 이동시켜 신호가 -75dBm 이하로 떨어졌을 때, `N_limit`이 **10**으로 자동 축소되는지 확인.
3. **가림막 테스트:** 기기를 금속함에 넣거나 몸으로 가려 RSSI 변동을 준 뒤, 마스터의 `calculate_score`에서 해당 노드의 우선순위가 낮아지는지 관찰.

---

## 6. 문제 해결 (Troubleshooting)

- **값이 계속 -65 근처로 고정됨:** 실제 무선 인터페이스를 찾지 못해 시뮬레이션 모드로 동작 중일 가능성이 높습니다. `iwconfig` 명령어가 사용 가능한지 확인하세요.
- **Permission Denied:** 일부 환경에서는 `/proc/net/wireless` 읽기에 권한이 필요할 수 있습니다. `sudo`로 실행해 보세요.
- **Interface Not Found:** `ifconfig` 또는 `ip addr` 명령어로 실제 무선 인터페이스 이름을 확인하고 코드를 수정하세요.
