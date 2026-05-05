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
        """큐에서 다음 이미지를 꺼내어 전송 준비 (pop 하지 않고 참조만 수행)"""
        if not self.image_queue:
            return False
        
        # [v3.2] pop(0) 대신 참조만 수행. 성공 시에만 _finalize_current_image에서 제거.
        self.current_image_path = self.image_queue[0]
        
        # 이미지 로드
        with open(self.current_image_path, "rb") as f:
            self.file_data = f.read()
            
        # [v3.1] Data_ID 생성 개선: mtime(16진수) + 파일크기(16진수) 조합 (충돌 방지)
        mtime = os.path.getmtime(self.current_image_path)
        fsize = len(self.file_data)
        self.data_id = f"{int(mtime):08x}{fsize:04x}"[-8:]
        
        self.total_chunks = (len(self.file_data) + CHUNK_SIZE - 1) // CHUNK_SIZE
        self.crc32_val = zlib.crc32(self.file_data) & 0xffffffff
        self.current_idx = 0
        
        return True

    def _finalize_current_image(self):
        """전송이 완료된 이미지를 큐에서 제거하고 폴더 이동"""
        if not self.current_image_path:
            return

        dest_path = os.path.join(self.sent_dir, os.path.basename(self.current_image_path))
        try:
            if os.path.exists(dest_path):
                dest_path = os.path.join(self.sent_dir, f"{int(time.time())}_{os.path.basename(self.current_image_path)}")
            shutil.move(self.current_image_path, dest_path)
            print(f">>> [Moved] {os.path.basename(dest_path)} -> sent/")
            
            # 성공한 경우에만 큐에서 제거
            if self.image_queue:
                self.image_queue.pop(0)
            
            # 상태 초기화
            self.current_image_path = None
            self.data_id = None
            self.file_data = None
        except Exception as e:
            print(f"[Move Error] {e}")

    def send_beacon(self):
        """드론에게 자신의 상태를 알림"""
        header = f"BEACON|{self.s_id}|{self.data_id}|{self.total_chunks}|{self.current_idx}|0|"
        try:
            self.sock.sendto(header.encode(), (DRONE_IP, DRONE_PORT))
        except Exception as e:
            print(f"[Network Error] Beacon send failed: {e}")

    def send_data_chunks(self, start_idx, count):
        """요청받은 개수만큼 데이터 전송 (REVOKE 감지 로직 추가)"""
        self.current_idx = start_idx
        
        # [v3.0] 루프 내 REVOKE 감지를 위해 소켓을 비차단 모드로 일시 전환
        self.sock.setblocking(False)
        
        try:
            for _ in range(count):
                if self.current_idx >= self.total_chunks:
                    break
                
                # 1. 마스터의 중단 명령(REVOKE) 수신 확인
                try:
                    data, _ = self.sock.recvfrom(1024)
                    msg = data.decode().split('|')
                    if msg[0] == "REVOKE" and msg[1] == self.s_id and msg[2] == self.data_id:
                        print(f"\n[Revoked] 마스터에 의해 전송이 중단되었습니다. (ID: {self.data_id})")
                        return # 전송 즉시 중단 및 루프 탈출
                except (BlockingIOError, socket.error):
                    pass # 수신 데이터 없음
                
                # 2. 데이터 청크 전송
                start = self.current_idx * CHUNK_SIZE
                end = min(start + CHUNK_SIZE, len(self.file_data))
                payload = self.file_data[start:end]
                
                last_flag = 1 if self.current_idx == self.total_chunks - 1 else 0
                header = f"DATA|{self.s_id}|{self.data_id}|{self.total_chunks}|{self.current_idx}|{last_flag}|"
                
                packet = header.encode() + payload
                try:
                    # 송신 소켓은 다시 차단 모드와 유사하게 동작하도록 처리 (UDP이므로 즉시 송신됨)
                    self.sock.sendto(packet, (DRONE_IP, DRONE_PORT))
                    self.current_idx += 1
                    time.sleep(0.005)
                except Exception as e:
                    print(f"[Network Error] Data chunk send failed: {e}")
                    break
        finally:
            # 소켓 상태 원복
            self.sock.setblocking(True)
            self.sock.settimeout(1.0)

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
        print(f"--- [Node {self.s_id}] Multi-Image Queue 가동 (v3.2 Stateful) ---")
        
        while True:
            self._scan_images()
            
            if not self.image_queue:
                print("--- [Waiting] 전송할 이미지가 없습니다. 5초 후 재검색... ---")
                time.sleep(5)
                continue
            
            # [v3.2] 현재 전송 중인 이미지가 없으면 새로 준비
            if not self.current_image_path:
                if not self._prepare_next_image():
                    continue
                print(f"\n>>> [Next Image] {os.path.basename(self.current_image_path)} (ID: {self.data_id}, {self.total_chunks} chunks)")
            
            # Inner Loop: 단일 이미지 전송 루프 (Resume 지원)
            is_finished = False
            while True:
                self.send_beacon()
                
                try:
                    data, addr = self.sock.recvfrom(2048)
                    msg = data.decode().split('|')
                    
                    if msg[0] == "GRANT" and msg[1] == self.s_id:
                        if msg[2] != self.data_id:
                            continue
                            
                        target_idx = int(msg[3])
                        if target_idx >= self.total_chunks:
                            is_finished = True
                            break # 전송 완료
                            
                        num_to_send = int(msg[4])
                        self.send_data_chunks(target_idx, num_to_send)

                    elif msg[0] == "REVOKE" and msg[1] == self.s_id and msg[2] == self.data_id:
                        print(f"\n[Pause] 슬롯 시간이 종료되어 전송이 일시 중단되었습니다. (ID: {self.data_id})")
                        break # 루프 탈출하여 비콘 대기 상태로 복귀 (Resume 준비)

                    elif msg[0] == "ERROR" and msg[1] == self.s_id:
                        error_code = msg[5]
                        print(f"[Error Received] Code: {error_code}")
                        if error_code == "CHECKSUM_FAIL":
                            self.current_idx = 0
                        elif error_code == "SESSION_MISMATCH":
                            self.current_idx = 0
                            self.current_image_path = None
                            break 
                        elif error_code == "TIMEOUT":
                            self.beacon_interval = min(5.0, self.beacon_interval * 2)
                            break
                    
                    self.beacon_interval = 0.1

                except socket.timeout:
                    time.sleep(self.beacon_interval)
                    continue
                except Exception as e:
                    print(f"[Runtime Error] {e}")
                    time.sleep(1)
                    continue
            
            # [v3.2] 정상 종료 시에만 COMPLETE 송신 및 파일 정리
            if is_finished:
                self.send_complete()
                time.sleep(2)
                self._finalize_current_image()
            else:
                print(f"--- [Paused] {self.s_id} 전송 일시 중단 (ID: {self.data_id}, Progress: {self.current_idx}/{self.total_chunks}) ---")

if __name__ == "__main__":
    node_id = sys.argv[1] if len(sys.argv) > 1 else "S01"
    node = SensorNode(node_id, "images")
    node.run()
