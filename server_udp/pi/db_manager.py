import sqlite3
import threading
import os
import zlib
import struct
import sys

# 상위 디렉토리 추가하여 common 패키지 인식
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from common.utility import CHUNK_SIZE


def dprint(*args, **kwargs):
    """실시간 로그 확인을 위한 flush 포함 print 함수"""
    print(*args, **kwargs)
    sys.stdout.flush()


class DroneDB:
    def __init__(self, db_name="drone_system.db", storage_dir="collected_images"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.lock = threading.Lock()

        # DB 안정성과 성능 균형 설정
        # WAL: 읽기/쓰기 동시성 향상
        # synchronous=NORMAL: OFF보다 안정적이며 성능도 비교적 유지
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")

        self.storage_dir = storage_dir

        if not os.path.exists(self.storage_dir):
            os.makedirs(self.storage_dir)

        self._create_tables()

        # 파일 핸들 캐싱
        self._current_file_path = None
        self._current_file_handle = None

    def _create_tables(self):
        with self.lock:
            cursor = self.conn.cursor()

            # 1. 센서 실시간 상태 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sensors (
                    id TEXT PRIMARY KEY,
                    last_seen DATETIME,
                    rssi INTEGER
                )
            """)

            # 2. 이미지 수집 세션 관리 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS image_sessions (
                    s_id TEXT,
                    data_id TEXT,
                    total_chunks INTEGER,
                    received_count INTEGER DEFAULT 0,
                    status TEXT,
                    received_mask BLOB,
                    PRIMARY KEY (s_id, data_id)
                )
            """)

            # 기존 DB 파일 호환성을 위한 migration
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
        """
        센서의 최근 통신 시간과 RSSI를 갱신합니다.
        """
        with self.lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO sensors (id, last_seen, rssi)
                VALUES (?, DATETIME('now'), ?)
            """, (s_id, rssi))
            self.conn.commit()

    def prepare_session(self, s_id, data_id, total_chunks):
        """
        이미지 수집 세션을 준비합니다.

        반환값:
            True  - 신규 세션 생성 또는 재생성
            False - 기존 세션 유지 또는 재개
        """
        session_created = False

        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT total_chunks, status
                FROM image_sessions
                WHERE s_id = ? AND data_id = ?
            """, (s_id, data_id))
            row = cursor.fetchone()

            tmp_file = os.path.join(self.storage_dir, f"{s_id}_{data_id}.tmp")
            final_file = os.path.join(self.storage_dir, f"{s_id}_{data_id}.jpg")

            # 1. 동일 세션인데 total_chunks가 달라진 경우: 비정상 상황으로 판단하고 초기화
            if row and row[0] != total_chunks:
                dprint(f"[Reset] {s_id}_{data_id} total_chunks mismatch. Reset session.")

                self._close_file_unlocked()

                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
                if os.path.exists(final_file):
                    os.remove(final_file)

                cursor.execute("""
                    DELETE FROM image_sessions
                    WHERE s_id = ? AND data_id = ?
                """, (s_id, data_id))
                row = None

            # 2. DB상 COMPLETED인 경우 실제 jpg 파일 존재 여부 확인
            elif row and row[1] == "COMPLETED":
                if os.path.exists(final_file):
                    self.conn.commit()
                    return False
                else:
                    # DB는 완료인데 실제 파일이 없으면 재수집 필요
                    dprint(f"[Reset] COMPLETED but final file missing: {s_id}_{data_id}")

                    self._close_file_unlocked()

                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)

                    cursor.execute("""
                        DELETE FROM image_sessions
                        WHERE s_id = ? AND data_id = ?
                    """, (s_id, data_id))
                    row = None

            # 3. 동일 센서의 다른 활성 세션은 PAUSED 처리
            cursor.execute("""
                UPDATE image_sessions
                SET status = 'PAUSED'
                WHERE s_id = ? AND data_id != ? AND status = 'COLLECTING'
            """, (s_id, data_id))

            # 4. 신규 세션 생성
            if not row:
                initial_mask = sqlite3.Binary(bytearray(total_chunks))

                cursor.execute("""
                    INSERT INTO image_sessions
                    (s_id, data_id, total_chunks, received_count, status, received_mask)
                    VALUES (?, ?, ?, 0, 'COLLECTING', ?)
                """, (s_id, data_id, total_chunks, initial_mask))

                session_created = True

            # 5. 기존 세션 재개
            else:
                cursor.execute("""
                    UPDATE image_sessions
                    SET status = 'COLLECTING'
                    WHERE s_id = ? AND data_id = ?
                """, (s_id, data_id))

            self.conn.commit()

        return session_created

    def is_session_completed(self, s_id, data_id):
        """
        해당 세션이 완료 상태인지 확인합니다.
        """
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT status
                FROM image_sessions
                WHERE s_id = ? AND data_id = ?
            """, (s_id, data_id))
            row = cursor.fetchone()

            return row is not None and row[0] == "COMPLETED"

    def reset_session(self, s_id, data_id):
        """
        CHECKSUM_FAIL 등에 대응하여 해당 세션을 초기화합니다.
        """
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT total_chunks
                FROM image_sessions
                WHERE s_id = ? AND data_id = ?
            """, (s_id, data_id))
            row = cursor.fetchone()

            if not row:
                return False

            total_chunks = row[0]
            initial_mask = sqlite3.Binary(bytearray(total_chunks))

            tmp_file = os.path.join(self.storage_dir, f"{s_id}_{data_id}.tmp")

            # 현재 열려 있는 파일이 해당 tmp 파일이면 먼저 닫음
            if self._current_file_path == tmp_file:
                self._close_file_unlocked()

            # 임시 파일 삭제
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

            cursor.execute("""
                UPDATE image_sessions
                SET received_count = 0,
                    received_mask = ?,
                    status = 'PAUSED'
                WHERE s_id = ? AND data_id = ?
            """, (initial_mask, s_id, data_id))

            self.conn.commit()

        return True

    def save_fragment(self, s_id, data_id, idx, payload):
        """
        수신한 chunk payload를 파일의 정확한 위치에 저장하고,
        DB의 received_mask 및 received_count를 갱신합니다.

        반환값:
            SUCCESS
            DUPLICATE
            NO_SESSION
            OUT_OF_RANGE
            EMPTY_PAYLOAD
            ERROR
        """
        if payload is None or len(payload) == 0:
            return "EMPTY_PAYLOAD"

        file_path = os.path.join(self.storage_dir, f"{s_id}_{data_id}.tmp")

        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("""
                    SELECT received_mask, received_count, total_chunks, status
                    FROM image_sessions
                    WHERE s_id = ? AND data_id = ?
                """, (s_id, data_id))
                row = cursor.fetchone()

                if not row:
                    return "NO_SESSION"

                mask_blob, received_count, total_chunks, status = row

                if mask_blob is None:
                    dprint(f"[DB Error] received_mask is None: {s_id}_{data_id}")
                    return "ERROR"

                mask = bytearray(mask_blob)

                # idx 범위 검증
                if idx < 0 or idx >= total_chunks or idx >= len(mask):
                    dprint(f"[Index Error] idx out of range: idx={idx}, total={total_chunks}")
                    return "OUT_OF_RANGE"

                # 이미 수신한 chunk면 중복 처리
                if mask[idx] == 1:
                    return "DUPLICATE"

                # 파일 핸들 캐싱
                if self._current_file_path != file_path:
                    self._close_file_unlocked()

                    if not os.path.exists(file_path):
                        open(file_path, "wb").close()

                    self._current_file_handle = open(file_path, "rb+")
                    self._current_file_path = file_path

                # 정확한 위치에 chunk 저장
                self._current_file_handle.seek(idx * CHUNK_SIZE)
                self._current_file_handle.write(payload)

                # CRC 계산 시점의 오검출 방지를 위해 flush
                self._current_file_handle.flush()

                # DB mask 및 count 갱신
                mask[idx] = 1
                received_count += 1

                cursor.execute("""
                    UPDATE image_sessions
                    SET received_count = ?,
                        received_mask = ?,
                        status = 'COLLECTING'
                    WHERE s_id = ? AND data_id = ?
                """, (
                    received_count,
                    sqlite3.Binary(mask),
                    s_id,
                    data_id
                ))

                self.conn.commit()

                return "SUCCESS"

        except Exception as e:
            dprint(f"[File Write Error] {e}")
            self.close_file()
            return "ERROR"

    def get_next_missing_idx(self, s_id, data_id):
        """
        가장 앞선 누락 chunk index를 반환합니다.
        모든 chunk가 수신되었으면 len(mask)를 반환합니다.
        """
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT received_mask
                FROM image_sessions
                WHERE s_id = ? AND data_id = ?
            """, (s_id, data_id))
            row = cursor.fetchone()

            if not row or row[0] is None:
                return 0

            mask = bytearray(row[0])

            for i, val in enumerate(mask):
                if val == 0:
                    return i

            return len(mask)

    def verify_and_finalize(self, s_id, data_id, remote_crc32_bin):
        """
        모든 chunk 수신 후 CRC32를 검증하고,
        성공 시 .tmp 파일을 .jpg 파일로 확정합니다.
        """
        tmp_file = os.path.join(self.storage_dir, f"{s_id}_{data_id}.tmp")
        final_file = os.path.join(self.storage_dir, f"{s_id}_{data_id}.jpg")

        # remote CRC32는 4바이트 Big Endian이어야 함
        if remote_crc32_bin is None or len(remote_crc32_bin) != 4:
            dprint(f"[Verify Error] Invalid remote CRC32: {s_id}_{data_id}")
            return False

        with self.lock:
            # CRC 계산 전에 열려 있는 파일 핸들을 닫아 디스크 반영 보장
            if self._current_file_path == tmp_file:
                self._close_file_unlocked()

            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT total_chunks, received_count, status
                FROM image_sessions
                WHERE s_id = ? AND data_id = ?
            """, (s_id, data_id))
            row = cursor.fetchone()

            # 이미 완료된 세션이면 성공으로 처리
            if row and row[2] == "COMPLETED" and os.path.exists(final_file):
                return True

            if not row:
                dprint(f"[Verify Error] Session not found: {s_id}_{data_id}")
                return False

            total_chunks, received_count, status = row

            if received_count == 0:
                dprint(f"[Verify Error] No received data: {s_id}_{data_id}")
                return False

            if total_chunks != received_count:
                dprint(
                    f"[Verify Error] Missing chunks: "
                    f"total={total_chunks}, received={received_count}"
                )
                return False

        # tmp 파일 존재 확인
        if not os.path.exists(tmp_file):
            dprint(f"[Verify Error] Temp file not found: {tmp_file}")
            return False

        try:
            # 로컬 파일 CRC32 계산
            with open(tmp_file, "rb") as f:
                local_crc32 = zlib.crc32(f.read()) & 0xffffffff

            # 원격 CRC32 Big Endian 언패킹
            remote_crc32 = struct.unpack(">I", remote_crc32_bin)[0]

            if local_crc32 != remote_crc32:
                dprint(
                    f"[FAILED] CRC mismatch {s_id}_{data_id} "
                    f"(Local: {hex(local_crc32)}, Remote: {hex(remote_crc32)})"
                )
                return False

            # 원자적 파일 확정
            os.replace(tmp_file, final_file)

            with self.lock:
                self.conn.execute("""
                    UPDATE image_sessions
                    SET status = 'COMPLETED'
                    WHERE s_id = ? AND data_id = ?
                """, (s_id, data_id))
                self.conn.commit()

            dprint(f"[SUCCESS] {s_id}_{data_id}.jpg saved. CRC matched.")
            return True

        except Exception as e:
            dprint(f"[Verify Error] Finalizing failed: {e}")
            return False

    def _close_file_unlocked(self):
        """
        lock이 이미 획득된 상태에서 현재 파일 핸들을 닫는 내부 함수입니다.
        """
        if self._current_file_handle:
            try:
                self._current_file_handle.flush()
                self._current_file_handle.close()
            except Exception:
                pass

            self._current_file_handle = None
            self._current_file_path = None

    def close_file(self):
        """
        현재 열려 있는 파일 핸들을 안전하게 닫습니다.
        """
        with self.lock:
            self._close_file_unlocked()

    def commit(self):
        """
        외부에서 명시적으로 DB commit을 호출할 수 있도록 제공하는 함수입니다.
        """
        with self.lock:
            self.conn.commit()

    def close(self):
        """
        프로그램 종료 시 파일 핸들과 DB 연결을 안전하게 종료합니다.
        """
        with self.lock:
            self._close_file_unlocked()

            try:
                self.conn.commit()
                self.conn.close()
            except Exception as e:
                dprint(f"[Close Error] {e}")
