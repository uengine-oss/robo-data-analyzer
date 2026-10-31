"""임베딩 재료 — 그래프에서 무엇을 읽어 어떤 문장으로 합칠지의 단일 진실.

임베딩 생성(embed_step)과 검색측 코퍼스 로딩이 **같은 문장**을 만들어야 검색이 성립한다.
그래서 조회 Cypher 와 조립 함수를 한곳에 묶는다 — 사본이 갈리면 벡터와 질의가 다른 세상을
가리킨다.

문장 형식은 라벨드 템플릿(``필드:값 | 필드:값``)이다. 요약만이 아니라 종류·역할·테이블 관계까지
머금어 그래프 구조를 벡터에 녹인다.
"""
from __future__ import annotations

import json

from graph.ownership import ANALYZER_GRAPH_OWNER

# 임베딩·검색 공용 식별자 (생성=embed_step / 조회=search/ranking/recall — 단일 진실).
#
# 검색 벡터 보유 표지는 **속성**이다(예전 ``:EMBEDDED`` 라벨). Ontological DB 는 만들어진 노드에
# 라벨을 더할 수 없고(라벨=만들 때 고정되는 타입), 임베딩은 분석 맨 끝에 붙는다. 벡터 인덱스는
# 라벨 하나가 필요하므로 전 노드 공통 ``:ANALYZER_OWNED`` 에 걸되, 코드노드 검색 벡터만 따로
# 이름 붙인 속성(``search_embedding``)에 둬 Table/Column 의 ``embedding``(설명 벡터 — Catalog 도
# 같은 이름으로 쓴다)이 검색 인덱스에 섞이지 않게 한다. 인덱스 이름도 바꿨다 — 옛
# ``embedded_code_vec``((:EMBEDDED).embedding)가 남은 Neo4j 에서 ``IF NOT EXISTS`` 가 옛 정의를
# 그대로 둘 수 있기 때문이다.
OWNED_LABEL = "ANALYZER_OWNED"               # 전 analyzer 노드 공통 부모 라벨
EMBEDDED_PROPERTY = "embedded"               # 코드노드 검색벡터 보유 표지 (true)
SEARCH_VECTOR_PROPERTY = "search_embedding"  # 코드노드 검색 벡터 (Table/Column 은 embedding)
EMBED_META_LABEL = "EMBED_META"              # 임베딩 지문 메타노드 (검색이 질의 임베더와 대조)
VECTOR_INDEX_NAME = "analyzer_search_vec"    # (:ANALYZER_OWNED).search_embedding 벡터 인덱스

# 노드 종류 = ``type`` 속성, 없으면 공통 부모를 뺀 첫 라벨. Ontological DB 의 ``labels(n)`` 은
# 상속 사슬 순서(부모→자식)라 ``head(labels(n))`` 가 늘 ANALYZER_OWNED 다.
NODE_KIND_EXPR = f"coalesce(n.type, head([l IN labels(n) WHERE l <> '{OWNED_LABEL}']))"


def as_vector(value) -> list[float]:
    """저장된 벡터 → float 목록. Ontological DB 는 벡터 색인 속성을 ``"[0.1,…]"`` 문자열로 돌려준다."""
    if isinstance(value, str):
        value = json.loads(value)
    return [float(x) for x in value]


# 임베딩 대상 = 요약을 가진 코드노드. 컨테이너(FILE/MODULE/PACKAGE)는 로직이 아니라 제외.
# 테이블 관계는 노드 속성이 아니라 엣지라 여기서 함께 걷어 온다 — 관계 1개당 1행으로 받고 노드별
# 묶음은 ``group_material`` 이 한다. Ontological DB 는 ``WITH n, collect(DISTINCT type(rw) + ':' …)``
# 를 ``operator does not exist: jsonb = text`` 로 거절한다(실측).
FETCH_MATERIAL = f"""
MATCH (n)
WHERE n.graph_owner = '{ANALYZER_GRAPH_OWNER}'
  AND n.summary IS NOT NULL AND n.summary <> ''
  AND NOT n:FILE AND NOT n:MODULE AND NOT n:PACKAGE
OPTIONAL MATCH (n)-[rw:READS|WRITES|AFFECTS_TABLE]->(t:TABLE {{graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
RETURN n.id AS id,
       {NODE_KIND_EXPR} AS label,
       coalesce(n.name, '') AS name,
       coalesce(n.stereotype, '') AS stereotype,
       n.summary AS summary,
       coalesce(n.search_text, '') AS search_text,
       type(rw) AS rel_type,
       t.name AS table_name
ORDER BY id
"""


def group_material(rows: list[dict]) -> list[dict]:
    """``FETCH_MATERIAL`` 행(노드×관계) → 노드당 1행 + ``rels`` (``"WRITES:주문"`` 태그, 정렬·중복 제거)."""
    grouped: dict[str, dict] = {}
    for row in rows:
        node = grouped.get(row["id"])
        if node is None:
            node = {key: value for key, value in row.items() if key not in ("rel_type", "table_name")}
            node["rels"] = []
            grouped[row["id"]] = node
        if row.get("rel_type") and row.get("table_name"):
            tag = f"{row['rel_type']}:{row['table_name']}"
            if tag not in node["rels"]:
                node["rels"].append(tag)
    for node in grouped.values():
        node["rels"].sort()
    return [grouped[key] for key in sorted(grouped)]


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
