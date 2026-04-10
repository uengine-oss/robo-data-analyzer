"""변수 참조 결정론적 매칭 (Variable Reference Deterministic Matcher).

변수/필드/상수의 사용처를 LLM 없이 결정론적으로 추적한다.
각 변수명을 ChunkBundle에서 텍스트 검색하여 해당 변수를 사용하는
함수를 찾고 (FUNCTION)-[:REFERENCES]->(VARIABLE|CONSTANT) 관계를 생성한다.

[설계 근거]
기존 DeepAgent(LLM) 방식은 변수 하나당 bundle_search 1회 → 결과 있으면 등록이
전부였다. LLM이 판단할 여지가 없는 순수 문자열 매칭이므로 Python 직접 처리로 전환.
50개 변수 × LLM 배치 5분 → Python 직접 매칭 2초 이내.

[chunk_id 규칙]
- 함수: "package.Module.functionName" (# 없음, C: 접두사 없음)
- 변수: "package.Module#varName_123" (# 포함)
- 콜:  "C:startLine:endLine:seq" (C: 접두사)
→ 함수 chunk만 필터링하여 REFERENCES 대상으로 사용.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from analyzer.strategy.framework.parsing.chunk_bundle import (
    bundle_search as _bundle_search,
    build_bundle_span_index,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# 너무 짧은 변수명은 오탐이 많으므로 최소 길이 제한
_MIN_VAR_NAME_LENGTH = 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_variable_references(
    variable_facts_list: list,
    state: Dict[str, Any],
) -> List[Tuple[str, str]]:
    """변수→함수 REFERENCES 관계를 결정론적으로 매칭한다.

    처리 흐름:
      1. ChunkBundle 인덱스 확보 (state에 없으면 빌드)
      2. chunk_index에서 함수 chunk_id 집합 추출
      3. 각 변수명으로 bundle_search(kind="text") 실행
      4. 검색 결과 중 함수 chunk만 필터링하여 (function_id, variable_id) 수집
      5. 중복 제거 후 반환

    Args:
        variable_facts_list: VariableFacts 리스트 (Phase 1 AST 결과).
        state: pipeline state. 필수 키:
            - data_dir: 데이터 디렉토리 경로
            - span_index / chunk_index: ChunkBundle 인덱스 (없으면 자동 빌드)

    Returns:
        [(function_id, variable_id), ...] — Neo4j REFERENCES 관계 생성용.
    """
    if not variable_facts_list:
        return []

    # ── 1. ChunkBundle 인덱스 확보 ──
    chunk_index = _ensure_chunk_index(state)
    if not chunk_index:
        logger.warning("[VARREF] chunk_index 비어있음 → 매칭 불가")
        return []

    # ── 2. 함수 chunk_id 집합 추출 ──
    function_chunk_ids = _extract_function_chunk_ids(chunk_index)

    total = len(variable_facts_list)
    logger.info(
        "[VARREF] 결정론적 매칭 시작 | variables=%d function_chunks=%d",
        total, len(function_chunk_ids),
    )

    # ── 3~4. 변수별 검색 + 함수 필터링 ──
    refs = _match_variables_to_functions(
        variable_facts_list, function_chunk_ids,
        state.get("data_dir") or "",
        state.get("span_index"),
        chunk_index,
    )

    logger.info("[VARREF] 결정론적 매칭 완료 | refs=%d (variables=%d)", len(refs), total)
    return refs


# ---------------------------------------------------------------------------
# 내부 함수
# ---------------------------------------------------------------------------


def _ensure_chunk_index(state: Dict[str, Any]) -> Dict[str, Any]:
    """state에서 chunk_index를 가져오거나, 없으면 빌드하여 state에 저장한다."""
    if state.get("span_index") and state.get("chunk_index"):
        return state["chunk_index"]

    data_dir = state.get("data_dir") or ""
    run_dir = state.get("run_dir")

    from analyzer.strategy.framework.parsing.chunk_bundle import (
        _get_chunk_bundle_dir_from_run_dir,
    )
    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir) if run_dir else None
    span_index, chunk_index = build_bundle_span_index(
        data_dir,
        chunk_bundle_dir=chunk_dir,
        artifact_index=state.get("artifact_index"),
        sql_index=state.get("sql_index"),
    )
    state["span_index"] = span_index
    state["chunk_index"] = chunk_index
    logger.info(
        "[VARREF] ChunkBundle 인덱스 빌드 | span=%d chunk=%d",
        len(span_index), len(chunk_index),
    )
    return chunk_index


def _extract_function_chunk_ids(chunk_index: Dict[str, Any]) -> frozenset:
    """chunk_index에서 함수 chunk_id만 추출한다.

    chunk_id 규칙:
      - 함수: "pkg.Module.func" → '#' 없음, 'C:' 접두사 없음
      - 변수: "pkg.Module#var_123" → '#' 포함
      - 콜:  "C:10:20:0" → 'C:' 접두사
    """
    return frozenset(
        cid for cid in chunk_index
        if "#" not in cid and not cid.startswith("C:")
    )


def _match_variables_to_functions(
    variable_facts_list: list,
    function_chunk_ids: frozenset,
    data_dir: str,
    span_index: Any,
    chunk_index: Dict[str, Any],
) -> List[Tuple[str, str]]:
    """각 변수명으로 bundle_search를 실행하고 함수 chunk와 매칭한다.

    Returns:
        중복 제거된 (function_id, variable_id) 리스트.
    """
    refs: List[Tuple[str, str]] = []
    seen: set = set()

    for v in variable_facts_list:
        name = getattr(v, "name", "") or ""
        variable_id = getattr(v, "variable_id", "") or ""

        # 유효성 검사: 이름과 ID가 모두 있어야 하고, 너무 짧은 이름은 오탐 방지
        if not name or not variable_id or len(name) < _MIN_VAR_NAME_LENGTH:
            continue

        # 변수명이 코드 본문에 등장하는 청크 검색
        search_results = _bundle_search(
            data_dir, name, kind="text",
            span_index=span_index, chunk_index=chunk_index,
        )

        for result in search_results:
            chunk_id = result.get("chunk_id", "")
            if not chunk_id:
                continue

            # 함수 청크만 대상 (변수/상수/콜 청크 제외)
            if chunk_id not in function_chunk_ids:
                continue

            # 중복 방지
            pair = (chunk_id, variable_id)
            if pair in seen:
                continue
            seen.add(pair)
            refs.append(pair)

    return refs
