"""경로 2 — DeepAgent 기반 파서/직렬화기 코드 생성.

Execute-Verify Loop:
  [extract tool] → run_parser → inspect_result → (자기판단) → (반복)
  → validate_result → submit_parser

파일 종류별 extract 도구:
  - text_unknown: read_sample (raw bytes)
  - pdf:          pdf_extract
  - pptx:         pptx_extract
  - xlsx:         excel_extract
  - docx:         docx_extract

동작 핵심:
  - run_parser 는 결과를 반환하지 않음 → inspect_result 강제 경유
  - 같은 에러 signature 연속 2회 시 조기 종료
  - 최대 5회 루프
  - 실패 시 error.log 에 상세 이력 기록
"""

import hashlib
import logging
import time
from pathlib import Path

import yaml
from langchain_core.tools import BaseTool

from client.llm_client import get_llm
from config.settings import settings

from .code_validator import validate_parser_code
from .error_log import AttemptRecord, write_error_log
from .tools.common_tools import build_common_tools
from .tools.text_tool import make_read_sample
from .tools.pdf_tool import make_pdf_extract
from .tools.pptx_tool import make_pptx_extract
from .tools.excel_tool import make_excel_extract
from .tools.docx_tool import make_docx_extract

logger = logging.getLogger(__name__)


# 정지 조건 (3중) — 한 파일이 무한정 토큰을 태우지 못하게.
REPEATED_ERROR_LIMIT = 3            # 같은 에러 signature 연속 N 회
DEFAULT_MAX_ATTEMPTS = 12           # run_parser 호출 총 횟수 상한
DEFAULT_WALL_CLOCK_SEC = 60.0       # 에이전트 루프 벽시계 상한
SIBLING_MAX_BYTES = 1 * 1024 * 1024  # 형제 1개당 로딩 상한 (1MB)


# =============================================================================
# 에러 signature (같은 에러 반복 감지)
# =============================================================================

def error_signature(error_msg: str) -> str:
    """에러 종류 판별용 해시 (앞 150자 기반)."""
    return hashlib.md5(error_msg[:150].encode('utf-8', errors='replace')).hexdigest()[:8]


# =============================================================================
# 파서 코드 실행 — parse + serialize 둘 다 namespace 에 로딩
# =============================================================================

def execute_parser(code: str, input_data):
    """생성된 parse() 함수를 safe builtins 로 실행해 결과만 반환.

    Args:
        code: parse() 포함 Python 코드
        input_data: parse() 에 넘길 입력 (bytes: 텍스트 / dict: extract 결과)

    주의: 호출 전 반드시 validate_parser_code(code) 로 AST 검증 통과.
    """
    namespace: dict = {'__builtins__': _safe_builtins()}
    exec(compile(code, '<llm_parser>', 'exec'), namespace)

    parse_fn = namespace.get('parse')
    if parse_fn is None:
        raise ValueError("parse function not found after exec")

    return parse_fn(input_data)


def _safe_builtins() -> dict:
    """화이트리스트 builtins 만 노출."""
    import builtins as _b
    safe_names = {
        'True', 'False', 'None',
        'abs', 'all', 'any', 'bool', 'bytes', 'chr', 'dict', 'enumerate',
        'filter', 'float', 'frozenset', 'hash', 'hex', 'int', 'isinstance',
        'issubclass', 'iter', 'len', 'list', 'map', 'max', 'min', 'next',
        'oct', 'ord', 'pow', 'range', 'repr', 'reversed', 'round',
        'set', 'slice', 'sorted', 'str', 'sum', 'tuple', 'type', 'zip',
        'Exception', 'ValueError', 'TypeError', 'KeyError', 'IndexError',
        'AttributeError', 'StopIteration', 'RuntimeError',
        '__import__',
    }
    return {name: getattr(_b, name) for name in safe_names if hasattr(_b, name)}


# =============================================================================
# Agent 공유 상태
# =============================================================================

