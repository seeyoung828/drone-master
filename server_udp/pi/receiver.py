import socket
import os

UDP_IP = "192.168.4.1"
UDP_PORT = 5005
STATE_FILE = "transfer_state.txt"
SAVE_PATH = "received_image.jpg"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

# 마지막 수신 인덱스 로드
def get_last_index():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return int(f.read())
    return -1

last_saved_index = get_last_index()
print(f"--- 드론 수신 대기 중 (마지막 수신: {last_saved_index}) ---")

# 데이터 조각을 임시 저장할 딕셔너리 (메모리 사용)
# 파일이 매우 크면 바로 파일에 write하는 방식이 좋으나, 이미지 테스트용으론 충분합니다.
received_chunks = {}

while True:
    data, addr = sock.recvfrom(2048) # 헤더 포함 넉넉히 설정
    
    # 메시지 타입 확인을 위해 헤더만 먼저 디코딩 시도
    raw_parts = data.split(b'|', 5)
    msg_type = raw_parts[0].decode()

    # 핸드쉐이크 처리
    if msg_type == "REQ_SYNC":
        s_id = raw_parts[1].decode()
        current_last = get_last_index()
        response = f"ACK_RESUME|{current_last}"
        sock.sendto(response.encode(), addr)
        print(f"[Handshake] {s_id}에게 마지막 인덱스({current_last}) 전달.")

    # 데이터 수신 처리
    elif msg_type == "DATA":
        s_id = raw_parts[1].decode()
        total = int(raw_parts[2])
        idx = int(raw_parts[3])
        rssi = raw_parts[4].decode()
        payload = raw_parts[5]

        if idx > last_saved_index:
            received_chunks[idx] = payload
            last_saved_index = idx
            
            # 상태 저장
            with open(STATE_FILE, "w") as f:
                f.write(str(idx))
            
            print(f"[수신] {idx}/{total-1} 조각 수신 (RSSI: {rssi})")

            # 전송 완료 여부 확인 및 파일 저장
            if idx == total - 1:
                print("--- 전송 완료! 파일 복원을 시작합니다. ---")
                with open(SAVE_PATH, "wb") as f:
                    for i in range(total):
                        if i in received_chunks:
                            f.write(received_chunks[i])
                print(f"--- 파일 저장 완료: {SAVE_PATH} ---")