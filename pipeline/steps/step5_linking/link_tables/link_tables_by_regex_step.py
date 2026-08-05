"""stage1_regex_grep — ``ScanResult.table_access_patterns`` 로 코드 grep → R/W 엣지.

LLM 0회 / Agent 0회. 매칭 안 된 케이스는 ``unresolved_cases`` 큐에 누적
(stage3_residual_patterns 의 잔여 패턴 루프가 처리, Framework 전용).

정규식은 step4 Scan이 **검증을 통과시킨** 것만 넘어온다 — 그래도 패턴이 있는데
엣지 0건이면 정규식이 소스와 안 맞는 것이므로 ``log_warning`` 으로 표면화한다.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import step_config

from agents.rule_compiler import compile_exclusion_rules, split_rules
from pipeline.steps.step4_scan_project.scan_result_contract import require_scan_result
from pipeline.steps.step5_linking.link_tables.regex_table_linker import RegexTableLinker
from pipeline.steps.step5_linking.link_tables.view_expansion import expand_view_edges
from shared.events.runtime_logger import log_info, log_warning
from graph.edges.reads_writes import upsert_reads_writes
from graph.neo4j_client import Neo4jClient
from pipeline.execution.graph_delta_stream import emit_edges, emit_message

_STEP = "step5.link_deterministic"


async def link_deterministic_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """결정론 grep → ``ReadsWrites`` 엣지 + ``unresolved`` 큐."""
    cfg = step_config(config)
    project = cfg.get("codebase")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}

    scan = require_scan_result(state)

    code_nodes_in = state.get("code_nodes") or []
    tables_in = state.get("tables") or []
    patterns = scan.table_access_patterns
    # embedded_sql 규칙 (spec 115) — 정규식으로 컴파일되지 않는 울타리 블록.
    # 링커가 블록을 추출해 DML 의미표로 직접 판정한다.
    _, embedded = split_rules(list(scan.table_access_rules))
    exclusions = compile_exclusion_rules(scan.table_access_exclusion_rules)
    started = time.monotonic()
    log_info(
        _STEP, "start",
        root=str(project.root),
        patterns=len(patterns), embedded_rules=len(embedded), exclusions=len(exclusions),
        code_nodes=len(code_nodes_in), tables=len(tables_in),
    )

    outcome = await RegexTableLinker().link(
        project_root=project.root,
        code_nodes=code_nodes_in,
        tables=tables_in,
        patterns=patterns,
        ddl_transform=scan.ddl_transform,
        embedded_rules=embedded,
        exclusion_patterns=exclusions,
    )

    # 뷰 펼치기 (spec 112) — VIEW 엣지에 기저 테이블 엣지 추가 (원본 유지).
    view_edges = expand_view_edges(outcome.edges, tables_in)
    if view_edges:
        log_info(_STEP, "view_expansion", added=len(view_edges))
        outcome.edges.extend(view_edges)

    # raw 중복 (같은 코드노드·테이블·동작을 여러 라인에서 매칭) 은 Neo4j MERGE 가 자동
    # dedup — state 의 list 는 raw 그대로. 다른 step 이 set 으로 변환해 사용.
    if neo is not None and outcome.edges:
        await upsert_reads_writes(neo, outcome.edges)

    # 라이브 스트림 — 결정론 R/W 링킹 결과를 step 완료 즉시 화면으로.
    stream = cfg.get("stream")
    await emit_edges(stream, outcome.edges)
    emit_message(stream, f"✅ 결정론 R/W 링킹 완료 — {len(outcome.edges)}건")

    # 패턴은 있는데 R/W 엣지 0 — 같은 증상이라도 처방이 다르므로 원인 3종을 가른다.
    if patterns and not outcome.edges:
        if outcome.matched_sites == 0:
            diagnosis = ("정규식이 소스의 어떤 호출과도 안 맞음 — "
                         "step4 Scan 검증을 통과한 패턴인지 확인 필요")
        elif outcome.partial_entities >= outcome.matched_sites:
            diagnosis = ("매치는 전건 성공했으나 완전성 검사가 전건을 부분추출로 탈락 — "
                         "패턴 구조(꼬리 소비형)와 검사 위치 충돌 의심")
        else:
            diagnosis = ("매치는 있으나 entity 가 DDL 테이블로 해소되지 않음 — "
                         "DDL 누락 또는 ddl_transform 규칙 확인 필요")
        log_warning(
            _STEP, "no_edges", diagnosis,
            patterns=len(patterns), matched_sites=outcome.matched_sites,
            partial_entities=outcome.partial_entities,
        )

    log_info(
        _STEP, "done",
        reads_writes_edges=len(outcome.edges),
        unresolved_cases=len(outcome.unresolved),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "reads_writes_edges": outcome.edges,
        "unresolved_cases": outcome.unresolved,
    }
