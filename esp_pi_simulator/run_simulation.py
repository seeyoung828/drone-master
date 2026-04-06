"""FastAPI 마스터 서버를 실행하고 여러 슬레이브 노드를 동시에 구동하는 시뮬레이션 실행 파일."""

import asyncio

import uvicorn

from common.config_loader import load_config
from slave.slave import SlaveNode


async def run_master_server(host: str, port: int) -> None:
    """uvicorn으로 FastAPI 마스터 서버를 실행한다."""

    config = uvicorn.Config(
        "master.app:app",
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_slaves(config: dict) -> None:
    """설정 파일 값을 바탕으로 슬레이브 노드들을 생성하고 동시에 실행한다."""

    master_host = config["master"]["host"]
    master_port = config["master"]["port"]
    num_slaves = config["simulation"]["num_slaves"]
    total_chunks = config["simulation"]["total_chunks_per_slave"]
    delay_sec = config["simulation"]["delay_sec"]

    master_url = f"http://{master_host}:{master_port}"

    # 마스터 서버가 먼저 실행될 시간을 잠깐 확보한다.
    await asyncio.sleep(1.5)

    slaves = [
        SlaveNode(
            node_id=f"node_{index}",
            master_url=master_url,
            total_chunks=total_chunks,
            delay_sec=delay_sec,
        )
        for index in range(1, num_slaves + 1)
    ]

    tasks = [asyncio.create_task(slave.run()) for slave in slaves]
    await asyncio.gather(*tasks)


async def main() -> None:
    """설정 파일을 읽고 마스터와 슬레이브를 함께 실행한 뒤 종료를 정리한다."""

    config = load_config()
    master_host = config["master"]["host"]
    master_port = config["master"]["port"]

    master_task = asyncio.create_task(run_master_server(master_host, master_port))

    try:
        await run_slaves(config)
    finally:
        master_task.cancel()
        try:
            await master_task
        except asyncio.CancelledError:
            print("[SIMULATION] 마스터 서버를 종료했습니다.")


if __name__ == "__main__":
    asyncio.run(main())