import socket
import sys
import os
import time
import struct
import zlib
import shutil
import subprocess
import re
import random
import platform

# 상위 디렉토리 추가하여 common 패키지 인식 (v2.7 성능 최적화 대응)
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from common.utility import CHUNK_SIZE

# --- [설정 및 상수] ---
DRONE_IP = "192.168.4.1"
DRONE_PORT = 5005
HEADER_FIELDS_COUNT = 6  # Payload 제외 필드 수

class SensorNode:
    def __init__(self, s_id, images_dir="images"):
        self.s_id = s_id
        self.images_dir = images_dir
        self.sent_dir = os.path.join(images_dir, "sent")
        
        if not os.path.exists(self.images_dir):
            os.makedirs(self.images_dir)
        if not os.path.exists(self.sent_dir):
            os.makedirs(self.sent_dir)
            
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        
        self.image_queue = []
        self.current_image_path = None
        self.data_id = None
        self.file_data = None
        self.total_chunks = 0
        self.crc32_val = 0
        self.current_idx = 0
        self.beacon_interval = 0.1 # [Fix] 기본 비콘 주기
        self.rssi_history = [] # RSSI 이동 평균을 위한 저장소

    def _scan_images(self):
        """디렉토리를 스캔하여 전송 대기 중인 이미지 목록 갱신"""
        files = [f for f in os.listdir(self.images_dir) if f.lower().endswith('.jpg')]
        # 파일명 기준 정렬 (보통 타임스탬프 포함)
        files.sort()
        self.image_queue = [os.path.join(self.images_dir, f) for f in files]

    def _prepare_next_image(self):
        """큐에서 다음 이미지를 꺼내어 전송 준비"""
        if not self.image_queue:
            return False
        
        self.current_image_path = self.image_queue.pop(0)
        
        # 이미지 로드
        with open(self.current_image_path, "rb") as f:
            self.file_data = f.read()
            
        # Data_ID 생성: 파일 수정 시간(mtime) 기반 Day+Hour+Min+Sec (8자리)
        mtime = os.path.getmtime(self.current_image_path)
        self.data_id = time.strftime("%d%H%M%S", time.localtime(mtime))
        
        self.total_chunks = (len(self.file_data) + CHUNK_SIZE - 1) // CHUNK_SIZE
        self.crc32_val = zlib.crc32(self.file_data) & 0xffffffff
        self.current_idx = 0
        
        return True

    def send_beacon(self):
        """드론에게 자신의 상태를 알림"""
        header = f"BEACON|{self.s_id}|{self.data_id}|{self.total_chunks}|{self.current_idx}|0|"
        try:
            self.sock.sendto(header.encode(), (DRONE_IP, DRONE_PORT))
        except Exception as e:
            print(f"[Network Error] Beacon send failed: {e}")

    def send_data_chunks(self, start_idx, count):
        """요청받은 개수만큼 데이터 전송"""
        self.current_idx = start_idx
        
        for _ in range(count):
            if self.current_idx >= self.total_chunks:
                break
            
            start = self.current_idx * CHUNK_SIZE
            end = min(start + CHUNK_SIZE, len(self.file_data))
            payload = self.file_data[start:end]
            
            last_flag = 1 if self.current_idx == self.total_chunks - 1 else 0
            header = f"DATA|{self.s_id}|{self.data_id}|{self.total_chunks}|{self.current_idx}|{last_flag}|"
            
            packet = header.encode() + payload
            try:
                self.sock.sendto(packet, (DRONE_IP, DRONE_PORT))
                self.current_idx += 1
                time.sleep(0.005)
                self.beacon_interval = 0.1 # 성공 시 대기 시간 복구
            except socket.timeout:
                print(f"[Network Error] Send timeout. Buffer may be full.")
                # [Fix 3.3] 송신 타임아웃 시 즉시 중단하고 지수 백오프 적용 유도
                self.beacon_interval = min(2.0, self.beacon_interval * 1.5)
                break
            except Exception as e:
                print(f"[Network Error] Data chunk send failed: {e}")
                break

    def send_complete(self):
        """전송 완료 및 체크섬 보고"""
        header = f"COMPLETE|{self.s_id}|{self.data_id}|0|0|0|"
        checksum_bin = struct.pack('>I', self.crc32_val)
        packet = header.encode() + checksum_bin
        try:
            self.sock.sendto(packet, (DRONE_IP, DRONE_PORT))
            print(f"--- [SUCCESS] {self.s_id} 전송 완료 보고 (ID: {self.data_id}, CRC32: {hex(self.crc32_val)}) ---")
        except Exception as e:
            print(f"[Network Error] Complete report failed: {e}")

    def run(self):
        print(f"--- [Node {self.s_id}] Multi-Image Queue 가동 ---")
        
        while True:
            self._scan_images()
            
            if not self.image_queue:
                print("--- [Waiting] 전송할 이미지가 없습니다. 5초 후 재검색... ---")
                time.sleep(5)
                continue
            
            while self.image_queue:
                if not self._prepare_next_image():
                    break
                
                print(f"\n>>> [Next Image] {os.path.basename(self.current_image_path)} (ID: {self.data_id}, {self.total_chunks} chunks)")
                
                # Inner Loop: 단일 이미지 전송 루프
                is_finished = False
                while True:
                    self.send_beacon()
                    
                    try:
                        data, addr = self.sock.recvfrom(2048)
                        msg = data.decode().split('|')
                        
                        if msg[0] == "GRANT" and msg[1] == self.s_id:
                            # 만약 마스터가 보낸 Data_ID가 현재와 다르면 무시
                            if msg[2] != self.data_id:
                                continue
                                
                            target_idx = int(msg[3])
                            if target_idx >= self.total_chunks:
                                is_finished = True
                                break # 전송 완료
                                
                            num_to_send = int(msg[4])
                            self.send_data_chunks(target_idx, num_to_send)

                        elif msg[0] == "ERROR" and msg[1] == self.s_id:
                            error_code = msg[5]
                            print(f"[Error Received] Code: {error_code}")
                            if error_code == "CHECKSUM_FAIL":
                                self.current_idx = 0
                            elif error_code == "SESSION_MISMATCH":
                                self.current_idx = 0
                                break # 루프 탈출 (is_finished=False)
                            elif error_code == "TIMEOUT":
                                self.beacon_interval = min(5.0, self.beacon_interval * 2)
                                break # 루프 탈출 (is_finished=False)
                        
                        self.beacon_interval = 0.1

                    except socket.timeout:
                        time.sleep(self.beacon_interval)
                        continue
                    except Exception as e:
                        print(f"[Runtime Error] {e}")
                        time.sleep(1)
                        continue
                
                # [Fix 2.1] 정상 종료 시에만 COMPLETE 송신
                if is_finished:
                    self.send_complete()
                    # 마스터의 최종 처리를 위해 잠시 대기
                    time.sleep(2)
                else:
                    print(f"--- [ABORT] {self.s_id} 전송 중단 (ID: {self.data_id}) ---")
                
                # 완료된 파일 이동
                dest_path = os.path.join(self.sent_dir, os.path.basename(self.current_image_path))
                try:
                    # 기존에 파일이 있으면 덮어쓰거나 이름 변경
                    if os.path.exists(dest_path):
                        dest_path = os.path.join(self.sent_dir, f"{int(time.time())}_{os.path.basename(self.current_image_path)}")
                    shutil.move(self.current_image_path, dest_path)
                    print(f">>> [Moved] {os.path.basename(dest_path)} -> sent/")
                except Exception as e:
                    print(f"[Move Error] {e}")

if __name__ == "__main__":
    node_id = sys.argv[1] if len(sys.argv) > 1 else "S01"
    # 'images' 폴더가 없으면 생성하고 테스트 파일을 하나 복사해둘 수 있음
    node = SensorNode(node_id, "images")
    node.run()
