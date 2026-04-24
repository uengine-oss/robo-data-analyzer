"""LLM 응답 가드레일 공용 유틸.

`llm_prompt_handler` 와 agent 경로가 공유하는 작은 헬퍼 모음.
Harmony 등 모델별 특수 처리는 모델·경로별 위치에 두고, 여기는 언어·모델 무관 공통 로직만.

## 제공 기능

1. 오염 제거 (sanitize_*) — 제어문자·코드펜스·JSON 주석·잘못된 escape
2. 재시도 분류 (is_parse_failure / is_rate_limit / should_retry)
3. 재시도 피드백 (build_retry_feedback) — LLM 에 직전 오류를 알려 수정 유도

## 사용 예

```python
from util.llm_guardrails import sanitize_llm_output, should_retry, build_retry_feedback

for attempt in range(MAX_RETRIES + 1):
    prompt = base if attempt == 0 else base + build_retry_feedback(last_err, attempt)
    try:
        ai_msg = llm.invoke(prompt)
        clean = sanitize_llm_output(ai_msg)
        return json.loads(clean.content)
    except Exception as e:
        last_err = e
        if not should_retry(e): raise
```
"""

from __future__ import annotations

import re
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# Sanitize — LLM 응답의 흔한 오염 제거
# ═══════════════════════════════════════════════════════════════════════════════

# JSON spec 상 유효한 escape
_VALID_JSON_ESCAPE_CHARS = frozenset('"\\/bfnrt')

# 제어 문자 (\x00~\x1f 중 tab/newline 제외)
_CONTROL_CHAR_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

# OpenAI Harmony / ChatML 특수 토큰 — 서버 변환 실패 시 content 에 raw leak 되는 경우 방어
_CONTROL_TOKEN_RE = re.compile(r'<\|[a-zA-Z_]+\|>')

# JSON 주석 — 문자열 리터럴 내부 `//` 는 보존 (alternation 으로 string literal 먼저 매칭)
_JSON_LINE_COMMENT_RE = re.compile(r'("(?:[^"\\]|\\.)*")|//[^\n]*')
_JSON_BLOCK_COMMENT_RE = re.compile(r'("(?:[^"\\]|\\.)*")|/\*[\s\S]*?\*/')


def fix_invalid_json_escapes(s: str) -> str:
    """JSON 에서 허용되지 않는 escape 를 수리 (예: `\\x` → `x`).

    LLM 이 가끔 `\\q` 같은 유효하지 않은 escape 를 내보내 파서가 튕기는 걸 방지.
    """
    if not s or '\\' not in s:
        return s
    result = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in _VALID_JSON_ESCAPE_CHARS:
                result.append(s[i:i + 2])
                i += 2
            elif nxt == 'u' and i + 5 < len(s):
                result.append(s[i:i + 6])
                i += 6
            else:
                result.append(nxt)
                i += 2
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)


def strip_code_fences(s: str) -> str:
    """코드펜스(```json ... ```) 와 내부 백틱 제거."""
    stripped = s.strip()
    if stripped.startswith("```"):
        nl = stripped.find("\n")
        if nl != -1:
            stripped = stripped[nl + 1:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    return stripped.replace("`", "")


def strip_control_tokens(s: str) -> str:
    """Harmony/ChatML 제어 토큰(`<|start|>`, `<|channel|>` 등) 제거."""
    return _CONTROL_TOKEN_RE.sub('', s)


def strip_json_comments(s: str) -> str:
    """JSON 주석(`//`, `/* */`) 제거. 문자열 리터럴 내부 `//` 는 보존."""
    s = _JSON_LINE_COMMENT_RE.sub(lambda m: m.group(1) if m.group(1) else '', s)
    s = _JSON_BLOCK_COMMENT_RE.sub(lambda m: m.group(1) if m.group(1) else '', s)
    return s


def sanitize_llm_output(text):
    """LLM 응답 공용 sanitize. AIMessage 객체 / 순수 str 모두 지원.

    순서: 제어문자 → 제어토큰(Harmony) → 코드펜스 → JSON 주석 → escape 수리.
    """
    if hasattr(text, 'content'):
        if isinstance(text.content, list):
            text.content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in text.content
            )
        text.content = _clean(text.content)
        return text
    if isinstance(text, str):
        return _clean(text)
    return text


