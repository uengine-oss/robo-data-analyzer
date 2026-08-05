"""summarize_module_step — 모듈 / 클래스 / 패키지 종합 요약.

흐름:
1. CodeNode → Module 매칭 — ``file_path`` **+** ``start_line`` ⊂ Module range.
   (한 파일 안 여러 클래스 / 중첩 모듈 정확 처리)
2. 매칭 안 된 코드노드는 file-level Module (start_line=0, end_line=0) 로.
3. 각 Module 마다 멤버 코드노드들의 summary 종합 → ``module_summary.yaml`` LLM 호출.
4. Module.summary / stereotype Neo4j 업서트 (replace 로 label 등 보존).
"""
from __future__ import annotations

import time
import json
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

from graph.schema.graph_nodes import CodeNode, Module
from shared.config.settings import SETTINGS
from llm.fanout import gather_with_warmup
from pipeline.models.code_analysis_output import ModuleSummaryOutput
from pipeline.models.module_summary_response_schema import module_summary_response_model
from pipeline.languages import require_module_stereotype
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.model_output_error import ModelOutputError
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.execution.quality_finding_log import record_analysis_findings
from shared.events.runtime_logger import log_info, log_warning
from shared.events.processing_events import step_processing_scope, track_processing
from graph.neo4j_client import Neo4jClient
from graph.nodes.module import upsert_modules
from pipeline.execution.graph_delta_stream import emit_nodes

_STEP = "step8.summarize_module"


async def summarize_module_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream = cfg.get("stream")
    pause: PauseController = cfg.get("pause") or PauseController()
    # 이 step 이 읽는 것은 코드노드의 signature·canonical summary뿐이다. 그래서 노드
    # 전체 완료가 아니라 전 대상의 #1 확정을
    # 기다린다. 기다리는 동안 step7 이 슬롯을 쓰고, 열리는 즉시 남은 빈 슬롯을 채운다.
    # 결과는 어떤 경로로 끝나든 모듈 결과 채널에도 인계한다.
    handoff = handoff_of(cfg)
    try:
        result = await _summarize(state, handoff, strategy, cost_budget, neo, stream, pause)
    except AnalysisHandoffAborted:
        log_handoff_skip(_STEP, "모듈 요약", pause)
        return {}
    except BaseException as error:
        # 실패한 요약을 정상 결과처럼 인계하지 않고 채널 소비자에게 원인을 넘긴다.
        handoff.abort(error)
        raise
    if not handoff.modules_published:
        # 요약을 못 낸 경로(설정 누락·모듈 0)도 반드시 인계 — 안 하면 용어사전이 굶는다.
        handoff.publish_modules(state.get("modules", []))
    return result


