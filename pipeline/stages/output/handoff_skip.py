"""step7 인계 종료 시 후처리 skip의 의미를 한 곳에서 판정한다."""
from __future__ import annotations

from pipeline.execution.pause_controller import PauseController
from shared.events.runtime_logger import log_info, log_warning


def log_handoff_skip(step: str, work: str, pause: PauseController) -> None:
    """사용자 stop과 실제 분석 실패를 구분해 같은 정책으로 기록한다."""
    if pause.status()["isStopped"]:
        log_info(step, "analysis_stopped", f"사용자 중단 — {work} 생략")
        return
    log_warning(step, "analysis_aborted", f"분석 실패 — {work} 생략")
