"""step1_parse_ddl — DDL 파일 → Schema/Table/Column/FK 노드 + 엣지.

LLM 0회. sqlglot 다중 방언으로 결정론 파싱.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.workflow.step_utils import step_config

from shared.ddl.parser import parse_ddl_file
from shared.log.logger import log_info, log_warning
from shared.neo4j.edges.belongs_to import upsert_belongs_to
from shared.neo4j.edges.fk import upsert_fk
from shared.neo4j.edges.has_column import upsert_has_column
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.column import upsert_columns
from shared.neo4j.nodes.schema import upsert_schemas
from shared.neo4j.nodes.table import upsert_tables

_STEP = "step1.parse_ddl"


async def parse_ddl_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """DDL 파일 디렉토리 → 노드/엣지 → Neo4j + state 누적.

    config 의 ``configurable`` 에서 ``project`` / ``neo_client`` 추출.
    """
    cfg = step_config(config)
    project = cfg.get("codebase")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None or neo is None:
        # config 누락 — 단위 테스트 등에선 정상, 운영기에선 정합성 오류.
        log_warning(_STEP, "config_missing", "project/neo_client 누락 — step skip (state 변경 X)")
        return {}

    ddl_dir = project.ddl_dir or project.root
    # db 자리는 DDL 명시값만 — 가공 식별자(예: 디렉토리 이름) 박지 X.
    # 명시 없으면 빈값 → fqn = ".schema.name" 형태로 db 부재 명확.
    db_name = ""
    started = time.monotonic()
    sql_files = sorted(Path(ddl_dir).rglob("*.sql"))
    log_info(_STEP, "start", root=str(project.root), ddl_dir=str(ddl_dir), sql_files=len(sql_files))

    schemas, tables, columns = [], [], []
    has_cols, belongs, fks = [], [], []

    for sql_file in sql_files:
        parse_started = time.monotonic()
        log_info(_STEP, "parse_file", file=str(sql_file), size_bytes=sql_file.stat().st_size)
        result = parse_ddl_file(sql_file, db=db_name)
        schemas.extend(result.schemas)
        tables.extend(result.tables)
        columns.extend(result.columns)
        has_cols.extend(result.has_column_edges)
        belongs.extend(result.belongs_to_edges)
        fks.extend(result.fk_edges)
        log_info(
            _STEP, "parsed",
            file=sql_file.name,
            tables=len(result.tables), columns=len(result.columns),
            elapsed_sec=f"{time.monotonic() - parse_started:.2f}",
        )

    # Neo4j 업서트 (UNWIND 배치). 노드는 라벨이 달라 동시쓰기 안전 → 병렬.
    # 엣지는 노드 선행 필요 + 같은 노드 락 경합 가능 → 순차 (hang 위치 추적 겸).
    log_info(_STEP, "upsert_nodes", schemas=len(schemas), tables=len(tables), columns=len(columns))
    await asyncio.gather(
        upsert_schemas(neo, schemas),
        upsert_tables(neo, tables),
        upsert_columns(neo, columns),
    )
    log_info(_STEP, "upsert_has_column", count=len(has_cols))
    await upsert_has_column(neo, has_cols)
    log_info(_STEP, "upsert_belongs_to", count=len(belongs))
    await upsert_belongs_to(neo, belongs)
    log_info(_STEP, "upsert_fk", count=len(fks))
    await upsert_fk(neo, fks)

    log_info(
        _STEP, "done",
        schemas=len(schemas), tables=len(tables), columns=len(columns),
        has_column_edges=len(has_cols), belongs_to_edges=len(belongs), fk_edges=len(fks),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )

    return {
        "schemas": schemas,
        "tables": tables,
        "columns": columns,
        "has_column_edges": has_cols,
        "belongs_to_edges": belongs,
        "fk_edges": fks,
    }