async def _summarize(
    state: dict, handoff, strategy, cost_budget, neo, stream, pause,
) -> dict:
    """모듈 요약 본체 — 게이트 통과 후. 인계·실패 정책은 호출부가 감싼다."""
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    await handoff.summaries()
    modules: list[Module] = state.get("modules", [])
    code_nodes: list[CodeNode] = handoff.merge(state.get("code_nodes", []))
    if not modules or not code_nodes:
        log_info(_STEP, "skip", modules=len(modules), code_nodes=len(code_nodes))
        return {}
    started = time.monotonic()
    log_info(_STEP, "start", modules=len(modules), code_nodes=len(code_nodes),
             concurrency=SETTINGS.analysis_pipeline.max_concurrency)

    # 모듈 설명의 근거는 step7 이 실제 의미 분석한 노드뿐이다. Framework 의 IF/CASE 같은
    # 구조 노드는 그래프에는 남지만 summary 생산 대상이 아니며, DBMS range처럼 전략이
    # 분석 대상으로 고른 노드는 그대로 포함한다. 소비자가 자체 라벨 목록을 만들지 않고
    # step6/step7 과 같은 strategy.analysis_targets 단일 진실을 사용한다.
    analyzed_code_nodes = _select_analyzed_code_nodes(strategy, code_nodes)
    fn_by_module = _match_code_to_modules(analyzed_code_nodes, modules)

    prompts_dir = Path(strategy.prompts_dir)
    # run 공유 슬롯 — step7 과 같은 세마포어를 낮은 우선순위로 쓴다 (spec 096).
    slots = handoff.slots

    async def _summarize_one(module: Module) -> Module:
        """멤버가 있으면 새 module을 반환하고, 의미 실패는 그대로 전파한다."""
        members = fn_by_module.get(module.id, [])
        if not members:
            return module
        analyzed_members = [
            fn for fn in members if (fn.summary or "").strip()
        ]
        missing = [
            fn.id for fn in members if not (fn.summary or "").strip()
        ]
        if missing:
            finding = AnalysisFinding(
                concern="module_summary",
                status="unavailable",
                target_id=module.id,
                reasons=(f"멤버 summary 누락 — 부분 모듈 요약 금지: {missing}",),
                evidence_rule="module_summary",
            )
            record_analysis_findings([finding])
            log_warning(
                _STEP, "quality_finding",
                "멤버 품질 finding 때문에 모듈 요약 후보를 만들지 않고 원본 모듈을 보존함",
                module_id=module.id, missing_count=len(missing),
            )
            return module
        if not analyzed_members:
            return module
        member_text = json.dumps(
            [
                {
                    "node_id": fn.id,
                    "signature": fn.signature,
                    "source_range": f"{fn.start_line}-{fn.end_line}",
                    "evidence_scope": "analyzed-member-summary",
                    "summary": fn.summary.strip(),
                }
                for fn in analyzed_members
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            async with track_processing("Module", module.id):
                async with slots.at(POSTPROCESS_PRIORITY):
                    result: ModuleSummaryOutput = await run_yaml_rule(
                        prompts_dir=prompts_dir,
                        rule_name="module_summary",
                        user_values={
                            "id": module.id,
                            "name": module.name,
                            "member_code_nodes": member_text,
                        },
                        response_model=module_summary_response_model(strategy.name),
                        budget=cost_budget,
                    )
        except ModelOutputError as error:
            finding = AnalysisFinding(
                concern="module_summary",
                status="unavailable",
                target_id=module.id,
                reasons=(str(error),),
                evidence_rule=error.rule_name,
            )
            record_analysis_findings([finding])
            log_warning(
                _STEP, "quality_finding",
                "모듈 요약 모델 후보를 검증할 수 없어 원본 모듈을 보존함",
                module_id=module.id, error=str(error),
            )
            return module
        # replace — label / comment / annotations 등 원본 필드 보존, 분석분만 갱신.
        # logical_name 은 여기서 채움 — 헤더 임시 정보가 아니라 멤버 분석 종합에서 도출.
        return replace(
            module,
            summary=result.summary,
            stereotype=require_module_stereotype(result.stereotype, strategy.name),
            logical_name=result.logical_name or None,
        )

    # 첫 모듈 1건으로 prefix 캐시 워밍 → 나머지 병렬. 의미 실패는 원본 모듈로
    # 바꾸지 않고 상위 step까지 전파한다.
    async def _run_fanout() -> list:
        return await gather_with_warmup(
            [_summarize_one(m) for m in modules],
        )

    with step_processing_scope(_STEP, stream):
        results = await pause.run_restartable_during_pause(_run_fanout)
    updated = results
    # Neo4j 적재를 기다리지 않고 모듈 결과 채널에 바로 인계한다.
    handoff.publish_modules(updated)

    if neo is not None:
        await upsert_modules(neo, updated)
    # 모듈 stereotype(FILE→Service 등)을 라이브 발신 — labels[1:]에 역할 실어 프론트 즉시 반영(spec 034).
    await emit_nodes(stream, updated)
    summarized = sum(1 for m in updated if getattr(m, "summary", None))
    log_info(
        _STEP, "done",
        modules=len(modules), summarized=summarized,
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"modules": updated}


def _match_code_to_modules(
    code_nodes: list[CodeNode],
    modules: list[Module],
) -> dict[str, list[CodeNode]]:
    """CodeNode → Module 매칭 (file_path + 라인 범위).

    한 파일 안 여러 module (e.g. Java inner class) → 가장 좁은 범위 매칭.
    file-level module (range 없음) 은 fallback.
    """
    # file_path → [(start, end, module_id)] sorted by 범위 좁은 순
    by_file: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for m in modules:
        by_file[m.file_path].append((m.start_line, m.end_line, m.id))
    for k in by_file:
        # 범위 좁은 모듈 먼저 — function 매칭 시 inner class 우선
        by_file[k].sort(key=lambda x: (x[1] - x[0]) if x[1] > 0 else 10**9)

    result: dict[str, list[CodeNode]] = defaultdict(list)
    for fn in code_nodes:
        candidates = by_file.get(fn.file_path, [])
        if not candidates:
            continue
        matched_id = None
        for start, end, mid in candidates:
            if start == 0 and end == 0:
                # file-level — 다른 후보 없으면 사용
                if matched_id is None:
                    matched_id = mid
                continue
            if start <= fn.start_line <= end:
                matched_id = mid
                break
        if matched_id is not None:
            result[matched_id].append(fn)
    return result


def _select_analyzed_code_nodes(
    strategy,
    code_nodes: list[CodeNode],
) -> list[CodeNode]:
    """step7 의미 분석 대상만 원래 순서대로 보존한다."""
    target_ids = strategy.analysis_targets(code_nodes)
    return [node for node in code_nodes if node.id in target_ids]
