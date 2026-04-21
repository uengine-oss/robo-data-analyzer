"""DeepAgent 용 "거울" 도구 — 파싱 결과를 AI 에게 읽기 쉽게 요약.

Execute-Verify Loop 의 핵심: AI 가 자기 파싱 결과를 이 도구로 다시 보고,
수상한 지표가 있으면 스스로 코드를 수정하도록 유도.

구조적 문제에 집중한 4가지 지표만 검사:
  1. 키에 괄호/특수문자 ([, ], {) → 섹션 헤더가 키로 잘못 들어감
     (단, [0], [1] 같은 순수 배열 index 는 평탄화 경로이므로 제외)
  2. 키가 너무 적음 (≤ 2개) → 파싱 자체 실패 가능성
  3. 빈 값이 30% 이상 → 값 날려먹음
  4. 한 값이 500자 이상 → 여러 줄이 한 값에 뭉침

타입 관련 지표 (숫자/불린인데 문자열) 는 제외 — 외부 파일 값은 기본 문자열이 정상.
"""

import json
import re


# 리스트 인덱스 패턴 ([0], [123] 같은 것) — 평탄화에서 생긴 정상 경로
ARRAY_INDEX_PATTERN = re.compile(r'\[\d+\]')

MIN_KEY_COUNT = 3
MAX_EMPTY_RATIO = 0.30
MAX_VALUE_LEN = 500


def inspect_result(result) -> str:
    """AI 가 읽을 수 있도록 파싱 결과를 요약한 리포트 문자열 반환.

    Args:
        result: parse() 함수의 출력 (dict, list, list[dict] 등)

    Returns:
        한글 리포트. 끝 부분에 수상 지표 목록 포함.
    """
    if result is None or result == {} or result == []:
        return "파싱 결과 분석:\n▶ 결과가 비어있음 — 파싱 실패\n▶ ⚠️ 수상한 지표:\n  - 결과 없음: 파서가 아무것도 뽑지 못했거나 parse() 가 None/빈값 반환"

    flat = _flatten_for_inspect(result)
    total_keys = len(flat)
    max_depth = _max_depth(result)

    # 키 샘플 (처음 10개)
    key_samples = list(flat.items())[:10]
    sample_lines = [f"  - {k!r} = {_preview_value(v)}" for k, v in key_samples]

    # 수상 지표 검사
    issues = _detect_suspicious(flat, result)

    # 카테고리 분포 (list[dict] + category 필드일 때만)
    category_line = _category_distribution(result)

    report_lines = [
        "파싱 결과 분석:",
        f"▶ 총 키 개수: {total_keys}",
        f"▶ 최대 깊이: {max_depth}",
    ]
    if category_line:
        report_lines.append(f"▶ 카테고리 분포: {category_line}")
    report_lines.append(f"▶ 키 샘플 (처음 10개):")
    report_lines.extend(sample_lines)
    report_lines.append("▶ ⚠️ 수상한 지표:")
    if issues:
        report_lines.extend([f"  - {msg}" for msg in issues])
    else:
        report_lines.append("  없음 (정상으로 보임)")

    return "\n".join(report_lines)


# =============================================================================
# 수상한 지표 4가지
# =============================================================================

def _detect_suspicious(flat: dict, original_result) -> list[str]:
    """4가지 구조적 문제 지표 탐지."""
    issues: list[str] = []

    # 1. 키에 괄호/특수문자 — 단, 배열 인덱스 [0], [1] 은 평탄화 경로이므로 제외
    for key in flat:
        cleaned = ARRAY_INDEX_PATTERN.sub('', key)
        if any(c in '[]{}<>' for c in cleaned):
            issues.append(
                f"키에 괄호 문자: {key!r} "
                "(섹션 헤더가 키로 들어갔을 가능성)"
            )

    # 2. 키가 너무 적음
    if len(flat) < MIN_KEY_COUNT:
        issues.append(
            f"키가 {len(flat)}개뿐 → 파싱 실패 가능성 높음 "
            f"(최소 {MIN_KEY_COUNT}개 이상 기대)"
        )

    # 3. 빈 값이 30% 이상
    empty_count = sum(1 for v in flat.values() if not _has_content(v))
    if flat and empty_count / len(flat) > MAX_EMPTY_RATIO:
        issues.append(
            f"빈 값이 {empty_count}/{len(flat)}개 ({empty_count/len(flat):.0%}) "
            "→ 값을 날려먹었거나 파싱 로직 문제 가능"
        )

    # 4. 한 값이 비정상적으로 길음
    for key, value in flat.items():
        vstr = str(value) if value is not None else ""
        if len(vstr) > MAX_VALUE_LEN:
            issues.append(
                f"값이 비정상적으로 긺: {key} ({len(vstr)}자) "
                "→ 여러 줄이 한 값에 뭉쳤을 가능성 (구분자 놓침)"
            )

    return issues


# =============================================================================
# 보조 함수
# =============================================================================

def _flatten_for_inspect(obj, parent: str = "", out: dict | None = None) -> dict:
    """얕은 수준으로 평탄화해 키-값 쌍 확보. max 200개."""
    if out is None:
        out = {}
    if len(out) >= 200:
        return out

    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{parent}.{k}" if parent else str(k)
            if isinstance(v, (dict, list)) and v:
                _flatten_for_inspect(v, key, out)
            else:
                out[key] = v
            if len(out) >= 200:
                return out
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{parent}[{i}]" if parent else f"[{i}]"
            if isinstance(v, (dict, list)) and v:
                _flatten_for_inspect(v, key, out)
            else:
                out[key] = v
            if len(out) >= 200:
                return out
    else:
        if parent:
            out[parent] = obj
    return out


def _max_depth(obj, depth: int = 0) -> int:
    if isinstance(obj, dict):
        if not obj:
            return depth
        return max(_max_depth(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        if not obj:
            return depth
        return max(_max_depth(v, depth + 1) for v in obj)
    return depth


def _has_content(value) -> bool:
    """값이 의미있는지 (비어있지 않은지)."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _preview_value(value, limit: int = 80) -> str:
    s = str(value)
    if len(s) > limit:
        return s[:limit] + f"... [+{len(s)-limit}]"
    return s


def _category_distribution(result) -> str:
    """list[dict] 이고 각 항목에 'category' 가 있으면 분포 리턴."""
    if not isinstance(result, list):
        return ""
    if not result or not isinstance(result[0], dict):
        return ""
    if 'category' not in result[0]:
        return ""

    counts: dict[str, int] = {}
    for item in result:
        if isinstance(item, dict) and 'category' in item:
            cat = str(item['category'])
            counts[cat] = counts.get(cat, 0) + 1
    if not counts:
        return ""
    return ", ".join(f"{c}={n}" for c, n in sorted(counts.items(), key=lambda x: -x[1]))
