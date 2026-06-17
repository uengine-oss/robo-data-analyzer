"""Codebase — 분석 대상 코드베이스 메타.

분석 1회의 입력 단위. 디렉토리 경로 + strategy (+ 옵션 datasource).

★ 가공 식별자(``name``) 폐기. 진실은 ``root`` 경로.
catalog **접속 설정**(``base_url`` / ``enabled``)은 운영 영역 (``env_catalog`` 의 ``ROBO_CATALOG_*``) —
분석기는 URL 호출·응답 처리만 책임진다. 단 ``datasource`` ("이번 분석에서 어느 DB 를 샘플링")는
분석 1회의 **입력값** 이라 여기 둔다 ("접속 설정"=env / "어느 DB"=입력 으로 구분).
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
        datasource: 메타 보강(샘플데이터·설명) 시 샘플링할 DB(MindsDB datasource).
            분석 1회의 **입력값** (운영=AnalyzeRequest / CLI 테스트=ENV.catalog.test_datasource).
            None = 미지정 → 샘플 보강 skip. (catalog 접속 base_url/enabled 는 운영 env.)
    """
    root: Path
    strategy: Literal["framework", "dbms"]
    ddl_dir: Optional[Path] = None
    has_xml_mapper: bool = False
    locale: str = "ko"
    datasource: Optional[str] = None
