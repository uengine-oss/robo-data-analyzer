"""임베딩 재료 — 그래프에서 무엇을 읽어 어떤 문장으로 합칠지의 단일 진실.

임베딩 생성(embed_step)과 검색측 코퍼스 로딩이 **같은 문장**을 만들어야 검색이 성립한다.
그래서 조회 Cypher 와 조립 함수를 한곳에 묶는다 — 사본이 갈리면 벡터와 질의가 다른 세상을
가리킨다.

문장 형식은 라벨드 템플릿(``필드:값 | 필드:값``)이다. 요약만이 아니라 종류·역할·테이블 관계까지
머금어 그래프 구조를 벡터에 녹인다.
"""
from __future__ import annotations

# 임베딩·검색 공용 식별자 (생성=embed_step / 조회=search/recall — 단일 진실).
EMBEDDED_LABEL = "EMBEDDED"              # 코드노드 검색벡터 보유 마커 (labels[1:], 역할 아님)
EMBED_META_LABEL = "EMBED_META"          # 임베딩 지문 메타노드 (검색이 질의 임베더와 대조)
VECTOR_INDEX_NAME = "embedded_code_vec"  # (:EMBEDDED).embedding 벡터 인덱스

# 임베딩 대상 = 요약을 가진 코드노드. 컨테이너(FILE/MODULE/PACKAGE)는 로직이 아니라 제외.
# 테이블 관계는 노드 속성이 아니라 엣지라 여기서 함께 걷어 온다.
FETCH_MATERIAL = """
MATCH (n)
WHERE n.summary IS NOT NULL AND n.summary <> ''
  AND NOT n:FILE AND NOT n:MODULE AND NOT n:PACKAGE
OPTIONAL MATCH (n)-[rw:READS|WRITES|AFFECTS_TABLE]->(t:TABLE)
WITH n, collect(DISTINCT type(rw) + ':' + coalesce(t.name, '')) AS rels
RETURN n.id AS id,
       coalesce(n.type, head(labels(n))) AS label,
       coalesce(n.name, '') AS name,
       coalesce(n.stereotype, '') AS stereotype,
       n.summary AS summary,
       coalesce(n.search_text, '') AS search_text,
       [r IN rels WHERE NOT r ENDS WITH ':'] AS rels
ORDER BY id
"""

_READ_RELS = ("READS:", "AFFECTS_TABLE:")


def _table_names(rels: list[str], *, prefix: str | tuple[str, ...]) -> str:
    """관계 태그(``WRITES:주문``)에서 테이블명만 뽑아 정렬·중복제거해 이어붙인다."""
    names = {r.split(":", 1)[1] for r in rels if r.startswith(prefix)}
    return ",".join(sorted(n for n in names if n))


def compose_embed_text(row: dict) -> str:
    """코드노드 1개 → 임베딩할 문장. 비어 있는 필드는 아예 넣지 않는다(잡음 방지)."""
    parts = [f"종류:{row.get('label', '')}"]
    if stereotype := (row.get("stereotype") or "").strip():
        parts.append(f"역할:{stereotype}")
    if name := (row.get("name") or "").strip():
        parts.append(f"이름:{name}")

    rels = row.get("rels") or []
    if writes := _table_names(rels, prefix="WRITES:"):
        parts.append(f"쓰는테이블:{writes}")
    if reads := _table_names(rels, prefix=_READ_RELS):
        parts.append(f"읽는테이블:{reads}")

    if summary := (row.get("summary") or "").strip():
        parts.append(f"요약:{summary}")
    if search_text := (row.get("search_text") or "").strip():
        parts.append(f"검색:{search_text}")
    return " | ".join(parts)
