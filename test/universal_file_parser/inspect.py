"""DeepAgent 용 "거울" 도구 — 파싱 결과를 AI 에게 읽기 쉽게 보여줌.

핵심: AI 가 자기 파싱 결과를 **실제 내용 그대로** 다시 보고,
내용이 섞였거나 카테고리가 이상하면 스스로 판단해서 재시도.

기본 케이스: 결과가 list[{name, value, category}] 면 청크 단위로 보여줌.
그 외 (중첩 dict 등): 평탄화해서 key=value 샘플 보여줌.

구조적 이상 지표 (참고용, 블로커 아님):
  1. 키에 괄호/특수문자 → 섹션 헤더가 키로 들어갔을 가능성
  2. 키 너무 적음 (≤ 2개) → 파싱 실패 가능성
  3. 빈 값 30% 이상 → 값 날림
  4. 값이 500자 이상 → 여러 줄 뭉침
"""

import re


ARRAY_INDEX_PATTERN = re.compile(r'\[\d+\]')

MIN_KEY_COUNT = 3
MAX_EMPTY_RATIO = 0.30
MAX_VALUE_LEN = 500

CHUNK_SAMPLE_LIMIT = 20    # 표준 청크 샘플 개수
VALUE_PREVIEW_LIMIT = 120  # 값 미리보기 길이


def inspect_result(result) -> str:
    """AI 가 읽을 수 있도록 파싱 결과를 "있는 그대로" 보여주는 리포트.

    Args:
        result: parse() 출력 (list[{name,value,category}] 권장, 그 외 형식도 지원)
    """
    if result is None or result == {} or result == []:
        return (
            "파싱 결과 분석:\n"
            "▶ 결과가 비어있음 — 파싱 실패\n"
            "▶ ⚠️ parse() 가 None/빈값 반환. 코드 로직 확인 필요."
        )

    # 표준 포맷 (list[{name, value, category}])
    if _is_standard_chunk_format(result):
        return _inspect_chunks(result)

    # 그 외 — 평탄화 기반 리포트
    return _inspect_fallback(result)


# =============================================================================
# 표준 포맷 — list[{name, value, category}]
# =============================================================================

def _is_standard_chunk_format(result) -> bool:
    """list[dict] 이고 첫 항목에 name/value/category 가 다 있는지."""
    if not isinstance(result, list) or not result:
        return False
    first = result[0]
    if not isinstance(first, dict):
        return False
    return 'name' in first and 'value' in first and 'category' in first


def _inspect_chunks(chunks: list[dict]) -> str:
    """list[{name,value,category}] 를 테이블처럼 보여줌."""
    lines = [
        "파싱 결과 분석:",
        f"▶ 총 청크 수: {len(chunks)}",
    ]

    # 카테고리 분포
    cat_counts: dict[str, int] = {}
    for item in chunks:
        if isinstance(item, dict):
            cat = str(item.get('category', 'MISSING'))
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
    if cat_counts:
        dist = ", ".join(
            f"{c}={n}"
            for c, n in sorted(cat_counts.items(), key=lambda x: -x[1])
        )
        lines.append(f"▶ 카테고리 분포: {dist}")

    # 실제 청크 샘플 (처음 N개)
    lines.append(f"▶ 청크 샘플 (처음 {min(CHUNK_SAMPLE_LIMIT, len(chunks))}개):")

    max_name_len = max(
        (len(str(c.get('name', ''))) for c in chunks[:CHUNK_SAMPLE_LIMIT]),
        default=10,
    )
    max_name_len = min(max_name_len, 40)

    for idx, item in enumerate(chunks[:CHUNK_SAMPLE_LIMIT]):
        if not isinstance(item, dict):
            lines.append(f"  [{idx:>3}] (not dict: {type(item).__name__})")
            continue
        name = str(item.get('name', 'MISSING'))[:40]
        value = _preview_value(item.get('value', ''), VALUE_PREVIEW_LIMIT)
        category = str(item.get('category', 'MISSING'))
        lines.append(f"  [{idx:>3}] {category:<11} {name:<{max_name_len}} = {value}")

    # 이상 지표
    issues = _detect_chunk_issues(chunks)
    lines.append("▶ ⚠️ 참고 지표 (판단은 AI 가):")
    if issues:
        lines.extend([f"  - {msg}" for msg in issues])
    else:
        lines.append("  특이 사항 없음")

    return "\n".join(lines)


