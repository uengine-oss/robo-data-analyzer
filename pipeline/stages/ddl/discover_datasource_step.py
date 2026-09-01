"""데이터소스 테이블 발견 — 단어장 이원화(DDL ∪ 실DB) + 샘플 수집 통합 (spec 107).

데이터소스 HTTP 접점은 **이 step 한 곳**이다 (구 step6 ``fetch_table_samples_step`` 흡수):
catalog ``GET /tables/discovery`` 의 단일 snapshot 전체 page를 닫은 뒤 테이블·컬럼·샘플을 받아
- DDL 단어장에 **합집합** 병합(대소문자 무시 정확 일치, DDL 정의 우선 — DDL = 진실 기준),
- 신규 테이블의 Table/Column/HAS_COLUMN 전체 state를 생산하고,
- step7 이 소비하는 ``table_sample_data``(``{Table.id: {columns, sample_rows}}``) 를 생산한다.

가드 1벌: catalog 비활성 / datasource 미지정 → skip. 404(미등록) → 경고 후 DDL 단어장으로
계속. 그 외 오류 → raise (헌법 IV — 활성 상태의 실패는 심각).
계약: specs/001-analyzer-redesign/spec.md
"""
from __future__ import annotations

import time
from typing import Optional

import httpx
from langchain_core.runnables import RunnableConfig

from graph.schema.graph_edges import HasColumn
from graph.schema.graph_nodes import Column, Table
from graph.schema.node_ids import sql_column_id
from integrations.catalog_client import catalog_base_url_or_skip
from integrations.catalog_discovery import get_complete_table_discovery
from pipeline.execution.step_config import step_config
from pipeline.execution.graph_delta_stream import emit_message
from pipeline.models.sql_identifier import sql_identifier_key
from shared.config.app_settings import SETTINGS
from shared.events.processing_events import step_processing_scope
from shared.events.runtime_logger import log_info, log_warning

_STEP = "step1.discover_datasource"
_SAMPLE_LIMIT = 5      # 테이블당 샘플 행 수 (구 fetch_table_samples 와 동일 — 토큰 통제)
_PAGE_SIZE = 1000      # 전송 단위일 뿐 전체 모집단 상한이 아니다.


async def discover_datasource_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """한 snapshot의 전 page 폐쇄 → 단어장 병합 + ``table_sample_data`` 생산."""
    cfg = step_config(config)
    stream = cfg.get("stream")
    codebase = cfg.get("codebase")

    base_url = catalog_base_url_or_skip(_STEP)
    if base_url is None:
        return {}
    datasource = (getattr(codebase, "datasource", None) or "").strip()
    if not datasource:
        log_info(_STEP, "skip", reason="datasource 미지정")
        return {}

    started = time.monotonic()
    ddl_tables: list[Table] = list(state.get("tables") or [])
    ddl_columns: list[Column] = list(state.get("columns") or [])
    ddl_has_column_edges: list[HasColumn] = list(
        state.get("has_column_edges") or []
    )
    log_info(_STEP, "start", datasource=datasource, ddl_tables=len(ddl_tables))

    async with httpx.AsyncClient(timeout=SETTINGS.catalog_api.timeout_seconds) as http:
        with step_processing_scope(_STEP, stream):
            payload = await get_complete_table_discovery(
                http, base_url, datasource,
                sample_limit=_SAMPLE_LIMIT, page_size=_PAGE_SIZE,
            )

    if payload is None:
        # datasource 미등록 — 고객 환경에서 정상일 수 있으나 조용히 넘기지 않는다.
        log_warning(_STEP, "datasource_not_found",
                    "catalog/fabric 에 미등록 datasource — DDL 단어장만으로 진행",
                    datasource=datasource)
        emit_message(stream, f"⚠️ 데이터소스 '{datasource}' 미등록 — DDL 단어장만 사용")
        return {}

    merged, new_tables, new_columns, new_edges, sample_data = _merge(
        ddl_tables, payload, datasource,
    )
    degraded = sum(1 for t in payload.get("tables") or [] if t.get("degraded"))
    if degraded:
        log_warning(_STEP, "degraded",
                    f"{degraded}개 테이블의 컬럼/샘플 조회 부분실패 — 해당 항목만 비움")

    emit_message(
        stream,
        f"✅ 데이터소스 발견 — 실DB {len(payload.get('tables') or [])}개 "
        f"(신규 {len(new_tables)} · 샘플 {len(sample_data)})",
    )
    log_info(
        _STEP, "done",
        discovered=len(payload.get("tables") or []),
        new_tables=len(new_tables), merged_total=len(merged),
        samples=len(sample_data), degraded=degraded,
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "tables": merged,
        "columns": [*ddl_columns, *new_columns],
        "has_column_edges": [*ddl_has_column_edges, *new_edges],
        "table_sample_data": sample_data,
    }


def _merge(
    ddl_tables: list[Table], payload: dict, datasource: str,
) -> tuple[list[Table], list[Table], list[Column], list[HasColumn], dict]:
    """DDL ∪ 발견 병합 — 대소문자 무시 정확 일치, DDL 정의 우선 (fuzzy 없음 — 계약 명시).

    Returns: (병합 단어장, 신규 Table, 신규 Column, 신규 HAS_COLUMN, table_sample_data)
    """
    by_identity = {
        (
            sql_identifier_key(t.catalog, t.catalog_quoted),
            sql_identifier_key(t.schema, t.schema_quoted),
            sql_identifier_key(t.name, t.name_quoted),
        ): t
        for t in ddl_tables
    }
    merged = list(ddl_tables)
    new_tables: list[Table] = []
    new_columns: list[Column] = []
    new_edges: list[HasColumn] = []
    sample_data: dict[str, dict] = {}

    for item in payload.get("tables") or []:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        schema = (item.get("schema") or "").strip()
        catalog = (item.get("catalog") or "").strip()
        catalog_quoted = bool(item.get("catalog_quoted", False))
        schema_quoted = bool(item.get("schema_quoted", False))
        name_quoted = bool(item.get("name_quoted", False))
        identity = (
            sql_identifier_key(catalog, catalog_quoted),
            sql_identifier_key(schema, schema_quoted),
            sql_identifier_key(name, name_quoted),
        )
        existing = by_identity.get(identity)
        if existing is not None:
            table = existing                     # DDL 정의 우선 — 샘플만 보강
        else:
            table = Table(
                db=datasource,
                catalog=catalog,
                schema=schema,
                name=name,
                catalog_quoted=catalog_quoted,
                schema_quoted=schema_quoted,
                name_quoted=name_quoted,
                description_source="catalog",    # 출처 추적 — DDL 유래와 구분 (FR-005)
            )
            by_identity[identity] = table
            merged.append(table)
            new_tables.append(table)
            for col in item.get("columns") or []:
                col_name = (col.get("name") or "").strip()
                if not col_name:
                    continue
                column = Column(
                    id=sql_column_id(
                        table.id, col_name, bool(col.get("name_quoted", False)),
                    ),
                    name=col_name,
                    name_quoted=bool(col.get("name_quoted", False)),
                    dtype=str(col.get("dtype") or col.get("type") or ""),
                    nullable=bool(col.get("nullable", True)),
                    description_source="catalog",
                )
                new_columns.append(column)
                new_edges.append(HasColumn(parent_id=table.id, child_id=column.id))

        columns = item.get("columns") or []
        rows = item.get("sample_rows") or []
        if columns or rows:
            sample_data[table.id] = {"columns": columns, "sample_rows": rows}

    return merged, new_tables, new_columns, new_edges, sample_data
