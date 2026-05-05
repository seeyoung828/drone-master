import socket
import time
import struct
import sys
import threading
import subprocess
import re
from db_manager import DroneDB

# --- [설정 및 상수] ---
UDP_IP = "0.0.0.0" 
UDP_PORT = 5005
STALE_TIMEOUT = 10.0   # 10초간 비콘 없으면 목록에서 제거
AGING_THRESHOLD = 60.0 # Aging 가산점 최대 기준 (초)
SLOT_TIME_LIMIT = 5.0  # 한 노드당 최대 점유 시간 (초)
IDLE_TIMEOUT = 1.2     # [Fix] 0.5 -> 1.2로 상향하여 안정성 확보

def dprint(*args, **kwargs):
    """실시간 로그 확인을 위해 즉시 출력(flush)하는 함수"""
    print(*args, **kwargs)
    sys.stdout.flush()

db = DroneDB()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.settimeout(0.001) 

# 스케줄링을 위한 메모리 캐시
sensors_mem = {}
prepared_sessions = set()  # (s_id, data_id) 캐시
current_target = None
slot_start_time = 0
chunks_in_slot = 0

# --- [Master-Side RSSI Tracking] ---
rssi_cache = {}    # {MAC: RSSI}
ip_mac_map = {}    # {IP: MAC}
cache_lock = threading.Lock()

def update_system_info():
    """백그라운드에서 RSSI 및 ARP 정보를 주기적으로 갱신"""
    interface = "wlx54c9ff00053c" # 실제 무선 인터페이스 이름
    while True:
        try:
            # 1. RSSI 정보 수집 (iw station dump)
            res_iw = subprocess.check_output(["iw", "dev", interface, "station", "dump"]).decode()
            stations = re.findall(r"Station ([0-9a-f:]+).+?signal:\s+(-?\d+) dBm", res_iw, re.DOTALL)
            
            # 2. ARP 정보 수집 (/proc/net/arp)
            with open("/proc/net/arp", "r") as f:
                arp_lines = f.readlines()[1:] # Header 스킵
            
            new_ip_mac = {}
            for line in arp_lines:
                cols = line.split()
                if len(cols) >= 4:
                    new_ip_mac[cols[0]] = cols[3].lower()

            with cache_lock:
                for mac, sig in stations:
                    rssi_cache[mac.lower()] = int(sig)
                ip_mac_map.update(new_ip_mac)
                
        except Exception as e:
            pass # dprint(f"[Internal] System info update failed: {e}")
        
        time.sleep(1.0) # 1초 주기로 갱신

# 백그라운드 스레드 시작
threading.Thread(target=update_system_info, daemon=True).start()

def get_node_rssi(ip):
    """IP를 기반으로 캐시된 RSSI 값을 반환"""
    with cache_lock:
        mac = ip_mac_map.get(ip)
        if mac:
            return rssi_cache.get(mac, -100)
    return -100

def send_error(s_id, data_id, addr, error_code):
    """에러 패킷 송신"""
    dprint(f"[Error Sent] {s_id} -> {error_code}")
    # v3.0 규격에 맞춰 에러 메시지도 수정 가능하나 일단 기존 유지 (RSSI 필드 없음 대응 필요 시 수정)
    err_msg = f"ERROR|{s_id}|{data_id}|0|0|{error_code}"
    sock.sendto(err_msg.encode(), addr)

def calculate_score(s_id, info):
    """Score = (0.3 * NormRSSI) + (0.4 * NormRemaining) + (0.3 * NormAging)"""
    now = time.time()
    if info.get('timeout_until', 0) > now:
        return 0

    # 캐시된 실시간 RSSI 사용
    rssi = get_node_rssi(info['addr'][0])
    norm_rssi = max(0, (rssi + 100) / 70)
    
    remaining = info['total'] - info['curr']
    norm_remaining = remaining / info['total'] if info['total'] > 0 else 0
    wait_time = now - info['last_seen']
    norm_aging = min(1.0, wait_time / AGING_THRESHOLD)

    score = (0.3 * norm_rssi) + (0.4 * norm_remaining) + (0.3 * norm_aging)
    return score

def get_dynamic_n(ip):
    """RSSI에 따른 가변 청크 수(N) 결정"""
    rssi = get_node_rssi(ip)
    if rssi > -50: return 40
    if rssi > -75: return 20
    return 10

dprint("--- [Drone Master] v3.0 Master-Side RSSI Tracking Start ---")

