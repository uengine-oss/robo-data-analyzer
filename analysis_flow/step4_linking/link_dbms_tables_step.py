"""step4 linking (DBMS 전용) — DML leaf range → DDL 테이블 결정론 매칭.

DBMS 는 SQL 이 코드에 그대로 있다 — leaf range(자식 없는 단일 statement)의 본문에서
DDL 테이블 이름을 단어경계 문자열검색해 ``ReadsWrites`` 엣지를 만든다. **AST 파싱·LLM 0.**

- relation / dml_type : 본문 첫 DML 키워드 (SELECT/WITH=READS · INSERT/UPDATE/DELETE/MERGE=WRITES).
- write 대상 (INSERT INTO / UPDATE / DELETE FROM / MERGE INTO 직후 테이블) = WRITES, 그 외 = READS.
- leaf 만 처리 — 컨테이너 range(PROCEDURE/IF/LOOP)는 자식 DML 을 품을 뿐 자기 statement 가
  아니므로 제외 (같은 테이블 R/W 엣지 중복 방지).

(구 ``extract_dml_metadata_step`` 의 LLM 테이블 추출을 결정론으로 대체 — plan §M13.)
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.source_file_cache import SourceFileCache
from shared.workflow.step_utils import step_config

from data_models.graph_edges import ReadsWrites
from data_models.graph_nodes import Function, Table
from shared.log.logger import log_info
from shared.neo4j.edges.reads_writes import upsert_reads_writes
from shared.neo4j.neo4j_client import Neo4jClient

_STEP = "step4.link_dbms_tables"

# 첫 등장 DML 키워드 → dml_type. SELECT/WITH = READS, 나머지 = WRITES.
_DML_KW_RE = re.compile(r"\b(SELECT|WITH|INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE)
# write 대상 테이블 — DML 키워드 직후 식별자 (스키마 prefix 허용, group 1 = 테이블명).
_WRITE_TARGET_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO)\s+(?:[\w$]+\.)?([A-Za-z_][\w$]*)",
    re.IGNORECASE,
)
# 주석 제거 (-- 라인 / /* */ 블록) — 주석 속 테이블명 오매칭 방지.
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


async def link_dbms_tables_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """DBMS DML leaf range → DDL 테이블 결정론 매칭 → ``ReadsWrites`` 엣지."""
    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")

    functions: list[Function] = state.get("functions") or []
    tables: list[Table] = state.get("tables") or []
    if not functions or not tables:
        log_info(_STEP, "skip", reason="no functions or no DDL tables")
        return {}

    started = time.monotonic()
    log_info(_STEP, "start", functions=len(functions), tables=len(tables))
    edges = _link(functions, tables, state.get("parent_of_edges") or [])

    if neo is not None and edges:
        await upsert_reads_writes(neo, edges)

    log_info(
        _STEP, "done",
        reads_writes_edges=len(edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"reads_writes_edges": edges}


def _link(
    functions: list[Function], tables: list[Table], parent_of_edges: list,
) -> list[ReadsWrites]:
    """leaf DML range 본문에서 DDL 테이블명 문자열검색 → ReadsWrites."""
    name_to_fqn = {t.name.upper(): t.fqn for t in tables}
    if not name_to_fqn:
        return []
    # 긴 이름 우선 — 부분매칭 방지. 단어경계.
    table_re = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in sorted(name_to_fqn, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )
    parent_ids = {e.parent_function_id for e in parent_of_edges}

    file_cache = SourceFileCache()
    edges: list[ReadsWrites] = []
    seen: set[tuple[str, str, str]] = set()

    for fn in functions:
        if fn.function_id in parent_ids:
            continue   # 컨테이너 range — leaf 만 처리
        body = _COMMENT_RE.sub(
            " ", file_cache.slice(fn.file_path, fn.start_line, fn.end_line),
        )
        if not body.strip():
            continue
        dml_type = _first_dml(body)
        if dml_type is None:
            continue   # DML statement 아님 (ASSIGN/RAISE/CALL 등)
        write_targets = {t.upper() for t in _WRITE_TARGET_RE.findall(body)}
        for m in table_re.finditer(body):
            fqn = name_to_fqn.get(m.group(1).upper())
            if fqn is None:
                continue
            relation = "WRITES" if m.group(1).upper() in write_targets else "READS"
            key = (fn.function_id, fqn, relation)
            if key in seen:
                continue
            seen.add(key)
            edges.append(ReadsWrites(
                function_id=fn.function_id,
                table_fqn=fqn,
                relation=relation,
                dml_type=dml_type,
                evidence_file=fn.file_path,
                evidence_line=fn.start_line,
            ))
    return edges


def _first_dml(body: str) -> Optional[str]:
    """본문 첫 DML 키워드 → dml_type (대문자). DML statement 아니면 None."""
    m = _DML_KW_RE.search(body)
    return m.group(1).upper() if m else None
