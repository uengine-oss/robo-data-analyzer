"""DeepAgent — 미지 포맷용 파서/직렬화기 코드 생성

Execute-Verify Loop:
  read_sample → run_parser → inspect_result → 자기판단 → (반복) → validate_result → submit_parser

핵심 특성:
  - 최대 5회 루프 (MAX_ATTEMPTS)
  - 같은 에러 지문 2회 반복 시 조기 종료
  - 각 시도를 CodegenState.attempts 에 구조화 기록 (error.log 용)
  - tool result 는 LangChain 이 자동으로 다음 턴 컨텍스트에 전달 (추가 피드백 불필요)
  - 최종 실패 시 error_log.write_error_log() 호출
"""

import hashlib
import json
import logging
from pathlib import Path

import yaml
from langchain_core.tools import tool

from client.llm_client import get_llm
from config.settings import settings

from .code_validator import validate_parser_code
from .error_log import AttemptRecord, write_error_log
from .inspect import inspect_result as inspect_result_fn
from .validate import validate as validate_result_fn, coverage

logger = logging.getLogger(__name__)


MAX_ATTEMPTS = 5
REPEATED_ERROR_LIMIT = 2
READ_SAMPLE_MAX_BYTES = 4096


# =============================================================================
# 에러 시그니처 (같은 에러 반복 감지)
# =============================================================================

def error_signature(error_msg: str) -> str:
    """같은 종류의 에러 판별용 해시 (앞 150자, 라인번호 제외)."""
    return hashlib.md5(error_msg[:150].encode('utf-8', errors='replace')).hexdigest()[:8]


# =============================================================================
# 파서 코드 실행 — parse + serialize 둘 다 namespace 에 로딩
# =============================================================================

def execute_parser(code: str, data: bytes, *, return_serialize: bool = False):
    """생성된 parse() 함수를 현재 프로세스에서 실행.

    Args:
        code: parse() + serialize() 를 포함한 완전한 Python 코드
        data: 원본 파일 bytes
        return_serialize: True 면 (parsed, serialize_fn) 반환, False 면 parsed 만

    주의: 호출 전 반드시 `validate_parser_code(code)` 로 AST 검증을 통과해야 함.
    """
    namespace: dict = {'__builtins__': _safe_builtins()}
    exec(compile(code, '<llm_parser>', 'exec'), namespace)

    parse_fn = namespace.get('parse')
    if parse_fn is None:
        raise ValueError("parse function not found after exec")

    parsed = parse_fn(data)

    if return_serialize:
        serialize_fn = namespace.get('serialize')
        return parsed, serialize_fn
    return parsed


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

class CodegenState:
    """DeepAgent 실행 중 공유 상태 + 시도 이력."""

    def __init__(self, path: str, data: bytes):
        self.path = path
        self.data = data

        self.last_code: str | None = None            # 가장 최근 run_parser 에 넘긴 코드
        self.last_result = None                       # 가장 최근 parse() 결과 (cache)
        self.last_serialize_fn = None                 # 가장 최근 serialize() 함수

        self.submitted_code: str | None = None
        self.submitted_description: str = ""

        self.attempts: list[AttemptRecord] = []
        self.error_signatures: list[str] = []
        self.termination_reason: str | None = None   # 'submitted' | 'repeated_error' | 'max_attempts' | 'agent_error'

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
        if signature:
            self.error_signatures.append(signature)

    def is_repeated_error(self) -> bool:
        """같은 signature 가 REPEATED_ERROR_LIMIT 회 이상 쌓였나?"""
        if not self.error_signatures:
            return False
        counts: dict[str, int] = {}
        for sig in self.error_signatures:
            counts[sig] = counts.get(sig, 0) + 1
            if counts[sig] >= REPEATED_ERROR_LIMIT:
                return True
        return False


# =============================================================================
# 도구 5개
# =============================================================================

