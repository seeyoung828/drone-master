import socket
import sys
import os
import time
import struct
import zlib
import subprocess
import re

# --- [설정 및 상수] ---
DRONE_IP = "192.168.4.1"
DRONE_PORT = 5005
CHUNK_SIZE = 1024
HEADER_FIELDS_COUNT = 7  # Payload 제외 필드 수

class SensorNode:
    def __init__(self, s_id, image_path):
        self.s_id = s_id
        self.image_path = image_path
        # [수정] 프로토콜 명세에 따라 Data_ID를 MMDDHHmm (8자리) 형식으로 생성
        self.data_id = time.strftime("%m%d%H%M")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        
        # 이미지 로드 및 초기화
        self.file_data = self._load_image()
        self.total_chunks = (len(self.file_data) + CHUNK_SIZE - 1) // CHUNK_SIZE
        self.crc32_val = zlib.crc32(self.file_data) & 0xffffffff
        self.current_idx = 0
        self.max_sent_idx = -1

    def _load_image(self):
        if not os.path.exists(self.image_path):
            print(f"Error: {self.image_path} not found.")
            sys.exit(1)
        with open(self.image_path, "rb") as f:
            return f.read()

    def get_rssi(self):
        """환경에 따른 RSSI 추출 (예시: Linux/Pi 환경용)"""
        try:
            # 테스트를 위해 임의의 값 반환하거나 실제 iwconfig 로직 사용
            return -65 
        except: return -99

    def send_beacon(self):
        """드론에게 자신의 상태를 알림"""
        rssi = self.get_rssi()
        # Type|ID|Data_ID|Total|Idx|RSSI|Last_Flag|
        header = f"BEACON|{self.s_id}|{self.data_id}|{self.total_chunks}|{self.current_idx}|{rssi}|0|"
        self.sock.sendto(header.encode(), (DRONE_IP, DRONE_PORT))

    def send_data_chunks(self, start_idx, count):
        """요청받은 개수만큼 데이터 전송"""
        # [해결] 마스터의 요청(Start_Idx)을 그대로 수용하여 유실 패킷 재전송 허용
        self.current_idx = start_idx
        
        for _ in range(count):
            if self.current_idx >= self.total_chunks:
                break
            
            start = self.current_idx * CHUNK_SIZE
            end = min(start + CHUNK_SIZE, len(self.file_data))
            payload = self.file_data[start:end]
            
            # 마지막 조각 여부 확인
            last_flag = 1 if self.current_idx == self.total_chunks - 1 else 0
            
            rssi = self.get_rssi()
            header = f"DATA|{self.s_id}|{self.data_id}|{self.total_chunks}|{self.current_idx}|{rssi}|{last_flag}|"
            
            # 헤더(텍스트) + 바이너리 결합 전송
            packet = header.encode() + payload
            self.sock.sendto(packet, (DRONE_IP, DRONE_PORT))
            
            self.max_sent_idx = self.current_idx
            self.current_idx += 1
            time.sleep(0.005) # 네트워크 폭주 방지

    def send_complete(self):
        """전송 완료 및 체크섬 보고 (Big Endian)"""
        rssi = self.get_rssi()
        header = f"COMPLETE|{self.s_id}|{self.data_id}|0|0|{rssi}|0|"
        
        # CRC32를 4바이트 Big Endian 바이너리로 패킹
        checksum_bin = struct.pack('>I', self.crc32_val)
        packet = header.encode() + checksum_bin
        
        self.sock.sendto(packet, (DRONE_IP, DRONE_PORT))
        print(f"--- [SUCCESS] {self.s_id} 전송 완료 (CRC32: {hex(self.crc32_val)}) ---")

    def run(self):
        print(f"--- [Node {self.s_id}] 가동: {self.total_chunks} 조각 ---")
        
        # [수정] max_sent_idx만으로는 실제 서버 수신 여부 확인 불가. 
        # 마스터가 모든 조각을 다 받았다고(Next_Idx >= Total) 할 때까지 루프 유지.
        while True:
            self.send_beacon()
            
            try:
                data, addr = self.sock.recvfrom(2048)
                msg = data.decode().split('|')
                
                if msg[0] == "GRANT" and msg[1] == self.s_id:
                    target_idx = int(msg[3]) # Start_Idx
                    
                    # 마스터가 모든 데이터를 받았다고 판단하면 전송 종료
                    if target_idx >= self.total_chunks:
                        break
                        
                    num_to_send = int(msg[4]) # Count
                    self.send_data_chunks(target_idx, num_to_send)
                    
            except socket.timeout:
                continue
        
        # 모든 데이터 전송 후 COMPLETE 송신
        self.send_complete()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sensor_node.py [Node_ID]")
        sys.exit()
        
    node = SensorNode(sys.argv[1], "test.jpg")
    node.run()