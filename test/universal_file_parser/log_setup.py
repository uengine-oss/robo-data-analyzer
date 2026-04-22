"""중앙 로그 설정 — 디버깅용 실시간 처리 로그.

파이프라인 내부에서 무슨 일이 일어나는지 한 파일에 다 적어둠:
  - parse() 시작/종료
  - detect 결과 (fmt, encoding)
  - 경로 결정 (library / deepagent / raw_fallback)
  - DeepAgent 각 시도 (AST/exec/validate)
  - 타이밍 + 토큰

기본 경로: c:/tmp/universal_parser.log
환경변수 UFP_LOG_PATH 로 override 가능.
"""

import logging
import os
from pathlib import Path


DEFAULT_LOG_PATH = os.environ.get("UFP_LOG_PATH", "c:/tmp/universal_parser.log")

_SETUP_DONE = False


def setup_logging(log_path: str | None = None, level: int = logging.INFO,
                  reset: bool = True) -> str:
    """파일 로거 설정. 중복 호출 방지.

    Args:
        log_path: 로그 파일 경로 (None 이면 UFP_LOG_PATH 또는 기본값)
        level: 로그 레벨 (기본 INFO)
        reset: True 면 기존 로그 파일을 초기화하고 새로 시작

    Returns:
        실제 사용된 로그 경로
    """
    global _SETUP_DONE

    path = log_path or DEFAULT_LOG_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if _SETUP_DONE:
        return path

    mode = 'w' if reset else 'a'

    # test.universal_file_parser 하위 로거만 이 파일로 보냄
    target_logger = logging.getLogger("test.universal_file_parser")
    target_logger.setLevel(level)
    target_logger.propagate = False

    # 기존 핸들러 제거 (중복 방지)
    for h in list(target_logger.handlers):
        target_logger.removeHandler(h)

    formatter = logging.Formatter(
        fmt='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    file_handler = logging.FileHandler(path, mode=mode, encoding='utf-8')
    file_handler.setFormatter(formatter)
    target_logger.addHandler(file_handler)

    _SETUP_DONE = True

    target_logger.info("=" * 72)
    target_logger.info("로그 시작 — %s", path)
    target_logger.info("=" * 72)

    return path


def get_logger(name: str) -> logging.Logger:
    """모듈용 logger. 이름은 module __name__ 전달."""
    return logging.getLogger(name)
