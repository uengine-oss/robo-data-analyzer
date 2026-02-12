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
# NONTARGET_FILE upsert + CONTAINS 관계 + 패키지 연결
# ============================================================================

_UPSERT_NONTARGET_FILE_CYPHER = """
UNWIND $files AS f
MERGE (nf:NONTARGET_FILE {file_path: f.file_path})
ON CREATE SET
  nf.file_name  = f.file_name,
  nf.ext        = f.ext,
  nf.directory  = f.directory,
  nf.created_at = timestamp()
ON MATCH SET
  nf.updated_at = timestamp()

WITH nf, f
// 해당 파일의 모든 블록을 CONTAINS로 연결
OPTIONAL MATCH (b:NONTARGET_BLOCK {file_path: f.file_path})
WHERE b.active = true
FOREACH (_ IN CASE WHEN b IS NOT NULL THEN [1] ELSE [] END |
  MERGE (nf)-[:CONTAINS]->(b)
)
RETURN elementId(nf) AS id, nf.file_name AS name
"""

def _build_package_hierarchy_queries(package_name: str) -> List[Dict[str, Any]]:
    """패키지 계층 구조 쿼리를 생성합니다 (Framework AST와 동일 패턴).

    예: com.kbstar.kee.dc 패키지의 경우
    - PACKAGE(name: "com", fqn: "com")
    - PACKAGE(name: "kbstar", fqn: "com.kbstar") -[:CONTAINS_PACKAGE]-> PACKAGE(fqn: "com")
    - PACKAGE(name: "kee", fqn: "com.kbstar.kee") -[:CONTAINS_PACKAGE]-> ...
    - PACKAGE(name: "dc", fqn: "com.kbstar.kee.dc") -[:CONTAINS_PACKAGE]-> ...
    """
    queries: List[Dict[str, Any]] = []
    parts = package_name.split(".")

    for i in range(len(parts)):
        segment = parts[i]
        fqn = ".".join(parts[:i + 1])
        parent_fqn = ".".join(parts[:i]) if i > 0 else None

        # PACKAGE 노드 생성 (MERGE)
        queries.append({
            "query": (
                "MERGE (__cy_pkg__:PACKAGE {fqn: $fqn})\n"
                "ON CREATE SET __cy_pkg__.created_at = timestamp()\n"
                "SET __cy_pkg__.name = $segment, __cy_pkg__.updated_at = timestamp()\n"
                "RETURN __cy_pkg__"
            ),
            "params": {"fqn": fqn, "segment": segment}
        })

        # 부모 패키지와의 CONTAINS_PACKAGE 관계 생성
        if parent_fqn:
            queries.append({
                "query": (
                    "MATCH (__cy_parent__:PACKAGE {fqn: $parent_fqn})\n"
                    "MATCH (__cy_child__:PACKAGE {fqn: $child_fqn})\n"
                    "MERGE (__cy_parent__)-[__cy_r__:CONTAINS_PACKAGE]->(__cy_child__)\n"
                    "RETURN __cy_r__"
                ),
                "params": {"parent_fqn": parent_fqn, "child_fqn": fqn}
            })

    return queries


