"""임베딩 재료 — 그래프에서 무엇을 읽어 어떤 문장으로 합칠지의 단일 진실.

검색 결과 생성과 검색측 코퍼스 로딩이 **같은 문장**을 만들어야 검색이 성립한다.
그래서 조회 Cypher 와 조립 함수를 한곳에 묶는다 — 사본이 갈리면 벡터와 질의가 다른 세상을
가리킨다.

의미 점수의 입력은 canonical ``summary`` 하나뿐이다. 이름·코드·관계가
점수에 섞이면 같은 사실을 여러 번 가중하거나 내부 식별자에 과적합하므로 사용하지 않는다.
"""
from __future__ import annotations

from graph.ownership import ANALYZER_GRAPH_OWNER

# 임베딩·검색 공용 식별자 (생성=build_search_segments_step / 조회=search/ranking/recall).
EMBEDDED_LABEL = "EMBEDDED"              # 코드노드 검색벡터 보유 마커 (labels[1:], 역할 아님)
EMBED_META_LABEL = "EMBED_META"          # 임베딩 지문 메타노드 (검색이 질의 임베더와 대조)
VECTOR_INDEX_NAME = "embedded_code_vec"  # (:EMBEDDED).embedding 벡터 인덱스

# 임베딩 대상 = canonical summary를 가진 코드노드. 컨테이너(FILE/MODULE/PACKAGE)는 로직이 아니라 제외.
FETCH_MATERIAL = f"""
MATCH (n)
WHERE n.graph_owner = '{ANALYZER_GRAPH_OWNER}'
  AND n.generation_id = $generation_id
  AND n.analysis_scope_id = $analysis_scope_id
  AND n.summary IS NOT NULL AND n.summary <> ''
  AND NOT n:FILE AND NOT n:MODULE AND NOT n:PACKAGE
RETURN n.id AS id,
       n.summary AS summary
ORDER BY id
"""

def compose_embed_text(row: dict) -> str:
    """코드노드 1개 → 의미검색 문장. 다른 필드로 대체하거나 보강하지 않는다."""
    return (row.get("summary") or "").strip()
