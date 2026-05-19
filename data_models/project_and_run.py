"""Project / Run — 분석 1회의 입출력 단위.

Project = 분석 대상 (디렉토리 경로 + strategy + 옵션).
Run     = 한 번의 분석 실행 (시각 + 결과 위치).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional


@dataclass(frozen=True)
class Project:
    """분석 대상 프로젝트.

    Fields:
        name: 식별자 (Neo4j 의 ``datasource_name`` 으로 박힘).
        root: 소스 디렉토리 경로.
        strategy: ``"framework"`` 또는 ``"dbms"`` — ``analysis_pipelines.py`` 의
            ``FRAMEWORK_PIPELINE`` / ``DBMS_PIPELINE`` 과 매칭.
        ddl_dir: 별도 DDL 디렉토리 (None 이면 root 안에서 자동 탐색).
        has_xml_mapper: Framework 의 XML mapper 자료 활성 여부 (옵션 자료).
        locale: LLM 응답 언어 (``"ko"`` / ``"en"``).
        catalog_*: DBMS 전용 — ``enrich_metadata_from_catalog`` /
                   ``enrich_lineage_from_catalog`` step 에서 사용.
    """
    name: str
    root: Path
    strategy: Literal["framework", "dbms"]
    ddl_dir: Optional[Path] = None
    has_xml_mapper: bool = False
    locale: str = "ko"

    # DBMS 전용 — Framework 는 None 유지
    catalog_service_url: Optional[str] = None
    catalog_api_key: Optional[str] = None


@dataclass(frozen=True)
class Run:
    """한 번의 분석 실행 메타.

    Fields:
        run_id: ``"YYYYMMDD_HHMMSS"`` 형식 식별자.
        project: 분석 대상.
        started_at: 실행 시작 시각 (UTC).
        run_dir: 산출물 디렉토리 — Plan §4 M1 정책상 ``logs/analysis.run/latest/``.
                 매 실행마다 rmtree 후 재생성 (timestamp 폴더 X).
    """
    run_id: str
    project: Project
    started_at: datetime
    run_dir: Path

    @classmethod
    def now(cls, project: Project, log_base: Path) -> "Run":
        """현재 시각으로 새 Run 생성 — ``logs/analysis.run/latest/`` 경로 박음."""
        now = datetime.now(timezone.utc)
        run_id = now.strftime("%Y%m%d_%H%M%S")
        return cls(
            run_id=run_id,
            project=project,
            started_at=now,
            run_dir=log_base / "analysis.run" / "latest",
        )