async def commit_nontarget_files(
    ctx: Any,
    files: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """NONTARGET_FILE 노드를 upsert하고, NONTARGET_BLOCK을 CONTAINS로 연결합니다.

    패키지 계층 구조를 Framework AST와 동일 패턴으로 생성합니다:
    - 각 세그먼트마다 PACKAGE 노드 생성 (name, fqn 속성)
    - CONTAINS_PACKAGE 관계로 계층 구조 구성
    - NONTARGET_FILE → BELONGS_TO_PACKAGE → 최종 패키지

    Args:
        ctx: RuntimeCtx
        files: [{file_path, file_name, ext, directory, package_name}, ...]

    Returns:
        생성/업데이트된 파일 노드 [{id, name}, ...]
    """
    if not files or not ctx.client:
        return []

    start = time.time()
    try:
        # 1. NONTARGET_FILE upsert + CONTAINS 관계
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    _UPSERT_NONTARGET_FILE_CYPHER, {"files": files}
                )
        else:
            result = await ctx.client.execute_with_params(
                _UPSERT_NONTARGET_FILE_CYPHER, {"files": files}
            )
        created = _parse_result(result)

        # 2. 패키지 계층 구조 생성 + BELONGS_TO_PACKAGE 연결 (Framework AST와 동일 패턴)
        # 패키지별로 파일 그룹화
        pkg_to_files: Dict[str, List[str]] = {}
        for f in files:
            pkg_name = f.get("package_name", "")
            if pkg_name:
                pkg_to_files.setdefault(pkg_name, []).append(f["file_path"])

        belongs_query = (
            "UNWIND $file_paths AS fp\n"
            "MATCH (nf:NONTARGET_FILE {file_path: fp})\n"
            "MATCH (pkg:PACKAGE {fqn: $package_fqn})\n"
            "MERGE (nf)-[:BELONGS_TO_PACKAGE]->(pkg)\n"
            "RETURN elementId(nf) AS id"
        )

        for pkg_name, file_paths in pkg_to_files.items():
            # 패키지 계층 구조 쿼리 생성 (세그먼트별 PACKAGE + CONTAINS_PACKAGE)
            pkg_queries = _build_package_hierarchy_queries(pkg_name)
            for q in pkg_queries:
                if ctx.cypher_lock:
                    async with ctx.cypher_lock:
                        await ctx.client.execute_with_params(q["query"], q["params"])
                else:
                    await ctx.client.execute_with_params(q["query"], q["params"])

            # BELONGS_TO_PACKAGE 연결 (UNWIND로 한 번에)
            if ctx.cypher_lock:
                async with ctx.cypher_lock:
                    await ctx.client.execute_with_params(
                        belongs_query,
                        {"file_paths": file_paths, "package_fqn": pkg_name}
                    )
            else:
                await ctx.client.execute_with_params(
                    belongs_query,
                    {"file_paths": file_paths, "package_fqn": pkg_name}
                )

        elapsed = int((time.time() - start) * 1000)
        log_nontarget("CYPHER", f"commit_nontarget_files: {len(created)}개 ({elapsed}ms)")
        return created

    except Exception as e:
        log_nontarget("CYPHER", f"commit_nontarget_files 실패: {e}")
        raise


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
SET n.summary        = item.summary,
    n.kind           = item.kind,
    n.keywords       = item.keywords,
    n.fk_inferred_json = item.fk_inferred_json,
    n.updated_at     = timestamp()
WITH n, item
// kind에 따라 레이블 부여 (:Query, :Table)
FOREACH (_ IN CASE WHEN toLower(item.kind) = 'query' THEN [1] ELSE [] END | SET n:Query)
FOREACH (_ IN CASE WHEN toLower(item.kind) = 'schema' THEN [1] ELSE [] END | SET n:Table)
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
  tbl.description_source = 'code',
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
  col.description_source = 'code',
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
# NONTARGET_BLOCK → Table/Column REFERS_TO 자동 생성 (시스템 레벨)
# ============================================================================

_UPSERT_BLOCK_TABLE_REFERS_TO_CYPHER = """
UNWIND $items AS item
MATCH (b:NONTARGET_BLOCK {name: item.block_name, file_path: item.file_path})
MATCH (t:Table {name: item.table_name, schema: item.schema})
MERGE (b)-[r:REFERS_TO]->(t)
SET r.keyword      = item.keyword,
    r.match_type   = 'system',
    r.confidence   = 1.0,
    r.evidence     = item.evidence,
    r.source_lines = item.source_lines,
    r.role         = item.role,
    r.updated_at   = timestamp()
RETURN elementId(r) AS id
"""


async def commit_block_table_refers_to(
    ctx: Any,
    items: List[Dict[str, Any]],
) -> int:
    """NONTARGET_BLOCK → Table REFERS_TO 관계를 시스템 레벨에서 자동 생성합니다.

    Args:
        ctx: RuntimeCtx
        items: [{block_name, file_path, table_name, schema, role, evidence}, ...]

    Returns:
        생성된 관계 수
    """
    if not items or not ctx.client:
        return 0

    start = time.time()
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    _UPSERT_BLOCK_TABLE_REFERS_TO_CYPHER, {"items": items}
                )
        else:
            result = await ctx.client.execute_with_params(
                _UPSERT_BLOCK_TABLE_REFERS_TO_CYPHER, {"items": items}
            )

        count = len(_parse_result(result))
        elapsed = int((time.time() - start) * 1000)
        log_nontarget("CYPHER", f"commit_block_table_refers_to: {count}개 ({elapsed}ms)")
        return count

    except Exception as e:
        log_nontarget("CYPHER", f"commit_block_table_refers_to 실패: {e}")
        raise


