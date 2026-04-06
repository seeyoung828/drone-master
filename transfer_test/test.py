import requests
import os
import time

# 설정
SERVER_IP = "192.168.4.1"
FILENAME = "drone.jpg" 
FILE_PATH = "knu.svg" 
CHUNK_SIZE = 1024 * 5

def start_upload():
    file_size = os.path.getsize(FILE_PATH)
    
    while True:
        # 1. 현재 어디까지 보냈는지 확인
        res = requests.get(f"http://{SERVER_IP}:5000/status", params={"filename": FILENAME})
        offset = res.json()['offset']
        
        if offset >= file_size:
            print("✅ 업로드 완료!")
            break
            
        # 2. 조각 보내기
        with open(FILE_PATH, 'rb') as f:
            f.seek(offset)
            chunk = f.read(CHUNK_SIZE)
            
            files = {'image': chunk}
            data = {'filename': FILENAME, 'offset': offset}
            
            try:
                response = requests.post(f"http://{SERVER_IP}:5000/upload_chunk", files=files, data=data)
                
                if response.status_code == 200:
                    new_offset = response.json()['current_offset']
                    print(f"📡 전송 성공: {new_offset}/{file_size}")
                elif response.status_code == 429: # PREEMPT 발생
                    backoff = response.json()['backoff_seconds']
                    print(f"🛑 [양보 명령] 다른 드론을 위해 {backoff}초간 대기합니다...")
                    time.sleep(backoff)
            except Exception as e:
                print(f"❌ 연결 오류: {e}")
                time.sleep(2)

if __name__ == "__main__":
    start_upload()