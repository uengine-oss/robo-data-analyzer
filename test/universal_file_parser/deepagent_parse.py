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
from pathlib import Path

import yaml
from langchain_core.tools import BaseTool

from client.llm_client import get_llm
from config.settings import settings

from .code_validator import validate_parser_code
from .error_log import AttemptRecord, write_error_log
from .tools.common_tools import build_common_tools, BINARY_FORMATS
from .tools.text_tool import make_read_sample
from .tools.pdf_tool import make_pdf_extract
from .tools.pptx_tool import make_pptx_extract
from .tools.excel_tool import make_excel_extract
from .tools.docx_tool import make_docx_extract

logger = logging.getLogger(__name__)


MAX_ATTEMPTS = 5
REPEATED_ERROR_LIMIT = 2


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

    def __init__(self, path: str, data: bytes, fmt: str):
        self.path = path
        self.data = data
        self.fmt = fmt                                # 'text_unknown' | 'pdf' | 'pptx' | 'xlsx' | 'docx' | ...

        self.last_code: str | None = None
        self.last_result = None
        self.last_extracted = None                    # extract 도구 결과 (바이너리 문서용)

        self.submitted_code: str | None = None
        self.submitted_description: str = ""

        self.attempts: list[AttemptRecord] = []
        self.error_signatures: list[str] = []          # 연속 판정용 — 성공 시 리셋
        self.termination_reason: str | None = None

    def next_version(self) -> int:
        return len(self.attempts) + 1

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

def build_tools(state: AgentState) -> list[BaseTool]:
    """파일 종류(state.fmt)에 맞는 extract 도구 + 공통 도구 4개 반환."""
    common = build_common_tools(state, execute_parser, error_signature)

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

def generate_parser_with_llm(path: str, data: bytes, fmt_hint: str = "") \
        -> tuple[object, str | None, int]:
    """DeepAgent 로 parse 코드 생성 + 실행해서 결과 반환.

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

    state = AgentState(path=path, data=data, fmt=fmt_hint or 'unknown')
    tools = build_tools(state)
    system_prompt = _load_system_prompt()

    llm = get_llm(api_key=settings.llm.api_key, model=settings.llm.model)

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
    user_msg = (
        f"파일 경로: {path}\n"
        f"파일 크기: {len(data)} bytes\n"
        f"파일 종류: {fmt_hint or 'unknown'}\n"
        f"최대 시도: {MAX_ATTEMPTS}\n\n"
        f"작업 순서:\n"
        f"  1. {extract_tool_name} 으로 재료 확인\n"
        f"  2. parse() 작성 (serialize 불필요)\n"
        f"  3. run_parser 로 실행 — 짧은 요약만 받음\n"
        f"  4. inspect_result 로 청크 샘플 + 카테고리 분포 확인 (필수)\n"
        f"  5. 내용이 이상하면 (카테고리 틀림 / 섹션 섞임 / 필드 누락 등)\n"
        f"     사유와 함께 수정 후 3번으로 복귀\n"
        f"  6. 깨끗하면 submit_parser 로 제출\n\n"
        f"같은 에러 연속 {REPEATED_ERROR_LIMIT}회 시 포기."
    )

    total_tokens = 0
    try:
        result = agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
        total_tokens = _extract_token_usage(result)
    except Exception as e:
        logger.warning("agent.invoke 실패: %s", e)
        state.termination_reason = f"agent_error: {type(e).__name__}: {str(e)[:100]}"
        write_error_log(path, len(data), state, fmt_hint)
        return None, None, total_tokens

    if state.submitted_code:
        state.termination_reason = "submitted"
        return state.last_result, state.submitted_code, total_tokens

    # 미제출 종료
    if state.is_repeated_error():
        state.termination_reason = "repeated_error"
    elif len(state.attempts) >= MAX_ATTEMPTS:
        state.termination_reason = "max_attempts_exceeded"
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
