# pi_controller.py

import asyncio
import sqlite3
import time
from config import (
    ESP_COUNT, BASE_PORT, HOST, SLOT_MAX_CHUNKS,
    SLOT_MAX_SECONDS, DB_PATH, PI_ID
)
from common import send_json, recv_json

def get_db():
    return sqlite3.connect(DB_PATH)

def get_last_received_chunk(node_id, data_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT last_received_chunk
        FROM transfers
        WHERE node_id = ? AND data_id = ?
    """, (node_id, data_id))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else -1

def upsert_transfer(node_id, data_id, total_chunks, last_received_chunk, state):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO transfers (node_id, data_id, total_chunks, last_received_chunk, state, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(node_id, data_id) DO UPDATE SET
            total_chunks = excluded.total_chunks,
            last_received_chunk = excluded.last_received_chunk,
            state = excluded.state,
            updated_at = CURRENT_TIMESTAMP
    """, (node_id, data_id, total_chunks, last_received_chunk, state))
    conn.commit()
    conn.close()

def insert_contact_log(node_id, data_id, received_chunks, stop_reason):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO contact_logs (node_id, data_id, received_chunks, stop_reason)
        VALUES (?, ?, ?, ?)
    """, (node_id, data_id, received_chunks, stop_reason))
    conn.commit()
    conn.close()

def all_completed():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*)
        FROM transfers
        WHERE state != 'COMPLETED'
    """)
    row = cur.fetchone()
    conn.close()
    return row[0] == 0

async def visit_esp(node_index):
    port = BASE_PORT + node_index - 1
    expected_node_id = f"esp_{node_index:02d}"

    try:
        reader, writer = await asyncio.open_connection(HOST, port)
    except Exception as e:
        print(f"[PI] {expected_node_id} connect failed: {e}")
        return

    received_in_slot = 0
    stop_reason = "unknown"
    slot_start = time.monotonic()

    try:
        await send_json(writer, {
            "type": "HELLO",
            "pi_id": PI_ID
        })

        msg = await recv_json(reader)
        if not msg or msg.get("type") != "HELLO_ACK":
            print(f"[PI] {expected_node_id}: HELLO_ACK failed")
            writer.close()
            await writer.wait_closed()
            return

        node_info = await recv_json(reader)
        if not node_info or node_info.get("type") != "NODE_INFO":
            print(f"[PI] {expected_node_id}: NODE_INFO failed")
            writer.close()
            await writer.wait_closed()
            return

        data_info = await recv_json(reader)
        if not data_info or data_info.get("type") != "DATA_INFO":
            print(f"[PI] {expected_node_id}: DATA_INFO failed")
            writer.close()
            await writer.wait_closed()
            return

        node_id = data_info["node_id"]
        data_id = data_info["data_id"]
        total_chunks = data_info["total_chunks"]

        last_chunk = get_last_received_chunk(node_id, data_id)
        start_chunk = last_chunk + 1

        if start_chunk >= total_chunks:
            upsert_transfer(node_id, data_id, total_chunks, last_chunk, "COMPLETED")
            print(f"[PI] {node_id}: already completed")
            writer.close()
            await writer.wait_closed()
            return

        await send_json(writer, {
            "type": "REQUEST_TRANSFER",
            "node_id": node_id,
            "data_id": data_id,
            "start_chunk": start_chunk,
            "max_chunks": SLOT_MAX_CHUNKS
        })

        current_last = last_chunk

        while True:
            elapsed = time.monotonic() - slot_start
            if elapsed >= SLOT_MAX_SECONDS:
                stop_reason = "time_limit"
                await send_json(writer, {
                    "type": "STOP",
                    "reason": stop_reason,
                    "last_acked_chunk": current_last
                })
                break

            msg = await recv_json(reader)
            if msg is None:
                stop_reason = "connection_closed"
                break

            if msg["type"] == "CHUNK":
                chunk_index = msg["chunk_index"]

                if chunk_index != current_last + 1:
                    print(f"[PI] {node_id}: unexpected chunk {chunk_index}, expected {current_last + 1}")
                    stop_reason = "invalid_chunk_order"
                    await send_json(writer, {
                        "type": "STOP",
                        "reason": stop_reason,
                        "last_acked_chunk": current_last
                    })
                    break

                await send_json(writer, {
                    "type": "ACK",
                    "node_id": node_id,
                    "data_id": data_id,
                    "chunk_index": chunk_index
                })

                current_last = chunk_index
                received_in_slot += 1

                state = "PAUSED"
                if current_last + 1 >= total_chunks:
                    state = "COMPLETED"

                upsert_transfer(node_id, data_id, total_chunks, current_last, state)

                print(f"[PI] {node_id}: received chunk {chunk_index}")

                if current_last + 1 >= total_chunks:
                    stop_reason = "completed"
                    break

                if received_in_slot >= SLOT_MAX_CHUNKS:
                    stop_reason = "chunk_limit"
                    await send_json(writer, {
                        "type": "STOP",
                        "reason": stop_reason,
                        "last_acked_chunk": current_last
                    })
                    break

            elif msg["type"] == "COMPLETE":
                upsert_transfer(node_id, data_id, total_chunks, current_last, "COMPLETED")
                stop_reason = "completed"
                break

            elif msg["type"] == "ERROR":
                print(f"[PI] {node_id}: error from ESP -> {msg}")
                stop_reason = "esp_error"
                break

            else:
                print(f"[PI] {node_id}: unknown message {msg}")
                stop_reason = "unknown_message"
                break

        if received_in_slot > 0:
            state = "COMPLETED" if stop_reason == "completed" else "PAUSED"
            upsert_transfer(node_id, data_id, total_chunks, current_last, state)

        insert_contact_log(node_id, data_id, received_in_slot, stop_reason)
        print(f"[PI] {node_id}: slot end, received={received_in_slot}, reason={stop_reason}")

    except Exception as e:
        print(f"[PI] {expected_node_id}: runtime error -> {e}")

    finally:
        writer.close()
        await writer.wait_closed()

async def main():
    round_no = 1
    while True:
        print(f"\n========== ROUND {round_no} ==========")
        for i in range(1, ESP_COUNT + 1):
            await visit_esp(i)
            await asyncio.sleep(0.2)

        if all_completed():
            print("\n[PI] All transfers completed.")
            break

        round_no += 1
        await asyncio.sleep(1.0)

if __name__ == "__main__":
    asyncio.run(main())