# ============================================================================
# NONTARGET_BLOCK NEXT 관계 생성 (파일 내 순서)
# ============================================================================

_UPSERT_BLOCK_NEXT_CYPHER = """
MATCH (b:NONTARGET_BLOCK {file_path: $file_path})
WHERE b.active = true
WITH b
ORDER BY b.start_line ASC
WITH collect(b) AS blocks
UNWIND range(0, size(blocks) - 2) AS idx
WITH blocks[idx] AS src, blocks[idx + 1] AS dst
MERGE (src)-[r:NEXT]->(dst)
SET r.updated_at = timestamp()
RETURN count(r) AS count
"""


async def commit_block_next(
    ctx: Any,
    file_path: str,
) -> int:
    """동일 파일 내 NONTARGET_BLOCK 사이에 NEXT 관계를 생성합니다.

    start_line 기준 오름차순으로 정렬하여 (b1)-[:NEXT]->(b2)->... 순서로 연결합니다.
    """
    if not ctx.client:
        return 0

    start = time.time()
    try:
        params = {"file_path": file_path}
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    _UPSERT_BLOCK_NEXT_CYPHER, params
                )
        else:
            result = await ctx.client.execute_with_params(
                _UPSERT_BLOCK_NEXT_CYPHER, params
            )

        rows = _parse_result(result)
        count = rows[0].get("count", 0) if rows else 0
        elapsed = int((time.time() - start) * 1000)
        log_nontarget("CYPHER", f"commit_block_next: {file_path} → {count}개 NEXT ({elapsed}ms)")
        return count

    except Exception as e:
        log_nontarget("CYPHER", f"commit_block_next 실패: {file_path} - {e}")
        raise


# ============================================================================
# NONTARGET_BLOCK → METHOD/CONSTRUCTOR/FIELD REFERS_TO 생성 (Linker)
# ============================================================================

_UPSERT_BLOCK_METHOD_REFERS_TO_CYPHER = """
UNWIND $items AS item
MATCH (b:NONTARGET_BLOCK {name: item.block_name, file_path: item.file_path})
MATCH (m) WHERE elementId(m) = item.method_id
MERGE (b)-[r:REFERS_TO]->(m)
SET r.keyword    = item.keyword,
    r.source     = 'nontarget_linker',
    r.evidence   = item.evidence,
    r.updated_at = timestamp()
RETURN elementId(r) AS id
"""


async def commit_block_method_refers_to(
    ctx: Any,
    items: List[Dict[str, Any]],
) -> int:
    """NONTARGET_BLOCK → METHOD/CONSTRUCTOR/FIELD REFERS_TO 관계를 생성합니다.

    Args:
        ctx: RuntimeCtx
        items: [{block_name, file_path, method_id, keyword, evidence}, ...]
    """
    if not items or not ctx.client:
        return 0

    start = time.time()
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    _UPSERT_BLOCK_METHOD_REFERS_TO_CYPHER, {"items": items}
                )
        else:
            result = await ctx.client.execute_with_params(
                _UPSERT_BLOCK_METHOD_REFERS_TO_CYPHER, {"items": items}
            )

        count = len(_parse_result(result))
        elapsed = int((time.time() - start) * 1000)
        log_nontarget("CYPHER", f"commit_block_method_refers_to: {count}개 ({elapsed}ms)")
        return count

    except Exception as e:
        log_nontarget("CYPHER", f"commit_block_method_refers_to 실패: {e}")
        raise


# ============================================================================
# FK 확정 (Summarizer fk_explicit → 시스템 즉시 확정)
# ============================================================================

