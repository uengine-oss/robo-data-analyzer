"""step1_parse_ddl — DDL 파일 → Schema/Table/Column/FK 노드 + 엣지.

LLM 0회. sqlglot 다중 방언으로 결정론 파싱.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import require_step_config_value, step_config

from shared.config.settings import SETTINGS
from shared.files.cache import SourceFileCache
from pipeline.steps.step1_parse_ddl.ddl_parser import parse_ddl_file
from shared.events.runtime_logger import log_info
from pipeline.execution.graph_delta_stream import emit_edges, emit_message, emit_nodes

_STEP = "step1.parse_ddl"


def _ensure_unique_table_ids(table_files_by_id: dict[str, list[str]]) -> None:
    """파일 사이 같은 table ID 재정의를 좌표와 함께 즉시 실패로 표면화한다."""
    duplicated = sorted(
        tid for tid, files in table_files_by_id.items() if len(files) > 1
    )
    if duplicated:
        shown = ", ".join(
            f"{tid} ({' · '.join(table_files_by_id[tid])})" for tid in duplicated[:5]
        )
        raise ValueError(
            f"DDL 파일 사이에 같은 테이블이 {len(duplicated)}개 재정의됨 — "
            f"어느 정의가 진실인지 결정할 수 없다: {shown}"
            + (" …" if len(duplicated) > 5 else "")
        )


async def parse_ddl_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """DDL 파일 디렉토리에서 전체 구조 state를 결정론적으로 만든다."""
    cfg = step_config(config)
    project = require_step_config_value(cfg, "codebase", step=_STEP)

    # ddl_dir 미설정 시 표준 data/ddl 로 폴백 — 절대 source 루트를 긁지 않는다
    # (입구 통일 후 data/ddl 엔 표 정의만 존재; 프로시저가 sqlglot 에 닿아 run 이 죽던 사고 차단).
    ddl_dir = project.ddl_dir or (SETTINGS.analysis_data.dir / "ddl")
    # 분석 요청의 datasource는 실제 대상 DB namespace다. 디렉토리명 같은 가공값은 쓰지 않고,
    # 명시되지 않은 run만 빈값으로 남긴다.
    db_name = (getattr(project, "datasource", None) or "").strip()
    source_cache = cfg.get("source_cache") or SourceFileCache()
    started = time.monotonic()
    sql_files = sorted(Path(ddl_dir).rglob("*.sql"))
    log_info(_STEP, "start", root=str(project.root), ddl_dir=str(ddl_dir), sql_files=len(sql_files))

    schemas, tables, columns = [], [], []
    has_cols, belongs, fks = [], [], []
    table_files_by_id: dict[str, list[str]] = {}

    for sql_file in sql_files:
        parse_started = time.monotonic()
        log_info(_STEP, "parse_file", file=str(sql_file), size_bytes=sql_file.stat().st_size)
        result = parse_ddl_file(
            sql_file,
            db=db_name,
            sealed_source_bytes=source_cache.read_bytes(sql_file),
        )
        schemas.extend(result.schemas)
        tables.extend(result.tables)
        columns.extend(result.columns)
        has_cols.extend(result.has_column_edges)
        belongs.extend(result.belongs_to_edges)
        fks.extend(result.fk_edges)
        for table in result.tables:
            table_files_by_id.setdefault(table.id, []).append(sql_file.name)
        log_info(
            _STEP, "parsed",
            file=sql_file.name,
            tables=len(result.tables), columns=len(result.columns),
            elapsed_sec=f"{time.monotonic() - parse_started:.2f}",
        )

    # 파일 사이 중복 테이블 정의는 스키마 모호성이다 — 여기서 좌표와 함께 즉시 실패한다.
    # (파일 안 중복은 parse_ddl_file 이 dedupe. 늦게 두면 LLM 소비 후 step8 에서야
    # duplicate IDs 로 터진다 — 2026-08-07 rwis 실측.)
    _ensure_unique_table_ids(table_files_by_id)

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
