"""템플릿 기반 Neo4j upsert 함수

에이전트가 반환한 JSON을 받아 고정된 Cypher 템플릿으로 Neo4j에 저장합니다.
에이전트는 Cypher를 생성하지 않음 — 시스템만 저장을 담당합니다.

모든 쓰기는 cypher_lock을 통해 순차 처리됩니다.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget


# ============================================================================
# NONTARGET_BLOCK upsert
# ============================================================================

_UPSERT_BLOCKS_CYPHER = """
UNWIND $blocks AS b
MERGE (n:NONTARGET_BLOCK {name: b.name, file_path: b.file_path})
ON CREATE SET
  n.node_code    = b.node_code,
  n.start_line   = b.start_line,
  n.end_line     = b.end_line,
  n.file_name    = b.file_name,
  n.file_ext     = b.file_ext,
  n.role_hint    = b.role_hint,
  n.active       = true,
  n.created_at   = timestamp()
ON MATCH SET
  n.node_code    = b.node_code,
  n.start_line   = b.start_line,
  n.end_line     = b.end_line,
  n.role_hint    = b.role_hint,
  n.active       = true,
  n.updated_at   = timestamp()
RETURN elementId(n) AS id, n.name AS name
"""


async def commit_blocks(
    ctx: Any,
    file_path: str,
    blocks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """NONTARGET_BLOCK 노드를 upsert합니다.

    Args:
        ctx: RuntimeCtx (client, cypher_lock)
        file_path: 파일 경로
        blocks: BlockJSON 리스트 (name, start_line, end_line, node_code, ...)

    Returns:
        생성/업데이트된 블록 [{id, name}, ...]
    """
    if not blocks or not ctx.client:
        return []

    start = time.time()
    params = {"blocks": blocks}

    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    _UPSERT_BLOCKS_CYPHER, params
                )
        else:
            result = await ctx.client.execute_with_params(
                _UPSERT_BLOCKS_CYPHER, params
            )

        created = _parse_result(result)
        elapsed = int((time.time() - start) * 1000)
        log_nontarget(
            "CYPHER",
            f"commit_blocks: {file_path} → {len(created)}개 ({elapsed}ms)",
        )
        return created

    except Exception as e:
        log_nontarget("CYPHER", f"commit_blocks 실패: {file_path} - {e}")
        raise


# ============================================================================
# Summary 업데이트
# ============================================================================

_UPDATE_SUMMARIES_CYPHER = """
UNWIND $items AS item
MATCH (n:NONTARGET_BLOCK {name: item.block_name, file_path: item.file_path})
SET n.summary    = item.summary,
    n.kind       = item.kind,
    n.keywords   = item.keywords,
    n.updated_at = timestamp()
RETURN elementId(n) AS id, n.name AS name
"""


async def commit_summaries(
    ctx: Any,
    file_path: str,
    summaries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """블록 summary/kind/keywords를 업데이트합니다.

    Args:
        ctx: RuntimeCtx
        file_path: 파일 경로
        summaries: [{block_name, file_path, summary, kind, keywords}, ...]
    """
    if not summaries or not ctx.client:
        return []

    start = time.time()
    params = {"items": summaries}

    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    _UPDATE_SUMMARIES_CYPHER, params
                )
        else:
            result = await ctx.client.execute_with_params(
                _UPDATE_SUMMARIES_CYPHER, params
            )

        updated = _parse_result(result)
        elapsed = int((time.time() - start) * 1000)
        log_nontarget(
            "CYPHER",
            f"commit_summaries: {file_path} → {len(updated)}개 ({elapsed}ms)",
        )
        return updated

    except Exception as e:
        log_nontarget("CYPHER", f"commit_summaries 실패: {file_path} - {e}")
        raise


# ============================================================================
# Table / Column upsert
# ============================================================================

_UPSERT_TABLE_CYPHER = """
UNWIND $tables AS t
MERGE (tbl:Table {name: t.name, schema: t.schema})
ON CREATE SET
  tbl.entity_name       = t.entity_name,
  tbl.mapping_type      = t.mapping_type,
  tbl.description       = t.description,
  tbl.description_source = 'nontarget',
  tbl.db                = t.db,
  tbl.table_type        = t.table_type,
  tbl.created_at        = timestamp()
