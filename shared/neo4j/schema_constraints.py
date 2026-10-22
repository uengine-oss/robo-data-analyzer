"""Neo4j 인덱스 / 제약 일괄 생성.

run 시작 시 1회 호출 (``ensure_schema()``).
``IF NOT EXISTS`` — 이미 있으면 무시. 스키마는 안정적이라 매 run 새로 만들지 않는다.

라벨은 UPPER_SNAKE (plan §4 명명 규칙).
새 노드 종류 추가 시: 이 파일에 CONSTRAINT 1줄 + INDEX (필요 시) 추가.
"""
from __future__ import annotations

import re

from shared.neo4j.neo4j_client import Neo4jClient


# 노드 유일성 제약 (UNIQUE)
CONSTRAINTS: list[str] = [
    # DB 노드 — 키 = fqn
    "CREATE CONSTRAINT schema_db_name IF NOT EXISTS "
    "FOR (s:SCHEMA) REQUIRE (s.db, s.name) IS UNIQUE",
    "CREATE CONSTRAINT table_fqn IF NOT EXISTS "
    "FOR (t:TABLE) REQUIRE t.fqn IS UNIQUE",
    "CREATE CONSTRAINT column_fqn IF NOT EXISTS "
    "FOR (c:COLUMN) REQUIRE c.fqn IS UNIQUE",

    # 코드 노드 — 키 = <타입>_id
    "CREATE CONSTRAINT function_id IF NOT EXISTS "
    "FOR (f:FUNCTION) REQUIRE f.function_id IS UNIQUE",
    "CREATE CONSTRAINT module_id IF NOT EXISTS "
    "FOR (m:MODULE) REQUIRE m.module_id IS UNIQUE",
    "CREATE CONSTRAINT package_id IF NOT EXISTS "
    "FOR (p:PACKAGE) REQUIRE p.package_id IS UNIQUE",
    "CREATE CONSTRAINT variable_id IF NOT EXISTS "
    "FOR (v:VARIABLE) REQUIRE v.variable_id IS UNIQUE",
    "CREATE CONSTRAINT constant_id IF NOT EXISTS "
    "FOR (c:CONSTANT) REQUIRE c.constant_id IS UNIQUE",
    "CREATE CONSTRAINT data_type_id IF NOT EXISTS "
    "FOR (d:DATA_TYPE) REQUIRE d.datatype_id IS UNIQUE",

    # Example Mapping 노드 (analyze_function 3분할 → EM 빌더)
    "CREATE CONSTRAINT rule_hash IF NOT EXISTS "
    "FOR (r:RULE) REQUIRE r.rule_hash IS UNIQUE",
    "CREATE CONSTRAINT example_id IF NOT EXISTS "
    "FOR (e:EXAMPLE) REQUIRE e.example_id IS UNIQUE",
    "CREATE CONSTRAINT question_id IF NOT EXISTS "
    "FOR (q:QUESTION) REQUIRE q.question_id IS UNIQUE",
]

# 조회 인덱스
INDEXES: list[str] = [
    "CREATE INDEX function_file_path IF NOT EXISTS FOR (f:FUNCTION) ON (f.file_path)",
    "CREATE INDEX function_parent_id IF NOT EXISTS FOR (f:FUNCTION) ON (f.parent_id)",
    "CREATE INDEX table_schema IF NOT EXISTS FOR (t:TABLE) ON (t.schema)",
]


async def ensure_schema(client: Neo4jClient) -> None:
    """제약 + 인덱스 생성 (``IF NOT EXISTS`` — 이미 있으면 무시). run 시작 시 1회.

    스키마는 안정적 — 매 run drop-recreate 하지 않는다 (다른 프로젝트와 DB 를 공유할 경우
    drop-all 은 남의 스키마까지 파괴). 단 옛 버전 잔재 인덱스가 제약 자리를 차지하면
    충돌하므로 — analyzer 는 깨끗한 전용 DB 를 써야 한다.
    """
    for stmt in CONSTRAINTS + INDEXES:
        await client.run_query(stmt)


