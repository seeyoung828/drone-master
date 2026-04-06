

"""config.json 파일을 읽어서 시뮬레이터 설정값을 반환하는 모듈."""

import json
from pathlib import Path


# 현재 파일(common/config_loader.py) 기준으로 프로젝트 루트 경로를 계산한다.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 프로젝트 루트에 있는 config.json 파일 경로를 저장한다.
CONFIG_PATH = PROJECT_ROOT / "config.json"


def load_config() -> dict:
    """config.json 파일을 읽어서 딕셔너리 형태로 반환한다."""

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {CONFIG_PATH}")

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    return config