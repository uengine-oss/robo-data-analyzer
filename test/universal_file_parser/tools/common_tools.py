"""파일 종류 무관 공통 도구 (범용 흐름).

AI 워크플로:
  1. extract 도구 (파일 종류별 1개) 로 재료 확인
  2. parse() 코드 작성 + run_parser 로 실행
  3. inspect_result 로 구조 검증 (청크 수, 누락, 중복 등)
  4. 이상 있으면 코드 수정 → 2 로 복귀
  5. 깨끗하면 submit_parser → 내부에서 자동으로:
        a) 쓸모없는 청크 필터 (name==value, 빈값 등)
        b) 중복 제거
        c) coverage 검증 (원본 토큰 중 몇 % 가 청크에 포함되나)
        d) 일괄 LLM 카테고리 배정
        e) 최종 반환

포맷별 하드코딩 없음. state.last_extracted 유무로 입력 타입 결정.
"""

import json
import logging
import re

from langchain_core.tools import tool

from ..code_validator import validate_parser_code
from ..inspect import inspect_result as inspect_result_fn

logger = logging.getLogger(__name__)


ALLOWED_CATEGORIES = {
    'sql', 'entity', 'rule', 'config', 'identifier', 'credential',
    'endpoint', 'path', 'reference', 'metadata', 'other',
}

# 검증 임계값
COVERAGE_WARN_THRESHOLD = 0.7    # 청크 전체가 원본 토큰의 70% 미만이면 누락 경고


# =============================================================================
# 공통 헬퍼
# =============================================================================

def _input_for_parse(state):
    """parse() 에 넘길 입력 결정.

    extract 도구가 호출됐으면 그 결과 (dict) 를, 아니면 원본 bytes.
    포맷 이름으로 분기하지 않음 — 범용.
    """
    if state.last_extracted is not None:
        return state.last_extracted
    return state.data


