"""분석 입력과 실행 메타데이터.

분석 1회의 입력 단위. 디렉토리 경로 + strategy (+ 옵션 datasource).

★ 가공 식별자(``name``) 폐기. 진실은 ``root`` 경로.
catalog **접속 설정**(``base_url`` / ``enabled``)은 운영 영역 (``shared.config.settings`` 의 ``ROBO_CATALOG_*``) —
분석기는 URL 호출·응답 처리만 책임진다. 단 ``datasource`` ("이번 분석에서 어느 DB 를 샘플링")는
분석 1회의 **입력값** 이라 여기 둔다 ("접속 설정"=SETTINGS / "어느 DB"=입력 으로 구분).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4


@dataclass(frozen=True)
class Codebase:
    """분석 대상 codebase.

    Fields:
        root: 소스 디렉토리 경로 — **분석 대상 단일 진실**. 그 외 식별자 없음.
        strategy: ``"framework"`` 또는 ``"dbms"`` — ``pipeline/strategies.py`` 의
            ``FRAMEWORK_PIPELINE`` / ``DBMS_PIPELINE`` 과 매칭.
        ddl_dir: 별도 DDL 디렉토리 (None 이면 root 안에서 자동 탐색).
        locale: LLM 응답 언어 (``"ko"`` / ``"en"``).
        datasource: 메타 보강(샘플데이터·설명) 시 샘플링할 DB(MindsDB datasource).
            분석 1회의 **입력값** (운영=AnalyzeRequest / CLI 테스트=SETTINGS.catalog_api.test_datasource).
            None = 미지정 → 샘플 보강 skip. (catalog 접속 base_url/enabled 는 운영 SETTINGS.)
    """
    root: Path
    strategy: Literal["framework", "dbms"]
    ddl_dir: Optional[Path] = None
    locale: str = "ko"
    datasource: Optional[str] = None
    # 분석 실행 manifest가 "실제 분석 source"와 "그 source를 담은 corpus"를 구분하도록
    # 호출 경계가 넘긴다. None은 API 등 기존 호출자의 하위호환이며 root를 사용한다.
    corpus_root: Optional[Path] = None


@dataclass(frozen=True)
class AnalysisRun:
    """한 번의 분석 실행을 식별하고 산출물 위치를 고정한다."""

    run_id: str
    codebase: Codebase
    started_at: datetime
    run_dir: Path

    @classmethod
    def now(cls, codebase: Codebase, run_output_root: Path) -> "AnalysisRun":
        """현재 UTC 시각으로 새 실행 메타를 만든다.

        산출물은 루트 바로 아래가 아니라 ``root/<run_id>/`` 에 격리된다. run 끼리
        서로의 증거를 지우지 않아야 전후 비교가 백업 없이 성립한다.
        """
        now = datetime.now(timezone.utc)
        run_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:12]}"
        return cls(
            run_id=run_id,
            codebase=codebase,
            started_at=now,
            run_dir=run_output_root / run_id,
        )
