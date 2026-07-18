"""Neo4j 관계 타입 — 단일 정의처 (plan §4 유지보수 규칙).

관계 타입 문자열은 여기서만 상수로 정의한다. ``edges/`` 등 모든 Cypher 코드는
이 상수를 import — Cypher 안에 관계명을 직접 박지 않는다.
이름 변경 = 이 파일 한 줄만 수정 (변경 1회).

노드 라벨은 antlr ``type`` → ``ontology`` 에서 도출되는 값이라 별도 레지스트리 없음.
"""
from __future__ import annotations

# 코드 구조 (antlr JSON 에서 도출)
CALLS = "CALLS"              # CodeNode → CodeNode
PARENT_OF = "PARENT_OF"      # 부모 노드 → 중첩 자식 노드 (AST 트리 — 구문 중첩·중첩함수)
HAS_MEMBER = "HAS_MEMBER"    # 그릇(FILE/CLASS/PACKAGE/DataType) → 멤버 (루틴/변수/상수/필드)
                             #   "가지다" — 멤버 종류는 노드 라벨로 구분. 구문은 멤버 아님(PARENT_OF/NEXT 만).
REFERENCES = "REFERENCES"    # CodeNode → Variable / Constant
INIT_BY = "INIT_BY"          # Variable → 출처 노드 (변수·상수)
NEXT = "NEXT"                # 실행 순서(흐름): 구문 → 구문 (DBMS, edges/next.py)

# 코드 ↔ DB (linking 에서 도출)
READS = "READS"              # CodeNode → Table
WRITES = "WRITES"            # CodeNode → Table

# 네임스페이스 소속 (DDL: Table → Schema / AST: 그릇 → Package·Folder)
BELONGS_TO = "BELONGS_TO"    # child → 네임스페이스 (Table→Schema, FILE/CLASS→Package)

# DB 구조 (DDL 에서 도출)
HAS_COLUMN = "HAS_COLUMN"    # Table → Column
FK = "FK"                    # Table → Table

# Example Mapping (analyze_code 3분할 → EM 그래프)
HAS_RULE = "HAS_RULE"            # CodeNode → Rule
HAS_EXAMPLE = "HAS_EXAMPLE"      # Rule → Example
HAS_QUESTION = "HAS_QUESTION"    # CodeNode → Question
AFFECTS_TABLE = "AFFECTS_TABLE"  # Example → Table

# 컴포넌트 멤버십은 관계가 아니라 FUNCTION.component_id 속성 (build_components_step) — 합성 엣지 없음.
