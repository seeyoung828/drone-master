import socket
import time
import struct
import sys
import threading
import subprocess
import re
import math
from db_manager import DroneDB

# --- [설정 및 상수] ---
UDP_IP = "0.0.0.0" 
UDP_PORT = 5005
STALE_TIMEOUT = 10.0   # 10초간 비콘 없으면 목록에서 제거
AGING_THRESHOLD = 60.0 # Aging 가산점 최대 기준 (초)
SLOT_TIME_LIMIT = 5.0  # 한 노드당 최대 점유 시간 (초)
IDLE_TIMEOUT = 1.5     # [v3.4] 1.2 -> 1.5로 상향하여 안정성 확보 (GRANT 재전송 주기와의 간격 확보)
REVOKE_RSSI_THRESHOLD = -85  # [v3.8] 즉시 회수 RSSI 임계치
LIVELOCK_THRESHOLD = 30      # [v3.8] 연속 중복 수신 임계치

# --- [최적화된 스케줄링 가중치 (Grid Search 결과)] ---
W1_RSSI = 0.26      # RSSI (tanh 적용)
W2_COMP = 0.48      # Completion (exp 적용)
W3_AGING = 0.26     # Aging (선형)

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
    """
    [v4.1] 배터리 조기 방전 방지 및 장애 격리 가드가 탑재된 비선형 스케줄링
    Score = (W1_RSSI * tanh(NormRSSI)) + exp(W2_COMP * NormCompletion) + (W3_AGING * NormAging) + (W4_BATT_URGENCY)
    """
    now = time.time()
    if info.get('timeout_until', 0) > now:
        return 0.0

    # [추가] 장애 격리(SUSPENDED) 노드는 스케줄링 점수를 0.0 처리하여 후보군에서 배제
    if db.get_sensor_status(s_id) == 'SUSPENDED':
        return 0.0

    # 1. RSSI Score (W1 = 0.26, tanh 적용)
    rssi = get_node_rssi(info['addr'][0])
    norm_rssi = max(0.0, min(1.0, (rssi + 100) / 70))
    rssi_term = math.tanh(norm_rssi)

    # 2. Completion Score (W2 = 0.48, exp 적용)
    total = info.get('total', 0)
    curr = info.get('curr', 0)
    norm_completion = curr / total if total > 0 else 0.0
    exp_term = math.exp(W2_COMP * norm_completion)

    # 3. Aging Score (W3 = 0.26, 선형)
    wait_time = now - info['last_seen']
    norm_aging = min(1.0, wait_time / AGING_THRESHOLD)

    # 4. 배터리 잔량에 따른 비선형 가중치 추가 (20% 이하일 때 우선도 급격 증가)
    battery = db.get_sensor_battery(s_id)
    battery_urgency = 0.0
    if battery <= 20.0:
        battery_urgency = math.exp((20.0 - battery) * 0.1)

    # 종합 비선형 점수 계산
    score = (W1_RSSI * rssi_term) + exp_term + (W3_AGING * norm_aging) + battery_urgency
    return score

def get_dynamic_n(ip):
    """RSSI에 따른 가변 청크 수(N) 결정"""
    rssi = get_node_rssi(ip)
    if rssi > -50: return 40
    if rssi > -75: return 20
    return 10