while True:
    now = time.time()

    # Stale 노드 정리
    stale_list = [s for s, info in sensors_mem.items() if now - info['last_seen'] > STALE_TIMEOUT]
    for s in stale_list:
        dprint(f"[System] {s} 노드 접속 끊김 (Timeout)")
        del sensors_mem[s]
        if current_target == s:
            current_target = None

    # [v3.0] 패킷 수집 및 처리 (v3.0 규격 반영: RSSI 필드 제거)
    while True:
        try:
            data, addr = sock.recvfrom(8192)
            parts = data.split(b'|', 6) # v3.0: Type|S_ID|Data_ID|Total|Idx|Last_Flag|Payload
            if len(parts) < 6: continue

            msg_type = parts[0].decode()
            s_id     = parts[1].decode()
            data_id  = parts[2].decode()
            total    = int(parts[3])
            curr     = int(parts[4])
            last_f   = int(parts[5])
            
            # 2. 메시지 유형별 처리
            if msg_type == "BEACON":
                if s_id not in sensors_mem:
                    sensors_mem[s_id] = {
                        'addr': addr, 'total': total, 'curr': curr, 
                        'last_seen': now, 'data_id': data_id,
                        'retry_count': 0, 'last_grant_time': 0, 'timeout_until': 0
                    }
                else:
                    if sensors_mem[s_id]['data_id'] != data_id:
                        dprint(f"[Session] {s_id} 세션 전환 감지: {sensors_mem[s_id]['data_id']} -> {data_id}")
                        sensors_mem[s_id]['retry_count'] = 0
                    
                    sensors_mem[s_id].update({
                        'addr': addr, 'total': total, 'curr': curr, 
                        'last_seen': now, 'data_id': data_id
                    })

                db.prepare_session(s_id, data_id, total)
                if (s_id, data_id) not in prepared_sessions:
                    dprint(f"[Session] {s_id} 신규 세션 준비됨: {data_id}")
                    prepared_sessions.add((s_id, data_id))
                
                sensors_mem[s_id]['curr'] = db.get_next_missing_idx(s_id, data_id)
                # RSSI 업데이트는 update_system_info 스레드에서 수행하므로 여기서는 DB 업데이트만 호출 (마스터 측정값 기반)
                db.update_sensor_status(s_id, get_node_rssi(addr[0]))

            elif msg_type == "DATA":
                payload = parts[6]
                if s_id in sensors_mem:
                    sensors_mem[s_id].update({
                        'last_seen': now,
                        'last_grant_time': time.time(),
                        'retry_count': 0
                    })

                if db.save_fragment(s_id, data_id, curr, payload):
                    if s_id in sensors_mem:
                        sensors_mem[s_id]['curr'] = db.get_next_missing_idx(s_id, data_id)
                    chunks_in_slot += 1

            elif msg_type == "COMPLETE":
                # [v3.1] 검증 전 유예 시간 확보 (지연 도착 DATA 패킷 처리 유도)
                time.sleep(0.1)
                # [v3.1] 검증 전 쓰기 핸들 강제 종료 (Flush & Close 보장)
                db.close_file()
                
                checksum_bin = parts[6]
                success = db.verify_and_finalize(s_id, data_id, checksum_bin)

                if success:
                    if s_id in sensors_mem:
                        del sensors_mem[s_id]
                    if (s_id, data_id) in prepared_sessions:
                        prepared_sessions.remove((s_id, data_id))
                    current_target = None
                else:
                    dprint(f"[Verification Failed] {s_id} CRC Checksum mismatch.")
                    send_error(s_id, data_id, addr, "CHECKSUM_FAIL")
                    db.reset_session(s_id, data_id) 
                    if s_id in sensors_mem:
                        sensors_mem[s_id]['curr'] = 0
                    current_target = None 

        except socket.timeout:
            break 
        except Exception as e:
            # dprint(f"[System Error] {e}")
            break

    # 3. 스케줄링 및 재시도 로직
    if current_target:
        if current_target not in sensors_mem:
            current_target = None
        else:
            info = sensors_mem[current_target]
            now_check = time.time()
            time_since_grant = now_check - info['last_grant_time']

            # GRANT 후 응답 없음 (재시도 로직)
            if time_since_grant > 1.2: 
                if info['retry_count'] < 3:
                    info['retry_count'] += 1
                    info['last_grant_time'] = now_check
                    n_limit = get_dynamic_n(info['addr'][0])
                    grant_msg = f"GRANT|{current_target}|{info['data_id']}|{info['curr']}|{n_limit}|"
                    sock.sendto(grant_msg.encode(), info['addr'])
                    dprint(f"[Retry] {current_target} GRANT 재전송 ({info['retry_count']}/3)")
                else:
                    dprint(f"[Timeout] {current_target} 재시도 횟수 초과. 슬롯 강제 종료.")
                    send_error(current_target, info['data_id'], info['addr'], "TIMEOUT")
                    info['timeout_until'] = now_check + 30 
                    current_target = None

            # 정상 슬롯 종료 조건 확인 (Idle Timeout 추가)
            if current_target:
                time_diff = now_check - slot_start_time
                max_n = get_dynamic_n(info['addr'][0])
                
                if time_diff > SLOT_TIME_LIMIT or chunks_in_slot >= max_n or time_since_grant > IDLE_TIMEOUT:
                    reason = "Time Limit" if time_diff > SLOT_TIME_LIMIT else "Chunk Limit"
                    if time_since_grant > IDLE_TIMEOUT: reason = "Idle Timeout"
                    
                    dprint(f"[Slot End] {current_target} 종료 ({reason}, Chunks: {chunks_in_slot})")
                    
                    # [v3.1] 노드에게 명시적 전송 중단(REVOKE) 알림 (UDP 유실 대비 3회 송신)
                    revoke_msg = f"REVOKE|{current_target}|{info['data_id']}|0|0|0|"
                    for _ in range(3):
                        sock.sendto(revoke_msg.encode(), info['addr'])
                        time.sleep(0.01) # 미세한 간격 추가

                    db.commit() 
                    db.close_file() 
                    info['timeout_until'] = now_check + 2.0
                    current_target = None

    # 새로운 타겟 선정
    if not current_target and sensors_mem:
        best_s_id = None
        max_score = -1

        for s_id, info in sensors_mem.items():
            score = calculate_score(s_id, info)
            if score > max_score:
                max_score = score
                best_s_id = s_id

        if best_s_id and max_score > 0:
            current_target = best_s_id
            slot_start_time = time.time()
            chunks_in_slot = 0

            info = sensors_mem[best_s_id]
            info['last_grant_time'] = time.time()
            info['retry_count'] = 0
            n_limit = get_dynamic_n(info['addr'][0])
            grant_msg = f"GRANT|{best_s_id}|{info['data_id']}|{info['curr']}|{n_limit}|"
            sock.sendto(grant_msg.encode(), info['addr'])
