"""READS / WRITES 엣지 — CodeNode → Table.

Linking 결정론 단계 또는 Linking Agent가 생성. source MATCH는 라벨을 고정하지 않는다
(코드 라벨은 antlr 열린집합). relation 별로 다른 엣지 type 으로 박음.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import ReadsWrites
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import READS, WRITES


# relation 별 분기 — Cypher 의 dynamic relationship type 은 APOC 필요 →
# 두 쿼리로 분리해서 호출.
READS_QUERY = f"""
UNWIND $items AS item
MATCH (node {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (t:TABLE {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (node)-[r:{READS}]->(t)
SET r.dml_type = item.dml_type,
    r.evidence_file = item.evidence_file,
    r.evidence_line = item.evidence_line
"""

WRITES_QUERY = f"""
UNWIND $items AS item
MATCH (node {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (t:TABLE {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (node)-[r:{WRITES}]->(t)
SET r.dml_type = item.dml_type,
    r.evidence_file = item.evidence_file,
    r.evidence_line = item.evidence_line
"""


def _to_item(e: ReadsWrites) -> dict:
    return {
        "source_id": e.source_id,
        "target_id": e.target_id,
        "dml_type": e.dml_type,
        "evidence_file": e.evidence_file,
        "evidence_line": e.evidence_line,
    }


async def upsert_reads_writes(client: Neo4jClient, edges: Iterable[ReadsWrites]) -> None:
    reads = [_to_item(e) for e in edges if e.relation == READS]
    writes = [_to_item(e) for e in edges if e.relation == WRITES]
    if reads:
        await client.run_batch_unwind(READS_QUERY, reads)
    if writes:
        await client.run_batch_unwind(WRITES_QUERY, writes)
