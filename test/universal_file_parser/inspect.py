"""파싱 결과 "거울" — 청크 샘플 + 범용 검증 지표.

AI 가 구조 확인하는 용도. 포맷별 하드코딩 없음.

지표:
  1. 필드 누락 (name / value / path)
  2. name == value (무의미 청크)
  3. 중복 value (L1/L2 복제 등)
  4. 빈 value 비율
  5. 비정상 긴 value (여러 주제가 한 청크에 뭉침)
  6. category enum 위반 (있는 경우)
"""

import re


ARRAY_INDEX_PATTERN = re.compile(r'\[\d+\]')

MIN_CHUNK_COUNT = 3
MAX_EMPTY_RATIO = 0.30
MAX_VALUE_LEN = 3000

CHUNK_SAMPLE_LIMIT = 20
VALUE_PREVIEW_LIMIT = 200

ALLOWED_CATEGORIES = {
    'sql', 'entity', 'rule', 'config', 'identifier', 'credential',
    'endpoint', 'path', 'reference', 'metadata', 'other',
}


def inspect_result(result) -> str:
    """파싱 결과에 대한 범용 검증 리포트."""
    if result is None or result == {} or result == []:
        return "파싱 결과 분석:\n▶ 결과 비어있음 — parse() 가 빈값 반환"

    if _is_chunk_list(result):
        return _inspect_chunks(result)
    return _inspect_fallback(result)


def _is_chunk_list(result) -> bool:
    if not isinstance(result, list) or not result:
        return False
    first = result[0]
    return isinstance(first, dict) and 'name' in first and 'value' in first


def _inspect_chunks(chunks: list[dict]) -> str:
    lines = [
        "파싱 결과 분석:",
        f"▶ 총 청크 수: {len(chunks)}",
    ]

    # 카테고리 분포 (있을 때만)
    cat_counts: dict[str, int] = {}
    for c in chunks:
        if isinstance(c, dict):
            cat = c.get('category')
            if cat:
                cat_counts[str(cat)] = cat_counts.get(str(cat), 0) + 1
    if cat_counts:
        dist = ", ".join(f"{c}={n}" for c, n in sorted(cat_counts.items(), key=lambda x: -x[1]))
        lines.append(f"▶ 카테고리 분포: {dist}")
    else:
        lines.append("▶ 카테고리: 미배정 (submit_parser 시 자동 배정됨)")

    # 청크 샘플
    lines.append(f"▶ 청크 샘플 (앞 {min(CHUNK_SAMPLE_LIMIT, len(chunks))}개):")
    for idx, item in enumerate(chunks[:CHUNK_SAMPLE_LIMIT]):
        if not isinstance(item, dict):
            lines.append(f"  [{idx:>3}] (not dict: {type(item).__name__})")
            continue
        name = str(item.get('name', 'MISSING'))
        value = _preview(item.get('value', ''), VALUE_PREVIEW_LIMIT)
        cat = str(item.get('category', '-'))
        path = str(item.get('path', ''))
        lines.append(f"  [{idx:>3}] [{cat:<10}] path={path!r}")
        lines.append(f"         name={name!r}")
        lines.append(f"         value={value}")

    # 범용 지표
    issues = _detect_issues(chunks)
    lines.append("▶ ⚠️ 참고 지표:")
    if issues:
        lines.extend(f"  - {m}" for m in issues)
    else:
        lines.append("  특이 사항 없음")

    return "\n".join(lines)