def _collect_text_from(obj) -> str:
    """임의의 구조에서 모든 문자열을 수집해 하나로 이음 (coverage 계산용)."""
    if obj is None:
        return ""
    if isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return ' '.join(_collect_text_from(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return ' '.join(_collect_text_from(v) for v in obj)
    return str(obj)


def _tokenize(text: str) -> set:
    """공백/구두점으로 자른 2자 이상 토큰 집합."""
    return set(t.lower() for t in re.findall(r'[A-Za-z0-9_./\-가-힣]+', text) if len(t) >= 2)


def _coverage(chunks: list, original_text: str) -> float:
    """원본 토큰 중 청크 value 에 포함된 비율 (0.0 ~ 1.0)."""
    orig = _tokenize(original_text)
    if not orig:
        return 1.0
    chunk_text = ' '.join(str(c.get('value', '')) for c in chunks if isinstance(c, dict))
    chunk_tokens = _tokenize(chunk_text)
    return len(orig & chunk_tokens) / len(orig)


# =============================================================================
# 청크 정제 (submit 시 자동 실행)
# =============================================================================

def _clean_chunks(chunks: list) -> list:
    """쓸모없는 청크 필터 + 중복 제거. 범용 로직."""
    seen_values: set[str] = set()
    clean: list = []

    for c in chunks:
        if not isinstance(c, dict):
            continue
        name = str(c.get('name', '')).strip()
        value = str(c.get('value', '')).strip()

        # 필수 필드
        if not name or not value:
            continue
        # name 과 value 가 동일 (제목 셀 하나만 청크화된 경우)
        if name == value:
            continue
        # value 중복 (L1 과 L2 가 같은 내용 갖는 경우)
        if value in seen_values:
            continue

        seen_values.add(value)
        clean.append(c)

    return clean


# =============================================================================
# 청크 검증 (submit 전 최종 체크)
# =============================================================================

def _verify_chunks(chunks: list, input_data) -> list[str]:
    """범용 검증 — 포맷 무관. 발견된 문제 리스트 반환 (빈 리스트 = OK)."""
    issues = []

    if not chunks:
        issues.append("결과가 비어있음")
        return issues

    # 1. 모든 청크에 name + value 존재
    for i, c in enumerate(chunks):
        if not isinstance(c, dict):
            issues.append(f"청크 [{i}] 가 dict 아님")
            continue
        if not str(c.get('name', '')).strip():
            issues.append(f"청크 [{i}] name 비어있음")
        if not str(c.get('value', '')).strip():
            issues.append(f"청크 [{i}] value 비어있음")

    # 2. Coverage — 청크 전체가 원본의 몇 % 를 담고 있나
    orig_text = _collect_text_from(input_data)
    if orig_text:
        cov = _coverage(chunks, orig_text)
        if cov < COVERAGE_WARN_THRESHOLD:
            issues.append(
                f"coverage={cov:.0%} — 원본 토큰 중 {int(cov*100)}% 만 청크에 포함됨 "
                f"(임계 {int(COVERAGE_WARN_THRESHOLD*100)}%). 누락된 내용 의심. "
                f"parse() 가 일부 구간을 건너뛰었을 가능성."
            )

    return issues


# =============================================================================
# 카테고리 일괄 배정 (submit 시 자동)
# =============================================================================

def _auto_categorize(chunks: list, llm) -> None:
    """카테고리 없는 청크를 LLM 일괄 호출로 배정 (in-place)."""
    if not llm:
        for c in chunks:
            if isinstance(c, dict):
                c.setdefault('category', 'other')
        return

    remaining = [
        (i, c) for i, c in enumerate(chunks)
        if isinstance(c, dict) and c.get('category') not in ALLOWED_CATEGORIES
    ]
    if not remaining:
        return

    categories = _batch_categorize_via_llm(llm, remaining)
    if categories is None:
        for _, c in remaining:
            c['category'] = 'other'
        return
    for (_, c), cat in zip(remaining, categories):
        cat = (cat or '').strip().lower()
        c['category'] = cat if cat in ALLOWED_CATEGORIES else 'other'


def _batch_categorize_via_llm(llm, items: list) -> list | None:
    """한 번의 LLM 호출로 여러 청크 카테고리 결정."""
    lines = []
    for idx, c in items:
        name = str(c.get('name', ''))[:80]
        path = str(c.get('path', ''))[:80]
        preview = str(c.get('value', ''))[:300].replace('\n', ' ')
        lines.append(f"[{idx}] name={name!r} path={path!r}\n    preview: {preview}")

    prompt = (
        "다음 청크들 각각에 enum 11 개 중 하나의 카테고리를 배정하세요.\n\n"
        "enum:\n"
        "  sql         — SQL 문장/쿼리\n"
        "  entity      — 테이블/컬럼/스키마 정의\n"
        "  rule        — 비즈니스 규칙/조건/절차/운영 지침\n"
        "  config      — 설정값/플래그/파라미터\n"
        "  identifier  — 이름/ID 식별자\n"
        "  credential  — password/token/secret\n"
        "  endpoint    — URL/IP/호스트/포트\n"
        "  path        — 파일/디렉토리 경로\n"
        "  reference   — 클래스/메서드 FQN 참조\n"
        "  metadata    — 제목/버전/설명/타임스탬프\n"
        "  other       — 위에 안 맞거나 섞여있음\n\n"
        "출력: JSON 배열 — [{\"idx\": N, \"category\": \"...\"}, ...]. 다른 텍스트 없이 JSON 만.\n\n"
        "청크:\n" + "\n".join(lines) + "\n\n"
        "JSON 배열:"
    )

    try:
        resp = llm.invoke(prompt)
        raw = resp.content if hasattr(resp, 'content') else resp
        if isinstance(raw, list):
            parts = []
            for part in raw:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    parts.append(part.get('text', '') or part.get('content', ''))
            text = ''.join(parts)
        else:
            text = str(raw)
        text = text.strip()
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\n?|\n?```$', '', text, flags=re.M)
        data = json.loads(text)
        if not isinstance(data, list):
            return None
        result = [''] * len(items)
        idx_map = {idx: pos for pos, (idx, _) in enumerate(items)}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            idx = entry.get('idx')
            cat = entry.get('category', '')
            if idx in idx_map:
                result[idx_map[idx]] = cat
        for i, v in enumerate(result):
            if not v:
                result[i] = 'other'
        return result
    except Exception as e:
        logger.warning("    categorize LLM 호출 실패: %s", e)
        return None


# =============================================================================
# 도구: run_parser
# =============================================================================

def make_run_parser(state, execute_parser, error_signature):
    @tool
    def run_parser(code: str) -> str:
        """작성한 parse() 코드를 AST 검증 후 실행.

        parse() 는 list[{name, value, path}] 를 반환해야 함.
        (path 는 계층 경로 문자열, 최상위면 name 과 동일해도 됨)
        category 는 채우지 않아도 됨 — submit 시 자동 배정됨.
        """
        # 정지 조건 먼저 체크 — 초과 시 run_parser 아예 실행 안 함.
        stop = state.stop_reason() if hasattr(state, 'stop_reason') else None
        if stop:
            logger.warning("    run_parser 거부 — %s", stop)
            return f"[FORCE_STOP] {stop} — 지금까지 결과 있으면 submit_parser 로 제출, 없으면 종료."

        state.last_code = code
        version = state.next_version()
        logger.info("    [v%d] run_parser: AST 검증 (code %d bytes)", version, len(code))

        ok, reason = validate_parser_code(code)
        if not ok:
            sig = error_signature(f"ast:{reason}")
            state.record_attempt(
                phase='ast', status='failed',
                error_type=reason.split(':', 1)[0],
                error_message=reason, signature=sig, code_preview=code[:400],
            )
            logger.warning("    [v%d] AST 실패: %s", version, reason)
            return f"[AST_FAIL] {reason}"

        input_data = _input_for_parse(state)
        try:
            parsed = execute_parser(code, input_data)
            state.last_result = parsed
        except Exception as e:
            sig = error_signature(f"exec:{type(e).__name__}:{str(e)}")
            state.record_attempt(
                phase='exec', status='failed',
                error_type=type(e).__name__, error_message=str(e)[:300],
                signature=sig, code_preview=code[:400],
            )
            logger.warning("    [v%d] EXEC 실패: %s: %s",
                           version, type(e).__name__, str(e)[:200])
            return f"[EXEC_FAIL] {type(e).__name__}: {str(e)[:300]}"

        n = len(parsed) if hasattr(parsed, '__len__') else '?'
        state.record_attempt(phase='exec', status='success')
        logger.info("    [v%d] ✓ items=%s", version, n)

        return (
            f"[OK] items={n}\n"
            "→ inspect_result() 로 구조 확인. 깨끗하면 submit_parser() (카테고리는 자동)."
        )
    return run_parser


# =============================================================================
# 도구: inspect_result
# =============================================================================

def make_inspect_result(state):
    @tool
    def inspect_result() -> str:
        """최근 run_parser 결과를 검토 — 청크 샘플, 중복/무의미/누락 지표."""
        if state.last_result is None:
            return "[NO_RESULT] run_parser 를 먼저 호출."
        report = inspect_result_fn(state.last_result)

        # coverage 정보도 포함
        orig = _collect_text_from(_input_for_parse(state))
        if orig:
            cov = _coverage(state.last_result, orig)
            report += f"\n▶ coverage={cov:.0%} (원본 토큰 중 청크에 포함된 비율)"
            if cov < COVERAGE_WARN_THRESHOLD:
                report += f" ⚠️ 임계 {int(COVERAGE_WARN_THRESHOLD*100)}% 미달 — 누락 의심"

        first_issue = next((ln for ln in report.split('\n') if '⚠' in ln), '정상')
        logger.info("    inspect: %s", first_issue.strip())
        return report
    return inspect_result


# =============================================================================
# 도구: submit_parser  (핵심 — 자동 정제 + 검증 + 카테고리)
# =============================================================================

def make_submit_parser(state, execute_parser, llm=None):
    @tool
    def submit_parser(code: str, description: str = "") -> str:
        """최종 파서 제출. 내부에서 자동으로:
          1) 쓸모없는 청크 필터 (name==value, 중복)
          2) coverage 등 범용 검증 — 실패 시 거부하고 AI 에게 재시도 요청
          3) LLM 일괄 카테고리 배정
          4) 최종 결과 저장 → Agent 종료

        Args:
            code: 최종 parse() 코드
            description: 한 줄 설명
        """
        # 1. AST 재검증
        ok, reason = validate_parser_code(code)
        if not ok:
            return f"[REJECT] AST: {reason}"

        # 2. 실행
        input_data = _input_for_parse(state)
        try:
            parsed = execute_parser(code, input_data)
        except Exception as e:
            return f"[REJECT] Exec: {type(e).__name__}: {str(e)[:150]}"

        if not parsed:
            return "[REJECT] 결과가 비어있음"

        # 3. 자동 정제 (무의미/중복 제거)
        before = len(parsed) if hasattr(parsed, '__len__') else 0
        cleaned = _clean_chunks(parsed) if isinstance(parsed, list) else parsed
        after = len(cleaned) if hasattr(cleaned, '__len__') else 0
        removed = before - after

        if not cleaned:
            return "[REJECT] 정제 후 청크 0개 (원본 name==value 뿐이거나 전부 중복)"

        # 4. 범용 검증
        issues = _verify_chunks(cleaned, input_data)
        if issues:
            issue_str = '; '.join(issues)
            logger.warning("    submit 거부: %s", issue_str)
            return (
                f"[REJECT] 검증 실패: {issue_str}\n"
                "→ parse() 코드를 수정하고 다시 run_parser 후 submit_parser 재시도."
            )

        # 5. 자동 카테고리 배정 (LLM 1회)
        if isinstance(cleaned, list):
            _auto_categorize(cleaned, llm)

        # 6. 제출
        state.submitted_code = code
        state.submitted_description = description
        state.last_result = cleaned
        state.record_attempt(phase='submit', status='ok', code_preview=code[:400])
        logger.info("    ✓ SUBMITTED: %s (items=%d, removed=%d)",
                    description[:80], after, removed)
        note = f" (정제에서 {removed}개 제거됨)" if removed else ""
        return f"SUBMITTED{note}"
    return submit_parser


# =============================================================================
# 도구: scan_siblings  (그룹 처리 시 형제 파일 샘플)
# =============================================================================

SIBLING_SAMPLE_BYTES = 2048      # 형제 1개당 프리뷰 크기


def make_scan_siblings(state):
    @tool
    def scan_siblings(n_bytes: int = SIBLING_SAMPLE_BYTES) -> str:
        """같은 확장자 형제 파일들의 앞부분 샘플 반환.

        대표 파일만 보고 파서를 짜면 한 파일 과적합 위험 — 형제들도 같은 구조인지
        직접 눈으로 확인하고 공통 패턴만 코드화 하세요.

        Args:
            n_bytes: 형제 1개당 프리뷰 크기 (기본 2048).
        """
        siblings = getattr(state, 'siblings', None) or []
        if not siblings:
            return "[NO_SIBLINGS] 대표 파일 단독 — 형제 없음. 이 파일 기준으로 파서 작성."

        n = max(int(n_bytes), 256)
        lines = [f"형제 파일 {len(siblings)}개 샘플:"]
        for i, (sib_path, sib_data) in enumerate(siblings, 1):
            chunk = sib_data[:n]
            try:
                text = chunk.decode('utf-8', errors='replace')
            except Exception:
                text = repr(chunk[:200])
            lines.append(f"\n── [{i}] {sib_path} ({len(sib_data)} bytes) ──")
            lines.append(text)
        return "\n".join(lines)
    return scan_siblings


# =============================================================================
# 도구: test_on_corpus  (대표 + 형제 전체에 파서 돌려보기)
# =============================================================================

TEST_CORPUS_MIN_COVERAGE = 0.7


def make_test_on_corpus(state, execute_parser):
    @tool
    def test_on_corpus(code: str) -> str:
        """작성한 parse() 를 **대표 + 모든 형제 파일** 에 돌려보고 요약 반환.

        submit 전에 반드시 한 번 호출해서 범용성 검증. 형제 중 하나라도 coverage 낮거나
        예외 나면 파서를 수정하세요.
        """
        ok, reason = validate_parser_code(code)
        if not ok:
            return f"[AST_FAIL] {reason}"

        targets = [(state.path, state.data)] + list(getattr(state, 'siblings', []) or [])
        if len(targets) == 1:
            return "[NO_SIBLINGS] 형제 없음 — 일반 run_parser + inspect_result 로 충분."

        lines = [f"corpus 실행 결과 ({len(targets)}개 파일):"]
        passed = 0
        for i, (p, data) in enumerate(targets):
            label = "대표" if i == 0 else f"형제{i}"
            try:
                result = execute_parser(code, data)
                n = len(result) if hasattr(result, '__len__') else 0
                orig = _collect_text_from(data)
                cov = _coverage(result, orig) if (orig and isinstance(result, list)) else 1.0
                ok_cov = cov >= TEST_CORPUS_MIN_COVERAGE and n > 0
                if ok_cov:
                    passed += 1
                mark = "✓" if ok_cov else "✗"
                lines.append(
                    f"  {mark} [{label}] {p}  items={n}, coverage={cov:.0%}"
                )
            except Exception as e:
                lines.append(
                    f"  ✗ [{label}] {p}  EXC: {type(e).__name__}: {str(e)[:120]}"
                )

        lines.append(f"\n총 {passed}/{len(targets)} 통과 "
                     f"(임계 coverage={int(TEST_CORPUS_MIN_COVERAGE*100)}%).")
        if passed == len(targets):
            lines.append("→ 전부 통과. submit_parser 로 제출하세요.")
        else:
            lines.append("→ 일부 실패 — 실패 파일 구조 확인하고 parse() 수정 후 재시도.")
        return "\n".join(lines)
    return test_on_corpus


# =============================================================================
# 도구 빌드
# =============================================================================

def build_common_tools(state, execute_parser, error_signature, llm=None) -> list:
    """공통 도구 묶음.

    - run_parser / inspect_result / submit_parser (항상)
    - scan_siblings / test_on_corpus (형제 파일이 있을 때만 의미 있지만 항상 빌드)

    categorize 는 submit_parser 내부에서 자동 수행되므로 별도 도구 없음.
    """
    return [
        make_run_parser(state, execute_parser, error_signature),
        make_inspect_result(state),
        make_scan_siblings(state),
        make_test_on_corpus(state, execute_parser),
        make_submit_parser(state, execute_parser, llm=llm),
    ]