def _clean(s: str) -> str:
    s = _CONTROL_CHAR_RE.sub('', s)
    s = strip_control_tokens(s)
    s = strip_code_fences(s)
    s = strip_json_comments(s)
    return fix_invalid_json_escapes(s)


# ═══════════════════════════════════════════════════════════════════════════════
# 재시도 분류 — 어떤 예외가 재시도 대상인지
# ═══════════════════════════════════════════════════════════════════════════════


def is_parse_failure(error: Exception) -> bool:
    """JSON 파싱 실패인지 판정."""
    err = str(error)
    return (
        "Invalid json output" in err
        or "OUTPUT_PARSING_FAILURE" in err
        or "OutputParserException" in err
    )


def is_rate_limit(error: Exception) -> bool:
    """429 / rate / quota 계열 판정."""
    err = str(error).lower()
    return "429" in err or "rate" in err or "quota" in err


def is_bad_request_parse(error: Exception) -> bool:
    """400 Bad Request + parse 관련 힌트 판정 (서버측 입력 파싱 실패)."""
    err = str(error).lower()
    return "400" in err and "parse" in err


def should_retry(error: Exception) -> bool:
    """이 예외가 재시도 대상인지 공용 판정."""
    return is_rate_limit(error) or is_bad_request_parse(error) or is_parse_failure(error)


# ═══════════════════════════════════════════════════════════════════════════════
# Harmony tool call 카운트 (관찰성 전용 · 실패 판정에 사용하지 말 것)
# ═══════════════════════════════════════════════════════════════════════════════

# Harmony 응답에서 tool 호출 표지자 — channel 이 commentary/analysis 어느 쪽이든 "to=functions.xxx" 는
# 모델이 도구 호출을 의도한다는 확정적 신호.
_HARMONY_TOOL_CALL_RE = re.compile(r'to=functions\.[A-Za-z_]\w*')


def count_tool_calls_from_harmony(text: str) -> int:
    """Harmony raw 텍스트에서 tool 호출 횟수 추출 — 로깅/관찰성 전용.

    서버가 Harmony → OpenAI JSON 변환에 실패해 content 에 raw 가 leak 된 경우에도
    실제 도구 호출 횟수를 확인하기 위한 보조 카운터.
    **실패 판정에 쓰지 말 것** (0 이 나와도 서버 변환 성공 후 standard tool_calls 필드에
    정상 기록된 경우가 있음).
    """
    if not text:
        return 0
    return len(_HARMONY_TOOL_CALL_RE.findall(text))


# ═══════════════════════════════════════════════════════════════════════════════
# 재시도 피드백 — 이전 오류를 LLM 에게 알려 수정 유도
# ═══════════════════════════════════════════════════════════════════════════════


def build_retry_feedback(
    error: Exception,
    attempt: int,
    *,
    max_err_len: int = 400,
    response_tail: str = "",
    tail_len: int = 200,
) -> str:
    """재시도용 피드백 블록. 기존 프롬프트 끝에 덧붙여 재호출하면 LLM 이 오류를 인식해 수정.

    response_tail 에 실패한 응답의 마지막 N 자를 넘기면 "어디서 깨졌는지" 까지 LLM 에 보여줘
    수정 정확도가 올라간다.
    """
    err_str = str(error)[:max_err_len]
    tail_block = ""
    if response_tail:
        clipped = response_tail[-tail_len:]
        tail_block = (
            f"\n응답이 이 지점에서 깨졌습니다 (마지막 {len(clipped)}자):\n"
            f"...{clipped}\n"
        )
    return (
        "---\n"
        f"⚠️ 이전 시도 {attempt} 의 응답이 파싱에 실패했습니다. 원인:\n"
        f"  {err_str}"
        f"{tail_block}\n"
        "다음 규칙을 반드시 지켜 **처음부터 다시** 응답해주세요:\n"
        "- 순수 JSON 객체만 출력. 앞뒤 설명·코드펜스·부가 텍스트 금지.\n"
        "- `//` 또는 `/* */` 주석 금지. 불확실한 값은 생략하거나 description 필드에 자연어로.\n"
        "- 트레일링 콤마 금지.\n"
        "- 특수 제어 토큰(`<|start|>`, `<|channel|>`, `<|message|>`, `<|call|>`, `<|return|>`, `<|end|>`) 금지.\n"
        "- 문자열 내부 double quote 는 `\\\"`, backslash 는 `\\\\` 로 escape.\n"
    )
