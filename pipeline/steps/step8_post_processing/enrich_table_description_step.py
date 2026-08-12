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
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.analysis_handoff import (
    POSTPROCESS_PRIORITY,
    AnalysisHandoffAborted,
    handoff_of,
)
from pipeline.execution.step_config import step_config
from pipeline.execution.pause_controller import PauseController
from pipeline.steps.step8_post_processing.handoff_skip import log_handoff_skip

from llm.fanout import gather_with_warmup
from pipeline.models.code_analysis_output import TableDescription
from pipeline.models.table_description_frame import (
    build_table_description_plans,
    normalize_table_description_frame_response,
    table_description_frame_response_model,
)
from pipeline.models.semantic_frame import profile_prompt_json, slot_specs_json
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.model_output_error import ModelOutputError
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.execution.quality_finding_log import record_analysis_findings
from shared.events.runtime_logger import log_info, log_warning
from shared.events.processing_events import step_processing_scope, track_processing
from graph.neo4j_client import Neo4jClient
from graph.nodes.column import update_analyzed_descriptions as update_column_analyzed
from graph.nodes.table import update_analyzed_descriptions as update_table_analyzed
from graph.schema.node_ids import fqn
from pipeline.steps.step8_post_processing.table_logical_names import (
    build_consolidated_column_updates,
)

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

    # ``table_insights`` 는 direct R/W required-key schema와 rules가 모두 끝난 노드에서만
    # 확정된다 — 그래서 #1 이 아니라 전 노드 완료를 기다린다(spec 096/116 §39).
    # 호출 대상은 아래에서 결정론 R/W 테이블로 제한한다.
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

    table_candidates, column_candidates = _collect_usage_candidates(code_nodes)

    source_columns_by_id = {column.id: column for column in state.get("columns", [])}
    source_columns_by_table = _source_columns_by_table(state, source_columns_by_id)
    direct_operations = _direct_operations_by_table(state)
    source_tables_by_id = {table.id: table for table in source_tables}
    table_fqns = _table_target_ids(source_tables, direct_operations)
    candidate_only = set(table_candidates) - set(direct_operations)
    if candidate_only:
        raise ValueError(
            "table insight has no deterministic READS/WRITES owner: "
            f"{sorted(candidate_only)!r}"
        )
    if not table_fqns:
        log_info(_STEP, "skip", reason="no deterministically accessed DDL tables")
        return {}
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
                "candidates": _focus_column_candidates(
                    column_candidates.get(table, {}).get(name, []),
                    target_name=name,
                    allowed_names=ordered_names,
                ),
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

    async def _summarize_one(
        table_fqn: str, evidence: dict,
    ) -> TableDescription | ModelOutputError:
        """테이블 의미를 한 번 요청한다. transport 재시도는 runner만 소유한다."""
        table_plan, column_plans = build_table_description_plans(table_fqn, evidence)
        response_model = table_description_frame_response_model(
            evidence, table_plan, column_plans,
        )

        async def _call() -> TableDescription:
            async with track_processing("Table", table_fqn):
                async with slots.at(POSTPROCESS_PRIORITY):
                    return await run_yaml_rule(
                        prompts_dir=prompts_dir,
                        rule_name="table_description",
                        user_values={
                            "table_fqn": table_fqn,
                            "candidates_json": json.dumps(evidence, ensure_ascii=False, indent=2),
                            "table_structural_profile": profile_prompt_json(table_plan[0]),
                            "table_applicable_slots": slot_specs_json(table_plan[1]),
                            "column_frame_assignments": json.dumps(
                                {
                                    name: {
                                        "structural_profile": profile.model_dump(
                                            mode="json", by_alias=True,
                                        ),
                                        "applicable_slots": [
                                            spec.model_dump(mode="json") for spec in specs
                                        ],
                                    }
                                    for name, (profile, specs) in column_plans.items()
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        },
                        response_model=response_model,
                        budget=cost_budget,
                    )

        try:
            wire = await _call()
            return normalize_table_description_frame_response(
                wire, table_plan, column_plans,
            )
        except ModelOutputError as error:
            return error

    # 첫 테이블 1건으로 prefix 캐시 워밍 → 나머지 병렬. 어떤 테이블도 실패를 값으로
    # 바꾸거나 drop하지 않는다.
    async def _run_fanout() -> list:
        return await gather_with_warmup(
            [_summarize_one(t, prompt_evidence[t]) for t in table_fqns],
        )

    with step_processing_scope(_STEP, stream):
        results = await pause.run_restartable_during_pause(_run_fanout)

    successful: list[tuple[str, TableDescription]] = []
    findings: list[AnalysisFinding] = []
    for table_fqn, result in zip(table_fqns, results):
        if isinstance(result, ModelOutputError):
            findings.append(AnalysisFinding(
                concern="table_description",
                status="unavailable",
                target_id=table_fqn,
                reasons=(str(result),),
                evidence_rule=result.rule_name,
            ))
        else:
            successful.append((table_fqn, result))
    if findings:
        record_analysis_findings(findings)
        log_warning(
            _STEP, "quality_findings",
            "일부 테이블 설명 후보를 검증할 수 없어 해당 테이블만 원본 상태로 보존함",
            count=len(findings), targets=[finding.target_id for finding in findings],
        )

    # 분석 합성 설명/논리명 → 노드에 SET. ★id = 원본 table_fqn(= 노드 id, 결정론) 직접 사용.
    # LLM 이 반환한 db/schema/name 으로 id 를 재조립하면 오파싱(name='TB' 등)으로 불일치·유실
    # (컬럼 경로와 동일하게 원본 fqn 사용 — spec 024). r 은 설명·논리명 내용만 쓴다.
    table_updates = [
        {
            "id": table_fqn,
            "logical_name": r.logical_name,
            "analyzed_description": r.description,
            "semantic_evidence_status": r.evidence_status,
            "semantic_missing_context": tuple(r.missing_context),
            "semantic_frame_schema": r.semantic_frame.schema_version,
            "semantic_frame_json": r.semantic_frame.canonical_json(),
        }
        for table_fqn, r in successful
    ]
    if neo is not None and table_updates:
        await update_table_analyzed(neo, table_updates)

    # 여러 함수 후보 중 첫 값이 아니라, 원래 schema 설명까지 함께 본 테이블별 통합 결과만
    # 컬럼에 반영한다. 허용 목록 밖/중복은 결정론적으로 거부하고 누락을 복사로 채우지 않는다.
    column_updates = build_consolidated_column_updates(
        [table_fqn for table_fqn, _ in successful],
        [result for _, result in successful],
        allowed_column_names,
    )
    column_frames = {
        fqn(table_fqn, name): frame
        for table_fqn, result in successful
        for name, frame in result.column_semantic_frames.items()
    }
    for update in column_updates:
        frame = column_frames[update["id"]]
        update["semantic_frame_schema"] = frame.schema_version
        update["semantic_frame_json"] = frame.canonical_json()
    if neo is not None and column_updates:
        await update_column_analyzed(neo, column_updates)

    # ★ logical_name 도 함께 반영 (FR-013) — Neo4j 에만 넣고 state 를 안 고치면
    #   artifact(ast.json)엔 빈값이 남아 그래프와 다른 사실을 말한다.
    updated_tables = _replace_by_id(
        state.get("tables", []), table_updates, "analyzed_description", "logical_name",
        "semantic_evidence_status", "semantic_missing_context",
        "semantic_frame_schema", "semantic_frame_json",
    )
    updated_columns = _replace_by_id(
        state.get("columns", []), column_updates, "analyzed_description", "logical_name",
        "semantic_evidence_status", "semantic_missing_context",
        "semantic_frame_schema", "semantic_frame_json",
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
        "db": str(getattr(table, "db", "") or ""),
        "schema": str(getattr(table, "schema", "") or ""),
        "name": str(getattr(table, "name", "") or ""),
        "source_logical_name": str(getattr(table, "logical_name", "") or ""),
        "source_description": str(getattr(table, "description", "") or ""),
        "source_comment": str(getattr(table, "comment", "") or ""),
        "table_type": str(getattr(table, "table_type", "") or ""),
    }


def _collect_usage_candidates(
    code_nodes: list[object],
) -> tuple[dict[str, list[dict]], dict[str, dict[str, list[dict[str, object]]]]]:
    """Aggregate table and column usage without dropping identity or source coordinates."""
    table_candidates: dict[str, list[dict]] = defaultdict(list)
    column_candidates: dict[str, dict[str, list[dict[str, object]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for fn in code_nodes:
        for insight in getattr(fn, "extra", {}).get("table_insights", []) or []:
            table = insight.get("table") or ""
            if not table:
                continue
            table_candidates[table].append({
                "relation": "CODE_NODE_USES_TABLE",
                "source_id": getattr(fn, "id", ""),
                "usage_summary": (insight.get("usage_summary") or "").strip(),
                "role": insight.get("role", ""),
            })
            for column in insight.get("columns", []) or []:
                name = column.get("name")
                if not name:
                    continue
                candidate = {
                    "relation": "CODE_NODE_USES_COLUMN",
                    "source_id": getattr(fn, "id", ""),
                    "table_role": insight.get("role", ""),
                    "ownership": column.get("ownership", ""),
                    "evidence_lines": list(column.get("evidence_lines") or ()),
                    "source_lines": list(column.get("source_lines") or ()),
                }
                if candidate not in column_candidates[table][name]:
                    column_candidates[table][name].append(candidate)
    return dict(table_candidates), {
        table: dict(columns) for table, columns in column_candidates.items()
    }


def _focus_column_candidates(
    candidates: list[dict[str, object]],
    *,
    target_name: str,
    allowed_names: list[str],
) -> list[dict[str, object]]:
    """Project raw coordinate evidence into one-column-at-a-time prompt text.

    The original file and absolute line remain the audit source.  The prompt projection labels
    the owned column and masks sibling DDL column identifiers so one source line cannot license
    semantic claims for a sibling candidate.  This creates no business meaning.
    """
    sibling_names = {
        str(name).casefold() for name in allowed_names
        if str(name).casefold() != str(target_name).casefold()
    }
    focused: list[dict[str, object]] = []
    for candidate in candidates:
        item = {key: value for key, value in candidate.items() if key != "source_lines"}
        item["source_lines"] = [
            {
                key: value for key, value in source_line.items() if key != "text"
            } | {
                "focus_text": _focus_column_source_text(
                    str(source_line.get("text") or ""),
                    target_name=target_name,
                    sibling_names=sibling_names,
                ),
            }
            for source_line in candidate.get("source_lines", []) or []
        ]
        focused.append(item)
    return focused


def _focus_column_source_text(
    text: str,
    *,
    target_name: str,
    sibling_names: set[str],
) -> str:
    """Label exact identifier tokens outside literals and discard comment-only suffixes."""
    target = str(target_name).casefold()
    result: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("--", index) or text.startswith("//", index):
            break
        if text.startswith("/*", index):
            closing = text.find("*/", index + 2)
            if closing < 0:
                break
            index = closing + 2
            continue
        char = text[index]
        if char == "'":
            end = index + 1
            while end < len(text):
                if text[end] == "'":
                    if end + 1 < len(text) and text[end + 1] == "'":
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
            result.append(text[index:end])
            index = end
            continue
        if char == '"':
            end = index + 1
            while end < len(text):
                if text[end] == '"':
                    if end + 1 < len(text) and text[end + 1] == '"':
                        end += 2
                        continue
                    break
                end += 1
            token = text[index + 1:end].replace('""', '"').casefold()
            replacement = (
                '"[TARGET_COLUMN]"' if token == target
                else '"[OTHER_COLUMN]"' if token in sibling_names
                else text[index:min(end + 1, len(text))]
            )
            result.append(replacement)
            index = min(end + 1, len(text))
            continue
        if char.isalpha() or char in "_$#":
            end = index + 1
            while end < len(text) and (text[end].isalnum() or text[end] in "_$#"):
                end += 1
            token = text[index:end].casefold()
            result.append(
                "[TARGET_COLUMN]" if token == target
                else "[OTHER_COLUMN]" if token in sibling_names
                else text[index:end]
            )
            index = end
            continue
        result.append(char)
        index += 1
    return "".join(result).strip()


def _table_target_ids(
    source_tables: list[object],
    direct_operations: dict[str, list[dict]],
) -> list[str]:
    """DDL 순서를 보존한 결정론 READS/WRITES 테이블 합성 대상."""
    accessed = set(direct_operations)
    return list(dict.fromkeys(
        str(getattr(table, "id"))
        for table in source_tables
        if str(getattr(table, "id")) in accessed
    ))


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


def _replace_by_id(nodes: list, updates: list[dict], *attrs: str) -> list:
    """Apply validated semantic updates while preserving unavailable targets.

    Model-output failures are recorded as findings and excluded from ``updates``.  Those nodes
    must remain byte-for-byte at their source state; one unavailable table cannot close the
    whole analysis.  Unknown or duplicate update IDs are still structural producer defects.

    **여러 필드**를 받는다 (spec 045 FR-013): 옛 단일 필드 버전은 ``analyzed_description``
    만 되돌려 써서 ``logical_name`` 이 Neo4j 에만 들어가고 state/artifact 에는 빈값으로 남았다
    → 그래프와 artifact 가 다른 사실을 말하는 검증 사각지대. 빈값은 덮어쓰지 않는다
    (LLM 이 논리명을 못 낸 테이블의 기존 값을 지우지 않도록).
    """
    node_ids = [str(getattr(node, "id", "") or "") for node in nodes]
    update_ids = [str(update.get("id") or "") for update in updates]
    if any(not node_id for node_id in node_ids + update_ids):
        raise ValueError("table description state/update IDs must not be empty")
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("table description state contains duplicate IDs")
    if len(update_ids) != len(set(update_ids)):
        raise ValueError("table description updates contain duplicate IDs")
    unknown_ids = set(update_ids) - set(node_ids)
    if unknown_ids:
        raise ValueError(
            "table description updates contain unknown IDs: "
            f"{sorted(unknown_ids)!r}"
        )
    id_to_vals = {u["id"]: u for u in updates}
    return [
        replace(n, **{a: id_to_vals[n.id].get(a, "") for a in attrs})
        if n.id in id_to_vals else n
        for n in nodes
    ]
