# mock_sensor.py 수정본 (이미지 전송용)
import requests
import base64
import os
import time

DRONE_IP = "192.168.4.1" 
URL = f"http://{DRONE_IP}:8000/upload"
IMAGE_PATH = "test.svg" # 합칠 대상 이미지
CHUNK_SIZE = 1024 * 5 # 5KB씩 쪼개기

def send_image():
    if not os.path.exists(IMAGE_PATH):
        print("이미지 파일이 없습니다!")
        return

    with open(IMAGE_PATH, "rb") as f:
        image_data = f.read()

    total_size = len(image_data)
    total_chunks = (total_size // CHUNK_SIZE) + 1
    data_id = f"img_{int(time.time())}"

    print(f"전송 시작: {total_chunks} 조각")

    for i in range(total_chunks):
        start = i * CHUNK_SIZE
        end = start + CHUNK_SIZE
        chunk_binary = image_data[start:end]
        
        # 바이너리를 Base64 문자열로 변환 (JSON 전송을 위해)
        payload = base64.b64encode(chunk_binary).decode('utf-8')

        data = {
            "node_id": "esp_01",
            "data_id": data_id,
            "chunk_index": i,
            "total_chunks": total_chunks,
            "payload": payload
        }

        try:
            res = requests.post(URL, json=data)
            if res.status_code == 200:
                print(f"Chunk {i} 보냄")
        except:
            print("연결 실패")
            break
        time.sleep(0.1)

if __name__ == "__main__":
    send_image()