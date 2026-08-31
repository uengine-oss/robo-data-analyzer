"""용어집과 약어 근거로 검색 질의를 보강한다.

약어 병기(expand_abbrevs, 결정론) + 근거제약 번역(rewrite_for_cluster, LLM 연결판단만) — 둘 다
원질의를 대체하지 않고 병합 재료를 만든다. 치환은 용어집 실재 canonical 만 허용한다.
LLM = search.query.query_llm(ROBO_SEARCH_LLM_CONFIG, 기본 Qwen/GPU). 문자열 조립은 코드가 결정론으로.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from pydantic import ConfigDict, Field

from llm.structured_schema import CompactLlmResponse
from search.query.glossary import build as build_glossary, render_for_prompt
from search.query import query_llm
from search.ranking.recall import tokenize

# 의미 대조 임계(spec 008) — 0 = off(글자 겹침만). >0 이면 용어카드 임베딩 대조(사내 임베더).
GLOSSARY_SEM_MIN = float(os.environ.get("ROBO_GLOSSARY_SEM_MIN", "0"))
_ccache: dict[tuple[str, str], "QueryRewriteResult"] = {}
_ABBREV = re.compile(r"^[A-Za-z][A-Za-z0-9&-]+$")


class GlossarySubstitution(CompactLlmResponse):
    """사용자 질의의 한 표현을 용어집의 정확한 표준어에 연결한다."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_text: str = Field(description="사용자 질의에 실제로 존재하는 원문 일부")
    canonical_term: str = Field(description="용어집에 실제로 존재하는 표준어 하나")


class GlossaryRewriteResponse(CompactLlmResponse):
    """모델이 결정하는 유일한 값: 근거가 있는 용어 치환 목록."""

    model_config = ConfigDict(extra="forbid", strict=True)

    substitutions: list[GlossarySubstitution]


@dataclass(frozen=True)
class QueryRewriteResult:
    """검증된 치환을 원문 질의에 결정론적으로 병기한 결과."""

    rewritten_query: str
    glossary_substitutions: tuple[GlossarySubstitution, ...]


def _semantic_scores(q: str, terms: list[dict]) -> list[float]:
    """질의↔용어카드(표준어+별칭+뜻) 코사인 — 사내 임베더(질의·노드와 동일 모델)."""
    import numpy as np

    from llm import embedder
    cards = [" ".join([t.get("canonical", ""), *(t.get("colloquial") or []), t.get("meaning", "")])
             for t in terms]
    vecs = np.array(embedder.encode_texts(cards), dtype=np.float32)
    qv = np.array(embedder.encode_texts([q])[0], dtype=np.float32)
    return (vecs @ qv).tolist()


def _shortlist(
    q: str,
    *,
    analysis_scope_id: str | None = None,
) -> list[dict]:
    """글자 겹침 또는 명시된 의미 기준을 만족하는 용어를 모두 반환한다."""
    glo = build_glossary(analysis_scope_id)
    terms = glo.get("terms", [])
    qt = set(tokenize(q))
    lex = []
    for i, t in enumerate(terms):
        text = " ".join([t.get("canonical", "")] + (t.get("colloquial") or []) + [t.get("meaning", "")])
        ov = len(qt & set(tokenize(text)))
        if ov:
            lex.append((ov, i))
    lex.sort(key=lambda x: -x[0])
    picked = [i for _, i in lex]
    if GLOSSARY_SEM_MIN > 0 and terms:
        sims = _semantic_scores(q, terms)
        sem = [(s, i) for i, s in enumerate(sims) if s >= GLOSSARY_SEM_MIN and i not in set(picked)]
        sem.sort(key=lambda x: -x[0])
        picked += [i for _, i in sem]
    return [terms[i] for i in picked]


