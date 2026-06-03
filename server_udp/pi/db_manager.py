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
            # [Migration] 구버전 sensors 테이블 컬럼 감지 시 포맷 전환
            cursor.execute("PRAGMA table_info(sensors)")
            col_names = [col[1] for col in cursor.fetchall()]
            if col_names and "id" in col_names and "s_id" not in col_names:
                dprint("[Migration] Recreating sensors table to match s_id Primary Key schema.")
                cursor.execute("DROP TABLE sensors")
                col_names = []

            # 1. 고도화된 센서 영구 관리 테이블 생성
            cursor.execute('''CREATE TABLE IF NOT EXISTS sensors 
                (s_id TEXT PRIMARY KEY, 
                 esp_name TEXT, 
                 mac_address TEXT UNIQUE, 
                 last_known_ip TEXT, 
                 status TEXT DEFAULT 'OFFLINE',
                 last_seen DATETIME, 
                 last_connected DATETIME,
                 average_rssi INTEGER DEFAULT -100,
                 battery_level REAL DEFAULT 100.0)''')
            
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

            # sensors 신규 컬럼들 마이그레이션
            cursor.execute("PRAGMA table_info(sensors)")
            curr_sensors_cols = [c[1] for c in cursor.fetchall()]
            new_sensors_cols = {
                "esp_name": "TEXT",
                "mac_address": "TEXT UNIQUE",
                "last_known_ip": "TEXT",
                "status": "TEXT DEFAULT 'OFFLINE'",
                "last_connected": "DATETIME",
                "average_rssi": "INTEGER DEFAULT -100",
                "battery_level": "REAL DEFAULT 100.0"
            }
            for col, col_type in new_sensors_cols.items():
                if col not in curr_sensors_cols:
                    dprint(f"[Migration] Adding missing '{col}' column to sensors.")
                    cursor.execute(f"ALTER TABLE sensors ADD COLUMN {col} {col_type}")

            self.conn.commit()

    def update_sensor_status(self, s_id, rssi):
        with self.lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO sensors (s_id, last_seen, average_rssi) 
                VALUES (?, DATETIME('now'), ?)""", (s_id, rssi))
            self.conn.commit()

    def prepare_session(self, s_id, data_id, total_chunks):
        """
        세션을 점검하고 필요시 신규 세션을 생성합니다.
        기존에 동일한 (s_id, data_id)가 있고 total_chunks가 다르면 해당 세션만 초기화합니다.
        [v3.5] 이미 COMPLETED 상태인 경우 물리 파일(.jpg) 존재 여부를 확인합니다.
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

            # [v3.6] 이미 완료된 세션인 경우, 물리 파일 유무와 관계없이 세션 유지 (Early Exit 지원)
            elif row and row[1] == 'COMPLETED':
                # 물리 파일이 없어도 DB상 완료 상태면 노드에게 COMPLETE_ACK를 보내기 위해 세션을 유지함
                pass

            # 3. 신규 세션 등록
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

    def is_session_completed(self, s_id, data_id):
        """[v3.5] 해당 세션이 성공적으로 완료되었는지 확인합니다."""
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT status FROM image_sessions WHERE s_id = ? AND data_id = ?", (s_id, data_id))
            row = cursor.fetchone()
            return row is not None and row[0] == 'COMPLETED'

    def reset_session(self, s_id, data_id):
        """[CHECKSUM_FAIL 대응] 수집 마스크와 카운트를 초기화하여 처음부터 다시 수집하게 합니다."""
        with self.lock:
            # [Fix] 파일 핸들 먼저 닫기 (캐시 오염 방지)
            # 수정 사항 1
            self._close_file_unlocked()
            
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
                if not row: return "ERROR"
                
                mask = bytearray(row[0])
                received_count = row[1]
                
                # 이미 수신된 조각인 경우 스킵
                if idx < len(mask) and mask[idx] == 1:
                    return "DUPLICATE"
                
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
            return "SUCCESS"
        except Exception as e:
            print(f"[File Write Error] {e}")
            self.close_file()
            return "ERROR"

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
        [v3.5] 이미 완료된 경우(idempotent) 성공 반환
        """
        final_path = os.path.join(self.storage_dir, f"{s_id}_{data_id}.jpg")
        
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT total_chunks, received_count, status FROM image_sessions 
                WHERE s_id = ? AND data_id = ?""", (s_id, data_id))
            row = cursor.fetchone()
            
            if row and row[2] == 'COMPLETED' and os.path.exists(final_path):
                # dprint(f"[Verify] 이미 완료된 세션입니다: {s_id}_{data_id}")
                return True

            if not row or row[1] == 0: 
                dprint(f"[Verify Error] 유효한 수집 데이터가 없음: {s_id}_{data_id}")
                return False
            
            total, received, status = row
            
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

    def register_or_update_sensor(self, s_id, esp_name, mac, ip, rssi, battery):
        """
        MAC 주소를 기준으로 센서 노드 상태를 영구 저장 및 갱신합니다.
        (IP 변동 감지 및 평균 RSSI 평활화 연산 포함)
        """
        with self.lock:
            cursor = self.conn.cursor()
            
            # 기존 MAC 주소 보유 노드 확인
            cursor.execute("SELECT s_id, last_known_ip, average_rssi FROM sensors WHERE mac_address = ?", (mac,))
            row = cursor.fetchone()
            
            if row:
                existing_sid, last_ip, avg_rssi = row
                # RSSI 이동 평균 계산 (가중치 0.8)
                new_avg_rssi = int(avg_rssi * 0.8 + rssi * 0.2)
                
                # 정보 업데이트 (온라인 상태로 복구)
                cursor.execute("""
                    UPDATE sensors 
                    SET s_id = ?, esp_name = ?, last_known_ip = ?, status = 'ONLINE', 
                        last_seen = DATETIME('now'), average_rssi = ?, battery_level = ?
                    WHERE mac_address = ?""", (s_id, esp_name, ip, new_avg_rssi, battery, mac))
                
                # IP 변경 탐지 시 로그 보고
                if last_ip != ip:
                    dprint(f"[IP Changed] ESP 노드 '{esp_name}'({s_id}) IP 변동 감지: {last_ip} -> {ip}")
            else:
                # 신규 등록
                cursor.execute("""
                    INSERT OR REPLACE INTO sensors 
                    (s_id, esp_name, mac_address, last_known_ip, status, last_seen, average_rssi, battery_level)
                    VALUES (?, ?, ?, ?, 'ONLINE', DATETIME('now'), ?, ?)""", 
                    (s_id, esp_name, mac, ip, rssi, battery))
                dprint(f"[Registry] 신규 ESP 노드 등록 성공: {esp_name} ({s_id} - MAC: {mac})")
                
            self.conn.commit()

    def get_sensor_status(self, s_id):
        """노드의 현재 동작/장애 상태를 반환합니다."""
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT status FROM sensors WHERE s_id = ?", (s_id,))
            row = cursor.fetchone()
            return row[0] if row else 'OFFLINE'

    def set_sensor_suspended(self, s_id):
        """심각한 연속 에러 유발 노드를 장애 격리(SUSPENDED) 처리합니다."""
        with self.lock:
            self.conn.execute("UPDATE sensors SET status = 'SUSPENDED' WHERE s_id = ?", (s_id,))
            self.conn.commit()

    def get_sensor_battery(self, s_id):
        """센서의 최근 배터리 잔량을 반환합니다."""
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT battery_level FROM sensors WHERE s_id = ?", (s_id,))
            row = cursor.fetchone()
            return row[0] if row else 100.0

    def get_offline_sensors(self):
        """오프라인(또는 일정 시간 무소식)인 센서 목록을 반환합니다."""
        with self.lock:
            cursor = self.conn.cursor()
            # 마지막 본지 15초 이상이거나 상태가 OFFLINE인 경우
            cursor.execute("""
                SELECT s_id, last_known_ip FROM sensors 
                WHERE status = 'OFFLINE' OR (strftime('%s', 'now') - strftime('%s', last_seen)) > 15
            """)
            rows = cursor.fetchall()
            return [{'s_id': r[0], 'last_known_ip': r[1]} for r in rows]
