"""enrich_table_description_step — 테이블 description 통합 LLM 호출.

흐름:
1. 결정론 ``READS/WRITES``와 ``direct_column_usage_evidence_by_table``을 읽는다.
2. ``table_fqn``별로 직접 operation과 컬럼 원문 근거를 모은다.
3. table family prompt로 LLM 호출 → 통합 description.
4. Table/Column 최종 상태 반환 — 제품 저장은 최종 공개 경로만 수행.

LLM 호출 = 테이블당 1 회 (병렬, ``max_concurrency``).
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.analysis_handoff import (
    POSTPROCESS_PRIORITY,
    POSTPROCESS_LANE,
    AnalysisHandoffAborted,
    handoff_of,
)
from pipeline.execution.step_config import require_step_config_value, step_config
from pipeline.execution.pause_controller import PauseController
from pipeline.steps.step8_post_processing.handoff_skip import log_handoff_skip
from pipeline.semantic_tasks.prompt_assets import semantic_prompt_dir

from llm.fanout import gather_with_warmup
from pipeline.models.code_analysis_output import TableDescription
from pipeline.semantic_tasks.table.description_frame import (
    build_table_description_plans,
    normalize_table_description_frame_response,
    table_description_prompt_values,
    table_description_frame_response_model,
)
from pipeline.models.sql_identifier import SqlIdentifierKey, sql_identifier_key
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.model_output_error import ModelOutputError
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.execution.quality_finding_log import record_analysis_findings
from pipeline.execution.semantic_artifacts import (
    persist_product_missing_context,
    persist_semantic_frame,
)
from pipeline.models.function_meaning import MissingContextItem
from pipeline.models.semantic_frame import SemanticFrame
from shared.events.runtime_logger import log_info, log_warning
from shared.events.processing_events import step_processing_scope, track_processing
from graph.schema.node_ids import fqn
from pipeline.steps.step8_post_processing.table_logical_names import (
    build_consolidated_column_updates,
)

_STEP = "step8.enrich_table_description"


async def enrich_table_description_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    strategy = require_step_config_value(cfg, "strategy", step=_STEP)
    cost_budget = cfg.get("cost_budget")
    stream = cfg.get("stream")
    pause: PauseController = cfg.get("pause") or PauseController()
    # 함수-테이블 접근 의미는 direct R/W 슬롯 응답이 끝난 노드에서만
    # 확정된다. 결정론 컬럼 근거는 그 응답 성공 여부와 관계없이 별도로 넘어온다.
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

    column_candidates = _collect_column_candidates(code_nodes)

    source_columns_by_id = {column.id: column for column in state.get("columns", [])}
    source_columns_by_table = _source_columns_by_table(state, source_columns_by_id)
    direct_operations = _direct_operations_by_table(state)
    source_tables_by_id = {table.id: table for table in source_tables}
    table_fqns = _table_target_ids(source_tables, direct_operations)
    allowed_column_names: dict[str, set[str]] = {}
    prompt_evidence: dict[str, dict] = {}
    for table in table_fqns:
        # DDL HAS_COLUMN이 최종 컬럼 집합의 단일 진실이다. step7 LLM 후보가 컬럼을
        # 누락하거나 추가해도 최종 prompt의 컬럼 소유권을 바꾸지 않는다.
        source_columns = source_columns_by_table.get(table, [])
        ordered_names = [column.name for column in source_columns]
        allowed_column_identities = [
            (column.name, bool(getattr(column, "name_quoted", False)))
            for column in source_columns
        ]
        allowed_column_names[table] = set(ordered_names)
        columns = []
        for source in source_columns:
            name = source.name
            columns.append({
                "name": name,
                "source_logical_name": getattr(source, "logical_name", ""),
                "source_description": getattr(source, "description", ""),
                "source_comment": getattr(source, "comment", ""),
                "candidates": _focus_column_candidates(
                    column_candidates.get(table, {}).get(name, []),
                    target_name=name,
                    target_quoted=bool(getattr(source, "name_quoted", False)),
                    allowed_columns=allowed_column_identities,
                ),
            })
        prompt_evidence[table] = {
            "table": _source_table_evidence(source_tables_by_id[table]),
            "direct_operations": direct_operations.get(table, []),
            "columns": columns,
        }

    log_info(
        _STEP, "start",
        tables=len(table_fqns),
        column_descriptions=sum(len(v) for v in column_candidates.values()),
    )
    prompts_dir = semantic_prompt_dir(family="table", strategy=strategy.name)
    # run 공유 슬롯 — step7 과 같은 세마포어를 낮은 우선순위로 쓴다 (spec 096).
    slots = handoff.slots

    async def _summarize_one(
        table_fqn: str, evidence: dict,
    ) -> TableDescription | ModelOutputError:
        """테이블 의미를 한 번 요청한다. transport 재시도는 runner만 소유한다."""
        table_plan, column_plans = build_table_description_plans(table_fqn, evidence)
        response_model = table_description_frame_response_model(column_plans)

        async def _call() -> TableDescription:
            async with track_processing("Table", table_fqn):
                async with slots.at(POSTPROCESS_PRIORITY, POSTPROCESS_LANE):
                    return await run_yaml_rule(
                        prompts_dir=prompts_dir,
                        rule_name="table_description",
                        user_values=table_description_prompt_values(
                            table_fqn,
                            table_plan,
                            column_plans,
                        ),
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
        results = (
            await pause.run_restartable_during_pause(_run_fanout)
            if table_fqns else []
        )

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
        raise RuntimeError(
            "table meaning invocation failed; generation publication is not allowed: "
            + ", ".join(finding.target_id for finding in findings)
        )

    # 분석 합성 설명/논리명은 원본 table id에 결합한다.
    # LLM 이 반환한 db/schema/name 으로 id 를 재조립하면 오파싱(name='TB' 등)으로 불일치·유실
    # (컬럼 경로와 동일하게 원본 fqn 사용 — spec 024). r 은 설명·논리명 내용만 쓴다.
    successful_by_id = dict(successful)
    table_updates = [
        _data_object_meaning_update(
            entity_kind="table",
            entity_id=table.id,
            logical_name=successful_by_id[table.id].logical_name,
            analyzed_description=successful_by_id[table.id].description,
            meaning_status=successful_by_id[table.id].evidence_status,
            semantic_frame=successful_by_id[table.id].semantic_frame,
        )
        if table.id in successful_by_id else
        _missing_usage_context_update("table", table.id)
        for table in source_tables
    ]
    _require_exact_update_population(
        "table",
        [table.id for table in source_tables],
        table_updates,
    )

    # 여러 함수 후보 중 첫 값이 아니라, 원래 schema 설명까지 함께 본 테이블별 통합 결과만
    # 컬럼에 반영한다. 허용 목록 밖/중복은 결정론적으로 거부하고 누락을 복사로 채우지 않는다.
    modeled_column_updates = build_consolidated_column_updates(
        [table_fqn for table_fqn, _ in successful],
        [result for _, result in successful],
        allowed_column_names,
    )
    column_frames = {
        fqn(table_fqn, name): frame
        for table_fqn, result in successful
        for name, frame in result.column_semantic_frames.items()
    }
    for update in modeled_column_updates:
        frame = column_frames[update["id"]]
        update.update(_meaning_metadata(
            entity_kind="column",
            entity_id=update["id"],
            meaning_status=update.pop("meaning_status"),
            semantic_frame=frame,
        ))
    modeled_column_by_id = {
        update["id"]: update for update in modeled_column_updates
    }
    column_updates = [
        modeled_column_by_id.get(column.id)
        or _missing_usage_context_update("column", column.id)
        for column in state.get("columns", [])
    ]
    _require_exact_update_population(
        "column",
        [column.id for column in state.get("columns", [])],
        column_updates,
    )

    # logical_name도 같은 state 객체에 반영해 최종 제품 계획의 단일 입력으로 만든다.
    updated_tables = _replace_by_id(
        state.get("tables", []), table_updates, "analyzed_description", "logical_name",
        "meaning_status", "missing_context_count", "missing_context_artifact_hash",
    )
    updated_columns = _replace_by_id(
        state.get("columns", []), column_updates, "analyzed_description", "logical_name",
        "meaning_status", "missing_context_count", "missing_context_artifact_hash",
    )

    log_info(
        _STEP, "done",
        tables=len(table_fqns),
        descriptions=len(table_updates), columns=len(column_updates),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"tables": updated_tables, "columns": updated_columns}


def _semantic_missing_context_items(
    frame: SemanticFrame,
) -> tuple[MissingContextItem, ...]:
    items: list[MissingContextItem] = []
    for slot in sorted(frame.slots.values(), key=lambda item: item.slot_id):
        if slot.status == "sufficient":
            continue
        description = "; ".join(dict.fromkeys(
            " ".join(str(reason).split())
            for reason in slot.missing_context
            if str(reason).strip()
        ))
        if not description:
            raise ValueError(
                f"non-sufficient data-object slot lacks missing context: {slot.slot_id}"
            )
        items.append(MissingContextItem(
            missing_context_ref=slot.slot_id,
            reason_code=f"{slot.family.lower()}_slot_{slot.status}",
            description=description,
        ))
    return tuple(items)


def _require_exact_update_population(
    entity_kind: str,
    required_ids: list[str],
    updates: list[dict],
) -> None:
    produced_ids = [str(item.get("id") or "") for item in updates]
    required_set = set(required_ids)
    produced_set = set(produced_ids)
    duplicate_required = sorted(
        entity_id for entity_id, count in Counter(required_ids).items() if count > 1
    )
    duplicate_produced = sorted(
        entity_id for entity_id, count in Counter(produced_ids).items() if count > 1
    )
    missing = sorted(required_set - produced_set)
    unexpected = sorted(produced_set - required_set)
    if duplicate_required or duplicate_produced or missing or unexpected:
        raise ValueError(
            f"{entity_kind} meaning population is not exact-once: "
            f"duplicate_required={duplicate_required}, "
            f"duplicate_produced={duplicate_produced}, missing={missing}, "
            f"unexpected={unexpected}"
        )


def _meaning_metadata(
    *,
    entity_kind: str,
    entity_id: str,
    meaning_status: str,
    semantic_frame: SemanticFrame,
) -> dict:
    if semantic_frame.target.id != entity_id:
        raise ValueError(
            f"{entity_kind} semantic frame target mismatch: "
            f"expected={entity_id!r} actual={semantic_frame.target.id!r}"
        )
    persist_semantic_frame(semantic_frame, entity_kind=entity_kind)
    items = _semantic_missing_context_items(semantic_frame)
    if meaning_status == "sufficient":
        if items:
            raise ValueError(f"{entity_id}: sufficient meaning has incomplete slots")
        return {"meaning_status": "sufficient", "missing_context_count": 0}
    if meaning_status != "insufficient" or not items:
        raise ValueError(f"{entity_id}: invalid data-object meaning state")
    artifact_hash = persist_product_missing_context(
        entity_kind=entity_kind,
        entity_id=entity_id,
        meaning_status="insufficient",
        items=items,
    )
    return {
        "meaning_status": "insufficient",
        "missing_context_count": len(items),
        "missing_context_artifact_hash": artifact_hash,
    }


def _data_object_meaning_update(
    *,
    entity_kind: str,
    entity_id: str,
    logical_name: str,
    analyzed_description: str,
    meaning_status: str,
    semantic_frame: SemanticFrame,
) -> dict:
    metadata = _meaning_metadata(
        entity_kind=entity_kind,
        entity_id=entity_id,
        meaning_status=meaning_status,
        semantic_frame=semantic_frame,
    )
    return {
        "id": entity_id,
        "logical_name": logical_name if meaning_status == "sufficient" else "",
        "analyzed_description": (
            analyzed_description if meaning_status == "sufficient" else ""
        ),
        **metadata,
    }


def _missing_usage_context_update(entity_kind: str, entity_id: str) -> dict:
    item = MissingContextItem(
        missing_context_ref=entity_id,
        reason_code="missing_usage_context",
        description="verified direct READS/WRITES usage context is unavailable",
    )
    artifact_hash = persist_product_missing_context(
        entity_kind=entity_kind,
        entity_id=entity_id,
        meaning_status="insufficient",
        items=(item,),
    )
    return {
        "id": entity_id,
        "logical_name": "",
        "analyzed_description": "",
        "meaning_status": "insufficient",
        "missing_context_count": 1,
        "missing_context_artifact_hash": artifact_hash,
    }


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


def _collect_column_candidates(
    code_nodes: list[object],
) -> dict[str, dict[str, list[dict[str, object]]]]:
    """Aggregate direct column source while keeping audit identity internal."""
    column_candidates: dict[str, dict[str, list[dict[str, object]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for fn in code_nodes:
        extra = getattr(fn, "extra", {}) or {}
        for table, columns in (
            extra.get("direct_column_usage_evidence_by_table", {}) or {}
        ).items():
            for name, column in (columns or {}).items():
                if not name:
                    continue
                candidate = {
                    "source_function_id": getattr(fn, "id", ""),
                    "ownership": column.get("ownership", ""),
                    "evidence_lines": list(column.get("evidence_lines") or ()),
                    "source_lines": list(column.get("source_lines") or ()),
                }
                if candidate not in column_candidates[table][name]:
                    column_candidates[table][name].append(candidate)
    return {
        table: dict(columns) for table, columns in column_candidates.items()
    }


def _focus_column_candidates(
    candidates: list[dict[str, object]],
    *,
    target_name: str,
    target_quoted: bool,
    allowed_columns: list[tuple[str, bool]],
) -> list[dict[str, object]]:
    """Project raw coordinate evidence into one-column-at-a-time prompt text.

    The original file and absolute line remain the audit source.  The prompt projection labels
    the owned column and masks sibling DDL column identifiers so one source line cannot license
    semantic claims for a sibling candidate.  This creates no business meaning.
    """
    target_identity = sql_identifier_key(target_name, target_quoted)
    allowed_identities = {
        sql_identifier_key(name, quoted) for name, quoted in allowed_columns
    }
    if target_identity not in allowed_identities:
        raise ValueError(f"target column is not in DDL identities: {target_identity!r}")
    sibling_identities = allowed_identities - {target_identity}
    focused: list[dict[str, object]] = []
    for candidate in candidates:
        item = {key: value for key, value in candidate.items() if key != "source_lines"}
        item["source_lines"] = [
            {
                key: value for key, value in source_line.items() if key != "text"
            } | {
                "focus_text": _focus_column_source_text(
                    str(source_line.get("text") or ""),
                    target_identity=target_identity,
                    sibling_identities=sibling_identities,
                ),
            }
            for source_line in candidate.get("source_lines", []) or []
        ]
        focused.append(item)
    return focused


def _focus_column_source_text(
    text: str,
    *,
    target_identity: SqlIdentifierKey,
    sibling_identities: set[SqlIdentifierKey],
) -> str:
    """Label exact identifier tokens outside literals and discard comment-only suffixes."""
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
            token = sql_identifier_key(
                text[index + 1:end].replace('""', '"'), True,
            )
            replacement = (
                '"[TARGET_COLUMN]"' if token == target_identity
                else '"[OTHER_COLUMN]"' if token in sibling_identities
                else text[index:min(end + 1, len(text))]
            )
            result.append(replacement)
            index = min(end + 1, len(text))
            continue
        if char.isalpha() or char in "_$#":
            end = index + 1
            while end < len(text) and (text[end].isalnum() or text[end] in "_$#"):
                end += 1
            token = sql_identifier_key(text[index:end], False)
            result.append(
                "[TARGET_COLUMN]" if token == target_identity
                else "[OTHER_COLUMN]" if token in sibling_identities
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

    ``analyzed_description``과 ``logical_name``을 같은 상태 전환에서 적용해 최종 제품
    입력이 서로 다른 시점의 값을 섞지 않게 한다. 빈값은 기존 값을 지우지 않는다.
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
