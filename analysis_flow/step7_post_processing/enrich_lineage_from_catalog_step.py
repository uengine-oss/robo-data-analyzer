"""enrich_lineage_from_catalog_step — DBMS only.

DBMS catalog ETL / 리니지 → ``ReadsWrites`` 엣지.

흐름:
1. ``project.catalog_service_url`` 로 HTTP — ETL flow 응답 받음.
2. flow 의 ``source`` (sql 파일 fqn / function_id) 를 코드의 실제 Function 과 매칭.
3. 매칭된 Function 의 ``ReadsWrites`` 엣지 생성. 매칭 실패 시 ``__catalog__:`` prefix 가상 노드.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx
from langchain_core.runnables import RunnableConfig

from data_models.graph_edges import ReadsWrites
from shared.logging_and_stream.logger import log_warning
from shared.neo4j.edges.reads_writes import upsert_reads_writes
from shared.neo4j.neo4j_client import Neo4jClient


async def enrich_lineage_from_catalog_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    project = cfg.get("project")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None or not getattr(project, "catalog_service_url", None):
        return {}

    base_url = project.catalog_service_url.rstrip("/")
    headers = {}
    if project.catalog_api_key:
        headers["Authorization"] = f"Bearer {project.catalog_api_key}"

    async with httpx.AsyncClient(timeout=120.0) as http:
        url = f"{base_url}/lineage/{project.name}"
        try:
            resp = await http.get(url, headers=headers)
        except httpx.HTTPError as e:
            log_warning(
                "analysis_flow.step7_post_processing.enrich_lineage_from_catalog_step",
                "catalog_fetch",
                "catalog lineage HTTP 오류 — lineage enrichment 건너뜀",
                project=project.name,
                error=f"{type(e).__name__}: {e}",
            )
            return {}
        # 404 = 정상 (이 프로젝트는 catalog 에 lineage 없음)
        if resp.status_code == 404:
            return {}
        if resp.status_code != 200:
            log_warning(
                "analysis_flow.step7_post_processing.enrich_lineage_from_catalog_step",
                "catalog_fetch",
                "catalog non-200 응답 — lineage enrichment 건너뜀",
                project=project.name,
                status=resp.status_code,
            )
            return {}
        try:
            data = resp.json()
        except ValueError as e:
            log_warning(
                "analysis_flow.step7_post_processing.enrich_lineage_from_catalog_step",
                "catalog_fetch",
                "catalog 응답 JSON 파싱 실패",
                project=project.name,
                error=str(e),
            )
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
        relation = (flow.get("relation") or "READS").upper()
        if relation == "READ":
            relation = "READS"
        elif relation == "WRITE":
            relation = "WRITES"
        if relation not in ("READS", "WRITES"):
            continue

        edges.append(ReadsWrites(
            function_id=fn_id,
            table_fqn=table_fqn,
            relation=relation,
            evidence_file=str(source or "catalog"),
            evidence_line=0,
        ))

    if neo is not None and edges:
        await upsert_reads_writes(neo, edges)
    return {}


def _build_function_index(functions: list, project_root: Path) -> dict[str, str]:
    """function_id / file 경로 다양한 변형 → function_id 매핑."""
    index: dict[str, str] = {}
    for fn in functions:
        index[fn.function_id] = fn.function_id
        index[fn.file_path] = fn.function_id
        try:
            abs_path = str(Path(fn.file_path).resolve())
            index[abs_path] = fn.function_id
            rel_path = str(Path(fn.file_path).resolve().relative_to(project_root.resolve()))
            index[rel_path] = fn.function_id
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