ON MATCH SET
  tbl.entity_name  = COALESCE(t.entity_name, tbl.entity_name),
  tbl.mapping_type = COALESCE(t.mapping_type, tbl.mapping_type),
  tbl.description  = CASE
    WHEN t.description IS NOT NULL AND size(t.description) > size(COALESCE(tbl.description, ''))
    THEN t.description ELSE tbl.description END,
  tbl.updated_at   = timestamp()
RETURN elementId(tbl) AS id, tbl.name AS name
"""

_UPSERT_COLUMN_CYPHER = """
UNWIND $columns AS c
MERGE (col:Column {fqn: c.fqn})
ON CREATE SET
  col.name            = c.name,
  col.dtype           = c.dtype,
  col.description     = c.description,
  col.description_source = 'nontarget',
  col.nullable        = c.nullable,
  col.pk_constraint   = c.pk_constraint,
  col.created_at      = timestamp()
ON MATCH SET
  col.description = CASE
    WHEN c.description IS NOT NULL AND size(c.description) > size(COALESCE(col.description, ''))
    THEN c.description ELSE col.description END,
  col.updated_at  = timestamp()

WITH col, c
MATCH (tbl:Table {name: c.table_name, schema: c.schema})
MERGE (tbl)-[:HAS_COLUMN]->(col)
RETURN elementId(col) AS id, col.name AS name
"""


async def commit_tables(
    ctx: Any,
    tables: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Table 노드를 upsert합니다."""
    if not tables or not ctx.client:
        return []

    start = time.time()
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    _UPSERT_TABLE_CYPHER, {"tables": tables}
                )
        else:
            result = await ctx.client.execute_with_params(
                _UPSERT_TABLE_CYPHER, {"tables": tables}
            )

        created = _parse_result(result)
        elapsed = int((time.time() - start) * 1000)
        log_nontarget("CYPHER", f"commit_tables: {len(created)}개 ({elapsed}ms)")
        return created

    except Exception as e:
        log_nontarget("CYPHER", f"commit_tables 실패: {e}")
        raise


