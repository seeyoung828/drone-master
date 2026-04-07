import sqlite3
import threading
import os
import zlib
import struct
import sys

def dprint(*args, **kwargs):
    """실시간 로그 확인을 위한 플러시 포함 프린트 함수"""
    print(*args, **kwargs)
    sys.stdout.flush()

class DroneDB:
    def __init__(self, db_name="drone_system.db", storage_dir="collected_images"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.lock = threading.Lock()
        self.storage_dir = storage_dir
        
        if not os.path.exists(self.storage_dir):
            os.makedirs(self.storage_dir)
            
        self._create_tables()

    def _create_tables(self):
        with self.lock:
            cursor = self.conn.cursor()
            # 1. 센서 실시간 상태 테이블
            cursor.execute('''CREATE TABLE IF NOT EXISTS sensors 
                (id TEXT PRIMARY KEY, last_seen DATETIME, rssi INTEGER)''')
            
            # 2. 이미지 수집 세션 관리 테이블
            cursor.execute('''CREATE TABLE IF NOT EXISTS image_sessions 
                (s_id TEXT, data_id TEXT, total_chunks INTEGER, 
                 received_count INTEGER DEFAULT 0, status TEXT,
                 PRIMARY KEY (s_id, data_id))''')
            self.conn.commit()

    def update_sensor_status(self, s_id, rssi):
        with self.lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO sensors (id, last_seen, rssi) 
                VALUES (?, DATETIME('now'), ?)""", (s_id, rssi))
            self.conn.commit()

    def prepare_session(self, s_id, data_id, total_chunks):
        """
        [Purge 로직 포함] 세션을 점검하고 필요시 기존 데이터를 파기합니다.
        """
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT data_id, total_chunks FROM image_sessions WHERE s_id = ?", (s_id,))
            row = cursor.fetchone()

            # 1. 동일 센서인데 Data_ID가 바뀌었거나, 같은 ID인데 전체 크기가 달라진 경우 (재촬영 등)
            if row and (row[0] != data_id or row[1] != total_chunks):
                print(f"[Purge] {s_id}의 이전 데이터({row[0]})를 삭제하고 새 세션({data_id})을 시작합니다.")
                # 물리 파일 삭제
                old_file = os.path.join(self.storage_dir, f"{row[0]}.tmp")
                if os.path.exists(old_file): os.remove(old_file)
                # DB 레코드 삭제
                cursor.execute("DELETE FROM image_sessions WHERE s_id = ?", (s_id,))

            # 2. 신규 세션 등록
            cursor.execute("""
                INSERT OR IGNORE INTO image_sessions (s_id, data_id, total_chunks, status)
                VALUES (?, ?, ?, 'COLLECTING')""", (s_id, data_id, total_chunks))
            self.conn.commit()

    def save_fragment(self, s_id, data_id, idx, payload):
        """
        [Seek 기반 기록] 파일의 정확한 위치에 바이너리를 기록합니다.
        """
        file_path = os.path.join(self.storage_dir, f"{data_id}.tmp")
        
        try:
            # 'rb+' 모드는 파일이 있어야 하므로, 없으면 생성 ('ab+'는 seek가 불안정할 수 있음)
            if not os.path.exists(file_path):
                open(file_path, 'wb').close()

            with open(file_path, "rb+") as f:
                # 명세서 v2.3: Idx * 1024 위치로 이동
                f.seek(idx * 1024)
                # 수신된 payload 크기만큼만 정확히 기록 (쓰레기 데이터 방지)
                f.write(payload)
            
            with self.lock:
                self.conn.execute("""
                    UPDATE image_sessions SET received_count = received_count + 1 
                    WHERE s_id = ? AND data_id = ?""", (s_id, data_id))
                self.conn.commit()
            return True
        except Exception as e:
            print(f"[File Write Error] {e}")
            return False

    def verify_and_finalize(self, s_id, data_id, remote_crc32_bin):
        
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT total_chunks, received_count FROM image_sessions WHERE data_id = ?", (data_id,))
            row = cursor.fetchone()
            
            if not row: return False
            total, received = row
            
            # [수정] 패킷 유실 체크: 모든 조각이 도착했는지 확인
            if total != received:
                dprint(f"--- [FAILED] {data_id} 유실 발생 (Total: {total}, Received: {received}) ---")
                return False
        """
        [무결성 검증] CRC32 체크 후 .tmp -> .jpg 변환
        """
        file_path = os.path.join(self.storage_dir, f"{data_id}.tmp")
        final_path = os.path.join(self.storage_dir, f"{data_id}.jpg")

        ## if not os.path.exists(file_path): return False

        # 1. 파일 읽어서 직접 CRC32 계산
        with open(file_path, "rb") as f:
            local_crc32 = zlib.crc32(f.read()) & 0xffffffff

        # 2. 원격 CRC32(Big Endian) 언패킹
        remote_crc32 = struct.unpack('>I', remote_crc32_bin)[0]

        if local_crc32 == remote_crc32:
            os.rename(file_path, final_path)
            with self.lock:
                self.conn.execute("UPDATE image_sessions SET status = 'COMPLETED' WHERE data_id = ?", (data_id,))
                self.conn.commit()
            dprint(f"--- [SUCCESS] {data_id}.jpg 저장 완료 (CRC 일치) ---")
            return True
        else:
            dprint(f"--- [FAILED] {data_id} CRC 불일치 (Local: {hex(local_crc32)}, Remote: {hex(remote_crc32)}) ---")
            return False