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

from pipeline.models.analysis_context import AnalysisContext
from shared.config.settings import SETTINGS
from shared.files.cache import SourceFileCache
from shared.files.source_excerpt import number_text

def _children(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext],
) -> list[AnalysisContext]:
    """ctx 의 직속 자식 컨텍스트 — start_line 순 정렬 (없으면 빈 리스트)."""
    kids = [contexts[c] for c in ctx.extra.get("child_ids", ()) if c in contexts]
    return sorted(kids, key=lambda c: c.start_line)


def _raw_body(ctx: AnalysisContext, cache: SourceFileCache) -> str:
    """ctx 의 raw 본문 (file_path 의 start~end 라인)."""
    return cache.slice(ctx.file_path, ctx.start_line, ctx.end_line)


def compact_body(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext], cache: SourceFileCache,
) -> str:
    """부모 본문 — 자식 구간을 ``[범위] 자식 summary`` 로 치환 (분석 입력용)."""
    children = _children(ctx, contexts)
    if not children:
        return cache.excerpt(ctx.file_path, ctx.start_line, ctx.end_line).numbered_text

    def _ph(child: AnalysisContext) -> str:
        # 다른 필드·고정문구로 메우지 않는다. compact는 실제 child summary가 있을 때만
        # 성립하며, 누락은 상위 의미를 오염시키기 전에 명시적 실패로 표면화한다.
        text = (child.summary or "").strip()
        if not text:
            raise ValueError(
                f"cannot compact child without LLM summary: {child.id}"
            )
        label = child.extra.get("label", "")
        tag = f"{label}: " if label else ""
        return (
            f"[CONTEXT_SUMMARY child_range={child.start_line}-{child.end_line}] "
            f"{tag}{text}"
        )

    return _transform_numbered(ctx, children, _ph, cache)


def _transform_numbered(
    ctx: AnalysisContext, children: list[AnalysisContext], placeholder, cache: SourceFileCache,
) -> str:
    """Render a sparse range without losing absolute physical line coordinates."""
    lines = cache.lines(ctx.file_path)
    if not lines:
        return ""
    end = ctx.end_line if ctx.end_line > 0 else len(lines)
    width = len(str(max(end, ctx.start_line, 1)))
    out: list[str] = []
    line_no = max(ctx.start_line, 1)
    ci = 0
    while line_no <= end and line_no <= len(lines):
        while ci < len(children) and children[ci].end_line < line_no:
            ci += 1
        if ci < len(children) and children[ci].start_line <= line_no:
            child = children[ci]
            out.append(
                f"{child.start_line:>{width}}-{child.end_line} | {placeholder(child)}"
            )
            line_no = max(child.end_line + 1, line_no + 1)
            ci += 1
        else:
            out.append(f"{line_no:>{width}} | {lines[line_no - 1]}")
            line_no += 1
    return "\n".join(out)


def _strip_sql_comments(text: str) -> str:
    """SQL 주석 제거 — 골격 전용, 문자열 리터럴과 물리 줄 수는 보존.

    골격은 "자기 위치 파악용"이라 주석이 불필요한데, 죽은(주석처리) 코드·노트가 그대로
    실려 LLM 이 실동작으로 오인하는 환각의 공급로였다(실측). 결정론 제거로 원천 차단.
    분석 본문(compact/raw body)의 주석은 유지 — 메타정보(지침이 처리).
    """
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            in_str = c != "'"
            i += 1
        elif c == "'":
            in_str = True
            out.append(c)
            i += 1
        elif c == "-" and text[i + 1 : i + 2] == "-":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and text[i + 1 : i + 2] == "*":
            i += 2
            while i < n and text[i : i + 2] != "*/":
                if text[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
        else:
            out.append(c)
            i += 1
    return "\n".join(line.rstrip() for line in "".join(out).split("\n"))


def skeleton_body(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext], cache: SourceFileCache,
) -> str:
    """range 골격 — 자식 구간을 ``....`` 로 치환 + 주석 제거 (구조만, 자식 의미·주석 X)."""
    children = _children(ctx, contexts)
    if not children:
        return number_text(_strip_sql_comments(_raw_body(ctx, cache)), ctx.start_line)
    return _transform_numbered(
        ctx,
        children,
        lambda child: (
            f"[OMITTED_CHILD child_range={child.start_line}-{child.end_line}; "
            "not executable source in this excerpt]"
        ),
        cache,
    )


def body_for(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext], cache: SourceFileCache,
) -> str:
    """분석 입력 본문 — **원문이 예산에 들어가면 원문(1차 증거), 아니면 compact**.

    단일 원리(모든 분할 공통): 요약 치환은 손실 압축이라 예산 초과(거대 컨테이너)에서만
    쓴다. 중소 부모까지 원문 승격 → 값 집합·매핑·보정 로직이 전달 게임 없이 직달.
    """
    if ctx.extra.get("child_ids") and ctx.token_count > SETTINGS.analysis_pipeline.batch_max_tokens:
        return compact_body(ctx, contexts, cache)
    return cache.excerpt(ctx.file_path, ctx.start_line, ctx.end_line).numbered_text


def raw_body_for(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext], cache: SourceFileCache,
) -> str:
    """Exact source used only by deterministic lexing, grounding, and quality checks."""
    return _raw_body(ctx, cache)


def model_visible_raw_body(
    ctx: AnalysisContext,
    contexts: dict[str, AnalysisContext],
    cache: SourceFileCache,
) -> str:
    """Raw source lines that remain visible when ``body_for`` compacts children.

    Child summaries are contextual handoff, not direct source evidence for the
    parent.  Blank the omitted physical lines while preserving absolute offsets
    so candidate coordinates still point to the canonical source file.
    """
    raw = _raw_body(ctx, cache)
    if not (
        ctx.extra.get("child_ids")
        and ctx.token_count > SETTINGS.analysis_pipeline.batch_max_tokens
    ):
        return raw
    lines = raw.splitlines()
    for child in _children(ctx, contexts):
        start = max(child.start_line - ctx.start_line, 0)
        stop = min(child.end_line - ctx.start_line + 1, len(lines))
        for index in range(start, stop):
            lines[index] = ""
    return "\n".join(lines)


def ancestor_context(
    ctx: AnalysisContext, contexts: dict[str, AnalysisContext], cache: SourceFileCache,
) -> str:
    """조상 range 들의 골격(skeleton) chain — root → 직속 부모. 자기 위치 파악용.

    조상은 구조 좌표를 보존한 skeleton으로 전달한다. 자유형 의미 요약으로 대체하지 않는다.
    """
    chain: list[str] = []
    seen: set[str] = set()
    pid = ctx.extra.get("parent_id")
    while pid and pid in contexts and pid not in seen:
        seen.add(pid)
        actx = contexts[pid]
        skel = skeleton_body(actx, contexts, cache)
        if skel:
            chain.append(f"[{actx.id}]\n{skel}")
        pid = actx.extra.get("parent_id")
    if not chain:
        return "(없음)"
    chain.reverse()    # root 가 먼저
    return "\n\n".join(chain)
