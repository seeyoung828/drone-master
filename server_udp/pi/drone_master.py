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

def send_error(s_id, data_id, addr, error_code):
    """에러 패킷 송신"""
    dprint(f"[Error Sent] {s_id} -> {error_code}")
    err_msg = f"ERROR|{s_id}|{data_id}|0|0|0|{error_code}"
    sock.sendto(err_msg.encode(), addr)

def calculate_score(s_id, info):
    """Score = (0.3 * NormRSSI) + (0.4 * NormRemaining) + (0.3 * NormAging)"""
    now = time.time()
    # 타임아웃 발생 노드는 일정 시간 동안 패널티 (점수 0)
    if info.get('timeout_until', 0) > now:
        return 0

    norm_rssi = max(0, (info['rssi'] + 100) / 70)
    remaining = info['total'] - info['curr']
    norm_remaining = remaining / info['total'] if info['total'] > 0 else 0
    wait_time = now - info['last_seen']
    norm_aging = min(1.0, wait_time / AGING_THRESHOLD)

    score = (0.3 * norm_rssi) + (0.4 * norm_remaining) + (0.3 * norm_aging)

    # 재시도 중인 노드는 우선순위 소폭 조정 가능 (여기서는 단순 유지)
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
            if s_id not in sensors_mem:
                sensors_mem[s_id] = {
                    'addr': addr, 'total': total, 'curr': curr, 
                    'rssi': rssi, 'last_seen': now, 'data_id': data_id,
                    'retry_count': 0, 'last_grant_time': 0, 'timeout_until': 0
                }
            else:
                sensors_mem[s_id].update({
                    'addr': addr, 'total': total, 'curr': curr, 
                    'rssi': rssi, 'last_seen': now, 'data_id': data_id
                })

            # [에러 처리] Data_ID 변경 감지 (Purge)
            if db.prepare_session(s_id, data_id, total):
                if (s_id, data_id) not in prepared_sessions:
                    dprint(f"[Session] {s_id} 신규 세션 준비됨: {data_id}")
                    prepared_sessions.add((s_id, data_id))
                    # 세션 미스매치 시 슬레이브에게 알림 (옵션)
                    send_error(s_id, data_id, addr, "SESSION_MISMATCH")

            db.update_sensor_status(s_id, rssi)

        elif msg_type == "DATA":
            payload = parts[7]

            # 데이터 수신 시 재시도 횟수 리셋
            if s_id in sensors_mem:
                sensors_mem[s_id]['retry_count'] = 0

            if db.save_fragment(s_id, data_id, curr, payload):
                if s_id in sensors_mem:
                    sensors_mem[s_id]['curr'] = db.get_next_missing_idx(s_id, data_id)
                chunks_in_slot += 1

        elif msg_type == "COMPLETE":
            checksum_bin = parts[7]
            success = db.verify_and_finalize(s_id, data_id, checksum_bin)

            if success:
                if s_id in sensors_mem:
                    del sensors_mem[s_id]
                if (s_id, data_id) in prepared_sessions:
                    prepared_sessions.remove((s_id, data_id))
                current_target = None
            else:
                # [에러 처리] CRC32 검증 실패
                dprint(f"[Verification Failed] {s_id} CRC Checksum mismatch.")
                send_error(s_id, data_id, addr, "CHECKSUM_FAIL")
                db.reset_session(s_id, data_id) # 세션 초기화하여 재수집 유도
                if s_id in sensors_mem:
                    sensors_mem[s_id]['curr'] = 0
                current_target = None # 타겟 해제하여 다른 노드에게 기회 부여

    except socket.timeout:
        pass 

    # 3. 스케줄링 및 재시도 로직
    if current_target:
        if current_target not in sensors_mem:
            current_target = None
        else:
            info = sensors_mem[current_target]
            time_since_grant = now - info['last_grant_time']

            # [에러 처리] GRANT 후 응답 없음 (재시도 로직)
            if time_since_grant > 0.3: # 0.3초 타임아웃
                if info['retry_count'] < 3:
                    info['retry_count'] += 1
                    info['last_grant_time'] = now
                    n_limit = get_dynamic_n(info['rssi'])
                    grant_msg = f"GRANT|{current_target}|{info['data_id']}|{info['curr']}|{n_limit}|"
                    sock.sendto(grant_msg.encode(), info['addr'])
                    dprint(f"[Retry] {current_target} GRANT 재전송 ({info['retry_count']}/3)")
                else:
                    dprint(f"[Timeout] {current_target} 재시도 횟수 초과. 슬롯 강제 종료.")
                    send_error(current_target, info['data_id'], info['addr'], "TIMEOUT")
                    info['timeout_until'] = now + 30 # 30초간 스케줄링 제외
                    current_target = None

            # 정상 슬롯 종료 조건 확인
            if current_target:
                time_diff = now - slot_start_time
                max_n = get_dynamic_n(info['rssi'])
                if time_diff > SLOT_TIME_LIMIT or chunks_in_slot >= max_n:
                    dprint(f"[Slot End] {current_target} 점유 종료 (Time: {time_diff:.1f}s, Chunks: {chunks_in_slot})")
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
            slot_start_time = now
            chunks_in_slot = 0

            info = sensors_mem[best_s_id]
            info['last_grant_time'] = now
            info['retry_count'] = 0
            n_limit = get_dynamic_n(info['rssi'])
            grant_msg = f"GRANT|{best_s_id}|{info['data_id']}|{info['curr']}|{n_limit}|"
            sock.sendto(grant_msg.encode(), info['addr'])