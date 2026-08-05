"""요청 데이터베이스의 원천 용어사전을 로드한다.

분석기가 사람 제공 입력을 검증·저장한 ``:GLOSSARY`` 노드를 **읽기만** 한다
(검색 시점 재생성 없음 = 별칭·뜻 복권 소멸, 결정론). 질의 번역 프롬프트(rewrite)에 주입.
DB(코퍼스)별 자산 → (uri, db) 키 캐시. 없으면 빈 사전(rewrite 는 무보강으로 degrade).
"""
from __future__ import annotations

import json

from graph.ownership import ANALYZER_GRAPH_OWNER

# (uri, db) -> glossary dict
_cache: dict[tuple[str, str], dict] = {}


def build() -> dict:
    """요청 DB 의 용어사전(캐시). ``:GLOSSARY`` 노드 없으면 빈 사전."""
    from search.corpus.neo4j_reader import read_driver, resolve_target

    uri = resolve_target()[0]
    drv, db = read_driver()
    key = (uri, db)
    if key in _cache:
        return _cache[key]
    glo = {"terms": [], "actions": [], "meta": {"db": db, "source": "none"}}
    try:
        with drv.session(database=db) as s:
            row = s.run(
                'MATCH (g:GLOSSARY {id:"glossary", graph_owner:$graph_owner}) '
                "RETURN g.terms_json AS tj",
                graph_owner=ANALYZER_GRAPH_OWNER,
            ).single()
        if row and row["tj"]:
            glo = json.loads(row["tj"])
            glo["meta"] = {"db": db, "source": "graph"}
    finally:
        drv.close()
    _cache[key] = glo
    return glo


def render_for_prompt(glo: dict, max_terms: int = 80) -> str:
    """용어집을 재작성 프롬프트에 인라인할 텍스트로 렌더."""
    lines = ["[도메인 용어집 — 현장말투→표준어]"]
    for t in glo.get("terms", [])[:max_terms]:
        col = ", ".join(t.get("colloquial", [])[:6])
        m = t.get("meaning", "")
        lines.append(f"- 표준어「{t['canonical']}」← 현장:{col}" + (f"  · {m}" if m else ""))
    lines.append("\n[동작 매핑 — 현장말투→DML(읽기/쓰기)]")
    for a in glo.get("actions", []):
        fl = ", ".join(a.get("field", [])[:8])
        lines.append(f"- {a['canonical']}({a.get('rw', '')}) ← 현장:{fl}")
    return "\n".join(lines)
