"""Phase 1 준비 단계 공개 API.

파일시스템 스캔 + 결정론 메타 수집 + scanning 에이전트 자연어 요약을 한 번에
수행해 CodebaseProfile (catalog + plan + info + pattern + resources) 로 반환한다.

흐름:
  1. scan_project()          — 파일 카탈로그 + ProcessingPlan (결정론)
  2. collect()               — 확장자/매니페스트/imports/대표 파일 뼈대/함수 인덱스
  3. _run_profile_agent()    — 프레임워크/DB 접근 패턴 자연어 패턴 추출 (LLM 에이전트)
  4. CodebaseProfile 로 묶어 반환
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

from util.llm_call_safety import strip_control_tokens
from analyzer.strategy.framework.common.models.node_models import FileCatalogEntry, ProcessingPlan
from analyzer.strategy.framework.scanning.agent.scanning_agent import build_scanning_agent
from analyzer.strategy.framework.scanning.codebase_scan_collector import (
    CodebaseInfo,
    collect,
    scan_project,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Phase 1 준비 단계 산출물 데이터 모델
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class CodebaseProfile:
    """Phase 1 준비 단계의 최종 산출물.

    scanner 결과 (catalog + plan) + 결정론 수집 메타 (info) +
    Deep Agent 가 관찰한 코드베이스 패턴 (Identifier Rule / Naming Rule / Access Mechanism 등).
    형식 검증 없음. agent 가 뱉은 그대로 `pattern` 에 보존되고 다운스트림이 그대로 사용.
    """

    catalog: List[FileCatalogEntry]
    plan: ProcessingPlan
    info: CodebaseInfo
    pattern: str
    resources: dict[str, list[str]]
    tool_calls: int = 0
    elapsed_sec: float = 0.0


def profile(
    root: Path,
    lang: str,
    scan_roots: Optional[List[Tuple[str, str]]] = None,
    api_key: str = "",
) -> CodebaseProfile:
    """프로젝트 루트 1 개 → CodebaseProfile 1 개."""
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

    pattern, tool_calls = _run_profile_agent(
        root=root_path,
        info=info,
        resources=resources,
        api_key=api_key,
    )

    elapsed = time.time() - started
    logger.info(
        "[PHASE:1:PROFILE] 완료 | tool_calls=%d elapsed=%.1fs pattern_len=%d",
        tool_calls, elapsed, len(pattern or ""),
    )

    return CodebaseProfile(
        catalog=catalog,
        plan=plan,
        info=info,
        pattern=pattern,
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


# ═══════════════════════════════════════════════════════════════════════
# Profile 에이전트 실행 (드라이버) — 입력 렌더링 / 출력 파싱
# ═══════════════════════════════════════════════════════════════════════


def _run_profile_agent(
    root: Path,
    info: CodebaseInfo,
    resources: dict[str, list[str]],
    api_key: str = "",
    _retry: int = 0,
) -> tuple[str, int]:
    """scanning 에이전트 호출 → (final_text, tool_calls).

    형식·길이 검증 없음. 단 **완전 빈 응답** (tool_calls=0 AND final_text="") 일 때만
    1 회 재시도 — OpenRouter/Gemma 등에서 가끔 빈 AI 메시지를 반환하는 transport 수준
    flake 복구. 출력 형식이나 내용에는 일절 관여 안 함.
    """
    agent, user_template = build_scanning_agent(root=root, api_key=api_key)
    user_msg = _render_prompt(root, info, resources, user_template)

    logger.info("[PHASE:1:PROFILE] Agent 시작 (retry=%d)", _retry)
    result = agent.invoke({"messages": [{"role": "user", "content": user_msg}]})

    raw_text = _extract_final_text(result)
    final_text = strip_control_tokens(raw_text).strip()
    tool_calls = _count_tool_calls(result)
    logger.info("[PHASE:1:PROFILE] Agent 완료 | tool_calls=%d", tool_calls)

    # 완전 빈 응답 (transport flake) 만 재시도. 형식/내용 검증 아님.
    # 최대 2 회 재시도 — Gemma 등 일부 모델의 비결정적 silent 응답 복구.
    if _retry < 2 and not final_text and tool_calls == 0:
        logger.warning("[PHASE:1:PROFILE] 완전 빈 응답 (transport flake) — 재시도 %d/2", _retry + 1)
        return _run_profile_agent(root, info, resources, api_key=api_key, _retry=_retry + 1)

    return final_text, tool_calls




def _render_prompt(root: Path, info: CodebaseInfo, resources: dict[str, list[str]], user_template: str) -> str:
    """user_template 에 시작 단서만 채움 — 모델이 도구로 직접 탐색하도록.

    function_index / ngrams / skeleton / call_sequences 등 풍부한 사전 분석은 의도적으로 빠짐.
    300+ 함수 프로젝트도 동일 흐름으로 확장 가능 (큰 인덱스 dump 없음).
    """
    lines: list[str] = []
    for cat in ("SQL", "ENTITY", "CONFIG", "DOCS"):
        paths = resources.get(cat) or []
        if paths:
            preview = ", ".join(paths[:8])
            more = f" (+ {len(paths) - 8} more)" if len(paths) > 8 else ""
            lines.append(f"- {cat}: {preview}{more}")
    resources_summary = "\n".join(lines) if lines else "(없음)"

    return user_template.format(
        project_root=str(root),
        extensions=info.extensions or "(비어있음)",
        representative_file=info.representative_file or "(없음)",
        resources_summary=resources_summary,
    )


# ─────────────────────────────────────────────────────────────────────
# 결과 파싱 헬퍼
# ─────────────────────────────────────────────────────────────────────


def _extract_final_text(result: dict[str, Any]) -> str:
    for msg in reversed(result.get("messages", [])):
        if getattr(msg, "type", "") != "ai":
            continue
        if getattr(msg, "tool_calls", None):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content if isinstance(b, dict)]
            content = "\n".join(p for p in parts if p)
        if content and content.strip():
            return content.strip()
    return ""


def _count_tool_calls(result: dict[str, Any]) -> int:
    return sum(
        len(getattr(msg, "tool_calls", None) or [])
        for msg in result.get("messages", [])
    )


