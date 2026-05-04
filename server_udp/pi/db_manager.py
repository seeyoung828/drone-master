import sqlite3
import threading
import os
import zlib
import struct
import sys

# 상위 디렉토리 추가하여 common 패키지 인식 (v2.7 성능 최적화 대응)
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from common.utility import CHUNK_SIZE

def dprint(*args, **kwargs):
    """실시간 로그 확인을 위한 플러시 포함 프린트 함수"""
    print(*args, **kwargs)
    sys.stdout.flush()

class DroneDB:
    def __init__(self, db_name="drone_system.db", storage_dir="collected_images"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.lock = threading.Lock()
        
        # [Step 1] DB 성능 최적화: 동기화 해제 및 WAL 모드 활성화 (v2.7)
        self.conn.execute("PRAGMA synchronous = OFF")
        self.conn.execute("PRAGMA journal_mode = WAL")
        
        self.storage_dir = storage_dir
        
        if not os.path.exists(self.storage_dir):
            os.makedirs(self.storage_dir)
            
        self._create_tables()

        # [v2.7] 파일 핸들 캐싱을 위한 멤버 변수
        self._current_file_path = None
        self._current_file_handle = None

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
                 received_mask BLOB,
                 PRIMARY KEY (s_id, data_id))''')
            
            # [Migration] 컬럼 누락 대응 (기존 DB 파일 호환성 유지)
            cursor.execute("PRAGMA table_info(image_sessions)")
            columns = [column[1] for column in cursor.fetchall()]
            if "received_mask" not in columns:
                dprint("[Migration] Adding missing 'received_mask' column to image_sessions.")
                cursor.execute("ALTER TABLE image_sessions ADD COLUMN received_mask BLOB")
            if "received_count" not in columns:
                dprint("[Migration] Adding missing 'received_count' column to image_sessions.")
                cursor.execute("ALTER TABLE image_sessions ADD COLUMN received_count INTEGER DEFAULT 0")

            self.conn.commit()

    def update_sensor_status(self, s_id, rssi):
        with self.lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO sensors (id, last_seen, rssi) 
                VALUES (?, DATETIME('now'), ?)""", (s_id, rssi))
            self.conn.commit()

    def prepare_session(self, s_id, data_id, total_chunks):
        """
        세션을 점검하고 필요시 신규 세션을 생성합니다.
        기존에 동일한 (s_id, data_id)가 있고 total_chunks가 다르면 해당 세션만 초기화합니다.
        반환값: True (신규 세션 생성됨), False (기존 세션 유지)
        """
        session_created = False
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT total_chunks, status FROM image_sessions WHERE s_id = ? AND data_id = ?", (s_id, data_id))
            row = cursor.fetchone()

            # 1. 동일한 (s_id, data_id)인데 전체 크기가 달라진 경우 (비정상 상황)
            if row and row[0] != total_chunks:
                dprint(f"[Reset] {s_id}의 세션({data_id}) 크기가 변경되어 초기화합니다.")
                # 물리 파일 삭제 (s_id 포함)
                old_file = os.path.join(self.storage_dir, f"{s_id}_{data_id}.tmp")
                if os.path.exists(old_file): os.remove(old_file)
                # DB 레코드 삭제 후 재삽입 유도
                cursor.execute("DELETE FROM image_sessions WHERE s_id = ? AND data_id = ?", (s_id, data_id))
                row = None 

            # 2. 신규 세션 등록
            if not row:
                # 동일 노드의 다른 활성 세션들을 PAUSED로 전환하여 정합성 유지
                cursor.execute("""
                    UPDATE image_sessions SET status = 'PAUSED' 
                    WHERE s_id = ? AND status = 'COLLECTING'""", (s_id,))
                
                # 비트마스크 초기화 (0으로 채워진 바이트 배열)
                initial_mask = sqlite3.Binary(bytearray(total_chunks))
                cursor.execute("""
                    INSERT OR IGNORE INTO image_sessions (s_id, data_id, total_chunks, status, received_mask)
                    VALUES (?, ?, ?, 'COLLECTING', ?)""", (s_id, data_id, total_chunks, initial_mask))
                
                if cursor.rowcount > 0:
                    session_created = True
                
            self.conn.commit()
        return session_created

    def reset_session(self, s_id, data_id):
        """[CHECKSUM_FAIL 대응] 수집 마스크와 카운트를 초기화하여 처음부터 다시 수집하게 합니다."""
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT total_chunks FROM image_sessions WHERE s_id = ? AND data_id = ?", (s_id, data_id))
            row = cursor.fetchone()
            if row:
                total_chunks = row[0]
                initial_mask = sqlite3.Binary(bytearray(total_chunks))
                cursor.execute("""
                    UPDATE image_sessions SET received_count = 0, received_mask = ?, status = 'PAUSED'
                    WHERE s_id = ? AND data_id = ?""", (initial_mask, s_id, data_id))
                self.conn.commit()
                # 임시 파일도 삭제 (s_id 포함)
                file_path = os.path.join(self.storage_dir, f"{s_id}_{data_id}.tmp")
                if os.path.exists(file_path):
                    os.remove(file_path)
                return True
        return False

    def save_fragment(self, s_id, data_id, idx, payload):
        """
        [Seek 기반 기록] 파일의 정확한 위치에 바이너리를 기록합니다.
        """
        file_path = os.path.join(self.storage_dir, f"{s_id}_{data_id}.tmp")
        
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("SELECT received_mask, received_count FROM image_sessions WHERE s_id = ? AND data_id = ?", (s_id, data_id))
                row = cursor.fetchone()
                if not row: return False
                
                mask = bytearray(row[0])
                received_count = row[1]
                
                # 이미 수신된 조각인 경우 스킵
                if idx < len(mask) and mask[idx] == 1:
                    return True
                
                # [Optimization] 파일 핸들 캐싱 로직 (v2.7)
                if self._current_file_path != file_path:
                    self._close_file_unlocked()
                    if not os.path.exists(file_path):
                        open(file_path, 'wb').close()
                    self._current_file_handle = open(file_path, "rb+")
                    self._current_file_path = file_path

                self._current_file_handle.seek(idx * CHUNK_SIZE)
                self._current_file_handle.write(payload)
                
                # 마스크 및 카운트 업데이트
                if idx < len(mask):
                    mask[idx] = 1
                    received_count += 1
                    self.conn.execute("""
                        UPDATE image_sessions SET received_count = ?, received_mask = ? 
                        WHERE s_id = ? AND data_id = ?""", (received_count, sqlite3.Binary(mask), s_id, data_id))
            return True
        except Exception as e:
            print(f"[File Write Error] {e}")
            self.close_file()
            return False

    def _close_file_unlocked(self):
        """락이 이미 획득된 상태에서 호출하는 내부 함수"""
        if self._current_file_handle:
            try:
                self._current_file_handle.close()
            except:
                pass
            self._current_file_handle = None
            self._current_file_path = None

    def close_file(self):
        """[v2.7] 현재 열려 있는 파일 핸들을 안전하게 닫음"""
        with self.lock:
            self._close_file_unlocked()

    def commit(self):
        """[Optimization] 명시적 커밋을 위한 함수"""
        with self.lock:
            self.conn.commit()

    def get_next_missing_idx(self, s_id, data_id):
        """가장 앞선 유실 인덱스(Hole)를 찾아 반환합니다."""
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT received_mask FROM image_sessions WHERE s_id = ? AND data_id = ?", (s_id, data_id))
            row = cursor.fetchone()
            if not row: return 0
            
            mask = bytearray(row[0])
            for i, val in enumerate(mask):
                if val == 0:
                    return i
            return len(mask) # 모두 수집됨

    def verify_and_finalize(self, s_id, data_id, remote_crc32_bin):
        """
        [무결성 검증] CRC32 체크 후 .tmp -> .jpg 변환
        """
        with self.lock:
            cursor = self.conn.cursor()
            # 쿼리에 s_id를 포함하여 정확한 세션 식별
            cursor.execute("""
                SELECT total_chunks, received_count FROM image_sessions 
                WHERE s_id = ? AND data_id = ?""", (s_id, data_id))
            row = cursor.fetchone()
            
            # [Fix 2.3] 가드 코드: 세션이 없거나 수집된 조각이 0개인 경우 즉시 실패 처리
            if not row or row[1] == 0: 
                dprint(f"[Verify Error] 유효한 수집 데이터가 없음: {s_id}_{data_id}")
                return False
            
            total, received = row
            
            if total != received:
                dprint(f"--- [FAILED] {s_id}_{data_id} 유실 발생 (Total: {total}, Received: {received}) ---")
                return False

        # 파일 경로 생성 (s_id 포함)
        file_path = os.path.join(self.storage_dir, f"{s_id}_{data_id}.tmp")
        final_path = os.path.join(self.storage_dir, f"{s_id}_{data_id}.jpg")

        # [Fix 2.3] 가드 코드: 물리 파일 존재 여부 확인
        if not os.path.exists(file_path):
            dprint(f"[Verify Error] 임시 파일을 찾을 수 없음: {file_path}")
            return False

        try:
            # 1. 파일 읽어서 직접 CRC32 계산
            with open(file_path, "rb") as f:
                local_crc32 = zlib.crc32(f.read()) & 0xffffffff

            # 2. 원격 CRC32(Big Endian) 언패킹
            remote_crc32 = struct.unpack('>I', remote_crc32_bin)[0]

            if local_crc32 == remote_crc32:
                # 3. 원자적 이름 변경
                if os.path.exists(final_path):
                    os.remove(final_path)
                os.rename(file_path, final_path)
                
                with self.lock:
                    self.conn.execute("""
                        UPDATE image_sessions SET status = 'COMPLETED' 
                        WHERE s_id = ? AND data_id = ?""", (s_id, data_id))
                    self.conn.commit()
                dprint(f"--- [SUCCESS] {s_id}_{data_id}.jpg 저장 완료 (CRC 일치) ---")
                return True
            else:
                dprint(f"--- [FAILED] {s_id}_{data_id} CRC 불일치 (Local: {hex(local_crc32)}, Remote: {hex(remote_crc32)}) ---")
                return False
        except Exception as e:
            dprint(f"[Verify Error] 최종 처리 중 오류 발생: {e}")
            return False
