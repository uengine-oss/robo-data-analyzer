"""파일 종류 무관 공통 도구 3개.

- run_parser: AST 검증 + 실행 + 짧은 요약 반환 (item 수 + 첫 청크 샘플)
- inspect_result: 결과를 "거울"로 자세히 보여줌 (청크 20개 샘플 + 참고 지표)
- submit_parser: 최종 제출 (Agent 루프 종료)

설계 원칙:
  - Coverage/Round-trip 검증 안 함. 수치 기반 블로킹 제거.
  - AI 가 inspect_result 보고 **내용으로** 판단해서 재시도 여부 결정.
  - 카테고리 이상, 내용 섞임, 청크 누락 등은 AI 판단 영역.
"""

import logging

from langchain_core.tools import tool

from ..code_validator import validate_parser_code
from ..inspect import inspect_result as inspect_result_fn

logger = logging.getLogger(__name__)


BINARY_FORMATS = {'pdf', 'pptx', 'xlsx', 'docx'}


def make_run_parser(state, execute_parser, error_signature):
    @tool
    def run_parser(code: str) -> str:
        """작성한 parse() 코드를 AST 검증 후 실행.

        반환값은 매우 짧은 요약만 (item 수 + 첫 청크). 상세는 inspect_result() 필수.

        Args:
            code: parse() 만 포함한 Python 코드 (serialize 불필요)
        """
        state.last_code = code
        version = state.next_version()
        logger.info("    [v%d] run_parser: AST 검증 시작 (code %d bytes)", version, len(code))

        ok, reason = validate_parser_code(code)
        if not ok:
            sig = error_signature(f"ast:{reason}")
            state.record_attempt(
                phase='ast', status='failed',
                error_type=reason.split(':', 1)[0],
                error_message=reason,
                signature=sig,
                code_preview=code[:400],
            )
            logger.warning("    [v%d] ✗ AST_VALIDATION_FAILED: %s", version, reason)
            return f"[AST_VALIDATION_FAILED] {reason}\n→ 금지 import/호출 또는 parse() 누락. 코드 수정 필요."

        # 바이너리 문서는 extract 선행 필수
        if state.fmt in BINARY_FORMATS and state.last_extracted is None:
            return (f"[ERROR] '{state.fmt}' 파일은 {state.fmt}_extract 도구를 먼저 호출해 "
                    f"재료(extracted dict)를 확보한 뒤 run_parser 호출하세요.")

        input_data = state.last_extracted if state.fmt in BINARY_FORMATS else state.data

        try:
            parsed = execute_parser(code, input_data)
            state.last_result = parsed
        except Exception as e:
            sig = error_signature(f"exec:{type(e).__name__}:{str(e)}")
            state.record_attempt(
                phase='exec', status='failed',
                error_type=type(e).__name__,
                error_message=str(e)[:300],
                signature=sig,
                code_preview=code[:400],
            )
            logger.warning("    [v%d] ✗ EXEC_ERROR: %s: %s",
                           version, type(e).__name__, str(e)[:200])
            return f"[EXEC_ERROR] {type(e).__name__}: {str(e)[:300]}\n→ parse() 실행 예외. 코드 수정 필요."

        # 결과 요약 한 줄 (item 수 + 첫 청크 힌트)
        n_items = len(parsed) if hasattr(parsed, '__len__') else '?'
        first_hint = _first_hint(parsed)
        state.record_attempt(phase='exec', status='success')
        logger.info("    [v%d] ✓ exec OK (items=%s) %s", version, n_items, first_hint)

        return (
            f"[OK] items={n_items}  {first_hint}\n"
            "→ inspect_result() 로 전체 청크 샘플과 카테고리 분포를 확인한 뒤, "
            "내용이 이상하면 (카테고리 틀림 / 섹션 섞임 / 놓친 필드 등) "
            "이유와 함께 코드 수정 후 재실행. 깨끗하면 submit_parser."
        )
    return run_parser


def _first_hint(parsed) -> str:
    """첫 청크 샘플을 한 줄로."""
    if not parsed:
        return ""
    try:
        if isinstance(parsed, list) and parsed:
            first = parsed[0]
            if isinstance(first, dict):
                name = str(first.get('name', ''))[:30]
                cat = first.get('category', '')
                return f"first: [{cat}] {name}"
            return f"first: {str(first)[:60]}"
        if isinstance(parsed, dict):
            k = next(iter(parsed))
            return f"first_key: {k}"
    except Exception:
        pass
    return ""


def make_inspect_result(state):
    @tool
    def inspect_result() -> str:
        """방금 run_parser 로 만든 결과를 '거울' 로 자세히 보여줌.

        청크 20개 샘플 + 카테고리 분포 + 참고 지표를 반환.
        이걸 보고 **내용이 이상한지 AI 가 직접 판단**:
          - 카테고리가 내용과 안 맞으면 재시도
          - 섹션/레코드가 섞였으면 재시도
          - 필드를 놓쳤으면 재시도
        재시도 시 run_parser 에 수정 사유를 담아 새 코드 전달.
        """
        if state.last_result is None:
            return "[NO_RESULT] run_parser 를 먼저 호출해 결과를 생성하세요."
        report = inspect_result_fn(state.last_result)
        first_warning = next(
            (ln for ln in report.split('\n') if '⚠' in ln or '참고 지표' in ln),
            '정상'
        )
        logger.info("    inspect: %s", first_warning.strip())
        return report
    return inspect_result


def make_submit_parser(state, execute_parser):
    @tool
    def submit_parser(code: str, description: str = "") -> str:
        """최종 파서 코드 제출. Agent 루프 종료.

        Args:
            code: 최종 parse() 코드
            description: 한 줄 설명 (예: 'INI-style with [section] headers')
        """
        ok, reason = validate_parser_code(code)
        if not ok:
            return f"[SUBMIT_REJECTED] AST validation failed: {reason}"

        input_data = state.last_extracted if state.fmt in BINARY_FORMATS else state.data

        try:
            parsed = execute_parser(code, input_data)
        except Exception as e:
            return f"[SUBMIT_REJECTED] Code fails on execute: {type(e).__name__}: {str(e)[:150]}"

        if not parsed:
            return "[SUBMIT_REJECTED] 결과가 비었음. parse() 로직 확인 필요."

        state.submitted_code = code
        state.submitted_description = description
        state.last_result = parsed
        state.record_attempt(phase='submit', status='ok', code_preview=code[:400])
        logger.info("    ✓ SUBMITTED: %s (items=%s)",
                    description[:80], len(parsed) if hasattr(parsed, '__len__') else '?')
        return "SUBMITTED"
    return submit_parser


def build_common_tools(state, execute_parser, error_signature) -> list:
    """공통 3개 도구 (run_parser, inspect_result, submit_parser)."""
    return [
        make_run_parser(state, execute_parser, error_signature),
        make_inspect_result(state),
        make_submit_parser(state, execute_parser),
    ]
