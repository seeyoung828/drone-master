import socket
import time
import os

# 네트워크 및 경로 설정
UDP_IP = "192.168.4.1"
UDP_PORT = 5005
RECEIVED_DIR = "received_data/"
if not os.path.exists(RECEIVED_DIR): os.makedirs(RECEIVED_DIR)

# 소켓 설정
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.settimeout(0.3) # 스케줄링 주기 결정

# 메모리 기반 상태 관리
sensors = {} 
# 구조: { id: {'rssi': 0, 'remaining': 0, 'addr': None, 'last_seen': 0, 'max_idx': -1} }

def calculate_score(rssi, remaining):
    """ RSSI(40%)와 잔여 데이터(60%)를 조합한 우선순위 점수 """
    norm_rssi = max(0, (rssi + 100) / 70) # -100~-30 범위를 0~1로 정규화
    norm_rem = min(remaining, 100) / 100  # 너무 큰 잔여량에 의한 왜곡 방지
    return (norm_rssi * 0.4) + (norm_rem * 0.6)

print("--- [Drone Master] 지능형 적응형 스케줄러 가동 (Final) ---")

while True:
    try:
        data, addr = sock.recvfrom(4096)
        raw_parts = data.split(b'|', 5)
        msg_type = raw_parts[0].decode()

        # 1. 비콘 수신: 센서의 존재와 상태 파악
        if msg_type == "BEACON":
            s_id = raw_parts[1].decode()
            total, curr, rssi = int(raw_parts[2]), int(raw_parts[3]), int(raw_parts[4])
            
            if s_id not in sensors:
                sensors[s_id] = {'max_idx': -1}
            
            sensors[s_id].update({
                'rssi': rssi,
                'remaining': total - curr,
                'addr': addr,
                'last_seen': time.time()
            })

        # 2. 데이터 수신: 실제 바이너리 조각 저장
        elif msg_type == "DATA":
            s_id = raw_parts[1].decode()
            idx = int(raw_parts[3])
            payload = raw_parts[5]
            
            if s_id not in sensors: # 비콘 없이 데이터가 먼저 온 경우 대응
                sensors[s_id] = {'max_idx': -1, 'rssi': -99, 'remaining': 999, 'last_seen': time.time()}

            # [핵심] 단조 증가 로직: 이미 받은 번호보다 클 때만 저장
            if idx > sensors[s_id]['max_idx']:
                sensors[s_id]['max_idx'] = idx
                sensors[s_id]['last_seen'] = time.time()
                
                with open(f"{RECEIVED_DIR}{s_id}_part_{idx}.bin", "wb") as f:
                    f.write(payload)
                
                if idx % 10 == 0: # 로그 최적화
                    print(f"[수신] {s_id} - {idx}번 조각 확보")

    except socket.timeout:
        # 3. 스케줄링: 전송 권한(GRANT) 부여 결정
        now = time.time()
        # 최근 3초 내 활성화 & 보낼 데이터가 남은 센서 필터링
        active = {k: v for k, v in sensors.items() if now - v['last_seen'] < 3 and v['remaining'] > 0}

        if active:
            best_id = max(active.keys(), key=lambda k: calculate_score(active[k]['rssi'], active[k]['remaining']))
            target = active[best_id]
            
            # 다음 필요한 번호 요청 (메모리 기반)
            next_idx = target['max_idx'] + 1
            grant_msg = f"GRANT|{best_id}|{next_idx}|5"
            sock.sendto(grant_msg.encode(), target['addr'])
            
            # 로그 출력 (스케줄링 흐름 확인용)
            print(f">>> [GRANT] {best_id}에게 {next_idx}번부터 5개 요청 (RSSI: {target['rssi']})")