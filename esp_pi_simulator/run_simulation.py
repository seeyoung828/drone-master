

"""FastAPI 마스터 서버를 실행하고 여러 슬레이브 노드를 동시에 구동하는 시뮬레이션 실행 파일."""

import asyncio

import uvicorn

from slave.slave import SlaveNode


async def run_master_server() -> None:
    """uvicorn으로 FastAPI 마스터 서버를 실행한다."""

    config = uvicorn.Config(
        "master.app:app",
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_slaves() -> None:
    """슬레이브 노드 5개를 생성하고 동시에 실행한다."""

    master_url = "http://127.0.0.1:8000"

    # 마스터 서버가 먼저 실행될 시간을 잠깐 확보한다.
    await asyncio.sleep(1.5)

    slaves = [
        SlaveNode(node_id=f"node_{index}", master_url=master_url, total_chunks=5, delay_sec=0.3)
        for index in range(1, 6)
    ]

    tasks = [asyncio.create_task(slave.run()) for slave in slaves]
    await asyncio.gather(*tasks)


async def main() -> None:
    """마스터와 슬레이브를 함께 실행하고 슬레이브가 끝나면 서버를 종료한다."""

    master_task = asyncio.create_task(run_master_server())

    try:
        await run_slaves()
    finally:
        master_task.cancel()
        try:
            await master_task
        except asyncio.CancelledError:
            print("[SIMULATION] 마스터 서버를 종료했습니다.")


if __name__ == "__main__":
    asyncio.run(main())