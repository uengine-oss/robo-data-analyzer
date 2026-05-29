"""Codebase — 분석 대상 코드베이스 메타.

분석 1회의 입력 단위. 디렉토리 경로 + strategy 만.

★ 가공 식별자(``name``) / catalog 연동 정보 모두 폐기. 진실은 ``root`` 경로뿐.
catalog 같은 외부 서비스 정보는 분석기 영역이 아니라 운영 영역 (``env_catalog`` 의
``ROBO_CATALOG_*``) — 분석기는 URL 호출하고 응답 처리만 책임진다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional


@dataclass(frozen=True)
class Codebase:
    """분석 대상 codebase.

    Fields:
        root: 소스 디렉토리 경로 — **분석 대상 단일 진실**. 그 외 식별자 없음.
        strategy: ``"framework"`` 또는 ``"dbms"`` — ``analysis_pipelines.py`` 의
            ``FRAMEWORK_PIPELINE`` / ``DBMS_PIPELINE`` 과 매칭.
        ddl_dir: 별도 DDL 디렉토리 (None 이면 root 안에서 자동 탐색).
        has_xml_mapper: Framework 의 XML mapper 자료 활성 여부 (옵션 자료).
        locale: LLM 응답 언어 (``"ko"`` / ``"en"``).
    """
    root: Path
    strategy: Literal["framework", "dbms"]
    ddl_dir: Optional[Path] = None
    has_xml_mapper: bool = False
    locale: str = "ko"
