# config.py

# 1. 설정
# [필독] 라즈베리 파이 AP의 IP 주소 (게이트웨이 주소)
DRONE_IP = "192.168.4.1" 
DRONE_URL = f"http://{DRONE_IP}:8000/upload"
NODE_ID = "esp_01"
TOTAL_CHUNKS = 40
CHUNK_SIZE = 256 # bytes

#######################################

# config.py

ESP_COUNT = 10
BASE_PORT = 9001
HOST = "127.0.0.1"

CHUNK_SIZE = 256
DEFAULT_TOTAL_CHUNKS = 40

SLOT_MAX_CHUNKS = 5
SLOT_MAX_SECONDS = 2.0

DB_PATH = "data/sessions.db"
PI_ID = "pi_01"