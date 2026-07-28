"""enrich_table_description_step — 테이블 description 통합 LLM 호출.

흐름:
1. ``step7_analyze_code`` 의 ``tableInsights`` (``CodeNode.extra["table_insights"]``) 누적.
2. ``table_fqn`` 별로 candidates (모든 코드노드 관점의 role + columns) 모음.
3. ``llm/prompts/<strategy>/table_description.yaml`` LLM 호출 → 통합 description.
4. Table/Column.analyzed_description Neo4j SET — DDL/카탈로그 1차 description 과 격리.

LLM 호출 = 테이블당 1 회 (병렬, ``max_concurrency``).
"""
from __future__ import annotations

import json
import hashlib
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Literal, Optional

from langchain_core.runnables import RunnableConfig
from pydantic import Field, create_model, model_validator

from pipeline.execution.analysis_handoff import (
    POSTPROCESS_PRIORITY,
    AnalysisHandoffAborted,
    handoff_of,
)
from pipeline.execution.step_config import step_config
from pipeline.execution.pause_controller import PauseController
from pipeline.steps.step8_post_processing.handoff_skip import log_handoff_skip

from llm.usage.cost_budget import BudgetExceededError
from llm.fanout import gather_with_warmup, partition_llm_results
from pipeline.models.code_analysis_output import ColumnInsight, TableDescription
from llm.runners.yaml_llm_runner import run_yaml_rule
from shared.events.runtime_logger import log_error, log_info, log_warning
from shared.events.processing_events import step_processing_scope, track_processing
from graph.neo4j_client import Neo4jClient
from graph.nodes.column import update_analyzed_descriptions as update_column_analyzed
from graph.nodes.table import update_analyzed_descriptions as update_table_analyzed
from graph.schema.node_ids import fqn
from pipeline.steps.step8_post_processing.table_logical_names import (
    build_consolidated_column_updates,
)
from pipeline.steps.step7_analyze_code.concern_retry import run_concern_with_retry

_STEP = "step8.enrich_table_description"


