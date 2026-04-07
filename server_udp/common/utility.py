import struct
import zlib

# --- [공통 상수 정의] ---
UDP_PORT = 5005
CHUNK_SIZE = 1024  # 1KB 단위 분할
HEADER_FIELDS_COUNT = 7  # Payload를 제외한 헤더 필드 수 (0~6번 인덱스)

# --- [헤더 파싱 유틸리티] ---
def parse_packet(raw_data):
    """
    바이너리 데이터를 헤더와 페이로드로 안전하게 분리합니다.
    구조: Type|S_ID|Data_ID|Total|Idx|RSSI|Last_Flag| [Binary Payload]
    """
    try:
        # 최대 7번만 split하여 마지막 8번째(Payload)는 원본 바이너리를 보존함
        parts = raw_data.split(b'|', HEADER_FIELDS_COUNT)
        
        if len(parts) < HEADER_FIELDS_COUNT:
            return None

        header = {
            'type':      parts[0].decode('utf-8'),
            's_id':      parts[1].decode('utf-8'),
            'data_id':   parts[2].decode('utf-8'),
            'total':     int(parts[3]),
            'idx':       int(parts[4]),
            'rssi':      int(parts[5]),
            'last_flag': int(parts[6])
        }
        
        # 7번 인덱스가 페이로드 (바이너리)
        payload = parts[7] if len(parts) > HEADER_FIELDS_COUNT else b''
        
        return header, payload
    except Exception as e:
        print(f"[Parsing Error] {e}")
        return None

# --- [무결성 검증 유틸리티] ---
def pack_crc32(data_id, binary_data):
    """
    CRC32 체크섬을 계산하고 Network Byte Order(Big Endian)로 패킹합니다.
    """
    checksum = zlib.crc32(binary_data) & 0xffffffff
    # '>I'는 Big Endian unsigned int (4바이트)를 의미함
    return struct.pack('>I', checksum)

def unpack_crc32(binary_checksum):
    """
    수신된 4바이트 바이너리 체크섬을 정수로 변환합니다.
    """
    return struct.unpack('>I', binary_checksum)[0]