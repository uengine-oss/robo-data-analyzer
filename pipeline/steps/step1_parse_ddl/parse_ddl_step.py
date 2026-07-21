"""step1_parse_ddl — DDL 파일 → Schema/Table/Column/FK 노드 + 엣지.

LLM 0회. sqlglot 다중 방언으로 결정론 파싱.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import step_config

from shared.config.settings import SETTINGS
from pipeline.steps.step1_parse_ddl.ddl_parser import parse_ddl_file
from shared.events.runtime_logger import log_info, log_warning
from graph.edges.belongs_to import upsert_belongs_to
from graph.edges.fk import upsert_fk
from graph.edges.has_column import upsert_has_column
from graph.neo4j_client import Neo4jClient
from graph.nodes.column import upsert_columns
from graph.nodes.db_schema import upsert_schemas
from graph.nodes.table import upsert_tables
from pipeline.execution.graph_delta_stream import emit_edges, emit_message, emit_nodes

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

    # ddl_dir 미설정 시 표준 data/ddl 로 폴백 — 절대 source 루트를 긁지 않는다
    # (입구 통일 후 data/ddl 엔 표 정의만 존재; 프로시저가 sqlglot 에 닿아 run 이 죽던 사고 차단).
    ddl_dir = project.ddl_dir or (SETTINGS.analysis_data.dir / "ddl")
    # 분석 요청의 datasource는 실제 대상 DB namespace다. 디렉토리명 같은 가공값은 쓰지 않고,
    # 명시되지 않은 run만 빈값으로 남긴다.
    db_name = (getattr(project, "datasource", None) or "").strip()
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

    # 라이브 스트림 — DDL 골격을 step 완료 즉시 화면으로(프론트 트리·그래프 라이브).
    stream = cfg.get("stream")
    await emit_nodes(stream, schemas)
    await emit_nodes(stream, tables)
    await emit_nodes(stream, columns)
    await emit_edges(stream, has_cols)
    await emit_edges(stream, belongs)
    await emit_edges(stream, fks)
    emit_message(stream, f"✅ DDL 파싱 완료 — 테이블 {len(tables)}, 컬럼 {len(columns)}, FK {len(fks)}")

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
