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


# relation 별 분기 — Cypher 의 dynamic relationship type 은 APOC 필요 → relation 별로 질의를 만든다.
#
# 근거 목록(evidence_lines·files·dml_types·locations)은 같은 엣지에 여러 step 이 누적한다
# (결정론 → 잔여 패턴 → catalog lineage). 예전엔 질의 안의 ``reduce(acc = coalesce(r.x, []) …)``
# 로 합쳤는데, Ontological DB 는 ``reduce`` 를 파싱하지 못하고 목록 ``+`` 를 문자열 이어붙이기로
# 푼다(``"[][7,19]"``, 실측). 그래서 기존 목록을 읽어 파이썬에서 합친 뒤 통째로 SET 한다. 같은
# 엣지를 쓰는 step 들은 순차라 읽기-쓰기 사이에 끼어드는 쓰기가 없다.
_LIST_FIELDS = ("evidence_lines", "evidence_files", "dml_types", "evidence_locations")


def _existing_query(rel_type: str) -> str:
    return f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})-[r:{rel_type}]->(t:TABLE {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
RETURN item.source_id AS source_id, item.target_id AS target_id,
       r.evidence_lines AS evidence_lines, r.evidence_files AS evidence_files,
       r.dml_types AS dml_types, r.evidence_locations AS evidence_locations
"""


def _merge_query(rel_type: str) -> str:
    return f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (t:TABLE {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (node)-[r:{rel_type}]->(t)
SET r.dml_type = item.dml_type,
    r.evidence_file = item.evidence_file,
    r.evidence_line = item.evidence_line,
    r.evidence_lines = item.evidence_lines,
    r.evidence_files = item.evidence_files,
    r.dml_types = item.dml_types,
    r.evidence_locations = item.evidence_locations
"""


READS_QUERY = _merge_query(READS)
WRITES_QUERY = _merge_query(WRITES)


def merge_existing(items: list[dict], existing_rows: list[dict]) -> list[dict]:
    """기존 엣지의 근거 목록 뒤에 새 값 중 없는 것만 붙인다 (옛 ``reduce`` 와 같은 순서·중복 규칙)."""
    existing = {(row["source_id"], row["target_id"]): row for row in existing_rows}
    merged: list[dict] = []
    for item in items:
        row = existing.get((item["source_id"], item["target_id"]))
        if row is None:
            merged.append(item)
            continue
        combined = dict(item)
        for name in _LIST_FIELDS:
            values = list(row.get(name) or [])
            for value in item[name]:
                if value not in values:
                    values.append(value)
            combined[name] = values
        merged.append(combined)
    return merged


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
    for relation, query in ((READS, READS_QUERY), (WRITES, WRITES_QUERY)):
        batch = [item for item in items if item["relation"] == relation]
        if not batch:
            continue
        existing = await client.run_read_query(_existing_query(relation), {"items": batch})
        await client.run_batch_unwind(query, merge_existing(batch, existing))
