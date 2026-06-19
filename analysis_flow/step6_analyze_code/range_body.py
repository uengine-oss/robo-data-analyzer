"""DBMS nested-range 본문 변환 — compact / skeleton / 조상 골격 context.

DBMS CodeNode 은 PROCEDURE 안에 IF/LOOP/DML 이 중첩된 트리(``parent_id``).
부모 range 를 LLM 에 줄 때 자식 raw 코드를 통째로 주면 토큰 폭증 + 자식 재분석이
되므로 — 자식 구간을 치환한다:

- ``compact_body``  : 자식 구간 → ``[범위] 자식 summary``  (분석 입력 — 자식 결과 합류)
- ``skeleton_body`` : 자식 구간 → ``....``               (조상 골격 — 자식이 자기 위치 파악)

자식→부모 단방향 흐름: 자식이 먼저 분석(Bottom-up Round)되어 ``ctx.summary`` 가 채워진
뒤 부모가 ``compact_body`` 로 자식 요약을 본다. 둘 다 결정론 — LLM 0회.

Framework 코드노드는 평면(자식 없음) — ``body_for`` 가 raw 본문 그대로 반환.

파일 읽기는 ``SourceFileCache`` 인스턴스 공유 (호출자가 step 단위로 생성·주입).
"""
from __future__ import annotations

from analysis_flow.step5_build_context.large_code_one_liner import estimate_tokens
from data_models.analysis_context import AnalysisContext
from shared.config.env_catalog import ENV
from shared.source_file_cache import SourceFileCache

_CHILD_MARK = "    ...."


def _children(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext],
) -> list[AnalysisContext]:
    """ctx 의 직속 자식 컨텍스트 — start_line 순 정렬 (없으면 빈 리스트)."""
    kids = [contexts[c] for c in ctx.extra.get("child_ids", ()) if c in contexts]
    return sorted(kids, key=lambda c: c.start_line)


def _raw_body(ctx: AnalysisContext, cache: SourceFileCache) -> str:
    """ctx 의 raw 본문 (file_path 의 start~end 라인)."""
    return cache.slice(ctx.file_path, ctx.start_line, ctx.end_line)


def _transform(
    ctx: AnalysisContext, children: list[AnalysisContext], placeholder, cache: SourceFileCache,
) -> str:
    """ctx 본문에서 각 자식 구간을 ``placeholder(child)`` 한 줄로 치환."""
    lines = cache.lines(ctx.file_path)
    if not lines:
        return ""
    end = ctx.end_line if ctx.end_line > 0 else len(lines)
    out: list[str] = []
    line_no = max(ctx.start_line, 1)
    ci = 0
    while line_no <= end and line_no <= len(lines):
        while ci < len(children) and children[ci].end_line < line_no:
            ci += 1
        if ci < len(children) and children[ci].start_line <= line_no:
            child = children[ci]
            out.append(placeholder(child))
            line_no = max(child.end_line + 1, line_no + 1)
            ci += 1
        else:
            out.append(lines[line_no - 1])
            line_no += 1
    return "\n".join(out)


def compact_body(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext], cache: SourceFileCache,
) -> str:
    """부모 본문 — 자식 구간을 ``[범위] 자식 summary`` 로 치환 (분석 입력용)."""
    children = _children(ctx, contexts)
    if not children:
        return _raw_body(ctx, cache)

    def _ph(child: AnalysisContext) -> str:
        text = (child.summary or child.one_liner or "(분석 전)").strip()
        return f"    [{child.start_line}~{child.end_line}] {text}"

    return _transform(ctx, children, _ph, cache)


def skeleton_body(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext], cache: SourceFileCache,
) -> str:
    """range 골격 — 자식 구간을 ``....`` 로 치환 (구조만, 자식 의미 X)."""
    children = _children(ctx, contexts)
    if not children:
        return _raw_body(ctx, cache)
    return _transform(ctx, children, lambda _child: _CHILD_MARK, cache)


def body_for(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext], cache: SourceFileCache,
) -> str:
    """분석 입력 본문 — 자식 있으면 compact, 없으면 raw."""
    if ctx.extra.get("child_ids"):
        return compact_body(ctx, contexts, cache)
    return _raw_body(ctx, cache)


def ancestor_context(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext], cache: SourceFileCache,
) -> str:
    """조상 range 들의 골격(skeleton) chain — root → 직속 부모. 자기 위치 파악용.

    조상 skeleton 이 토큰 임계 초과 시 그 조상의 ``one_liner`` 로 대체
    (one_liner 는 build_context_step 이 큰 코드노드에 대해 이미 산출 — 추가 LLM 0).
    """
    threshold = ENV.pipeline.big_context_threshold_tokens
    chain: list[str] = []
    seen: set[str] = set()
    pid = ctx.extra.get("parent_id")
    while pid and pid in contexts and pid not in seen:
        seen.add(pid)
        actx = contexts[pid]
        skel = skeleton_body(actx, contexts, cache)
        if skel and estimate_tokens(skel) > threshold and actx.one_liner:
            skel = actx.one_liner.strip()
        if skel:
            chain.append(f"[{actx.id}]\n{skel}")
        pid = actx.extra.get("parent_id")
    if not chain:
        return "(없음)"
    chain.reverse()    # root 가 먼저
    return "\n\n".join(chain)
