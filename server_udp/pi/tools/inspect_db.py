import argparse
import os
import sqlite3
import time


DEFAULT_DB_PATH = "drone_system.db"
MISSING_PREVIEW_LIMIT = 15


def clear_screen():
    """watch 모드에서 화면을 갱신하기 위해 터미널 화면을 지운다."""
    os.system("cls" if os.name == "nt" else "clear")


def connect_db(db_path):
    """SQLite DB 파일에 연결한다."""
    if not os.path.exists(db_path):
        print(f"[ERROR] DB 파일을 찾을 수 없습니다: {db_path}")
        print("drone_master.py를 실행한 위치 또는 올바른 DB 경로에서 실행하세요.")
        return None

    return sqlite3.connect(db_path)


def fetch_sensors(conn, sensor_filter=None):
    """sensors 테이블에서 센서 상태를 조회한다."""
    cursor = conn.cursor()

    if sensor_filter:
        cursor.execute(
            """
            SELECT id, last_seen, rssi
            FROM sensors
            WHERE id = ?
            ORDER BY id
            """,
            (sensor_filter,),
        )
    else:
        cursor.execute(
            """
            SELECT id, last_seen, rssi
            FROM sensors
            ORDER BY id
            """
        )

    return cursor.fetchall()


def fetch_sessions(conn, sensor_filter=None):
    """image_sessions 테이블에서 이미지 수집 세션 상태를 조회한다."""
    cursor = conn.cursor()

    if sensor_filter:
        cursor.execute(
            """
            SELECT s_id, data_id, total_chunks, received_count, status, received_mask
            FROM image_sessions
            WHERE s_id = ?
            ORDER BY s_id, data_id
            """,
            (sensor_filter,),
        )
    else:
        cursor.execute(
            """
            SELECT s_id, data_id, total_chunks, received_count, status, received_mask
            FROM image_sessions
            ORDER BY s_id, data_id
            """
        )

    return cursor.fetchall()


def get_missing_indices(received_mask):
    """received_mask를 기반으로 아직 수신되지 않은 chunk 인덱스를 반환한다."""
    if received_mask is None:
        return None

    mask = bytearray(received_mask)
    return [idx for idx, value in enumerate(mask) if value == 0]


def format_progress(received_count, total_chunks):
    """수신 진행률을 문자열로 변환한다."""
    if total_chunks == 0:
        return "0/0 chunks | 0.0%"

    percent = (received_count / total_chunks) * 100
    return f"{received_count}/{total_chunks} chunks | {percent:.1f}%"


def print_sensors(sensors):
    """센서 상태를 출력한다."""
    print("\n=== Sensor Status ===")

    if not sensors:
        print("등록된 센서가 없습니다.")
        return

    for sensor_id, last_seen, rssi in sensors:
        print(f"- {sensor_id} | last_seen={last_seen} | rssi={rssi}")


def print_sessions(sessions):
    """이미지 세션 상태와 누락 chunk 정보를 출력한다."""
    print("\n=== Image Session Progress ===")

    if not sessions:
        print("이미지 수집 세션이 없습니다.")
        return

    for s_id, data_id, total_chunks, received_count, status, received_mask in sessions:
        progress = format_progress(received_count, total_chunks)
        missing = get_missing_indices(received_mask)

        print(f"\n- {s_id}_{data_id}")
        print(f"  status   : {status}")
        print(f"  progress : {progress}")

        if missing is None:
            print("  missing  : received_mask 없음")
        elif not missing:
            print("  missing  : 없음")
        else:
            preview = missing[:MISSING_PREVIEW_LIMIT]
            suffix = " ..." if len(missing) > MISSING_PREVIEW_LIMIT else ""
            print(f"  missing  : count={len(missing)}, first={preview}{suffix}")


def print_summary(sensors, sessions):
    """전체 요약 정보를 출력한다."""
    total_sessions = len(sessions)
    completed = sum(1 for row in sessions if row[4] == "COMPLETED")
    collecting = sum(1 for row in sessions if row[4] == "COLLECTING")
    paused = sum(1 for row in sessions if row[4] == "PAUSED")

    print("\n=== Summary ===")
    print(f"- sensors        : {len(sensors)}")
    print(f"- total sessions : {total_sessions}")
    print(f"- completed      : {completed}")
    print(f"- collecting     : {collecting}")
    print(f"- paused         : {paused}")


def inspect_once(db_path, sensor_filter=None):
    """DB 상태를 한 번 조회하고 출력한다."""
    conn = connect_db(db_path)
    if conn is None:
        return

    try:
        sensors = fetch_sensors(conn, sensor_filter)
        sessions = fetch_sessions(conn, sensor_filter)

        print(f"\n[DB Inspector] db={db_path}")
        if sensor_filter:
            print(f"[Filter] sensor={sensor_filter}")

        print_summary(sensors, sessions)
        print_sensors(sensors)
        print_sessions(sessions)

    except sqlite3.Error as error:
        print(f"[ERROR] DB 조회 중 오류 발생: {error}")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Inspect Drone Master SQLite DB status."
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="SQLite DB file path. Default: drone_system.db",
    )
    parser.add_argument(
        "--sensor",
        default=None,
        help="Filter output by sensor id. Example: S01",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Refresh DB status repeatedly.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Refresh interval in seconds for watch mode. Default: 1.0",
    )

    args = parser.parse_args()

    if args.watch:
        while True:
            clear_screen()
            inspect_once(args.db, args.sensor)
            time.sleep(args.interval)
    else:
        inspect_once(args.db, args.sensor)


if __name__ == "__main__":
    main()
