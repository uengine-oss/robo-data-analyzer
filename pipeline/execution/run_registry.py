"""활성 분석 run 단일 슬롯 — control 엔드포인트가 컨트롤러를 찾는 통로.

프론트 control payload 에 run_id 가 없음 → **"동시 분석 1개"** 모델. 새 run 이 시작되면
직전 슬롯을 덮어쓴다(직전 stream 은 이미 종료됐을 것 — 안 끝났으면 warning 으로 표면화).
``execute_run`` 이 시작 시 ``set_active``, 종료 시 ``clear`` (성공·실패·중단 무관).
"""
from __future__ import annotations

from typing import Optional

from pipeline.execution.pause_controller import PauseController

_active: Optional[tuple[str, PauseController]] = None


class ConcurrentAnalysisRunError(RuntimeError):
    """단일 활성 분석 슬롯이 이미 사용 중이다."""


def set_active(run_id: str, controller: PauseController) -> None:
    global _active
    if _active is not None:
        raise ConcurrentAnalysisRunError(
            f"분석 run이 이미 활성 상태입니다: {_active[0]}"
        )
    _active = (run_id, controller)


def get_active() -> Optional[tuple[str, PauseController]]:
    return _active


def clear(run_id: str) -> None:
    """자기 run 일 때만 비운다 — 뒤늦은 정리가 새 run 슬롯을 지우지 않도록."""
    global _active
    if _active is not None and _active[0] == run_id:
        _active = None
