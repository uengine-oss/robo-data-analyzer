"""READS / WRITES 엣지 — CodeNode → Table.

Linking 결정론 단계 또는 Linking Agent가 생성. source MATCH는 라벨을 고정하지 않는다
(코드 라벨은 antlr 열린집합). relation 별로 다른 엣지 type 으로 박음.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable

from graph.schema.graph_edges import ReadsWrites
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import READS, WRITES


# relation 별 분기 — Cypher 의 dynamic relationship type 은 APOC 필요 →
# 두 쿼리로 분리해서 호출.
READS_QUERY = f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
MATCH (t:ANALYZER_OWNED {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
WHERE t:TABLE OR t:OBJECT_REFERENCE
MERGE (node)-[r:{READS}]->(t)
SET r.dml_type = item.dml_type,
    r.generation_id = $generation_id,
    r.evidence_file = item.evidence_file,
    r.evidence_line = item.evidence_line,
    r.evidence_lines = reduce(
      acc = coalesce(r.evidence_lines, []), value IN item.evidence_lines |
      CASE WHEN value IN acc THEN acc ELSE acc + value END
    ),
    r.evidence_files = reduce(
      acc = coalesce(r.evidence_files, []), value IN item.evidence_files |
      CASE WHEN value IN acc THEN acc ELSE acc + value END
    ),
    r.dml_types = reduce(
      acc = coalesce(r.dml_types, []), value IN item.dml_types |
      CASE WHEN value IN acc THEN acc ELSE acc + value END
    ),
    r.evidence_locations = reduce(
      acc = coalesce(r.evidence_locations, []), value IN item.evidence_locations |
      CASE WHEN value IN acc THEN acc ELSE acc + value END
    )
"""

WRITES_QUERY = f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
MATCH (t:ANALYZER_OWNED {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
WHERE t:TABLE OR t:OBJECT_REFERENCE
MERGE (node)-[r:{WRITES}]->(t)
SET r.dml_type = item.dml_type,
    r.generation_id = $generation_id,
    r.evidence_file = item.evidence_file,
    r.evidence_line = item.evidence_line,
    r.evidence_lines = reduce(
      acc = coalesce(r.evidence_lines, []), value IN item.evidence_lines |
      CASE WHEN value IN acc THEN acc ELSE acc + value END
    ),
    r.evidence_files = reduce(
      acc = coalesce(r.evidence_files, []), value IN item.evidence_files |
      CASE WHEN value IN acc THEN acc ELSE acc + value END
    ),
    r.dml_types = reduce(
      acc = coalesce(r.dml_types, []), value IN item.dml_types |
      CASE WHEN value IN acc THEN acc ELSE acc + value END
    ),
    r.evidence_locations = reduce(
      acc = coalesce(r.evidence_locations, []), value IN item.evidence_locations |
      CASE WHEN value IN acc THEN acc ELSE acc + value END
    )
"""


def _to_items(edges: Iterable[ReadsWrites]) -> list[dict]:
    """Group edge occurrences while retaining every exact source location."""
    grouped: dict[tuple[str, str, str], list[ReadsWrites]] = defaultdict(list)
    for edge in edges:
        grouped[(edge.source_id, edge.target_id, edge.relation)].append(edge)

    items: list[dict] = []
    for (source_id, target_id, relation), occurrences in sorted(grouped.items()):
        locations = sorted({
            (edge.evidence_file, edge.evidence_line, edge.dml_type)
            for edge in occurrences
            if edge.evidence_file and edge.evidence_line > 0
        })
        first_file, first_line, first_dml = locations[0] if locations else ("", 0, "")
        dml_types = sorted({edge.dml_type for edge in occurrences if edge.dml_type})
        files = sorted({edge.evidence_file for edge in occurrences if edge.evidence_file})
        lines = sorted({edge.evidence_line for edge in occurrences if edge.evidence_line > 0})
        items.append({
            "source_id": source_id,
            "target_id": target_id,
            "relation": relation,
            "dml_type": first_dml or (dml_types[0] if dml_types else ""),
            "evidence_file": first_file or (files[0] if files else ""),
            "evidence_line": first_line or (lines[0] if lines else 0),
            "evidence_lines": lines,
            "evidence_files": files,
            "dml_types": dml_types,
            "evidence_locations": [
                json.dumps(
                    {"file": file, "line": line, "dml_type": dml_type},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for file, line, dml_type in locations
            ],
        })
    return items


async def upsert_reads_writes(client: Neo4jClient, edges: Iterable[ReadsWrites]) -> None:
    items = _to_items(edges)
    reads = [item for item in items if item["relation"] == READS]
    writes = [item for item in items if item["relation"] == WRITES]
    if reads:
        await client.run_batch_unwind(READS_QUERY, reads)
    if writes:
        await client.run_batch_unwind(WRITES_QUERY, writes)
