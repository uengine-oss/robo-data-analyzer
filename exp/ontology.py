"""온톨로지: Neo4j에 저장하는 노드/관계 vs 내부 분석 전용 노드 타입.

Neo4j에 저장하는 노드: Class, Method, Field, Table, Column, SqlStatement,
ConfigSection, ArtifactChunk, PACKAGE.
관계: HAS_METHOD, HAS_FIELD, REFER_TO, READS, WRITES, HAS_COLUMN, CALLS, USES 등.

아래 내부 전용 노드 타입은 Neo4j에 저장하지 않으며, AST/ChunkBundle/인덱스 분석용만 사용.
"""

from __future__ import annotations

# AST 내부 노드 타입 (Neo4j 미저장, 내부 분석·ChunkBundle·인덱스용만)
ONTOLOGY_INTERNAL_NODE_TYPES = frozenset({
    "VARIABLE",
    "METHOD_CALL",
    "NEW_INSTANCE",
    "IMPORT",
    "FILE",
    "ASSIGNMENT",
})

# 노드 타입별 상수 (코드에서 문자열 대신 사용 권장)
NODE_VARIABLE = "VARIABLE"
NODE_METHOD_CALL = "METHOD_CALL"
NODE_NEW_INSTANCE = "NEW_INSTANCE"
NODE_IMPORT = "IMPORT"
NODE_FILE = "FILE"

def is_internal_only(node_type: str) -> bool:
    """Neo4j에 저장하지 않는 내부 전용 노드 타입이면 True."""
    return node_type in ONTOLOGY_INTERNAL_NODE_TYPES
