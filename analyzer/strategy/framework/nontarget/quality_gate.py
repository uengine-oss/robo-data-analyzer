"""Phase 1/2 품질 게이트

메인은 "정답"이 아니라 "규칙"으로 통과 여부를 판단합니다.
정답 검증이 아닌 품질 검증입니다.
"""

from typing import Any, Dict, List, Optional, Tuple

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget


# ============================================================================
# Phase 1 — 블록 품질 게이트
# ============================================================================

class BlockQualityError(Exception):
    """블록 품질 게이트 실패"""
    pass


class SummaryQualityError(Exception):
    """요약 품질 게이트 실패"""
    pass


class LinkQualityError(Exception):
    """링크 품질 게이트 실패"""
    pass


def quality_gate_phase1_blocks(
    blocks: List[Dict[str, Any]],
    file_path: str = "",
    min_blocks: int = 1,
    max_blocks: int = 500,
    min_lines_per_block: int = 1,
    max_lines_per_block: int = 2000,
) -> Tuple[bool, List[str]]:
    """Phase 1 블록 품질 체크

    Args:
        blocks: BlockJSON 리스트
        file_path: 파일 경로 (로깅용)
        min_blocks: 최소 블록 수
        max_blocks: 최대 블록 수
        min_lines_per_block: 블록당 최소 줄 수
        max_lines_per_block: 블록당 최대 줄 수

    Returns:
        (passed, warnings) — passed=True면 통과, warnings=경고 메시지 리스트
    """
    warnings: List[str] = []

    # 블록 수 범위
    count = len(blocks)
    if count < min_blocks:
        msg = f"블록 수 부족: {count} < {min_blocks}"
        log_nontarget("GATE", f"[FAIL] {file_path}: {msg}")
        return False, [msg]

    if count > max_blocks:
        warnings.append(f"블록 수 과다: {count} > {max_blocks}")

    # 블록 크기
    has_undersized = False
    has_empty_code = False
    for b in blocks:
        start = b.get("start_line", 0)
        end = b.get("end_line", 0)
        size = end - start + 1
        if size < min_lines_per_block:
            warnings.append(f"블록 {b.get('name', '?')}: 크기 {size}줄 (최소 {min_lines_per_block})")
            has_undersized = True
        if size > max_lines_per_block:
            warnings.append(f"블록 {b.get('name', '?')}: 크기 {size}줄 (최대 {max_lines_per_block})")

    # node_code 비어있는지
    empty_code = [b for b in blocks if not b.get("node_code", "").strip()]
    if empty_code:
        warnings.append(f"node_code 비어있는 블록: {len(empty_code)}개")
        has_empty_code = True

    passed = not has_undersized and not has_empty_code

    if warnings:
        log_nontarget("GATE", f"[WARN] {file_path}: {'; '.join(warnings[:3])}")
    else:
        log_nontarget("GATE", f"[PASS] {file_path}: {count}개 블록 통과")

    return passed, warnings


# ============================================================================
# Phase 1 — 요약 품질 게이트
# ============================================================================

def quality_gate_phase1_summaries(
    summaries: List[Dict[str, Any]],
    file_path: str = "",
    max_unknown_ratio: float = 0.8,
    min_keywords_per_block: int = 2,
) -> Tuple[bool, List[str]]:
    """Phase 1 요약 품질 체크

    Args:
        summaries: [{block_name, summary, kind, keywords}, ...]
        file_path: 파일 경로 (로깅용)
        max_unknown_ratio: kind="unknown" 허용 비율 (80% 이상이면 실패)
        min_keywords_per_block: 블록당 최소 키워드 수

    Returns:
        (passed, warnings)
    """
    warnings: List[str] = []

    if not summaries:
        return True, []

    total = len(summaries)

    # kind 분포
    unknown_count = sum(1 for s in summaries if s.get("kind", "unknown") == "unknown")
    unknown_ratio = unknown_count / total if total > 0 else 0
    if unknown_ratio > max_unknown_ratio:
        warnings.append(
            f"kind=unknown 비율 과다: {unknown_count}/{total} ({unknown_ratio:.0%})"
        )

    # summary 비어있는 블록
    empty_summary = sum(1 for s in summaries if not s.get("summary", "").strip())
    if empty_summary > 0:
        warnings.append(f"summary 비어있는 블록: {empty_summary}/{total}")

    # keywords 부족
    low_keywords = sum(
        1 for s in summaries
        if len(s.get("keywords", [])) < min_keywords_per_block
    )
    if low_keywords > total * 0.5:
        warnings.append(f"키워드 부족 블록: {low_keywords}/{total}")

    passed = unknown_ratio <= max_unknown_ratio and empty_summary <= total * 0.3

    if warnings:
        log_nontarget("GATE", f"[WARN] {file_path} summaries: {'; '.join(warnings[:3])}")
    else:
        log_nontarget("GATE", f"[PASS] {file_path} summaries: {total}개 통과")

    return passed, warnings


# ============================================================================
# Phase 2 — 링크 품질 게이트
# ============================================================================

def quality_gate_phase2_links(
    links_json: Dict[str, Any],
    file_path: str = "",
) -> Tuple[bool, List[str]]:
    """Phase 2 링크 품질 체크 — 필터링 없이 로깅만 수행

    가드레일 제거: 에이전트 출력을 그대로 Neo4j에 저장합니다.

    Args:
        links_json: Linker 결과 JSON (수정 없음)
        file_path: 파일 경로 (로깅용)

    Returns:
        (passed, warnings)
    """
    warnings: List[str] = []

    type_key_map = {
        "from": "FROM",
        "writes": "WRITES",
        "fk_to_table": "FK_TO_TABLE",
        "fk_to": "FK_TO",
    }

    total_links = sum(
        len(links_json.get(k, []))
        for k in type_key_map.keys()
    )

    # 필터링 없이 로깅만 수행
    log_nontarget("GATE",
        f"[PASS] {file_path} links: {total_links}개 (필터링 없음)")

    return True, warnings