def _detect_chunk_issues(chunks: list[dict]) -> list[str]:
    issues = []

    # 필드 누락
    missing_fields = sum(
        1 for c in chunks
        if not isinstance(c, dict)
        or 'name' not in c or 'value' not in c or 'category' not in c
    )
    if missing_fields:
        issues.append(
            f"필드 누락 청크 {missing_fields}개 — name/value/category 중 하나 빠짐"
        )

    # 카테고리 enum 체크
    ALLOWED_CATS = {
        'sql', 'entity', 'rule', 'config', 'identifier', 'credential',
        'endpoint', 'path', 'reference', 'metadata', 'other',
    }
    wrong_cats = set()
    for c in chunks:
        if isinstance(c, dict):
            cat = c.get('category')
            if cat and cat not in ALLOWED_CATS:
                wrong_cats.add(cat)
    if wrong_cats:
        issues.append(
            f"허용 안 된 category 사용: {sorted(wrong_cats)} "
            f"(enum 11개만 허용: sql/entity/rule/config/identifier/credential/endpoint/path/reference/metadata/other)"
        )

    # 청크 수 너무 적음
    if len(chunks) < MIN_KEY_COUNT:
        issues.append(
            f"청크 {len(chunks)}개뿐 — 파싱 놓친 부분 있을 가능성"
        )

    # name 에 이상 문자
    for i, c in enumerate(chunks[:50]):
        if isinstance(c, dict):
            name = str(c.get('name', ''))
            cleaned = ARRAY_INDEX_PATTERN.sub('', name)
            if any(ch in cleaned for ch in '[]{}<>'):
                issues.append(
                    f"[{i}].name 에 괄호 문자: {name!r} "
                    "(섹션 헤더를 name 으로 썼을 가능성)"
                )
                break  # 하나만 예시로

    # 빈 값 비율
    empty_count = sum(
        1 for c in chunks
        if isinstance(c, dict) and not _has_content(c.get('value'))
    )
    if chunks and empty_count / len(chunks) > MAX_EMPTY_RATIO:
        issues.append(
            f"빈 value {empty_count}/{len(chunks)}개 ({empty_count/len(chunks):.0%}) "
            "→ 값을 놓쳤을 가능성"
        )

    # 값이 너무 긺
    for c in chunks[:100]:
        if isinstance(c, dict):
            vstr = str(c.get('value', ''))
            if len(vstr) > MAX_VALUE_LEN:
                issues.append(
                    f"name={c.get('name', '?')!r} 의 value 가 {len(vstr)}자 "
                    "→ 여러 줄이 한 청크에 뭉쳤을 가능성"
                )
                break  # 하나만

    return issues


# =============================================================================
# 평탄화 기반 fallback
# =============================================================================

def _inspect_fallback(result) -> str:
    flat = _flatten_for_inspect(result)
    total_keys = len(flat)
    max_depth = _max_depth(result)

    key_samples = list(flat.items())[:CHUNK_SAMPLE_LIMIT]
    sample_lines = [
        f"  - {k} = {_preview_value(v)}" for k, v in key_samples
    ]

    issues = _detect_suspicious(flat)

    lines = [
        "파싱 결과 분석:",
        f"▶ 총 키 개수: {total_keys}",
        f"▶ 최대 깊이: {max_depth}",
        f"▶ 키 샘플 (처음 {len(sample_lines)}개):",
    ]
    lines.extend(sample_lines)
    lines.append("▶ ⚠️ 참고 지표 (판단은 AI 가):")
    if issues:
        lines.extend([f"  - {msg}" for msg in issues])
    else:
        lines.append("  특이 사항 없음")

    return "\n".join(lines)


def _detect_suspicious(flat: dict) -> list[str]:
    issues = []

    for key in flat:
        cleaned = ARRAY_INDEX_PATTERN.sub('', key)
        if any(c in '[]{}<>' for c in cleaned):
            issues.append(f"키에 괄호: {key!r}")
            break

    if len(flat) < MIN_KEY_COUNT:
        issues.append(f"키 {len(flat)}개 — 파싱 실패 가능")

    empty_count = sum(1 for v in flat.values() if not _has_content(v))
    if flat and empty_count / len(flat) > MAX_EMPTY_RATIO:
        issues.append(f"빈 값 {empty_count}/{len(flat)}개 ({empty_count/len(flat):.0%})")

    for key, value in list(flat.items())[:100]:
        vstr = str(value) if value is not None else ""
        if len(vstr) > MAX_VALUE_LEN:
            issues.append(f"{key} 값 {len(vstr)}자 — 여러 줄 뭉침?")
            break

    return issues


# =============================================================================
# 보조 함수
# =============================================================================

def _flatten_for_inspect(obj, parent: str = "", out: dict | None = None) -> dict:
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
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _preview_value(value, limit: int = 120) -> str:
    s = str(value) if value is not None else ""
    s = s.replace('\n', '\\n').replace('\r', '')
    if len(s) > limit:
        return s[:limit] + f"... [+{len(s)-limit}]"
    return s
