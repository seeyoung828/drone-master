# esp_node.py

import asyncio
import sys
import time
from common import send_json, recv_json
from config import CHUNK_SIZE, DEFAULT_TOTAL_CHUNKS, HOST

def make_chunk_payload(node_id, chunk_index):
    base = f"{node_id}-chunk-{chunk_index}-"
    payload = (base * 50)[:CHUNK_SIZE]
    return payload

class ESPNode:
    def __init__(self, node_id, port, total_chunks=DEFAULT_TOTAL_CHUNKS):
        self.node_id = node_id
        self.port = port
        self.data_id = f"{node_id}_data_01"
        self.total_chunks = total_chunks

    async def handle_client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        print(f"[{self.node_id}] connected from {peer}")

        try:
            hello = await recv_json(reader)
            if not hello or hello.get("type") != "HELLO":
                await send_json(writer, {
                    "type": "ERROR",
                    "code": "INVALID_HELLO",
                    "message": "HELLO expected"
                })
                writer.close()
                await writer.wait_closed()
                return

            await send_json(writer, {
                "type": "HELLO_ACK",
                "node_id": self.node_id
            })

            await send_json(writer, {
                "type": "NODE_INFO",
                "node_id": self.node_id,
                "status": "READY"
            })

            await send_json(writer, {
                "type": "DATA_INFO",
                "node_id": self.node_id,
                "data_id": self.data_id,
                "total_chunks": self.total_chunks
            })

            req = await recv_json(reader)
            if not req or req.get("type") != "REQUEST_TRANSFER":
                await send_json(writer, {
                    "type": "ERROR",
                    "code": "INVALID_REQUEST",
                    "message": "REQUEST_TRANSFER expected"
                })
                writer.close()
                await writer.wait_closed()
                return

            start_chunk = req["start_chunk"]
            max_chunks = req["max_chunks"]

            sent_count = 0
            current = start_chunk

            while current < self.total_chunks and sent_count < max_chunks:
                payload = make_chunk_payload(self.node_id, current)

                await send_json(writer, {
                    "type": "CHUNK",
                    "node_id": self.node_id,
                    "data_id": self.data_id,
                    "chunk_index": current,
                    "payload": payload
                })

                ack = await recv_json(reader)
                if ack is None:
                    print(f"[{self.node_id}] connection closed before ACK")
                    break

                if ack.get("type") == "STOP":
                    print(f"[{self.node_id}] received STOP from PI")
                    break

                if ack.get("type") != "ACK" or ack.get("chunk_index") != current:
                    await send_json(writer, {
                        "type": "ERROR",
                        "code": "INVALID_ACK",
                        "message": f"ACK for chunk {current} expected"
                    })
                    break

                current += 1
                sent_count += 1

            if current >= self.total_chunks:
                await send_json(writer, {
                    "type": "COMPLETE",
                    "node_id": self.node_id,
                    "data_id": self.data_id
                })

        except Exception as e:
            print(f"[{self.node_id}] error:", e)

        finally:
            writer.close()
            await writer.wait_closed()
            print(f"[{self.node_id}] disconnected")

    async def run(self):
        server = await asyncio.start_server(self.handle_client, HOST, self.port)
        print(f"[{self.node_id}] listening on {HOST}:{self.port}")
        async with server:
            await server.serve_forever()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 esp_node.py <node_id> <port> [total_chunks]")
        sys.exit(1)

    node_id = sys.argv[1]
    port = int(sys.argv[2])
    total_chunks = int(sys.argv[3]) if len(sys.argv) >= 4 else DEFAULT_TOTAL_CHUNKS

    esp = ESPNode(node_id=node_id, port=port, total_chunks=total_chunks)
    asyncio.run(esp.run())