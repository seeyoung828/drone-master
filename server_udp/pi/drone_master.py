import socket
import time
import struct
import sys
from db_manager import DroneDB

# --- [설정 및 상수] ---
UDP_IP = "0.0.0.0" 
UDP_PORT = 5005
STALE_TIMEOUT = 10.0   # 10초간 비콘 없으면 목록에서 제거
AGING_THRESHOLD = 60.0 # Aging 가산점 최대 기준 (초)
SLOT_TIME_LIMIT = 5.0  # 한 노드당 최대 점유 시간 (초)

def dprint(*args, **kwargs):
    """실시간 로그 확인을 위해 즉시 출력(flush)하는 함수"""
    print(*args, **kwargs)
    sys.stdout.flush()

db = DroneDB()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.settimeout(0.1) 

# 스케줄링을 위한 메모리 캐시
sensors_mem = {}
prepared_sessions = set()  # (s_id, data_id) 캐시
current_target = None
slot_start_time = 0
chunks_in_slot = 0

def calculate_score(s_id, info):
    """Score = (0.4 * NormRSSI) + (0.3 * NormRemaining) + (0.3 * NormAging)"""
    now = time.time()
    norm_rssi = max(0, (info['rssi'] + 100) / 70)
    remaining = info['total'] - info['curr']
    norm_remaining = remaining / info['total'] if info['total'] > 0 else 0
    wait_time = now - info['last_seen']
    norm_aging = min(1.0, wait_time / AGING_THRESHOLD)
    
    score = (0.4 * norm_rssi) + (0.3 * norm_remaining) + (0.3 * norm_aging)
    return score

def get_dynamic_n(rssi):
    """RSSI에 따른 가변 청크 수(N) 결정"""
    if rssi > -50: return 40
    if rssi > -75: return 20
    return 10

dprint("--- [Drone Master] v2.4 Intelligent Scheduler Start ---")

while True:
    now = time.time()

    # [해결 1] Stale 노드 정리: 비콘이 끊긴 노드는 메모리에서 제거
    stale_list = [s for s, info in sensors_mem.items() if now - info['last_seen'] > STALE_TIMEOUT]
    for s in stale_list:
        dprint(f"[System] {s} 노드 접속 끊김 (Timeout)")
        del sensors_mem[s]
        if current_target == s:
            current_target = None

    try:
        # 1. 패킷 수신 및 파싱
        data, addr = sock.recvfrom(2048)
        parts = data.split(b'|', 7)
        if len(parts) < 7: continue
        
        msg_type = parts[0].decode()
        s_id     = parts[1].decode()
        data_id  = parts[2].decode()
        total    = int(parts[3])
        curr     = int(parts[4])
        rssi     = int(parts[5])
        last_f   = int(parts[6])

        # 2. 메시지 유형별 처리
        if msg_type == "BEACON":
            sensors_mem[s_id] = {
                'addr': addr, 'total': total, 'curr': curr, 
                'rssi': rssi, 'last_seen': time.time(), 'data_id': data_id
            }
            db.update_sensor_status(s_id, rssi)

        elif msg_type == "DATA":
            payload = parts[7]
            # [Optimization] 이미 준비된 세션이면 prepare_session 호출 생략
            if (s_id, data_id) not in prepared_sessions:
                db.prepare_session(s_id, data_id, total)
                prepared_sessions.add((s_id, data_id))

            if db.save_fragment(s_id, data_id, curr, payload):
                if s_id in sensors_mem:
                    # [해결] 단순 last_received가 아닌, DB의 비트마스크를 확인하여 첫 번째 유실 지점을 curr로 설정
                    sensors_mem[s_id]['curr'] = db.get_next_missing_idx(s_id, data_id)
                chunks_in_slot += 1

        elif msg_type == "COMPLETE":
            # [해결 2] 무결성 검증 및 세션 종료 시 조건부 타겟 해제
            checksum_bin = parts[7]
            success = db.verify_and_finalize(s_id, data_id, checksum_bin)
            
            if success:
                # 성공했을 때만 메모리에서 제거
                if s_id in sensors_mem:
                    del sensors_mem[s_id]
                if (s_id, data_id) in prepared_sessions:
                    prepared_sessions.remove((s_id, data_id))
            else:
                # 실패(유실) 시 노드를 유지하여 재전송 기회 부여
                if s_id in sensors_mem:
                    sensors_mem[s_id]['curr'] = db.get_next_missing_idx(s_id, data_id)
            
            current_target = None

    except socket.timeout:
        pass 

    # 3. 스케줄링 결정 로직
    # 슬롯 종료 조건 확인 (시간 초과, 전송량 초과, 혹은 타겟이 목록에서 사라짐)
    if current_target:
        if current_target not in sensors_mem:
            current_target = None
        else:
            time_diff = now - slot_start_time
            max_n = get_dynamic_n(sensors_mem[current_target]['rssi'])
            
            if time_diff > SLOT_TIME_LIMIT or chunks_in_slot >= max_n:
                dprint(f"[Slot End] {current_target} 점유 종료 (Time: {time_diff:.1f}s, Chunks: {chunks_in_slot})")
                current_target = None

    # 새로운 타겟 선정 (Idle 상태일 때)
    if not current_target and sensors_mem:
        best_s_id = None
        max_score = -1
        
        for s_id, info in sensors_mem.items():
            score = calculate_score(s_id, info)
            if score > max_score:
                max_score = score
                best_s_id = s_id
        
        if best_s_id:
            current_target = best_s_id
            slot_start_time = time.time()
            chunks_in_slot = 0
            
            info = sensors_mem[best_s_id]
            n_limit = get_dynamic_n(info['rssi'])
            # GRANT|S_ID|Data_ID|Start_Idx|Count|
            grant_msg = f"GRANT|{best_s_id}|{info['data_id']}|{info['curr']}|{n_limit}|"
            sock.sendto(grant_msg.encode(), info['addr'])