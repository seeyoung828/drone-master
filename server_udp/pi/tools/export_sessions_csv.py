import argparse
import csv
import os
import sqlite3


DEFAULT_DB_PATH = "drone_system.db"
DEFAULT_OUTPUT_PATH = "session_report.csv"


def connect_db(db_path):
    if not os.path.exists(db_path):
        print(f"[ERROR] DB 파일을 찾을 수 없습니다: {db_path}")
        return None

    return sqlite3.connect(db_path)


def calculate_progress(received_count, total_chunks):
    if total_chunks == 0:
        return 0.0

    return round((received_count / total_chunks) * 100, 2)


def count_missing_chunks(received_mask):
    if received_mask is None:
        return None

    mask = bytearray(received_mask)
    return sum(1 for value in mask if value == 0)


def fetch_sessions(conn, sensor_filter=None):
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


def export_sessions(db_path, output_path, sensor_filter=None):
    conn = connect_db(db_path)
    if conn is None:
        return

    try:
        sessions = fetch_sessions(conn, sensor_filter)

        with open(output_path, "w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.writer(csv_file)

            writer.writerow([
                "s_id",
                "data_id",
                "total_chunks",
                "received_count",
                "progress_percent",
                "status",
                "missing_count",
            ])

            for s_id, data_id, total_chunks, received_count, status, received_mask in sessions:
                progress = calculate_progress(received_count, total_chunks)
                missing_count = count_missing_chunks(received_mask)

                writer.writerow([
                    s_id,
                    data_id,
                    total_chunks,
                    received_count,
                    progress,
                    status,
                    missing_count,
                ])

        print(f"[SUCCESS] CSV 파일 생성 완료: {output_path}")
        print(f"[INFO] exported sessions: {len(sessions)}")

    except sqlite3.Error as error:
        print(f"[ERROR] DB 조회 중 오류 발생: {error}")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Export Drone Master image session status to CSV."
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="SQLite DB file path. Default: drone_system.db",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help="CSV output file path. Default: session_report.csv",
    )
    parser.add_argument(
        "--sensor",
        default=None,
        help="Filter output by sensor id. Example: S01",
    )

    args = parser.parse_args()
    export_sessions(args.db, args.output, args.sensor)


if __name__ == "__main__":
    main()