_UPSERT_FK_TO_TABLE_BY_NAME_CYPHER = """
UNWIND $items AS item
MATCH (src:Table {name: item.source_table})
MATCH (tgt:Table {name: item.target_table})
WHERE src <> tgt
MERGE (src)-[r:FK_TO_TABLE]->(tgt)
SET r.evidence   = item.evidence,
    r.fk_type    = item.fk_type,
    r.confidence = item.confidence,
    r.updated_at = timestamp()
RETURN elementId(r) AS id
"""

_UPSERT_FK_TO_BY_NAME_CYPHER = """
UNWIND $items AS item
MATCH (src:Column {fqn: toLower(item.source_table) + '.' + toLower(item.source_column)})
MATCH (tgt:Column {fqn: toLower(item.target_table) + '.' + toLower(item.target_column)})
WHERE src <> tgt
MERGE (src)-[r:FK_TO]->(tgt)
SET r.evidence   = item.evidence,
    r.fk_type    = item.fk_type,
    r.confidence = item.confidence,
    r.updated_at = timestamp()
RETURN elementId(r) AS id
"""


async def commit_explicit_fk(
    ctx: Any,
    fk_items: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Summarizer의 fk_explicit을 바로 FK_TO_TABLE/FK_TO 관계로 확정합니다.

    Args:
        ctx: RuntimeCtx
        fk_items: [{source_table, source_column, target_table, target_column, fk_type, evidence, confidence}, ...]

    Returns:
        {"FK_TO_TABLE": n, "FK_TO": n}
    """
    if not fk_items or not ctx.client:
        return {"FK_TO_TABLE": 0, "FK_TO": 0}

    start = time.time()
    counts = {"FK_TO_TABLE": 0, "FK_TO": 0}

    try:
        # 1. Table → Table (FK_TO_TABLE)
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    _UPSERT_FK_TO_TABLE_BY_NAME_CYPHER, {"items": fk_items}
                )
        else:
            result = await ctx.client.execute_with_params(
                _UPSERT_FK_TO_TABLE_BY_NAME_CYPHER, {"items": fk_items}
            )
        counts["FK_TO_TABLE"] = len(_parse_result(result))

        # 2. Column → Column (FK_TO) — 컬럼 정보가 있는 항목만
        col_items = [
            item for item in fk_items
            if item.get("source_column") and item.get("target_column")
        ]
        if col_items:
            if ctx.cypher_lock:
                async with ctx.cypher_lock:
                    result = await ctx.client.execute_with_params(
                        _UPSERT_FK_TO_BY_NAME_CYPHER, {"items": col_items}
                    )
            else:
                result = await ctx.client.execute_with_params(
                    _UPSERT_FK_TO_BY_NAME_CYPHER, {"items": col_items}
                )
            counts["FK_TO"] = len(_parse_result(result))

        elapsed = int((time.time() - start) * 1000)
        log_nontarget("CYPHER", f"commit_explicit_fk: {counts} ({elapsed}ms)")
        return counts

    except Exception as e:
        log_nontarget("CYPHER", f"commit_explicit_fk 실패: {e}")
        raise


# ============================================================================
# 관계 upsert (FROM / WRITES / FK) — REFERS_TO는 시스템 레벨에서 처리
# ============================================================================

_UPSERT_FROM_CYPHER = """
UNWIND $links AS l
MATCH (src) WHERE elementId(src) = l.source_id
MATCH (tgt:Table {name: l.target_table})
MERGE (src)-[r:FROM]->(tgt)
SET r.evidence      = l.evidence,
    r.source_name   = l.source_name,
    r.source_file   = l.source_file,
    r.updated_at    = timestamp()
RETURN elementId(r) AS id
"""

_UPSERT_WRITES_CYPHER = """
UNWIND $links AS l
MATCH (src) WHERE elementId(src) = l.source_id
MATCH (tgt:Table {name: l.target_table})
MERGE (src)-[r:WRITES]->(tgt)
SET r.evidence      = l.evidence,
    r.source_name   = l.source_name,
    r.source_file   = l.source_file,
    r.updated_at    = timestamp()
RETURN elementId(r) AS id
"""

_UPSERT_FK_TO_TABLE_CYPHER = """
UNWIND $links AS l
MATCH (src:Table {name: l.source_table})
MATCH (tgt:Table {name: l.target_table})
WHERE src <> tgt
MERGE (src)-[r:FK_TO_TABLE]->(tgt)
SET r.evidence      = l.evidence,
    r.fk_type       = l.fk_type,
    r.source_file   = l.source_file,
    r.updated_at    = timestamp()
RETURN elementId(r) AS id
"""

_UPSERT_FK_TO_CYPHER = """
UNWIND $links AS l
MATCH (src:Column {fqn: toLower(l.source_table) + '.' + toLower(l.source_column)})
MATCH (tgt:Column {fqn: toLower(l.target_table) + '.' + toLower(l.target_column)})
WHERE src <> tgt
MERGE (src)-[r:FK_TO]->(tgt)
SET r.evidence      = l.evidence,
    r.fk_type       = l.fk_type,
    r.source_file   = l.source_file,
    r.updated_at    = timestamp()
RETURN elementId(r) AS id
"""

_RELATION_CYPHER_MAP = {
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
    """관계를 유형별로 upsert합니다 (REFERS_TO는 시스템 레벨에서 처리).

    Args:
        ctx: RuntimeCtx
        file_path: 파일 경로 (로깅용)
        links_json: {
            "from": [...],
            "writes": [...],
            "fk_to_table": [...],
            "fk_to": [...],
        }

    Returns:
        유형별 생성 수 {"FROM": 2, "WRITES": 1, ...}
    """
    if not ctx.client:
        return {}

    start = time.time()
    counts: Dict[str, int] = {}

    type_key_map = {
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
# Description 업데이트 — Table-Column 쌍 처리
# ============================================================================

_UPDATE_TABLE_DESC_CYPHER = """
UNWIND $items AS item
MATCH (t) WHERE elementId(t) = item.table_id
SET t.description        = item.table_description,
    t.description_source = 'code',
    t.updated_at         = timestamp()
RETURN elementId(t) AS id
"""

_UPDATE_COLUMN_DESC_CYPHER = """
UNWIND $items AS item
MATCH (c) WHERE elementId(c) = item.column_id
SET c.description        = item.column_description,
    c.description_source = 'code',
    c.updated_at         = timestamp()
RETURN elementId(c) AS id
"""


async def commit_descriptions(
    ctx: Any,
    descriptions: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Table-Column 쌍의 description을 업데이트합니다.

    Args:
        ctx: RuntimeCtx
        descriptions: [{table_id, table_description, columns: [{column_id, description}]}, ...]

    Returns:
        {"tables": 업데이트된 테이블 수, "columns": 업데이트된 컬럼 수}
    """
    if not descriptions or not ctx.client:
        return {"tables": 0, "columns": 0}

    start = time.time()
    tables_updated = 0
    columns_updated = 0

    try:
        # Table description 업데이트
        table_items = [
            {"table_id": d["table_id"], "table_description": d["table_description"]}
            for d in descriptions
            if d.get("table_description", "").strip()
        ]
        if table_items:
            if ctx.cypher_lock:
                async with ctx.cypher_lock:
                    result = await ctx.client.execute_with_params(
                        _UPDATE_TABLE_DESC_CYPHER, {"items": table_items}
                    )
            else:
                result = await ctx.client.execute_with_params(
                    _UPDATE_TABLE_DESC_CYPHER, {"items": table_items}
                )
            tables_updated = len(_parse_result(result))

        # Column description 업데이트
        column_items = []
        for d in descriptions:
            for c in d.get("columns", []):
                if c.get("description", "").strip() and c.get("column_id"):
                    column_items.append({
                        "column_id": c["column_id"],
                        "column_description": c["description"],
                    })

        if column_items:
            if ctx.cypher_lock:
                async with ctx.cypher_lock:
                    result = await ctx.client.execute_with_params(
                        _UPDATE_COLUMN_DESC_CYPHER, {"items": column_items}
                    )
            else:
                result = await ctx.client.execute_with_params(
                    _UPDATE_COLUMN_DESC_CYPHER, {"items": column_items}
                )
            columns_updated = len(_parse_result(result))

        elapsed = int((time.time() - start) * 1000)
        log_nontarget(
            "CYPHER",
            f"commit_descriptions: 테이블 {tables_updated}개, 컬럼 {columns_updated}개 ({elapsed}ms)",
        )
        return {"tables": tables_updated, "columns": columns_updated}

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