class AgentState:
    """DeepAgent 실행 중 공유 상태 + 시도 이력."""

    def __init__(self, path: str, data: bytes, fmt: str,
                 siblings: list[tuple[str, bytes]] | None = None,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 wall_clock_sec: float = DEFAULT_WALL_CLOCK_SEC):
        self.path = path
        self.data = data
        self.fmt = fmt                                # 'text_unknown' | 'pdf' | 'pptx' | 'xlsx' | 'docx' | ...

        # 같은 확장자 형제 파일 (그룹 처리 시 제공). 파서 범용성 확인용.
        self.siblings: list[tuple[str, bytes]] = siblings or []

        self.last_code: str | None = None
        self.last_result = None
        self.last_extracted = None                    # extract 도구 결과 (바이너리 문서용)

        self.submitted_code: str | None = None
        self.submitted_description: str = ""

        self.attempts: list[AttemptRecord] = []
        self.error_signatures: list[str] = []          # 연속 판정용 — 성공 시 리셋
        self.termination_reason: str | None = None

        # 정지 조건
        self.max_attempts = max_attempts
        self.wall_clock_sec = wall_clock_sec
        self.started_at = time.monotonic()

    def next_version(self) -> int:
        return len(self.attempts) + 1

    def stop_reason(self) -> str | None:
        """지금 멈춰야 하면 그 이유, 아니면 None."""
        if len(self.attempts) >= self.max_attempts:
            return f"max_attempts({self.max_attempts}) reached"
        if time.monotonic() - self.started_at > self.wall_clock_sec:
            return f"wall_clock({self.wall_clock_sec}s) exceeded"
        if self.is_repeated_error():
            return f"repeated_error x{REPEATED_ERROR_LIMIT}"
        return None

    def record_attempt(self, phase: str, status: str, *,
                       error_type: str | None = None,
                       error_message: str | None = None,
                       signature: str | None = None,
                       code_preview: str | None = None) -> None:
        self.attempts.append(AttemptRecord(
            version=self.next_version(),
            phase=phase,
            status=status,
            error_type=error_type,
            error_message=error_message,
            signature=signature,
            code_preview=code_preview,
        ))
        if status == 'failed' and signature:
            self.error_signatures.append(signature)
        else:
            # 성공하면 연속 카운터 리셋
            self.error_signatures = []

    def is_repeated_error(self) -> bool:
        """같은 signature 가 **연속** REPEATED_ERROR_LIMIT 회 이상 나타났나?"""
        if len(self.error_signatures) < REPEATED_ERROR_LIMIT:
            return False
        last = self.error_signatures[-REPEATED_ERROR_LIMIT:]
        return len(set(last)) == 1


# =============================================================================
# 도구 조립 — 파일 종류별 extract 도구 + 공통 4개
# =============================================================================

def build_tools(state: AgentState, llm=None) -> list[BaseTool]:
    """파일 종류(state.fmt)에 맞는 extract 도구 + 공통 도구 반환.

    Args:
        llm: categorize_chunks 에 넘길 LLM 인스턴스 (Phase 2 분류 용)
    """
    common = build_common_tools(state, execute_parser, error_signature, llm=llm)

    fmt = state.fmt
    if fmt == 'pdf':
        extract = [make_pdf_extract(state)]
    elif fmt == 'pptx':
        extract = [make_pptx_extract(state)]
    elif fmt == 'xlsx':
        extract = [make_excel_extract(state)]
    elif fmt == 'docx':
        extract = [make_docx_extract(state)]
    else:
        # 텍스트 미지 포맷 (text_unknown, unknown 등)
        extract = [make_read_sample(state)]

    return extract + common


# =============================================================================
# 시스템 프롬프트 로드
# =============================================================================

def _load_system_prompt() -> str:
    yaml_path = Path(__file__).parent / "universal_parse.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return data["system_prompt"]


# =============================================================================
# 메인 진입점
# =============================================================================