dprint("--- [Drone Master] v3.9 Handshake-based Active Monitoring Start ---")

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
            # 1. 패킷 분할 파싱 (BEACON과 타 패킷 분기)
            temp_parts = data.split(b'|')
            if len(temp_parts) < 6: continue
            
            msg_type = temp_parts[0].decode('utf-8', errors='ignore')
            
            mac_addr = ''
            esp_name = 'Unknown'
            battery = 100.0
            
            if msg_type == "BEACON":
                if len(temp_parts) < 9: continue
                s_id = temp_parts[1].decode('utf-8', errors='ignore')
                data_id = temp_parts[2].decode('utf-8', errors='ignore')
                total = int(temp_parts[3])
                curr = int(temp_parts[4])
                last_f = int(temp_parts[5])
                mac_addr = temp_parts[6].decode('utf-8', errors='ignore')
                esp_name = temp_parts[7].decode('utf-8', errors='ignore')
                battery = float(temp_parts[8])
            else:
                parts = data.split(b'|', 6)
                if len(parts) < 6: continue
                s_id     = parts[1].decode('utf-8', errors='ignore')
                data_id  = parts[2].decode('utf-8', errors='ignore')
                total    = int(parts[3])
                curr     = int(parts[4])
                last_f   = int(parts[5])
            
            # [v3.6.2] 수신 패킷 로깅 강화
            # dprint(f"[Recv] {msg_type} from {s_id} (ID: {data_id}, Idx: {curr})")

            # 2. 메시지 유형별 처리
            if msg_type == "BEACON":
                rssi_now = get_node_rssi(addr[0])
                
                # DB 레지스트리 상태 영구 저장 및 최신화
                db.register_or_update_sensor(s_id, esp_name, mac_addr, addr[0], rssi_now, battery)

                # IP 변동에 따른 세션 실시간 IP/PORT 바인딩 복구
                if s_id in sensors_mem:
                    old_addr = sensors_mem[s_id]['addr']
                    if old_addr[0] != addr[0]:
                        dprint(f"[Session Recovery] '{esp_name}'({s_id}) IP 변동 감지 및 갱신: {old_addr[0]} -> {addr[0]}")
                        sensors_mem[s_id]['addr'] = addr

                # [v3.8] IDLE 비콘 처리: RSSI 정보만 갱신하고 스케줄링 대상에서는 제외
                if data_id == "IDLE":
                    if s_id in sensors_mem:
                        sensors_mem[s_id]['last_seen'] = now # 타임아웃 방지
                    
                    # [v3.9] IDLE_ACK 송신: 노드에게 연결이 정상임을 응답
                    ack_msg = f"IDLE_ACK|{s_id}|IDLE|0|0|0|"
                    sock.sendto(ack_msg.encode(), addr)
                    continue

                db.prepare_session(s_id, data_id, total)
                
                # [v3.5] 이미 완료된 세션인 경우 즉시 COMPLETE_ACK 송신하고 스케줄링에서 제외
                if db.is_session_completed(s_id, data_id):
                    dprint(f"[Early Exit] {s_id} 이미 완료된 세션입니다: {data_id}")
                    ack_msg = f"COMPLETE_ACK|{s_id}|{data_id}|0|0|0|"
                    sock.sendto(ack_msg.encode(), addr)
                    dprint(f"[Sent] COMPLETE_ACK to {s_id} (ID: {data_id})")
                    
                    if s_id in sensors_mem:
                        del sensors_mem[s_id]
                    if current_target == s_id:
                        current_target = None
                    continue

                if s_id not in sensors_mem:
                    sensors_mem[s_id] = {
                        'addr': addr, 'total': total, 'curr': curr, 
                        'last_seen': now, 'data_id': data_id,
                        'retry_count': 0, 'last_grant_time': 0, 'timeout_until': 0,
                        'consecutive_duplicates': 0  # [v3.8] 초기화
                    }
                    dprint(f"[System] {s_id} 신규 노드 등록 (Addr: {addr})")
                else:
                    if sensors_mem[s_id]['data_id'] != data_id:
                        dprint(f"[Session] {s_id} 세션 전환 감지: {sensors_mem[s_id]['data_id']} -> {data_id}")
                        sensors_mem[s_id]['retry_count'] = 0
                        sensors_mem[s_id]['consecutive_duplicates'] = 0 # [v3.8] 세션 전환 시 초기화
                    
                    sensors_mem[s_id].update({
                        'addr': addr, 'total': total, 'curr': curr, 
                        'last_seen': now, 'data_id': data_id
                    })

                if (s_id, data_id) not in prepared_sessions:
                    dprint(f"[Session] {s_id} 신규 세션 준비됨: {data_id}")
                    prepared_sessions.add((s_id, data_id))
                
                sensors_mem[s_id]['curr'] = db.get_next_missing_idx(s_id, data_id)
                db.update_sensor_status(s_id, get_node_rssi(addr[0]))

            elif msg_type == "DATA":
                payload = parts[6]
                # [v3.6] 어떤 노드의 데이터든 수신 시 슬롯 카운트 증가 (Buffer Over-consumption 방지)
                chunks_in_slot += 1 

                if s_id in sensors_mem:
                    sensors_mem[s_id].update({
                        'last_seen': now,
                        'last_grant_time': time.time(),
                        'retry_count': 0
                    })
                    
                    # [v3.8] DB 저장 결과에 따른 중복 카운터 관리
                    result = db.save_fragment(s_id, data_id, curr, payload)
                    
                    if result == "SUCCESS":
                        sensors_mem[s_id]['consecutive_duplicates'] = 0 # 신규 조각이면 카운트 리셋
                        sensors_mem[s_id]['curr'] = db.get_next_missing_idx(s_id, data_id)
                        # 100개마다 진행 상황 출력
                        if curr % 100 == 0:
                            dprint(f"[Data] {s_id} 수신 중... (Idx: {curr}/{total})")
                    elif result == "DUPLICATE":
                        sensors_mem[s_id]['consecutive_duplicates'] += 1 # 중복이면 카운트 증가
                    elif result == "ERROR":
                        # dprint(f"[Data Error] {s_id} fragment save failed.")
                        pass

            elif msg_type == "COMPLETE":
                dprint(f"[Recv] COMPLETE from {s_id} (ID: {data_id})")
                # [v3.4] 검증 전 유예 시간 단축 (0.1 -> 0.02) 및 Flush 우선 수행
                time.sleep(0.02)
                db.close_file()
                
                checksum_bin = parts[6]
                # [Fix] 수신 카운트 먼저 확인하여 조기 실패 방지
                # 수정 사항 2
                with db.lock:
                    cursor = db.conn.cursor()
                    cursor.execute("SELECT total_chunks, received_count FROM image_sessions WHERE s_id=? AND data_id=?", (s_id, data_id))
                    chk = cursor.fetchone()

                if chk:
                    dprint(f"[Verify Pre-check] {s_id} total={chk[0]}, received={chk[1]}")
                success = db.verify_and_finalize(s_id, data_id, checksum_bin)

                if success:
                    dprint(f"[Success] {s_id} 검증 통과. 세션 종료: {data_id}")
                    # [v3.4] 노드에게 수집 확정(COMPLETE_ACK) 알림 (유실 대비 3회 송신)
                    ack_msg = f"COMPLETE_ACK|{s_id}|{data_id}|0|0|0|"
                    for _ in range(3):
                        sock.sendto(ack_msg.encode(), addr)
                        time.sleep(0.01)

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

            # [v3.4] GRANT 후 응답 없음 재시도 임계값 단축 (1.2 -> 0.8)
            if time_since_grant > 0.8: 
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

            # [v3.8] 실시간 선점형 감시 (RSSI 및 Livelock)
            if current_target:
                rssi = get_node_rssi(info['addr'][0])
                duplicates = info.get('consecutive_duplicates', 0)
                trigger_preemptive = False
                pre_reason = ""

                if rssi < REVOKE_RSSI_THRESHOLD:
                    pre_reason = f"Low RSSI ({rssi}dBm)"
                    info['timeout_until'] = now_check + 5.0 # 5초 페널티
                    trigger_preemptive = True
                elif duplicates >= LIVELOCK_THRESHOLD:
                    pre_reason = f"Livelock Detected ({duplicates} dups)"
                    send_error(current_target, info['data_id'], info['addr'], "LIVELOCK_PREVENT")
                    db.set_sensor_suspended(current_target)  # 장애 격리 등록
                    info['timeout_until'] = now_check + 30.0 # 30초 페널티
                    trigger_preemptive = True

                if trigger_preemptive:
                    dprint(f"[Preemptive End] {current_target} 종료 ({pre_reason})")
                    revoke_msg = f"REVOKE|{current_target}|{info['data_id']}|0|0|0|"
                    for _ in range(3):
                        sock.sendto(revoke_msg.encode(), info['addr'])
                        time.sleep(0.01)
                    db.commit()
                    db.close_file()
                    current_target = None
                    chunks_in_slot = 0

            # 정상 슬롯 종료 조건 확인 (Idle Timeout 추가)
            if current_target:
                time_diff = now_check - slot_start_time
                max_n = get_dynamic_n(info['addr'][0])
                
                # [v3.6] 종료 전 최종 패킷 확인 (Buffer Drain)
                # 이 시점에서 이미 recvfrom 루프를 돌았으므로, 
                # 여기서 추가로 체크하기보다는 조건문 내에서 정밀하게 판단함.
                
                if time_diff > SLOT_TIME_LIMIT or chunks_in_slot >= max_n or time_since_grant > IDLE_TIMEOUT:
                    # [v3.6] 중요: 만약 이번 루프에서 이미 COMPLETE를 처리했다면 
                    # (즉, current_target이 None이 되었다면) 이 블록은 실행되지 않음.
                    
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
                    chunks_in_slot = 0 # [v3.6] 통계 초기화 명시

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
            dprint(f"[Sent] GRANT to {best_s_id} (ID: {info['data_id']}, Start: {info['curr']}, N: {n_limit})")

def active_polling_loop():
    """
    15초 주기로 DB에서 오프라인 노드를 찾아 마지막 알려진 IP로 POLL 패킷을 쏘아 깨웁니다.
    """
    while True:
        try:
            offline_nodes = db.get_offline_sensors()
            for node in offline_nodes:
                s_id, last_ip = node['s_id'], node['last_known_ip']
                if last_ip:
                    poll_msg = f"POLL_REQ|{s_id}|IDLE|0|0|0|"
                    sock.sendto(poll_msg.encode(), (last_ip, UDP_PORT))
        except Exception as e:
            pass
        time.sleep(15.0)

# 백그라운드 구동 시작
threading.Thread(target=active_polling_loop, daemon=True).start()