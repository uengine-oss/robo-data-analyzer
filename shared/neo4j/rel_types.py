"""Neo4j 관계 타입 — 단일 정의처 (plan §4 유지보수 규칙).

관계 타입 문자열은 여기서만 상수로 정의한다. ``edges/`` 등 모든 Cypher 코드는
이 상수를 import — Cypher 안에 관계명을 직접 박지 않는다.
이름 변경 = 이 파일 한 줄만 수정 (변경 1회).

노드 라벨은 antlr ``type`` → ``ontology`` 에서 도출되는 값이라 별도 레지스트리 없음.
"""
from __future__ import annotations

# 코드 구조 (antlr JSON 에서 도출)
CALLS = "CALLS"              # Function → Function
PARENT_OF = "PARENT_OF"      # Function → nested Function (DBMS range 트리)
USES = "USES"                # Module → Module
HAS_FUNCTION = "HAS_FUNCTION"               # Module → Function
HAS_VARIABLE = "HAS_VARIABLE"               # Module → Variable
HAS_MEMBER = "HAS_MEMBER"                   # DataType → Variable (struct 멤버)
BELONGS_TO_PACKAGE = "BELONGS_TO_PACKAGE"   # Module → Package
REFERENCES = "REFERENCES"                   # Function → Variable / Constant
INIT_BY = "INIT_BY"                         # Variable → 출처 노드 (변수·상수)

# 코드 ↔ DB (linking 에서 도출)
READS = "READS"              # Function → Table
WRITES = "WRITES"            # Function → Table

# DB 구조 (DDL 에서 도출)
HAS_COLUMN = "HAS_COLUMN"    # Table → Column
BELONGS_TO = "BELONGS_TO"    # Table → Schema
FK = "FK"                    # Table → Table

# Example Mapping (analyze_function 3분할 → EM 그래프)
HAS_RULE = "HAS_RULE"            # Function → Rule (per-function 정보는 이 엣지 속성)
HAS_EXAMPLE = "HAS_EXAMPLE"      # Rule → Example
HAS_QUESTION = "HAS_QUESTION"    # Function → Question
NEXT = "NEXT"                    # Rule → Rule (메인 흐름 순서)
BRANCH = "BRANCH"                # Rule → Rule (분기)
AFFECTS_TABLE = "AFFECTS_TABLE"  # Example → Table