async def commit_columns(
    ctx: Any,
    columns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Column 노드를 upsert합니다."""
    if not columns or not ctx.client:
        return []

    start = time.time()
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    _UPSERT_COLUMN_CYPHER, {"columns": columns}
                )
        else:
            result = await ctx.client.execute_with_params(
                _UPSERT_COLUMN_CYPHER, {"columns": columns}
            )

        created = _parse_result(result)
        elapsed = int((time.time() - start) * 1000)
        log_nontarget("CYPHER", f"commit_columns: {len(created)}개 ({elapsed}ms)")
        return created

    except Exception as e:
        log_nontarget("CYPHER", f"commit_columns 실패: {e}")
        raise


# ============================================================================
# 관계 upsert (REFERS_TO / FROM / WRITES / FK)
# ============================================================================

_UPSERT_REFERS_TO_CYPHER = """
UNWIND $links AS l
MATCH (src) WHERE elementId(src) = l.source_id
MATCH (tgt) WHERE elementId(tgt) = l.target_id
MERGE (src)-[r:REFERS_TO]->(tgt)
SET r.keyword      = l.keyword,
    r.match_type   = l.match_type,
    r.confidence   = l.confidence,
    r.evidence     = l.evidence,
    r.source_lines = l.source_lines,
    r.role         = l.role,
    r.updated_at   = timestamp()
RETURN elementId(r) AS id
"""

_UPSERT_FROM_CYPHER = """
UNWIND $links AS l
MATCH (src) WHERE elementId(src) = l.source_id
MATCH (tgt:Table) WHERE elementId(tgt) = l.target_id
MERGE (src)-[r:FROM]->(tgt)
SET r.evidence   = l.evidence,
    r.updated_at = timestamp()
RETURN elementId(r) AS id
"""

_UPSERT_WRITES_CYPHER = """
UNWIND $links AS l
MATCH (src) WHERE elementId(src) = l.source_id
MATCH (tgt:Table) WHERE elementId(tgt) = l.target_id
MERGE (src)-[r:WRITES]->(tgt)
SET r.evidence   = l.evidence,
    r.updated_at = timestamp()
RETURN elementId(r) AS id
"""

_UPSERT_FK_TO_TABLE_CYPHER = """
UNWIND $links AS l
MATCH (src:Table) WHERE elementId(src) = l.source_id
MATCH (tgt:Table) WHERE elementId(tgt) = l.target_id
MERGE (src)-[r:FK_TO_TABLE]->(tgt)
SET r.evidence    = l.evidence,
    r.fk_type     = l.fk_type,
    r.updated_at  = timestamp()
RETURN elementId(r) AS id
"""

_UPSERT_FK_TO_CYPHER = """
UNWIND $links AS l
MATCH (src:Column) WHERE elementId(src) = l.source_id
MATCH (tgt:Column) WHERE elementId(tgt) = l.target_id
MERGE (src)-[r:FK_TO]->(tgt)
SET r.evidence   = l.evidence,
    r.fk_type    = l.fk_type,
    r.updated_at = timestamp()
RETURN elementId(r) AS id
"""

_RELATION_CYPHER_MAP = {
    "REFERS_TO": _UPSERT_REFERS_TO_CYPHER,
    "FROM": _UPSERT_FROM_CYPHER,
    "WRITES": _UPSERT_WRITES_CYPHER,
    "FK_TO_TABLE": _UPSERT_FK_TO_TABLE_CYPHER,
    "FK_TO": _UPSERT_FK_TO_CYPHER,
}


async def commit_links(
    ctx: Any,
    file_path: str,
    links_json: Dict[str, Any],
) -> Dict[str, int]:
    """관계를 유형별로 upsert합니다.

    Args:
        ctx: RuntimeCtx
        file_path: 파일 경로 (로깅용)
        links_json: {
            "refers_to": [{source_id, target_id, keyword, ...}],
            "from": [...],
            "writes": [...],
            "fk_to_table": [...],
            "fk_to": [...],
        }

    Returns:
        유형별 생성 수 {"REFERS_TO": 5, "FROM": 2, ...}
    """
    if not ctx.client:
        return {}

    start = time.time()
    counts: Dict[str, int] = {}

    type_key_map = {
        "refers_to": "REFERS_TO",
        "from": "FROM",
        "writes": "WRITES",
        "fk_to_table": "FK_TO_TABLE",
        "fk_to": "FK_TO",
    }

    for json_key, rel_type in type_key_map.items():
        items = links_json.get(json_key, [])
        if not items:
            continue

        cypher = _RELATION_CYPHER_MAP.get(rel_type)
        if not cypher:
            continue

        try:
            if ctx.cypher_lock:
                async with ctx.cypher_lock:
                    result = await ctx.client.execute_with_params(
                        cypher, {"links": items}
                    )
            else:
                result = await ctx.client.execute_with_params(
                    cypher, {"links": items}
                )

            counts[rel_type] = len(_parse_result(result))
        except Exception as e:
            log_nontarget("CYPHER", f"commit_links({rel_type}) 실패: {e}")
            counts[rel_type] = 0

    elapsed = int((time.time() - start) * 1000)
    total = sum(counts.values())
    log_nontarget(
        "CYPHER",
        f"commit_links: {file_path} → {total}개 관계 ({counts}) ({elapsed}ms)",
    )
    return counts


# ============================================================================
# Description 업데이트
# ============================================================================

_UPDATE_DESCRIPTION_CYPHER = """
UNWIND $items AS item
MATCH (n) WHERE elementId(n) = item.entity_id
SET n.description        = item.description,
    n.description_source = 'nontarget_enriched',
    n.updated_at         = timestamp()
RETURN elementId(n) AS id
"""


async def commit_descriptions(
    ctx: Any,
    descriptions: List[Dict[str, Any]],
) -> int:
    """Table/Column의 description을 업데이트합니다.

    Args:
        ctx: RuntimeCtx
        descriptions: [{entity_id, description}, ...]

    Returns:
        업데이트된 엔티티 수
    """
    if not descriptions or not ctx.client:
        return 0

    start = time.time()
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    _UPDATE_DESCRIPTION_CYPHER, {"items": descriptions}
                )
        else:
            result = await ctx.client.execute_with_params(
                _UPDATE_DESCRIPTION_CYPHER, {"items": descriptions}
            )

        count = len(_parse_result(result))
        elapsed = int((time.time() - start) * 1000)
        log_nontarget(
            "CYPHER",
            f"commit_descriptions: {count}개 ({elapsed}ms)",
        )
        return count

    except Exception as e:
        log_nontarget("CYPHER", f"commit_descriptions 실패: {e}")
        raise


# ============================================================================
# 헬퍼
# ============================================================================

def _parse_result(result: Any) -> List[Dict[str, Any]]:
    """Neo4j 결과를 딕셔너리 리스트로 변환"""
    if not result:
        return []
    return [
        dict(r) if isinstance(r, dict) else r.data()
        for r in result
    ]