def _owned_labels() -> list[str]:
    """analyzer 가 만드는 노드 라벨 — 위 ``CONSTRAINTS`` 에서 뽑는다.

    목록을 따로 두지 않는 이유: 이 파일 머리말이 이미 *"새 노드 종류 추가 시 CONSTRAINT
    1줄 추가"* 를 규칙으로 정해 두었다. 별도 목록을 두면 노드를 추가한 사람이 한쪽만
    고치게 되고, 그러면 **지워지지 않는 잔재**가 조용히 남는다 — wipe 의 목적 자체가 깨진다.

    복합 라벨(``:FUNCTION:METHOD`` 등)은 따로 챙기지 않아도 된다. 기본 라벨이 항상 함께
    붙으므로 기본 라벨로 지우면 같이 사라진다 (``nodes/function.py`` 참조).
    """
    labels = re.findall(r"FOR \(\w+:([A-Z][A-Z_]*)\)", " ".join(CONSTRAINTS))
    # 유도가 실패하면 조용히 "지울 것이 없다" 가 되어 잔재가 남는다. 차라리 멈춘다.
    if not labels:
        raise RuntimeError("CONSTRAINTS 에서 라벨을 뽑지 못했습니다 — wipe 범위를 알 수 없습니다.")
    return sorted(set(labels))


async def wipe_graph(client: Neo4jClient) -> None:
    """**analyzer 가 만든 노드만** 삭제 — 매 run 시작 시 호출.

    옛 run 의 잔재가 새 분석 결과와 섞이면 검증 사각지대가 생기므로 지우는 것은 맞다.
    다만 지우는 범위가 문제였다.

    ## 왜 전역 삭제(``MATCH (n) DETACH DELETE n``)를 쓰지 않는가

    이 Neo4j 는 **혼자 쓰는 DB 가 아니다.** 같은 그래프에 data-fabric 의 카탈로그
    (``:DataSource`` / ``:Table`` / ``:Column``)와 온톨로지가 함께 산다. 전역 삭제는
    그것들까지 지운다.

    실제로 그렇게 됐다 (2026-08-03 실측): 사용자가 데이터소스를 등록한 뒤 인제스천을
    돌리자 ``:DataSource`` 가 0건이 되고 데이터소스 목록이 비었다. 매뉴얼 순서
    (2장 데이터소스 등록 → 3장 인제스천)를 그대로 따르면 **3장이 2장을 지운다.**
    플랫폼 계약 원칙 IV 가 금지하는 동작이다.

    ## 왜 라벨로 가르는 것이 안전한가 — 그리고 한때 안전하지 않았던 이유

    Neo4j 라벨은 대소문자를 구분한다. analyzer 는 UPPER_SNAKE(``:TABLE``)를 쓰고
    카탈로그는 PascalCase(``:Table``)를 쓴다 — 이름이 겹쳐 보여도 서로 다른 라벨이다.

    **그런데 이것만으로는 부족했다.** ``nodes/table.py`` 와 ``nodes/column.py`` 가
    자기 노드에 ``SET t:Table`` 로 **카탈로그 라벨을 함께** 붙이고 있었다. 그러면 같은 노드가
    두 라벨을 갖고, ``MATCH (n:TABLE) DETACH DELETE n`` 이 노드째 지우므로 **카탈로그
    정체성까지 사라진다.**

    실측(2026-08-05): ``legacy`` 예제를 분석하자 ``manufacturing`` 스키마의 ``:Table``
    11개가 0개가 됐다. 라벨 범위 삭제로 바꾼 뒤에도 그랬다 — 범위가 아니라 **소유 표시**가
    틀려 있었다.

    그래서 이중 라벨을 걷어냈다. analyzer 는 이제 자기 라벨에만 쓰고, 알아낸 설명은
    ``catalog_projection.py`` 가 기존 카탈로그 노드에 투영한다. 그 뒤로 이 함수의 전제 —
    *"내 라벨을 지우면 내 것만 사라진다"* — 가 비로소 참이 됐다.

    ``DETACH DELETE`` 는 지우는 노드에 붙은 엣지만 함께 지운다. analyzer 노드와 카탈로그
    노드를 잇는 엣지가 있다면 그 엣지는 사라지고 카탈로그 노드는 남는다 — 의도한 대로다.

    제약 / 인덱스는 보존한다 — ``ensure_schema`` 가 idempotent.
    """
    for label in _owned_labels():
        await client.run_query(f"MATCH (n:{label}) DETACH DELETE n")
