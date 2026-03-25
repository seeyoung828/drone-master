# common.py

import json

async def send_json(writer, obj):
    message = json.dumps(obj, ensure_ascii=False) + "\n"
    writer.write(message.encode("utf-8"))
    await writer.drain()

async def recv_json(reader):
    line = await reader.readline()
    if not line:
        return None
    return json.loads(line.decode("utf-8").strip())