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
    Deep Agent 가 관찰한 3블록 (프레임워크/데이터접근/반복 이디엄) 자연어 요약.
    """

    catalog: List[FileCatalogEntry]
    plan: ProcessingPlan
    info: CodebaseInfo
    summary: str
    resources: dict[str, list[str]]
    # Block 별 자연어 섹션 — 파싱 성공 시 {"framework", "data_access", "idioms"}.
    # 파싱 실패 시 빈 dict 으로 두고 summary 전체만 쓰는 폴백 허용.
    summary_sections: dict[str, str] = field(default_factory=dict)
    tool_calls: int = 0
    elapsed_sec: float = 0.0