def build_tools(state: CodegenState):
    """DeepAgent 가 쓸 5개 도구 생성."""

    @tool
    def read_sample(n_bytes: int = READ_SAMPLE_MAX_BYTES) -> str:
        """파일 앞부분 샘플을 텍스트로 반환. 파일 구조 파악에 사용.

        Args:
            n_bytes: 읽을 바이트 수 (기본 4096, 최대 8192)
        """
        n = min(max(n_bytes, 128), 8192)
        sample = state.data[:n]
        text = sample.decode('utf-8', errors='replace')
        return text + f"\n[파일 전체 크기: {len(state.data)} bytes, 표시: {n} bytes]"

    @tool
    def run_parser(code: str) -> str:
        """작성한 parse() + serialize() 코드를 실행. AST 검증 후 현재 파일에 적용.

        Args:
            code: parse(raw)->list 와 serialize(data)->bytes 를 포함한 완전 Python 코드
        """
        state.last_code = code

        # AST 검증 (parse + serialize 둘 다 있는지 포함)
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
            return f"[AST_VALIDATION_FAILED] {reason}\n→ 금지된 import/호출 또는 parse/serialize 누락. 코드를 수정하세요."

        # 실행
        try:
            parsed, serialize_fn = execute_parser(code, state.data, return_serialize=True)
            state.last_result = parsed
            state.last_serialize_fn = serialize_fn
        except Exception as e:
            sig = error_signature(f"exec:{type(e).__name__}:{str(e)}")
            state.record_attempt(
                phase='exec', status='failed',
                error_type=type(e).__name__,
                error_message=str(e)[:300],
                signature=sig,
                code_preview=code[:400],
            )
            return f"[EXEC_ERROR] {type(e).__name__}: {str(e)[:300]}\n→ parse() 또는 serialize() 실행 중 예외 발생. 코드를 수정하세요."

        # 성공 — 결과 미리보기
        try:
            preview = json.dumps(parsed, ensure_ascii=False, default=str)
            if len(preview) > 2000:
                preview = preview[:2000] + f"\n... [전체 {len(preview)}자, 절삭]"
        except Exception:
            preview = str(parsed)[:2000]

        return (f"[EXEC_OK] 결과 미리보기 (앞 2000자):\n{preview}\n\n"
                f"→ 이제 반드시 inspect_result() 를 호출해 결과를 검토하세요.")

    @tool
    def inspect_result() -> str:
        """방금 실행한 파서의 결과를 "거울" 로 분석. 수상한 지표 리포트 반환.

        run_parser 직후 반드시 호출해야 함. 스킵하고 submit 하면 거부됨.
        """
        if state.last_result is None:
            return "[NO_RESULT] run_parser 를 먼저 호출해서 결과를 생성하세요."
        return inspect_result_fn(state.last_result)

    @tool
    def validate_result() -> str:
        """객관 검증 — Coverage ≥ 85% + Round-trip (serialize(parse(raw)) ≈ raw) 확인."""
        if state.last_result is None:
            return "[NO_RESULT] run_parser 를 먼저 호출하세요."

        cov = coverage(state.last_result, state.data)
        ok, reason = validate_result_fn(
            state.last_result, state.data, fmt='unknown',
            serialize_fn=state.last_serialize_fn, strict=True,
        )

        if not ok:
            sig = error_signature(f"validate:{reason}")
            state.record_attempt(
                phase='validate', status='failed',
                error_type=reason.split(':', 1)[0] if ':' in reason else reason,
                error_message=reason,
                signature=sig,
                code_preview=(state.last_code or '')[:400],
            )
            return (f"[VALIDATE_FAILED] coverage={cov:.3f}, reason={reason}\n"
                    f"→ Coverage 85% 미만이거나 Round-trip 실패. 파서/직렬화기 로직 점검 필요.")

        return f"[VALIDATE_OK] coverage={cov:.3f}, round-trip 통과. submit_parser 호출 가능."

    @tool
    def submit_parser(code: str, description: str = "") -> str:
        """최종 파서 코드를 제출. validate_result 통과 후에만 호출.

        Args:
            code: 최종 parse() + serialize() 코드
            description: 한 줄 설명 (예: 'INI-style with [section] headers')
        """
        # AST 재검증 (방어)
        ok, reason = validate_parser_code(code)
        if not ok:
            return f"[SUBMIT_REJECTED] AST validation failed: {reason}"

        # 실행 가능성 재확인
        try:
            execute_parser(code, state.data, return_serialize=True)
        except Exception as e:
            return f"[SUBMIT_REJECTED] Code fails on execute: {type(e).__name__}: {str(e)[:150]}"

        state.submitted_code = code
        state.submitted_description = description
        state.record_attempt(
            phase='submit', status='ok',
            code_preview=code[:400],
        )
        return "SUBMITTED"

    return [read_sample, run_parser, inspect_result, validate_result, submit_parser]


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

def generate_parser_with_llm(path: str, data: bytes,
                              fmt_hint: str = "") -> tuple[str | None, int]:
    """LLM 으로 parser 코드 생성 시도. 실패 시 error.log 기록.

    Returns:
        (parser_code: str | None, total_tokens: int)
        성공 시 parser_code, 실패 시 None
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
        return None, 0

    state = CodegenState(path=path, data=data)
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

    user_msg = (
        f"파일 경로: {path}\n"
        f"파일 크기: {len(data)} bytes\n"
        f"포맷 힌트: {fmt_hint or 'unknown'}\n"
        f"최대 시도: {MAX_ATTEMPTS}\n\n"
        "read_sample → 파서/직렬화기 작성 → run_parser → inspect_result(필수) → "
        "검토 → validate_result → submit_parser 순서로 진행하세요. "
        f"같은 에러 {REPEATED_ERROR_LIMIT}회 반복 시 즉시 포기하세요."
    )

    total_tokens = 0
    try:
        result = agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
        total_tokens = _extract_token_usage(result)
    except Exception as e:
        logger.warning("agent.invoke 실패: %s", e)
        state.termination_reason = f"agent_error: {type(e).__name__}: {str(e)[:100]}"
        write_error_log(path, len(data), state, fmt_hint)
        return None, total_tokens

    # 종료 사유 판정
    if state.submitted_code:
        state.termination_reason = "submitted"
        return state.submitted_code, total_tokens

    if state.is_repeated_error():
        state.termination_reason = "repeated_error"
    elif len(state.attempts) >= MAX_ATTEMPTS:
        state.termination_reason = "max_attempts_exceeded"
    else:
        state.termination_reason = "agent_stopped_without_submit"

    # 마지막 시도 코드가 AST + 실행 통과하면 그거라도 반환 (관대 모드)
    if state.last_code:
        ok, _ = validate_parser_code(state.last_code)
        if ok:
            try:
                execute_parser(state.last_code, data)
                # 실행 가능. 그러나 submit 없이 끝났으므로 실패로 기록
                write_error_log(path, len(data), state, fmt_hint)
                return state.last_code, total_tokens
            except Exception:
                pass

    # 완전 실패 — error.log 기록
    write_error_log(path, len(data), state, fmt_hint)
    return None, total_tokens


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
