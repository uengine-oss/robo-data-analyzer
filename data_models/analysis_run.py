"""AnalysisRun — 분석 1회 실행 메타.

시각 + 결과 디렉토리 + 대상 ``Codebase`` 참조.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from data_models.codebase import Codebase


@dataclass(frozen=True)
class AnalysisRun:
    """한 번의 분석 실행 메타.

    Fields:
        run_id: ``"YYYYMMDD_HHMMSS"`` 형식 식별자.
        codebase: 분석 대상.
        started_at: 실행 시작 시각 (UTC).
        run_dir: 산출물 디렉토리 — Plan §4 M1 정책상 ``logs/analysis.run/latest/``.
                 매 실행마다 rmtree 후 재생성 (timestamp 폴더 X).
    """
    run_id: str
    codebase: Codebase
    started_at: datetime
    run_dir: Path

    @classmethod
    def now(cls, codebase: Codebase, log_base: Path) -> "AnalysisRun":
        """현재 시각으로 새 AnalysisRun 생성 — ``logs/analysis.run/latest/`` 경로 박음."""
        now = datetime.now(timezone.utc)
        run_id = now.strftime("%Y%m%d_%H%M%S")
        return cls(
            run_id=run_id,
            codebase=codebase,
            started_at=now,
            run_dir=log_base / "analysis.run" / "latest",
        )