async def enrich_table_description_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream = cfg.get("stream")
    pause: PauseController = cfg.get("pause") or PauseController()
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    # ``table_insights`` 는 ``constrain_tables_to_rw`` 가 rules 와 함께 정리하므로 노드
    # **완료** 시점에만 확정된다 — 그래서 #1 이 아니라 전 노드 완료를 기다린다(spec 096).
    # 7콜뿐이라 이 게이트가 늦어도 손해가 없다.
    handoff = handoff_of(cfg)
    try:
        await handoff.analysis()
    except AnalysisHandoffAborted:
        log_handoff_skip(_STEP, "테이블 설명", pause)
        return {}

    code_nodes = handoff.merge(state.get("code_nodes", []))
    source_tables = state.get("tables", [])
    if not source_tables:
        log_info(_STEP, "skip", reason="no parsed DDL tables")
        return {}
    started = time.monotonic()

    # table_fqn → [{usage_summary, role}, ...] — 함수 전체 summary를 각 테이블에 복사하지 않는다.
    table_candidates: dict[str, list[dict]] = defaultdict(list)
    # table_fqn → column_name → 여러 코드노드 관점 후보. 첫 후보 승리(setdefault)를 금지하고
    # 원래 DDL 설명과 함께 테이블 통합 호출이 의미를 확정한다.
    column_candidates: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for fn in code_nodes:
        for insight in fn.extra.get("table_insights", []) or []:
            table = insight.get("table") or ""
            if not table:
                continue
            table_candidates[table].append({
                "usage_summary": (insight.get("usage_summary") or "").strip(),
                "role": insight.get("role", ""),
            })
            for col in insight.get("columns", []) or []:
                cname = col.get("name")
                if not cname:
                    continue
                candidate = {
                    "logical_name": (col.get("logical_name") or "").strip(),
                    "description": (col.get("description") or "").strip(),
                }
                if candidate not in column_candidates[table][cname]:
                    column_candidates[table][cname].append(candidate)

    source_columns_by_id = {column.id: column for column in state.get("columns", [])}
    source_columns_by_table = _source_columns_by_table(state, source_columns_by_id)
    source_tables_by_id = {table.id: table for table in source_tables}
    table_fqns = _table_target_ids(source_tables)
    direct_operations = _direct_operations_by_table(state)
    allowed_column_names: dict[str, set[str]] = {}
    prompt_evidence: dict[str, dict] = {}
    for table in table_fqns:
        usages = table_candidates.get(table, [])
        # DDL HAS_COLUMN이 최종 컬럼 집합의 단일 진실이다. step7 LLM 후보가 컬럼을
        # 누락하거나 추가해도 최종 prompt의 컬럼 소유권을 바꾸지 않는다.
        source_columns = source_columns_by_table.get(table, [])
        ordered_names = [column.name for column in source_columns]
        allowed_column_names[table] = set(ordered_names)
        columns = []
        for name in ordered_names:
            source = source_columns_by_id.get(fqn(table, name))
            columns.append({
                "name": name,
                "source_logical_name": getattr(source, "logical_name", "") if source else "",
                "source_description": getattr(source, "description", "") if source else "",
                "source_comment": getattr(source, "comment", "") if source else "",
                "candidates": column_candidates.get(table, {}).get(name, []),
            })
        prompt_evidence[table] = {
            "table": _source_table_evidence(source_tables_by_id[table]),
            "table_usages": usages,
            "direct_operations": direct_operations.get(table, []),
            "columns": columns,
        }

    log_info(
        _STEP, "start",
        tables=len(table_fqns),
        code_insights=sum(len(v) for v in table_candidates.values()),
        column_descriptions=sum(len(v) for v in column_candidates.values()),
    )
    prompts_dir = Path(strategy.prompts_dir)
    # run 공유 슬롯 — step7 과 같은 세마포어를 낮은 우선순위로 쓴다 (spec 096).
    slots = handoff.slots

    async def _summarize_one(table_fqn: str, evidence: dict) -> TableDescription:
        """동일 요청을 최대 두 번 시도하고 성공값 하나만 반환한다.

        재시도는 스키마·전송·반복출력 실패에만 반응한다. 첫 응답을 재작성하거나 다른
        의미 필드로 보정·병합하지 않는다. 두 번 모두 실패하면 기존 fan-out fail 정책이
        해당 테이블을 명시적 실패로 남긴다.
        """
        required_names = tuple(column["name"] for column in evidence.get("columns", []))
        response_model = _table_description_response_model(required_names)

        async def _call() -> TableDescription:
            async with track_processing("Table", table_fqn):
                async with slots.at(POSTPROCESS_PRIORITY):
                    return await run_yaml_rule(
                        prompts_dir=prompts_dir,
                        rule_name="table_description",
                        user_values={
                            "table_fqn": table_fqn,
                            "candidates_json": json.dumps(evidence, ensure_ascii=False, indent=2),
                        },
                        response_model=response_model,
                        budget=cost_budget,
                    )

        attempt = await run_concern_with_retry(
            component=_STEP,
            concern="table_description",
            target_id=table_fqn,
            call=_call,
        )
        if attempt.succeeded:
            assert attempt.value is not None
            return attempt.value
        assert attempt.error is not None
        raise attempt.error

    # 첫 테이블 1건으로 prefix 캐시 워밍 → 나머지 병렬. fail 정책 = 개별 테이블 LLM 실패 시
    # 로그 + 결과 drop (분석 흐름 유지). BudgetExceededError 만 즉시 전파.
    async def _run_fanout() -> list:
        return await gather_with_warmup(
            [_summarize_one(t, prompt_evidence[t]) for t in table_fqns],
            return_exceptions=True,
        )

    with step_processing_scope(_STEP, stream):
        raw_results = await pause.run_restartable_during_pause(_run_fanout)
    results = _apply_table_fail_policy(table_fqns, raw_results)

    # 분석 합성 설명/논리명 → 노드에 SET. ★id = 원본 table_fqn(= 노드 id, 결정론) 직접 사용.
    # LLM 이 반환한 db/schema/name 으로 id 를 재조립하면 오파싱(name='TB' 등)으로 불일치·유실
    # (컬럼 경로와 동일하게 원본 fqn 사용 — spec 024). r 은 설명·논리명 내용만 쓴다.
    table_updates = [
        {"id": table_fqn, "logical_name": r.logical_name, "analyzed_description": r.description}
        for table_fqn, r in zip(table_fqns, results) if r is not None
    ]
    if neo is not None and table_updates:
        matched = await update_table_analyzed(neo, table_updates)
        _warn_unmatched("테이블", table_updates, matched)

    # 여러 함수 후보 중 첫 값이 아니라, 원래 schema 설명까지 함께 본 테이블별 통합 결과만
    # 컬럼에 반영한다. 허용 목록 밖/중복은 결정론적으로 거부하고 누락을 복사로 채우지 않는다.
    column_updates = build_consolidated_column_updates(
        table_fqns, results, allowed_column_names,
    )
    if neo is not None and column_updates:
        matched = await update_column_analyzed(neo, column_updates)
        _warn_unmatched("컬럼", column_updates, matched)

    # ★ logical_name 도 함께 반영 (FR-013) — Neo4j 에만 넣고 state 를 안 고치면
    #   artifact(ast.json)엔 빈값이 남아 그래프와 다른 사실을 말한다.
    updated_tables = _replace_by_id(
        state.get("tables", []), table_updates, "analyzed_description", "logical_name",
    )
    updated_columns = _replace_by_id(
        state.get("columns", []), column_updates, "analyzed_description", "logical_name",
    )

    log_info(
        _STEP, "done",
        tables=len(table_fqns),
        descriptions=len(table_updates), columns=len(column_updates),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"tables": updated_tables, "columns": updated_columns}