def generate_parser_with_llm(path: str, data: bytes, fmt_hint: str = "",
                              siblings: list[tuple[str, bytes]] | None = None,
                              max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                              wall_clock_sec: float = DEFAULT_WALL_CLOCK_SEC) \
        -> tuple[object, str | None, int]:
    """DeepAgent 로 parse 코드 생성 + 실행해서 결과 반환.

    Args:
        path, data: 대표 파일 (파서를 이 파일 기준으로 작성).
        fmt_hint: 감지된 포맷 (unknown/pdf/xlsx/...).
        siblings: 같은 확장자 형제 파일 [(path, data), ...]. 있으면 scan_siblings /
                  test_on_corpus 도구로 에이전트가 범용성 검증 가능.
        max_attempts: run_parser 호출 총 상한.
        wall_clock_sec: 에이전트 루프 벽시계 상한.

    Returns:
        (parsed_result, parser_code, total_tokens)
        - 성공: parsed_result 있음, parser_code 있음
        - 실패: parsed_result = None, parser_code = None (error.log 기록됨)
    """
    try:
        from langchain.agents import create_agent
        from langchain.agents.middleware import (
            TodoListMiddleware,
            ContextEditingMiddleware,
            ClearToolUsesEdit,
            SummarizationMiddleware,
        )
    except ImportError as e:
        logger.error("langchain create_agent import 실패: %s", e)
        return None, None, 0

    # 형제 파일 크기 상한 (1MB) — 메모리 안전 + 토큰 폭주 방지
    bounded_siblings = [
        (p, d) for p, d in (siblings or []) if len(d) <= SIBLING_MAX_BYTES
    ]

    state = AgentState(
        path=path, data=data, fmt=fmt_hint or 'unknown',
        siblings=bounded_siblings,
        max_attempts=max_attempts,
        wall_clock_sec=wall_clock_sec,
    )
    system_prompt = _load_system_prompt()

    llm = get_llm(api_key=settings.llm.api_key, model=settings.llm.model)

    # llm 을 categorize_chunks 도구에 전달 (Phase 2 일괄 분류용)
    tools = build_tools(state, llm=llm)

    middleware = [
        TodoListMiddleware(),
        ContextEditingMiddleware(edits=[ClearToolUsesEdit(trigger=40_000, keep=3)]),
        SummarizationMiddleware(model=llm, trigger=("tokens", 60_000)),
    ]

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
    )

    extract_tool_name = _extract_tool_name_for(fmt_hint)
    sibling_note = (
        f"형제 파일 {len(bounded_siblings)}개 제공됨 — scan_siblings / test_on_corpus "
        f"도구로 파서를 그 파일들에도 돌려보고 submit 하세요.\n"
        if bounded_siblings else
        "형제 파일 없음 — 이 파일 단독 기준.\n"
    )
    user_msg = (
        f"파일 경로: {path}\n"
        f"파일 크기: {len(data)} bytes\n"
        f"파일 종류: {fmt_hint or 'unknown'}\n"
        f"{sibling_note}\n"
        f"작업: {extract_tool_name} → parse() 작성 → run_parser → inspect_result → "
        f"(형제 있으면) test_on_corpus → submit_parser\n\n"
        f"정지 조건 (아무거나 먼저 걸리면 즉시 제출 or 포기):\n"
        f"  - 같은 에러 signature 연속 {REPEATED_ERROR_LIMIT}회\n"
        f"  - run_parser 총 {max_attempts}회\n"
        f"  - 벽시계 {wall_clock_sec:.0f}초"
    )

    total_tokens = 0
    try:
        # recursion_limit 사실상 제한 없음 (LangGraph 내부 안전치)
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_msg}]},
            config={"recursion_limit": 1000},
        )
        total_tokens = _extract_token_usage(result)
    except Exception as e:
        logger.warning("agent.invoke 실패: %s", e)
        state.termination_reason = f"agent_error: {type(e).__name__}: {str(e)[:100]}"
        write_error_log(path, len(data), state, fmt_hint)
        # agent 실행 중단됐더라도 exec 성공한 결과가 있으면 그거라도 사용
        if state.last_result is not None:
            logger.info("agent 중단 but exec 성공 결과 있음 → 그 결과 사용")
            return state.last_result, state.last_code, total_tokens
        return None, None, total_tokens

    if state.submitted_code:
        state.termination_reason = "submitted"
        return state.last_result, state.submitted_code, total_tokens

    # submit 없이 종료됐지만 **exec 성공한 결과가 남아있으면** 그 결과를 사용.
    # AI 완벽주의로 submit 못하고 끝난 경우를 구제. 에러는 아니지만 "미제출" 기록.
    if state.last_result is not None:
        logger.warning(
            "submit 없이 agent 종료 — but last_result 있음 (v%d). 그 결과 사용.",
            state.next_version() - 1,
        )
        state.termination_reason = "used_last_result_without_submit"
        write_error_log(path, len(data), state, fmt_hint)  # 기록은 남김
        return state.last_result, state.last_code, total_tokens

    # 진짜 실패 — 결과 자체가 없음
    if state.is_repeated_error():
        state.termination_reason = "repeated_error"
    else:
        state.termination_reason = "agent_stopped_without_submit"

    write_error_log(path, len(data), state, fmt_hint)
    return None, None, total_tokens


def _extract_tool_name_for(fmt: str) -> str:
    mapping = {
        'pdf': 'pdf_extract',
        'pptx': 'pptx_extract',
        'xlsx': 'excel_extract',
        'docx': 'docx_extract',
    }
    return mapping.get(fmt, 'read_sample')


def _extract_token_usage(result) -> int:
    total = 0
    messages = result.get('messages', []) if isinstance(result, dict) else []
    for msg in messages:
        usage = getattr(msg, 'usage_metadata', None)
        if usage:
            total += usage.get('total_tokens', 0)
        else:
            meta = getattr(msg, 'response_metadata', {}) or {}
            token_usage = meta.get('token_usage') or meta.get('usage') or {}
            total += token_usage.get('total_tokens', 0)
    return total
