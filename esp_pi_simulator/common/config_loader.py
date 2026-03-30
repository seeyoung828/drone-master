"""Configuration loader for the ESP-PI simulator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

DEFAULT_CONFIG: Dict[str, Any] = {
    "master_host": "127.0.0.1",
    "master_port": 8000,
    "num_slaves": 5,
    "chunk_size": 128,
    "quota_chunks_per_turn": 4,
    "scheduler_policy": "round_robin",
    "tick_delay_sec": 0.5,
    "auto_generate_data": True,
    "per_node_total_chunks": {},
    "faults": {
        "enabled": True,
        "network_delay_prob": 0.15,
        "temporary_disconnect_prob": 0.1,
        "chunk_drop_prob": 0.1,
        "min_delay_sec": 0.2,
        "max_delay_sec": 1.0,
        "disconnect_backoff_sec_min": 0.8,
        "disconnect_backoff_sec_max": 1.5,
    },
}


def _merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            result[key] = _merge_dict(base[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path) -> Dict[str, Any]:
    """Load the JSON config file and merge with defaults."""

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fp:
        user_conf = json.load(fp)

    config = _merge_dict(DEFAULT_CONFIG, user_conf)

    per_node = config.get("per_node_total_chunks") or {}
    num_slaves = int(config.get("num_slaves", 5))
    for idx in range(1, num_slaves + 1):
        node_id = f"node_{idx}"
        per_node.setdefault(node_id, int(per_node.get(node_id, 10 + (idx % 4) * 2)))
    config["per_node_total_chunks"] = per_node

    return config
