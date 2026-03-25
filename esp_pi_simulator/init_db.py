# init_db.py

import sqlite3
from config import DB_PATH

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS transfers (
        node_id TEXT NOT NULL,
        data_id TEXT NOT NULL,
        total_chunks INTEGER NOT NULL,
        last_received_chunk INTEGER NOT NULL DEFAULT -1,
        state TEXT NOT NULL DEFAULT 'PAUSED',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (node_id, data_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS contact_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id TEXT NOT NULL,
        data_id TEXT NOT NULL,
        received_chunks INTEGER NOT NULL,
        stop_reason TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()
    print("DB initialized:", DB_PATH)

if __name__ == "__main__":
    init_db()