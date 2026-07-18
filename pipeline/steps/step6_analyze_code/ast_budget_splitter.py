"""결정론 AST 예산 분할기 (spec 054 US3 / FR-019·FR-020) — LLM 미사용.

품질검사(rule_quality)가 이상으로 판정한 함수를, 제어문 서브트리를 따라 예산 크기의
조각으로 자른다. **분할 계획에 LLM 을 쓰지 않는다** — AST 라인 범위 + 문자 길이 근사 예산만.

규칙:
- 함수 전체가 예산 안이면 분할하지 않는다(빈 리스트 → 폴백은 전체 재분석 1회로 수렴).
- 상위 제어문 서브트리가 예산 안이면 통째로 한 조각(if 조건+몸통 불가분 = 연속 라인 범위).
- 초과할 때만 그 서브트리의 **한 단계 아래 자식**으로 재귀한다.
- 배타적 then/else 는 한 IF 서브트리의 연속 원문(else 키워드 포함)이라 순차 조각으로 합쳐지지 않는다.
- 각 조각에 조상 제어문 헤더(조건 경로)를 함께 실어 문맥 손실을 막는다(FR-021).
"""
from __future__ import annotations

from dataclasses import dataclass

from pipeline.models.analysis_context import ControlSubtreeNode

# 문자 길이 → 근사 토큰 (research.md §2: 코드 평균 ~3.5 char/token). 외부 tokenizer 의존 없음.
_CHARS_PER_TOKEN = 3.5


def approx_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


# Historical public name retained inside the active step6 module; the transport
# model itself belongs to the shared analysis-context boundary.
SubtreeNode = ControlSubtreeNode


@dataclass
class CodeFragment:
    """폴백 분할 산출 1개 — 조각 분석 LLM 입력."""
    fragment_id: str
    start_line: int
    end_line: int
    ancestor_path: list[str]   # 이 조각을 감싸는 제어문 헤더(조건 경로)
    text: str


def _slice(code_lines: list[str], start: int, end: int) -> str:
    """1-based [start, end] 줄 슬라이스 (범위 밖은 안전 클램프)."""
    if start < 1:
        start = 1
    lo, hi = start - 1, min(end, len(code_lines))
    return "\n".join(code_lines[lo:hi])


def _header(code_lines: list[str], node: SubtreeNode) -> str:
    """조상 경로 표기용 — 제어문 첫 줄(조건)을 요약으로 쓴다."""
    first = _slice(code_lines, node.start, node.start).strip()
    return f"{node.ntype}@{node.start}: {first}" if first else f"{node.ntype}@{node.start}"


def split(
    func_start: int,
    func_end: int,
    top_children: list[SubtreeNode],
    code_lines: list[str],
    budget_tokens: int,
    func_id: str = "fn",
) -> list[CodeFragment]:
    """함수 → 조각 목록. 예산 안이면 ``[]``(분할 불필요)."""
    whole = _slice(code_lines, func_start, func_end)
    if approx_tokens(whole) <= budget_tokens or not top_children:
        return []
    frags: list[CodeFragment] = []
    _pack(top_children, code_lines, budget_tokens, ancestor=[], func_id=func_id, out=frags)
    return frags


def _pack(
    children: list[SubtreeNode],
    code_lines: list[str],
    budget: int,
    ancestor: list[str],
    func_id: str,
    out: list[CodeFragment],
) -> None:
    """형제 제어문들을 라인 순으로 예산까지 묶어 조각화. 단독 초과는 재귀."""
    ordered = sorted(children, key=lambda n: n.start)
    buf: list[SubtreeNode] = []

    def flush() -> None:
        if not buf:
            return
        s, e = buf[0].start, max(n.end for n in buf)
        out.append(CodeFragment(
            fragment_id=f"{func_id}#frag{len(out) + 1}",
            start_line=s, end_line=e,
            ancestor_path=list(ancestor),
            text=_slice(code_lines, s, e),
        ))
        buf.clear()

    for node in ordered:
        node_tokens = approx_tokens(_slice(code_lines, node.start, node.end))
        if node_tokens > budget and node.children:
            # 이 서브트리 단독으로 예산 초과 → 지금까지 buffer flush 후 한 단계 아래로 재귀.
            flush()
            _pack(node.children, code_lines, budget,
                  ancestor + [_header(code_lines, node)], func_id, out)
            continue
        # 예산 안: buffer 에 누적하되, 누적이 예산 넘으면 먼저 flush.
        cur = approx_tokens(_slice(code_lines, buf[0].start, node.end)) if buf else node_tokens
        if buf and cur > budget:
            flush()
        buf.append(node)
    flush()
