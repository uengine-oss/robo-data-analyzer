"""파싱 결과 품질 검증

검증:
  1. Non-empty
  2. Coverage ≥ threshold (원본 non-whitespace 토큰 중 결과에 포함된 비율)
  3. Round-trip:
     - JSON: json.dumps 로 재직렬화 후 정규화 비교
     - 미지 포맷 (DeepAgent 파서): AI 가 만든 serialize() 사용 가능 → loose_match 비교
     - 기타 (YAML/XML 등): 생략
"""

import json
import re


# 문서 기준 0.85, 라이브러리 경로의 주석 손실 고려 시 0.70 실측값.
# DeepAgent 경로에선 엄격한 0.85 적용 (품질 중요).
COVERAGE_THRESHOLD_LIBRARY = 0.70
COVERAGE_THRESHOLD_DEEPAGENT = 0.85


def validate(parsed, original: bytes, fmt: str, *,
             serialize_fn=None, strict: bool = False) -> tuple[bool, str]:
    """파싱 결과 검증.

    Args:
        parsed: 파서가 반환한 dict | list
        original: 원본 파일 bytes
        fmt: 포맷 이름 ('json', 'yaml', ..., 'unknown')
        serialize_fn: DeepAgent 가 만든 serialize 함수 (없으면 None)
        strict: True 면 엄격 (DeepAgent 경로), False 면 완화 (라이브러리)

    Returns:
        (ok: bool, reason: str)
    """
    # 1. Non-empty
    if not parsed:
        return False, "empty_result"

    # 2. Coverage
    threshold = COVERAGE_THRESHOLD_DEEPAGENT if strict else COVERAGE_THRESHOLD_LIBRARY
    cov = coverage(parsed, original)
    if cov < threshold:
        return False, f"low_coverage_{cov:.2f}_below_{threshold}"

    # 3. Round-trip
    if fmt == 'json':
        if not _roundtrip_json(parsed, original):
            return False, "roundtrip_mismatch_json"
    elif serialize_fn is not None:
        # DeepAgent 가 만든 serialize 사용
        ok, reason = _roundtrip_with_serialize(parsed, original, serialize_fn)
        if not ok:
            return False, f"roundtrip_mismatch_serialize: {reason}"

    return True, "ok"


def coverage(parsed, original: bytes) -> float:
    """원본 non-whitespace 토큰 중 결과에 포함된 비율."""
    try:
        original_str = original.decode('utf-8', errors='replace')
    except Exception:
        original_str = str(original)

    original_tokens = set(_tokenize(original_str))
    if not original_tokens:
        return 1.0

    parsed_str = json.dumps(parsed, ensure_ascii=False, default=str)
    parsed_tokens = set(_tokenize(parsed_str))

    hit = original_tokens & parsed_tokens
    return len(hit) / len(original_tokens)


def _tokenize(text: str) -> list[str]:
    """공백·구조문자 기준 토큰 분리. 최소 2자 이상.

    값 내부의 `.`, `/`, `-`, `_` 는 토큰에 포함 (URL/path 보존).
    """
    tokens = re.findall(r'[A-Za-z0-9_./\-]+', text)
    return [t.lower() for t in tokens if len(t) >= 2]


# =============================================================================
# Round-trip 검증
# =============================================================================

def _roundtrip_json(parsed, original: bytes) -> bool:
    """JSON 파싱 결과 재직렬화 후 원본과 의미적 동등성 비교."""
    try:
        original_obj = json.loads(original.decode('utf-8', errors='replace'))
        from .stage1_library import _stringify
        original_norm = _stringify(original_obj)
        a = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
        b = json.dumps(original_norm, sort_keys=True, ensure_ascii=False)
        return a == b
    except Exception:
        return False


def _roundtrip_with_serialize(parsed, original: bytes, serialize_fn) -> tuple[bool, str]:
    """DeepAgent 가 만든 serialize 함수로 재직렬화 → 원본과 loose_match.

    Returns:
        (ok: bool, reason: str)
    """
    try:
        reserialized = serialize_fn(parsed)
    except Exception as e:
        return False, f"serialize_error: {type(e).__name__}: {str(e)[:100]}"

    # serialize 가 str 을 리턴하면 bytes 로 변환
    if isinstance(reserialized, str):
        reserialized = reserialized.encode('utf-8', errors='replace')
    if not isinstance(reserialized, bytes):
        return False, f"serialize_wrong_type: {type(reserialized).__name__}"

    if loose_match(reserialized, original):
        return True, "ok"
    return False, "loose_match_failed"


def loose_match(a: bytes, b: bytes) -> bool:
    """공백·주석·빈 줄을 무시하고 의미적으로 같은지 비교."""
    return _normalize_for_match(a) == _normalize_for_match(b)


def _normalize_for_match(data: bytes) -> str:
    """주석(#, ;, //) 과 공백 제거. 남은 의미있는 문자만 이어붙임."""
    try:
        text = data.decode('utf-8', errors='replace')
    except Exception:
        text = str(data)

    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith((';', '#', '//')):
            continue
        # 줄 중간 주석도 제거 (간단하게 # 이후만)
        if ' #' in stripped and not stripped.startswith('http'):
            stripped = stripped.split(' #', 1)[0].strip()
        lines.append(stripped)
    # 공백/탭 전부 제거
    joined = ''.join(lines)
    return re.sub(r'\s+', '', joined)