def _source_table_evidence(table: object) -> dict[str, str]:
    """DDL 소유 테이블의 원래 의미 필드만 prompt evidence로 직렬화한다."""
    return {
        "source_logical_name": str(getattr(table, "logical_name", "") or ""),
        "source_description": str(getattr(table, "description", "") or ""),
        "source_comment": str(getattr(table, "comment", "") or ""),
        "table_type": str(getattr(table, "table_type", "") or ""),
    }


def _table_target_ids(source_tables: list[object]) -> list[str]:
    """parsed DDL 테이블 순서를 보존한 table-level 합성 대상 집합."""
    return list(dict.fromkeys(str(getattr(table, "id")) for table in source_tables))


def _direct_operations_by_table(state: dict) -> dict[str, list[dict]]:
    """결정론 R/W 엣지를 테이블별 중복 없는 직접 사용 근거로 만든다."""
    result: dict[str, list[dict]] = defaultdict(list)
    seen: dict[str, set[tuple]] = defaultdict(set)
    edges = sorted(
        state.get("reads_writes_edges", []),
        key=lambda edge: (
            getattr(edge, "target_id", ""),
            getattr(edge, "evidence_file", ""),
            getattr(edge, "evidence_line", 0),
            getattr(edge, "source_id", ""),
            getattr(edge, "relation", ""),
            getattr(edge, "dml_type", ""),
        ),
    )
    for edge in edges:
        table_id = getattr(edge, "target_id", "")
        if not table_id:
            continue
        identity = (
            getattr(edge, "source_id", ""),
            getattr(edge, "relation", ""),
            getattr(edge, "dml_type", ""),
            getattr(edge, "evidence_file", ""),
            getattr(edge, "evidence_line", 0),
        )
        if identity in seen[table_id]:
            continue
        seen[table_id].add(identity)
        result[table_id].append({
            "source_id": identity[0],
            "relation": identity[1],
            "dml_type": identity[2],
            "evidence_file": identity[3],
            "evidence_line": identity[4],
        })
    return dict(result)


def _source_columns_by_table(
    state: dict,
    source_columns_by_id: dict[str, object],
) -> dict[str, list[object]]:
    """HAS_COLUMN 순서대로 table_id → DDL Column 목록을 만든다.

    Column id를 잘라 부모를 추측하지 않는다. 관계가 소유권의 단일 진실이며, 중복 엣지는
    첫 항목만 보존한다.
    """
    result: dict[str, list[object]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)
    for edge in state.get("has_column_edges", []):
        table_id = getattr(edge, "parent_id", "")
        column_id = getattr(edge, "child_id", "")
        column = source_columns_by_id.get(column_id)
        if not table_id or column is None or column_id in seen[table_id]:
            continue
        seen[table_id].add(column_id)
        result[table_id].append(column)
    return dict(result)


