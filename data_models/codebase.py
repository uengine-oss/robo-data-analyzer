"""Codebase — 분석 대상 코드베이스 메타.

분석 1회의 입력 단위. 디렉토리 경로 + strategy + (DBMS 옵션) catalog 메타.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional


@dataclass(frozen=True)
class Codebase:
    """분석 대상 codebase.

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
