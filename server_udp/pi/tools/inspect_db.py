import sqlite3
import os

DB_PATH = "drone_system.db"


def show_sensors(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT id, last_seen, rssi FROM sensors")
    rows = cursor.fetchall()

    print("\n=== sensors 테이블 ===")
    if not rows:
        print("데이터 없음")
        return

    for row in rows:
        print(f"id={row[0]}, last_seen={row[1]}, rssi={row[2]}")


def show_sessions(conn):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s_id, data_id, total_chunks, received_count, status
        FROM image_sessions
    """)
    rows = cursor.fetchall()

    print("\n=== image_sessions 테이블 ===")
    if not rows:
        print("데이터 없음")
        return

    for row in rows:
        print(
            f"s_id={row[0]}, data_id={row[1]}, "
            f"total_chunks={row[2]}, received_count={row[3]}, status={row[4]}"
        )


def show_missing_indices(conn):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s_id, data_id, received_mask
        FROM image_sessions
    """)
    rows = cursor.fetchall()

    print("\n=== 누락 chunk 정보 ===")
    if not rows:
        print("데이터 없음")
        return

    for s_id, data_id, received_mask in rows:
        if received_mask is None:
            print(f"{s_id}_{data_id}: received_mask 없음")
            continue

        mask = bytearray(received_mask)
        missing = [i for i, value in enumerate(mask) if value == 0]

        if missing:
            print(f"{s_id}_{data_id}: missing={missing}")
        else:
            print(f"{s_id}_{data_id}: 누락 chunk 없음")


def main():
    if not os.path.exists(DB_PATH):
        print(f"DB 파일이 없습니다: {DB_PATH}")
        print("drone_master.py를 실행한 위치에서 이 도구를 실행해야 합니다.")
        return

    conn = sqlite3.connect(DB_PATH)

    try:
        show_sensors(conn)
        show_sessions(conn)
        show_missing_indices(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