def _table_description_response_model(required_names: tuple[str, ...]):
    """테이블별 허용 컬럼명과 정확 개수를 structured-output schema에 넣는다.

    의미 판정은 LLM이 하되, DDL로 이미 확정된 이름·개수·비어 있지 않은 설명은 스키마가
    보장한다. 누락을 sibling 후보 복사로 메우는 후처리는 없다.
    """
    fingerprint = hashlib.sha256("\0".join(required_names).encode("utf-8")).hexdigest()[:12]
    if required_names:
        allowed_name = Literal.__getitem__(required_names)
        column_model = create_model(
            f"GroundedColumn_{fingerprint}",
            __base__=ColumnInsight,
            name=(allowed_name, Field(..., description="DDL HAS_COLUMN 허용 이름 중 하나")),
            logical_name=(str, Field(..., min_length=1, description="사용자용 컬럼 표시명")),
            description=(str, Field(..., min_length=1, description="근거 기반 컬럼 의미")),
        )
        column_field = Field(
            ...,
            min_length=len(required_names),
            max_length=len(required_names),
            description="DDL 허용 컬럼을 누락·중복 없이 정확히 한 번씩 반환",
        )
    else:
        column_model = ColumnInsight
        column_field = Field(default_factory=list, max_length=0)

    expected = frozenset(required_names)

    def _exact_columns(self):
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)) or set(names) != expected:
            raise ValueError(
                f"columns must match the DDL allowlist exactly: expected={sorted(expected)!r}"
            )
        return self

    validator = model_validator(mode="after")(_exact_columns)
    return create_model(
        f"GroundedTableDescription_{fingerprint}",
        __base__=TableDescription,
        description=(str, Field(..., min_length=1, description="사용자용 테이블 의미")),
        columns=(list[column_model], column_field),
        __validators__={"_exact_columns": validator},
    )


def _apply_table_fail_policy(
    table_fqns: list[str], results: list,
) -> list[Optional[TableDescription]]:
    """fan-out 결과에 fail 정책 적용 — 예외면 None drop + log, BudgetExceededError 는 raise.

    분류 규칙 자체는 ``partition_llm_results`` 단일 정의처가 갖는다 (spec 046 FR-011).
    여기 정책 = ``on_failure="none"`` — 실패한 테이블은 **자리(None)를 남긴다**.
    호출부가 ``zip(table_fqns, results)`` 로 짝지으므로 길이가 맞아야 한다.
    """
    out, _failed = partition_llm_results(
        table_fqns, results,
        component="pipeline.steps.step8_post_processing.enrich_table_description_step",
        operation="table_description",
        id_of=lambda fqn: fqn,
        on_failure="none",
    )
    return out


def _warn_unmatched(kind: str, updates: list[dict], matched: int) -> None:
    """미반영 = LLM 설명의 id 가 실제 노드와 불일치(= 노드 아닌 대상) → 그 설명만 버려진다.
    실제 노드는 id 일치 시 채워지므로 이건 **컬럼 유실이 아니라 무해한 잉여**다. 따라서
    error 로 오해되지 않게 INFO(analyzer.log)에만 남긴다 — error.log(WARNING+) 에 안 띄움.
    (진짜 유실 = '쓰는 컬럼이 빈값'은 spec 027 컬럼 주입으로 예방. spec 024→027 정정.)
    """
    if matched < len(updates):
        log_info(
            _STEP, "analyzed_desc_unmatched",
            f"{kind} 분석설명 {len(updates) - matched}/{len(updates)}건 미반영"
            " (노드 아닌 대상 = 무해한 잉여, 컬럼 유실 아님)",
        )


def _replace_by_id(nodes: list, updates: list[dict], *attrs: str) -> list:
    """``updates`` 의 id → ``attrs`` 값들을 같은 노드에 박은 새 인스턴스 리스트.

    매치 안 된 노드는 그대로 유지 — state 키엔 reducer 가 없어(default replace) 전체
    리스트를 돌려줘야 다음 step·artifact 가 노드를 잃지 않는다.

    **여러 필드**를 받는다 (spec 045 FR-013): 옛 단일 필드 버전은 ``analyzed_description``
    만 되돌려 써서 ``logical_name`` 이 Neo4j 에만 들어가고 state/artifact 에는 빈값으로 남았다
    → 그래프와 artifact 가 다른 사실을 말하는 검증 사각지대. 빈값은 덮어쓰지 않는다
    (LLM 이 논리명을 못 낸 테이블의 기존 값을 지우지 않도록).
    """
    id_to_vals = {u["id"]: u for u in updates}
    return [
        replace(n, **{
            a: id_to_vals[n.id][a]
            for a in attrs
            if a in id_to_vals[n.id] and id_to_vals[n.id][a]
        })
        if n.id in id_to_vals else n
        for n in nodes
    ]
