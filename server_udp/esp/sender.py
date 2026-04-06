import socket
import os
import time
import subprocess
import re

# 설정
DRONE_IP = "192.168.4.1"
DRONE_PORT = 5005
SENSOR_ID = "LAPTOP_01"
IMAGE_PATH = "test.svg"  # 테스트할 이미지 파일명
CHUNK_SIZE = 1024        # 조각 당 크기 (1KB)

def get_real_rssi():
    try:
        output = subprocess.check_output("netsh wlan show interfaces", shell=True).decode('cp949')
        rssi_match = re.search(r"Rssi\s+:\s+(-\d+)", output)
        return int(rssi_match.group(1)) if rssi_match else -99
    except: return -99

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2.0) # 핸드쉐이크 응답 대기용 타임아웃

# 1. 이미지 로드 및 분할 준비
if not os.path.exists(IMAGE_PATH):
    print(f"오류: {IMAGE_PATH} 파일이 없습니다.")
    exit()

with open(IMAGE_PATH, "rb") as f:
    file_data = f.read()

total_chunks = (len(file_data) // CHUNK_SIZE) + 1
print(f"--- 파일 로드 완료: {len(file_data)} bytes ({total_chunks} 조각) ---")

# 2. 핸드쉐이크 (SYNC)
print("--- 핸드쉐이크 시작 (REQ_SYNC) ---")
start_index = 0
try:
    sync_msg = f"REQ_SYNC|{SENSOR_ID}"
    sock.sendto(sync_msg.encode(), (DRONE_IP, DRONE_PORT))
    
    data, addr = sock.recvfrom(1024)
    response = data.decode().split('|')
    
    if response[0] == "ACK_RESUME":
        start_index = int(response[1]) + 1
        print(f"--- 서버 응답 수신: {start_index}번부터 전송을 재개합니다. ---")
except socket.timeout:
    print("--- 서버 응답 없음: 0번부터 새로 시작합니다. ---")

# 3. 데이터 전송 (DATA)
for i in range(start_index, total_chunks):
    start = i * CHUNK_SIZE
    end = start + CHUNK_SIZE
    chunk = file_data[start:end]
    
    rssi = get_real_rssi()
    # 헤더: DATA | SENSOR_ID | Total | CurrentIndex | RSSI | [Binary Data]
    header = f"DATA|{SENSOR_ID}|{total_chunks}|{i}|{rssi}|".encode()
    packet = header + chunk
    
    sock.sendto(packet, (DRONE_IP, DRONE_PORT))
    print(f"[송신] Index: {i}/{total_chunks-1} | RSSI: {rssi}dBm")
    time.sleep(0.01) # 너무 빠르면 패킷 유실 발생 가능

print("--- 전송 시도 완료 ---")