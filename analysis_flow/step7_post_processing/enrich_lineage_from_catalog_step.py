"""enrich_lineage_from_catalog_step — DBMS only.

DBMS catalog ETL / 리니지 → ``ReadsWrites`` 엣지.

흐름:
1. ``ENV.catalog.base_url`` (운영 단일 진실) 로 HTTP — ETL flow 응답 받음.
2. flow 의 ``source`` (sql 파일 fqn / function_id) 를 코드의 실제 Function 과 매칭.
3. 매칭된 Function 의 ``ReadsWrites`` 엣지 생성. 매칭 실패 시 ``__catalog__:`` prefix 가상 노드.

분석기 영역 = HTTP 응답 처리. dataset 식별·인증은 catalog 영역 (URL 만 호출).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import httpx
from langchain_core.runnables import RunnableConfig

from shared.workflow.step_utils import step_config

from data_models.graph_edges import ReadsWrites, derive_dml_type, normalize_relation
from shared.external.catalog_client import catalog_base_url_or_skip, get_catalog_json
from shared.config.env_catalog import ENV
from shared.log.logger import log_info, log_warning
from shared.neo4j.edges.reads_writes import upsert_reads_writes
from shared.neo4j.neo4j_client import Neo4jClient

_STEP = "step7.enrich_lineage_from_catalog"


async def enrich_lineage_from_catalog_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    project = cfg.get("codebase")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}
    base_url = catalog_base_url_or_skip(_STEP)
    if base_url is None:
        return {}

    started = time.monotonic()
    log_info(_STEP, "start", catalog_url=ENV.catalog.base_url)

    async with httpx.AsyncClient(timeout=ENV.catalog.timeout_seconds) as http:
        url = f"{base_url}/lineage"
        # non-200/파싱 실패는 전파. 404 만 정상(None).
        data = await get_catalog_json(http, url, label="lineage")
    if data is None:
        # 404 — catalog 에 lineage 없음 (실패 아님).
        return {}

    # 함수 매칭 인덱스: 절대 경로 / 상대 경로 / function_id 모두로 lookup
    fn_index = _build_function_index(state.get("functions", []), Path(project.root))

    edges: list[ReadsWrites] = []
    for flow in data.get("flows", []) or []:
        # source = sql 파일 경로 또는 procedure fqn (catalog 응답 형식)
        source = flow.get("source") or flow.get("source_file") or flow.get("function_id")
        fn_id = _resolve_function_id(source, fn_index) or f"__catalog__:{source}"

        table_fqn = flow.get("table_fqn") or flow.get("to_table") or flow.get("from_table")
        if not table_fqn:
            continue
        relation = normalize_relation(flow.get("relation") or "READS")
        if relation is None:
            continue

        # catalog 응답에 dml_type 이 있으면 표준화 (없으면 빈값 — 단일 진실 헬퍼).
        edges.append(ReadsWrites(
            source_id=fn_id,
            target_id=table_fqn,
            relation=relation,
            dml_type=derive_dml_type(flow.get("dml_type")),
            evidence_file=str(source or "catalog"),
            evidence_line=0,
        ))

    if neo is not None and edges:
        await upsert_reads_writes(neo, edges)

    catalog_only = sum(1 for e in edges if e.source_id.startswith("__catalog__:"))
    log_info(_STEP, "done",
             flows=len(data.get("flows") or []),
             reads_writes_edges=len(edges),
             catalog_only=catalog_only or None,
             elapsed_sec=f"{time.monotonic() - started:.2f}")
    # state 반영 — Neo4j MERGE 가 같은 (fn, table, relation) 키로 자동 dedup.
    # catalog 발견 R/W 가 artifact dump 에도 같이 나온다 (raw 그대로).
    return {"reads_writes_edges": edges}


def _build_function_index(functions: list, project_root: Path) -> dict[str, str]:
    """id / file 경로 다양한 변형 → 함수 id 매핑."""
    index: dict[str, str] = {}
    for fn in functions:
        index[fn.id] = fn.id
        index[fn.file_path] = fn.id
        try:
            abs_path = str(Path(fn.file_path).resolve())
            index[abs_path] = fn.id
            rel_path = str(Path(fn.file_path).resolve().relative_to(project_root.resolve()))
            index[rel_path] = fn.id
        except (ValueError, OSError):
            pass
    return index


def _resolve_function_id(source: str | None, index: dict[str, str]) -> str | None:
    """catalog source → 실제 function_id (없으면 None)."""
    if not source:
        return None
    if source in index:
        return index[source]
    # suffix 매칭 (절대/상대 경로 차이)
    for key, fid in index.items():
        if key.endswith(source) or source.endswith(key):
            return fid
    return None
