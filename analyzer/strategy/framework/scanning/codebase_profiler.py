"""Phase 1 준비 단계 공개 API.

파일시스템 스캔 + 결정론 메타 수집 + Deep Agent 요약을 한 번에 수행해
CodebaseProfile (catalog + plan + info + summary + resources) 로 반환한다.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple

from analyzer.strategy.framework.scanning.file_classifier import scan_project
from analyzer.strategy.framework.scanning.agent.profile_agent import run_profile_agent
from analyzer.strategy.framework.scanning.metadata_collector import collect
from analyzer.strategy.framework.scanning.profile_models import CodebaseProfile

logger = logging.getLogger(__name__)


def profile(
    root: Path,
    lang: str,
    scan_roots: Optional[List[Tuple[str, str]]] = None,
    api_key: str = "",
) -> CodebaseProfile:
    """프로젝트 루트 1 개 → CodebaseProfile 1 개.

    흐름:
      1. scan_project() — 파일 카탈로그 + ProcessingPlan 생성 (결정론)
      2. collect() — 확장자/매니페스트/imports/대표 파일 뼈대/함수 인덱스
      3. run_profile_agent() — 프레임워크/DB 접근 패턴 자연어 요약 (Deep Agent)
      4. 전체를 CodebaseProfile 로 묶어 반환
    """
    started = time.time()
    root_path = Path(root).resolve()
    logger.info("[PHASE:1:PROFILE] 시작 | root=%s lang=%s", root_path, lang)

    catalog, plan = scan_project(scan_roots=scan_roots, lang=lang) if scan_roots else scan_project(lang=lang, project_root=str(root_path))
    logger.info(
        "[PHASE:1:PROFILE] 스캔 완료 | catalog=%d source_ast=%d sql=%d entity=%d config=%d",
        len(catalog), len(plan.source_ast_jobs), len(plan.sql_jobs),
        len(plan.entity_jobs), len(plan.config_jobs),
    )

    info = collect(str(root_path))

    resources = {
        "SQL": [_strip_prefix(p) for p in plan.sql_jobs],
        "ENTITY": [_strip_prefix(p) for p in plan.entity_jobs],
        "CONFIG": [_strip_prefix(p) for p in plan.config_jobs],
        "DOCS": [_strip_prefix(p) for p in plan.docs_jobs],
        "OTHER": [],
    }

    summary, tool_calls = run_profile_agent(
        root=root_path,
        info=info,
        resources=resources,
        api_key=api_key,
    )

    elapsed = time.time() - started
    logger.info(
        "[PHASE:1:PROFILE] 완료 | tool_calls=%d elapsed=%.1fs summary_preview=%s",
        tool_calls, elapsed,
        (summary or "")[:160].replace("\n", " "),
    )

    return CodebaseProfile(
        catalog=catalog,
        plan=plan,
        info=info,
        summary=summary,
        resources=resources,
        tool_calls=tool_calls,
        elapsed_sec=elapsed,
    )


def _strip_prefix(path: str) -> str:
    """scanner 가 붙인 'source/' 경로 접두부 제거."""
    p = path.replace("\\", "/")
    if p.startswith("source/"):
        return p[len("source/"):]
    return p