def _cluster_system(analysis_scope_id: str | None = None) -> str:
    glo_text = render_for_prompt(build_glossary(analysis_scope_id))
    return f"""너는 레거시 코드 검색의 **질의 번역기**다. 사용자의 현장 말투 질의를,
코드그래프 요약문이 쓰는 표준어로 번역한다. 도메인은 아래 용어집에서 스스로 파악하라.

[왜] 질의와 코드 요약문의 어휘가 다르면 검색이 실패한다. 용어집 근거로 현장말투를
표준어로 치환해 요약문 어휘에 근접시키는 것이 네 일이다.

{glo_text}

[규칙]
1) 치환·병기의 대상은 **반드시 위 용어집에 실재하는 canonical** 이어야 한다(새 단어 창작 금지).
   단 연결 판단에는 네 세상지식을 써라 — 질의의 일상어가 별칭 목록에 없어도, canonical 의
   실체(시스템·망·프로토콜·테이블)가 질의 개념과 같은 것을 가리키면 병기로 제안하라.
   용어집에 그 실체 자체가 없으면 원문 그대로 둔다.
2) **영문 약어·시스템명 canonical**(대문자·하이픈 표기)은 검색 변별력이 가장 크다 —
   후보에 있으면 반드시 채택해 원 표현과 병기하라(예: "배송망(CJ-EXP)"). 일반 개념어는
   약어가 없을 때만. ★질의어가 범주(망·시스템 종류·수단)를 가리키고 그 범주에 속하는
   canonical 이 여럿이면 하나만 고르지 말고 전부 병기하라(예: "배송망(CJ-EXP·HJ-LOG)").
3) 사용자가 준 구체 엔터티(고유명사·숫자·영문 식별자·기간)는 **그대로 보존**한다.
4) 질의의 의도(조회/변경/흐름 질문)를 바꾸지 말고, 사용자가 말하지 않은 개념을 추가하지 마라.
5) 바꿀 근거가 없으면 substitutions를 빈 배열로 반환한다.
6) source_text는 질의에 실제로 있는 문자열을 그대로 복사한다.
7) canonical_term은 위 용어집에 적힌 표준어 하나를 그대로 복사한다. 여러 표준어가
   필요하면 같은 source_text의 항목을 각각 만든다.
8) 의도·설명·재작성 문장은 출력하지 않는다. 최종 질의 조립은 코드가 수행한다."""


def _harvest_subs(
    q: str,
    out: GlossaryRewriteResponse,
    analysis_scope_id: str | None = None,
) -> tuple[GlossarySubstitution, ...]:
    """모든 모델 치환을 용어집·원질의와 대조하고 안정된 순서로 반환한다."""
    glo = build_glossary(analysis_scope_id)
    canonical_order = {
        canonical.lower(): index
        for index, term in enumerate(glo.get("terms", []))
        if (canonical := (term.get("canonical") or "").strip())
    }
    unique: dict[tuple[str, str], GlossarySubstitution] = {}
    for substitution in out.substitutions:
        source_text = substitution.source_text.strip()
        canonical_term = substitution.canonical_term.strip()
        if not source_text or source_text not in q:
            raise ValueError(
                f"Search rewrite source_text is not an exact query substring: {source_text!r}"
            )
        canonical_key = canonical_term.lower()
        if canonical_key not in canonical_order:
            raise ValueError(
                f"Search rewrite canonical_term is absent from the glossary: {canonical_term!r}"
            )
        if source_text == canonical_term or canonical_term in q:
            raise ValueError(
                "Search rewrite substitution does not add a new glossary term: "
                f"{source_text!r} -> {canonical_term!r}"
            )
        normalized = GlossarySubstitution(
            source_text=source_text,
            canonical_term=canonical_term,
        )
        unique[(source_text, canonical_key)] = normalized
    return tuple(sorted(
        unique.values(),
        key=lambda item: (
            q.index(item.source_text),
            -len(item.source_text),
            canonical_order[item.canonical_term.lower()],
        ),
    ))


def _apply_subs(q: str, subs: tuple[GlossarySubstitution, ...]) -> str:
    """결정론 병기 조립 — 원문 보존하고 frm(to1·to2…) 형태로 삽입(범주어는 전부 병기)."""
    by: dict[str, list[str]] = {}
    for substitution in subs:
        tos = by.setdefault(substitution.source_text, [])
        if substitution.canonical_term not in tos:
            tos.append(substitution.canonical_term)
    rew = q
    for frm, tos in by.items():
        rew = rew.replace(frm, f"{frm}({'·'.join(tos)})", 1)
    return rew


def rewrite_for_cluster(
    q: str,
    *,
    analysis_scope_id: str | None = None,
) -> QueryRewriteResult:
    """질의 하나당 LLM을 한 번 호출하고 검증된 치환만 코드로 조립한다."""
    system = _cluster_system(analysis_scope_id)
    k = (system, q)
    if k in _ccache:
        return _ccache[k]
    response = query_llm.invoke_structured(
        system_prompt=system,
        user_prompt=q,
        response_model=GlossaryRewriteResponse,
    )
    substitutions = _harvest_subs(q, response, analysis_scope_id)
    res = QueryRewriteResult(
        rewritten_query=(
            _apply_subs(q, substitutions) if substitutions else q
        ),
        glossary_substitutions=substitutions,
    )
    _ccache[k] = res
    return res


def expand_abbrevs(q: str, *, analysis_scope_id: str | None = None) -> dict:
    """결정론 질의 보강 — 질의와 겹치는 용어집의 영문 약어/시스템명 canonical 병기. LLM 없음."""
    hits = [t["canonical"] for t in _shortlist(
        q, analysis_scope_id=analysis_scope_id,
    )
            if _ABBREV.match((t.get("canonical") or "").strip())]
    if not hits:
        return {"expanded": q, "terms": []}
    return {"expanded": f"{q} (관련 표준어: {', '.join(hits)})", "terms": hits}
