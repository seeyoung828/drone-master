"""Entry point to run the ESP-PI FastAPI simulator."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import uvicorn

BASE_DIR = Path(__file__).parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from common.config_loader import load_config
from common.logger import init_logging, get_logger
from common.utils import ensure_dirs, dump_json
from master import MasterState, build_master_app
from slave import SlaveNode


async def run_simulation() -> None:
    config_path = BASE_DIR / "config.json"
    config = load_config(config_path)

    logs_dir = BASE_DIR / "logs"
    sessions_dir = BASE_DIR / "sessions"
    slave_states_dir = sessions_dir / "slave_states"
    data_dir = BASE_DIR / "data"
    ensure_dirs([logs_dir, sessions_dir, slave_states_dir, data_dir])

    init_logging(logs_dir)
    logger = get_logger("runner")

    metrics_path = data_dir / "metrics_summary.json"
    state = MasterState(session_dir=sessions_dir, metrics_path=metrics_path, quota=int(config["quota_chunks_per_turn"]))
    app = build_master_app(state)

    uvicorn_config = uvicorn.Config(app, host=config["master_host"], port=int(config["master_port"]), log_level="warning", lifespan="on")
    server = uvicorn.Server(uvicorn_config)

    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(1.0)  # give the server time to start

    slaves = [
        SlaveNode(node_id=f"node_{i+1}", config=config, session_dir=slave_states_dir)
        for i in range(int(config["num_slaves"]))
    ]

    slave_tasks = [asyncio.create_task(slave.run()) for slave in slaves]

    try:
        await asyncio.gather(*slave_tasks)
    finally:
        server.should_exit = True
        await server_task

    summary = state.finalize()
    logger.info("[METRIC] summary: %s", summary)
    print("=== Simulation Summary ===")
    print(summary)


def main() -> None:
    try:
        asyncio.run(run_simulation())
    except KeyboardInterrupt:
        print("Interrupted by user.")
        raise


if __name__ == "__main__":
    main()
