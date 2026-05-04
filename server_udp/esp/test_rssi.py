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
            res = subprocess.check_output(cmd, encoding='cp949')

            # 1순위: RSSI 직접 파싱
            match = re.search(r"Rssi\s*:\s*(-?\d+)", res, re.IGNORECASE)

            if match:
                return int(match.group(1))
            # 2순위: Signal → RSSI 변환 (fallback)
            match = re.search(r"Signal\s*:\s*(\d+)%", res, re.IGNORECASE)

            if match:
                signal_percent = int(match.group(1))
                return (signal_percent / 2) - 100

        except Exception as e:
            print("Error:", e)

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

if __name__ == "__main__":
    rssi = get_universal_rssi()
    print("RSSI:", rssi)