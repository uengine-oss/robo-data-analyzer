"""완료된 Analyzer generation의 사람 제공 용어를 읽는다.

분석 시 검증된 각 ``GlossaryTerm`` 제품을 그대로 복원하며 검색 시점에
추론·병합·재생성하지 않는다. 입력이 없으면 빈 용어집을 반환한다.
"""
from __future__ import annotations

# (uri, db, analysis_scope_id, generation) -> glossary dict
_cache: dict[tuple[str, str, str, str], dict] = {}

_GLOSSARY_TERMS_QUERY = """
MATCH (term:GlossaryTerm {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})
RETURN term.canonical_term AS canonical,
       term.aliases AS colloquial,
       term.meaning AS meaning,
       term.category AS category
ORDER BY term.stable_id
"""


def build(analysis_scope_id: str | None = None) -> dict:
    """요청 범위의 완료 generation에서 용어 제품 전체를 한 번 읽는다."""
    from search.corpus.neo4j_reader import read_driver, resolve_target
    from search.corpus.generation import (
        CompletedGenerationUnavailable,
        completed_generation_session,
    )

    uri = resolve_target()[0]
    drv, db = read_driver()
    glo = {"terms": [], "actions": [], "meta": {"db": db, "source": "none"}}
    try:
        with completed_generation_session(
            drv, db, analysis_scope_id=analysis_scope_id,
        ) as s:
            key = (uri, db, s.analysis_scope_id, s.generation_id)
            if key in _cache:
                return _cache[key]
            rows = s.run(
                _GLOSSARY_TERMS_QUERY,
                analysis_scope_id=s.analysis_scope_id,
                generation_id=s.generation_id,
            ).data()
        if rows:
            glo = {"terms": [dict(row) for row in rows], "actions": []}
            glo["meta"] = {"db": db, "source": "graph"}
    except CompletedGenerationUnavailable:
        key = (uri, db, analysis_scope_id or "unresolved", "unpublished")
    finally:
        drv.close()
    _cache[key] = glo
    return glo


def render_for_prompt(glo: dict) -> str:
    """검증된 용어를 누락·축약·중복 없이 재작성 문맥으로 렌더한다."""
    lines = ["[도메인 용어집 — 현장말투→표준어]"]
    for t in glo.get("terms", []):
        col = ", ".join(t.get("colloquial", []))
        m = t.get("meaning", "")
        lines.append(f"- 표준어「{t['canonical']}」← 현장:{col}" + (f"  · {m}" if m else ""))
    return "\n".join(lines)
