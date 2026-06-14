import socket
import errno
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
import json

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
        self.state_file = f"node_state_{self.s_id}.json"
        
        if not os.path.exists(self.images_dir):
            os.makedirs(self.images_dir)
        if not os.path.exists(self.sent_dir):
            os.makedirs(self.sent_dir)
            
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # [Fix] Windows 송신 버퍼 확장 (128KB)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 128 * 1024)
        except: pass
        self.sock.settimeout(0.5) # [v3.6] 1.0 -> 0.5 (Master Retry 0.8s 보다 짧게 설정)
        
        self.image_queue = []
        self.current_image_path = None
        self.data_id = None
        self.file_data = None
        self.total_chunks = 0
        self.crc32_val = 0
        self.current_idx = 0
        self.beacon_interval = 0.1 # [Fix] 기본 비콘 주기
        self.rssi_history = [] # RSSI 이동 평균을 위한 저장소
        self.is_revoked_in_slot = False # [v3.4] 중복 REVOKE 방지 플래그

        # [v3.7] 이전 상태 복구 시도
        self.load_state()

    def save_state(self):
        """[v3.7] 현재 전송 상태를 파일에 저장 (Resume 지원)"""
        try:
            state = {
                "current_image_path": self.current_image_path,
                "data_id": self.data_id,
                "current_idx": self.current_idx
            }
            with open(self.state_file, "w") as f:
                json.dump(state, f)
        except Exception as e:
            print(f"[State Error] Save failed: {e}")

    def load_state(self):
        """[v3.7] 저장된 상태가 있으면 복구"""
        if not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)
                path = state.get("current_image_path")
                if path and os.path.exists(path):
                    self.current_image_path = path
                    self.data_id = state.get("data_id")
                    self.current_idx = state.get("current_idx", 0)
                    
                    with open(self.current_image_path, "rb") as f_img:
                        self.file_data = f_img.read()
                    
                    self.total_chunks = (len(self.file_data) + CHUNK_SIZE - 1) // CHUNK_SIZE
                    self.crc32_val = zlib.crc32(self.file_data) & 0xffffffff
                    print(f">>> [Restored] 이전 세션 복구됨: {os.path.basename(path)} (Idx: {self.current_idx})")
                    return True
        except Exception as e:
            print(f"[State Error] Load failed: {e}")
        return False

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
            
        # [v3.3] Data_ID 생성 고도화: S_ID + mtime + fsize 조합 (노드 간 충돌 원천 차단)
        mtime = os.path.getmtime(self.current_image_path)
        fsize = len(self.file_data)
        seed = f"{self.s_id}_{mtime}_{fsize}"
        self.data_id = f"{zlib.crc32(seed.encode()) & 0xffffffff:08x}"
        
        self.total_chunks = (len(self.file_data) + CHUNK_SIZE - 1) // CHUNK_SIZE
        self.crc32_val = zlib.crc32(self.file_data) & 0xffffffff
        self.current_idx = 0
        
        self.save_state() # 신규 이미지 준비 시 상태 저장
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
            
            # 상태 파일 삭제
            if os.path.exists(self.state_file):
                os.remove(self.state_file)
        except Exception as e:
            print(f"[Move Error] {e}")

    def send_beacon(self):
        """드론에게 자신의 상태를 알림 (성공 여부 반환)"""
        header = f"BEACON|{self.s_id}|{self.data_id}|{self.total_chunks}|{self.current_idx}|0|"
        try:
            self.sock.sendto(header.encode(), (DRONE_IP, DRONE_PORT))
            return True
        except Exception as e:
            print(f"[Network Error] Beacon send failed: {e}")
            return False

    def send_data_chunks(self, start_idx, count):
        """요청받은 개수만큼 데이터 전송 (REVOKE 감지 로직 추가)"""
        self.current_idx = start_idx
        self.is_revoked_in_slot = False # [v3.4] 슬롯 시작 시 플래그 초기화
        
        # [v3.0] 루프 내 REVOKE 감지를 위해 소켓을 비차단 모드로 일시 전환
        self.sock.setblocking(False)
        
        try:
            for i in range(count):
                if self.current_idx >= self.total_chunks:
                    break
                
                # 1. 마스터의 중단 명령(REVOKE) 수신 확인
                try:
                    data, _ = self.sock.recvfrom(1024)
                    msg = data.decode().split('|')
                    if msg[0] == "REVOKE" and msg[1] == self.s_id and msg[2] == self.data_id:
                        print(f"\n[Revoked] 마스터에 의해 전송이 중단되었습니다. (ID: {self.data_id})")
                        self.is_revoked_in_slot = True # [v3.4] 중단 플래그 설정
                        self.save_state() # 중단 시점 저장
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
                
                # [v3.4] Windows 송신 버퍼 오버플로우(10035) 대응 재시도 로직 강화
                retry_count = 0
                while retry_count < 10:
                    try:
                        self.sock.sendto(packet, (DRONE_IP, DRONE_PORT))
                        self.current_idx += 1
                        time.sleep(0.005)
                        break # 성공 시 탈출
                    except (BlockingIOError, socket.error) as e:
                        if getattr(e, 'winerror', None) == 10035 or e.errno == errno.EWOULDBLOCK:
                            time.sleep(0.01) # 대기 시간 상향 (0.001 -> 0.01)
                            retry_count += 1
                            continue
                        print(f"[Network Error] Data chunk send failed: {e}")
                        return 
            
            # 한 번의 GRANT 루프가 끝나면 상태 저장 (100개 단위 등 최적화 가능하나 일단 루프당 1회)
            self.save_state()

        finally:
            # 소켓 상태 원복
            self.sock.setblocking(True)
            self.sock.settimeout(0.5) # [v3.6] 1.0 -> 0.5

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
        print(f"--- [Node {self.s_id}] Multi-Image Queue 가동 (v3.9 Handshake-based Monitoring) ---")
        
        while True:
            self._scan_images()
            
            # [v3.9] 전송할 이미지가 없는 경우 (Handshake-based Monitoring)
            if not self.image_queue and not self.current_image_path:
                self.data_id = "IDLE"
                self.total_chunks = 0
                self.current_idx = 0
                
                if self.send_beacon():
                    # 마스터의 응답(IDLE_ACK)을 기다려 실제 연결 여부 확인 (Deaf Loop 방지)
                    try:
                        self.sock.settimeout(2.0) # 응답 대기 타임아웃
                        data, addr = self.sock.recvfrom(1024)
                        msg = data.decode().split('|')
                        if msg[0] == "IDLE_ACK" and msg[1] == self.s_id:
                            print("--- [Waiting] 전송할 이미지가 없습니다. (연결 정상) ---")
                        elif msg[0] == "GRANT":
                            # 마스터가 아직 이전 세션을 종료하지 않았을 경우 대응
                            print("--- [Waiting] 마스터가 아직 이전 세션을 처리 중입니다... ---")
                    except socket.timeout:
                        print("[Network Error] 드론으로부터 응답이 없습니다. (연결 유실 가능성)")
                    except Exception as e:
                        print(f"[Network Error] {e}")
                    finally:
                        self.sock.settimeout(0.5) # 타임아웃 원복
                else:
                    print("[Network Error] 드론 AP와 연결되지 않았습니다. (송신 실패)")
                
                time.sleep(2) # 5초 -> 2초로 단축하여 반응성 향상
                continue
            
            # [v3.2] 현재 전송 중인 이미지가 없으면 새로 준비
            if not self.current_image_path:
                if not self._prepare_next_image():
                    continue
                print(f"\n>>> [Next Image] {os.path.basename(self.current_image_path)} (ID: {self.data_id}, {self.total_chunks} chunks)")
            
            # Inner Loop: 단일 이미지 전송 루프 (Resume 지원)
            is_finished = False
            while True:
                # [v3.6.1] 비콘 전송 실패 시 대기 후 재시도 (Unreachable Host 대응)
                if not self.send_beacon():
                    time.sleep(2)
                    continue
                
                try:
                    data, addr = self.sock.recvfrom(2048)
                    
                    # [v3.7] 가속 핸드셰이크 (Promiscuous Listening)
                    # 발신자가 드론이면 (자신을 향한 패킷이 아니더라도 감지 가능한 환경인 경우) interval 단축
                    if addr[0] == DRONE_IP:
                        if self.beacon_interval > 0.1:
                            print(f"[Accelerated] Drone signal detected. Resetting back-off interval.")
                            self.beacon_interval = 0.1

                    msg = data.decode().split('|')
                    
                    # [v3.6.1] Early Exit 대응: 마스터가 이미 완료한 세션인 경우
                    if msg[0] == "COMPLETE_ACK" and msg[1] == self.s_id and msg[2] == self.data_id:
                        print(f"\n[Early Exit] 마스터가 이미 완료한 이미지입니다. (ID: {self.data_id})")
                        is_finished = True
                        break

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
                        # [v3.4] 중복 로그 방지: send_data_chunks에서 이미 출력했다면 스킵
                        if not self.is_revoked_in_slot:
                            print(f"\n[Pause] 슬롯 시간이 종료되어 전송이 일시 중단되었습니다. (ID: {self.data_id})")
                        break # 루프 탈출하여 비콘 대기 상태로 복귀 (Resume 준비)

                    elif msg[0] == "ERROR" and msg[1] == self.s_id and msg[2] == self.data_id:
                        error_code = msg[5]
                        print(f"[Error Received] Code: {error_code}")
                        if error_code == "CHECKSUM_FAIL":
                            self.current_idx = 0
                            self.save_state()
                        elif error_code == "SESSION_MISMATCH":
                            self.current_idx = 0
                            self.current_image_path = None # 세션 정보 초기화하여 재시작 유도
                            if os.path.exists(self.state_file): os.remove(self.state_file)
                            break 
                        elif error_code == "TIMEOUT":
                            self.beacon_interval = min(5.0, self.beacon_interval * 2)
                            break 
                        elif error_code == "LIVELOCK_PREVENT":
                            print(f"[Livelock] 진행 중단 감지. 세션을 초기화하고 백오프를 실행합니다. (ID: {self.data_id})")
                            self.current_idx = 0
                            self.save_state()
                            time.sleep(2.0) # 즉각적인 재접속 방지
                            self.beacon_interval = 2.0
                            break 
                    
                    self.beacon_interval = 0.1

                except socket.timeout:
                    # [v3.7] 백오프 상태에서 listen을 계속 수행하기 위해 time.sleep 대신 loop 구조 권장되나
                    # 일단 기존 구조를 유지하며 beacon_interval을 짧게 가져가는 방식으로 구현
                    time.sleep(self.beacon_interval)
                    continue
                except Exception as e:
                    print(f"[Runtime Error] {e}")
                    time.sleep(1)
                    continue
            
            # [v3.3] 정상 종료 시에만 COMPLETE 송신 및 검증 대기
            if is_finished:
                self.send_complete()
                
                # [v3.3] Wait-for-Verdict 상태: 마스터의 최종 판정을 기다림
                print(f"--- [Wait] 마스터의 수집 확정(ACK)을 기다리는 중... (ID: {self.data_id}) ---")
                verdict_received = False
                wait_start = time.time()
                self.sock.settimeout(0.1) # [v3.6] 0.2 -> 0.1 (이미지 간 전환 속도 개선)
                while time.time() - wait_start < 3.0:
                    try:
                        data, addr = self.sock.recvfrom(2048)
                        msg = data.decode().split('|')
                        
                        if len(msg) >= 3 and msg[1] == self.s_id and msg[2] == self.data_id:
                            if msg[0] == "COMPLETE_ACK":
                                print(f"[Verdict] 수집 성공 확정 (COMPLETE_ACK 수신)")
                                verdict_received = True
                                break
                            elif msg[0] == "ERROR":
                                print(f"[Verdict] 수집 실패 보고 (Code: {msg[5]})")
                                is_finished = False # 다시 전송 시도 루프로 돌아가도록 처리
                                break
                    except socket.timeout:
                        continue
                    except Exception as e:
                        break
                
                self.sock.settimeout(0.5) # [v3.6] 1.0 -> 0.5 (타임아웃 원복)
                
                # 판정이 성공적이거나 대기 시간이 종료(낙관적 완료)되면 정리
                if is_finished:
                    self._finalize_current_image()
                else:
                    print(f"--- [Retry] 검증 실패로 인해 세션을 유지합니다. ---")
            else:
                # REVOKE 등으로 인한 일시 중단 시에는 아무것도 하지 않고 다음 BEACON 루프로 돌아감
                print(f"--- [Paused] {self.s_id} 전송 일시 중단 (ID: {self.data_id}, Progress: {self.current_idx}/{self.total_chunks}) ---")

if __name__ == "__main__":
    node_id = sys.argv[1] if len(sys.argv) > 1 else "S01"
    # 'images' 폴더가 없으면 생성하고 테스트 파일을 하나 복사해둘 수 있음
    node = SensorNode(node_id, "images")
    node.run()
