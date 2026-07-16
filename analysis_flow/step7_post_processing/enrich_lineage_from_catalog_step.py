"""enrich_lineage_from_catalog_step — DBMS only.

DBMS catalog ETL / 리니지 → ``ReadsWrites`` 엣지.

흐름:
1. ``ENV.catalog.base_url`` (운영 단일 진실) 로 HTTP — ETL flow 응답 받음.
2. flow 의 ``source`` (sql 파일 fqn / function_id) 를 코드의 실제 CodeNode 과 매칭.
3. 매칭된 CodeNode 의 ``ReadsWrites`` 엣지 생성. 매칭 실패 시 ``__catalog__:`` prefix 가상 노드.

분석기 영역 = HTTP 응답 처리. dataset 식별·인증은 catalog 영역 (URL 만 호출).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import httpx
from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import step_config

from graph.schema.graph_edges import ReadsWrites, derive_dml_type, normalize_relation
from shared.external.catalog_client import catalog_base_url_or_skip, get_catalog_json
from system.env_catalog import ENV
from shared.log.logger import log_info, log_warning
from graph.edges.reads_writes import upsert_reads_writes
from graph.neo4j_client import Neo4jClient

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

    # 코드노드 매칭 인덱스: 절대 경로 / 상대 경로 / code_id 모두로 lookup
    fn_index = _build_code_index(state.get("code_nodes", []), Path(project.root))

    edges: list[ReadsWrites] = []
    for flow in data.get("flows", []) or []:
        # source = sql 파일 경로 또는 procedure fqn (catalog 응답 형식)
        source = flow.get("source") or flow.get("source_file") or flow.get("function_id")
        fn_id = _resolve_code_id(source, fn_index) or f"__catalog__:{source}"

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


def _build_code_index(code_nodes: list, project_root: Path) -> dict[str, str]:
    """id / file 경로 다양한 변형 → 코드노드 id 매핑."""
    index: dict[str, str] = {}
    for fn in code_nodes:
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


def _path_suffix_match(key: str, source: str) -> bool:
    """``key`` 가 ``source`` 로 끝나는가 — **경로 세그먼트 경계**를 존중 (spec 045 FR-017).

    옛 구현은 맨 ``str.endswith`` 라 ``"a.sql"`` 이 ``".../xxa.sql"`` 에 매칭됐다 →
    엉뚱한 코드노드에 R/W 엣지가 붙는 **조용한 오답**. 경계를 강제해 차단한다.
    (``"x/a.sql".endswith("a.sql")`` 는 참이되 ``"x/xxa.sql"`` 은 거짓.)
    """
    k = key.replace("\\", "/")
    s = source.replace("\\", "/").lstrip("/")
    if not s:
        return False
    return k == s or k.endswith("/" + s)


def _resolve_code_id(source: str | None, index: dict[str, str]) -> str | None:
    """catalog source → 실제 code_id (없으면 None).

    정확 일치 우선. 그다음 **경로 세그먼트 경계**를 지키는 접미 매칭(절대/상대 경로 차이 흡수).
    후보가 **여럿이면 추측하지 않고** 경고 후 드롭한다 (FR-018) — 하나를 골라 붙이면
    그래프가 거짓을 말하고 아무도 모른다.
    """
    if not source:
        return None
    if source in index:
        return index[source]

    hits = {
        fid
        for key, fid in index.items()
        if _path_suffix_match(key, source) or _path_suffix_match(source, key)
    }
    if not hits:
        return None
    if len(hits) > 1:
        log_warning(
            _STEP, "lineage_ambiguous_source",
            f"catalog lineage 의 source 가 코드노드 {len(hits)}개에 해당 — "
            "추측 금지, 이 lineage 는 드롭한다.",
            source=source, candidates=sorted(hits)[:5],
        )
        return None
    return next(iter(hits))
