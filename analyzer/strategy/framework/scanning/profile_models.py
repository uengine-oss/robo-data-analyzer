"""스캐닝 산출물 데이터 구조."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from analyzer.strategy.framework.common.node_models import FileCatalogEntry, ProcessingPlan


@dataclass
class CodebaseInfo:
    """결정론 수집 결과 한 덩어리."""

    root: str
    primary_language: Optional[str] = None

    extensions: dict[str, int] = field(default_factory=dict)

    manifest_name: str = ""
    manifest_content: str = ""
    config_name: str = ""
    config_content: str = ""

    import_frequency: dict[str, int] = field(default_factory=dict)
    sample_file_count: int = 0

    representative_file: str = ""
    representative_skeleton: str = ""
    function_index: list[tuple[str, list[str]]] = field(default_factory=list)

    standard_resources: list[str] = field(default_factory=list)
    custom_resources: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class CodebaseProfile:
    """Phase 1 준비 단계의 최종 산출물.

    scanner 결과 (catalog + plan) + 결정론 수집 메타 (info) +
    Deep Agent 가 만든 프레임워크/DB 접근 패턴 자연어 요약을 한 덩어리로 묶는다.
    """

    catalog: List[FileCatalogEntry]
    plan: ProcessingPlan
    info: CodebaseInfo
    summary: str
    resources: dict[str, list[str]]
    tool_calls: int = 0
    elapsed_sec: float = 0.0