def _detect_issues(chunks: list[dict]) -> list[str]:
    issues = []

    # 1. 필수 필드 누락
    missing_nv = sum(
        1 for c in chunks if not isinstance(c, dict)
        or not str(c.get('name', '')).strip()
        or not str(c.get('value', '')).strip()
    )
    if missing_nv:
        issues.append(f"필드 누락 청크 {missing_nv}개 (name 또는 value 비어있음)")

    missing_path = sum(
        1 for c in chunks if isinstance(c, dict) and 'path' not in c
    )
    if missing_path:
        issues.append(f"path 필드 누락 {missing_path}개")

    # 2. name == value — 무의미 청크
    useless = [
        i for i, c in enumerate(chunks)
        if isinstance(c, dict)
        and str(c.get('name', '')).strip()
        and str(c.get('name', '')).strip() == str(c.get('value', '')).strip()
    ]
    if useless:
        issues.append(
            f"무의미 청크 {len(useless)}개 (name==value, 예: 인덱스 {useless[:3]}) — "
            f"submit 시 자동 제거됨"
        )

    # 3. 중복 value
    value_seen: dict[str, list[int]] = {}
    for i, c in enumerate(chunks):
        if isinstance(c, dict):
            v = str(c.get('value', '')).strip()
            if v:
                value_seen.setdefault(v, []).append(i)
    dups = [(v, idxs) for v, idxs in value_seen.items() if len(idxs) > 1]
    if dups:
        issues.append(
            f"중복 value 청크 {sum(len(idxs)-1 for _, idxs in dups)}개 "
            f"— submit 시 자동 제거됨"
        )

    # 4. 청크 수가 너무 적음
    if len(chunks) < MIN_CHUNK_COUNT:
        issues.append(f"청크 {len(chunks)}개뿐 — 파싱 놓친 부분 있을 가능성")

    # 5. 빈 값 비율
    empty = sum(1 for c in chunks if isinstance(c, dict) and not _has_content(c.get('value')))
    if chunks and empty / len(chunks) > MAX_EMPTY_RATIO:
        issues.append(f"빈 value {empty}/{len(chunks)}개 ({empty/len(chunks):.0%})")

    # 6. value 너무 긺 (여러 주제 뭉침 가능성)
    over_long = [i for i, c in enumerate(chunks[:100])
                 if isinstance(c, dict) and len(str(c.get('value', ''))) > MAX_VALUE_LEN]
    if over_long:
        issues.append(
            f"value 길이 {MAX_VALUE_LEN}자 초과 청크 {len(over_long)}개 — "
            f"여러 주제가 한 청크에 뭉쳤을 가능성"
        )

    # 7. category enum 위반 (있을 때만)
    wrong = set()
    for c in chunks:
        if isinstance(c, dict):
            cat = c.get('category')
            if cat and cat not in ALLOWED_CATEGORIES:
                wrong.add(cat)
    if wrong:
        issues.append(f"허용 안 된 category: {sorted(wrong)}")

    # 8. name 에 이상 문자 (섹션 구분자가 name 으로 잘못 들어감)
    for i, c in enumerate(chunks[:50]):
        if isinstance(c, dict):
            name = str(c.get('name', ''))
            cleaned = ARRAY_INDEX_PATTERN.sub('', name)
            if any(ch in cleaned for ch in '[]{}<>'):
                issues.append(f"[{i}].name 에 괄호 문자: {name!r} (구분자가 name 으로 들어갔을 가능성)")
                break

    return issues


# =============================================================================
# 평탄화 fallback (list[{name,value}] 형식 아닌 경우)
# =============================================================================

def _inspect_fallback(result) -> str:
    flat = _flatten(result)
    total = len(flat)
    max_d = _max_depth(result)

    samples = list(flat.items())[:CHUNK_SAMPLE_LIMIT]
    sample_lines = [f"  - {k} = {_preview(v)}" for k, v in samples]

    issues = _detect_suspicious(flat)

    lines = [
        "파싱 결과 분석 (non-chunk 포맷):",
        f"▶ 총 키: {total}",
        f"▶ 최대 깊이: {max_d}",
        f"▶ 키 샘플 ({len(sample_lines)}개):",
    ]
    lines.extend(sample_lines)
    lines.append("▶ ⚠️ 참고 지표:")
    if issues:
        lines.extend(f"  - {m}" for m in issues)
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
    if len(flat) < MIN_CHUNK_COUNT:
        issues.append(f"키 {len(flat)}개 — 파싱 실패 가능")
    empty = sum(1 for v in flat.values() if not _has_content(v))
    if flat and empty / len(flat) > MAX_EMPTY_RATIO:
        issues.append(f"빈 값 {empty}/{len(flat)}개 ({empty/len(flat):.0%})")
    return issues


def _flatten(obj, parent: str = "", out: dict | None = None) -> dict:
    if out is None:
        out = {}
    if len(out) >= 200:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{parent}.{k}" if parent else str(k)
            if isinstance(v, (dict, list)) and v:
                _flatten(v, key, out)
            else:
                out[key] = v
            if len(out) >= 200:
                return out
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{parent}[{i}]" if parent else f"[{i}]"
            if isinstance(v, (dict, list)) and v:
                _flatten(v, key, out)
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


def _preview(value, limit=VALUE_PREVIEW_LIMIT) -> str:
    s = str(value) if value is not None else ""
    s = s.replace('\n', '\\n').replace('\r', '')
    if limit is None or len(s) <= limit:
        return s
    return s[:limit] + f"... [+{len(s)-limit}]"
