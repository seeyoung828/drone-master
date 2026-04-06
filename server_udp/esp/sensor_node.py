import socket
import sys
import os
import time
import subprocess
import re

# 실행 인자 확인
if len(sys.argv) < 2:
    print("Usage: python sensor_node.py [SENSOR_ID]")
    sys.exit()

SENSOR_ID = sys.argv[1]
DRONE_IP = "192.168.4.1"
DRONE_PORT = 5005
CHUNK_SIZE = 1024
IMAGE_PATH = "test.jpg" # 실제 테스트할 이미지 경로

def get_rssi():
    """ Windows 환경의 RSSI 추출 (리눅스/파이의 경우 iwconfig 로직으로 변경 필요) """
    try:
        output = subprocess.check_output("netsh wlan show interfaces", shell=True).decode('cp949')
        rssi = re.search(r"Rssi\s+:\s+(-\d+)", output)
        return int(rssi.group(1)) if rssi else -99
    except: return -99

# 이미지 로드 및 조각화
if not os.path.exists(IMAGE_PATH):
    print(f"Error: {IMAGE_PATH} 파일이 없습니다.")
    sys.exit()

with open(IMAGE_PATH, "rb") as f:
    file_data = f.read()

total_chunks = (len(file_data) // CHUNK_SIZE) + 1
current_idx = 0
max_sent_idx = -1 # 본인이 전송 완료한 마지막 인덱스

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(0.2)

print(f"--- [Sensor {SENSOR_ID}] 가동 (총 {total_chunks} 조각) ---")

while current_idx < total_chunks:
    rssi = get_rssi()
    
    # 1. 비콘 전송 (나 여기 있고, 이만큼 보냈어!)
    beacon = f"BEACON|{SENSOR_ID}|{total_chunks}|{current_idx}|{rssi}"
    sock.sendto(beacon.encode(), (DRONE_IP, DRONE_PORT))
    
    try:
        # 2. 드론의 명령 대기
        data, addr = sock.recvfrom(1024)
        msg = data.decode().split('|')
        
        if msg[0] == "GRANT" and msg[1] == SENSOR_ID:
            start_from = int(msg[2])
            count = int(msg[3])
            
            # [핵심] 역행 방지: 드론이 요청한 번호와 내가 보낸 번호 중 최신 것 선택
            current_idx = max(start_from, max_sent_idx + 1)
            
            for _ in range(count):
                if current_idx >= total_chunks: break
                
                start = current_idx * CHUNK_SIZE
                end = start + CHUNK_SIZE
                payload = file_data[start:end]
                
                # 헤더와 데이터 결합 전송
                header = f"DATA|{SENSOR_ID}|{total_chunks}|{current_idx}|{rssi}|".encode()
                sock.sendto(header + payload, (DRONE_IP, DRONE_PORT))
                
                max_sent_idx = current_idx
                current_idx += 1
                time.sleep(0.005) # 전송 안정성을 위한 미세 지연
            
            print(f"[*] {max_sent_idx}번까지 전송 완료 (드론 요청 시작점: {start_from})")

    except socket.timeout:
        pass
    
    time.sleep(0.1) # 비콘 주기

print("모든 데이터 전송이 완료되었습니다.")