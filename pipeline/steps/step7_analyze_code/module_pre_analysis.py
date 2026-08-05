"""step7 phase-0 — 모듈 사전분석 (Framework 전용).

코드노드 분석(Bottom-up DAG) 직전, 모듈마다 LLM 1회로 헤더 주석의 변경이력·작성자
노이즈를 제거해 짧은 **모듈 의도(intent, 최대 2줄)** 를 뽑는다. 그 intent 는 코드노드
3분할 분석의 공통 context prefix 로만 사용되고 끝 — 영구 그래프 속성 X.

strategy 게이트: ``<prompts_dir>/module_pre_analysis.yaml`` 이 있는 strategy 만
동작한다. DBMS 는 ``llm/prompts/dbms`` 에 이 yaml 이 없어 자연히 skip.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from pipeline.models.code_analysis_output import ModulePreAnalysisOutput
from graph.schema.graph_nodes import CodeNode, Module
from pipeline.languages import statement_labels
from shared.concurrency.priority_semaphore import PrioritySemaphore
from llm.usage.cost_budget import CostBudget
from llm.fanout import gather_with_warmup
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.model_output_error import ModelOutputError
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.execution.quality_finding_log import record_analysis_findings
from shared.events.runtime_logger import log_info, log_warning
from shared.events.ndjson_event_stream import NDJSONStream
from shared.events.processing_events import step_processing_scope, track_processing

_STEP = "step7.module_pre_analysis"
_RULE = "module_pre_analysis"
_MAX_SIGNATURES = 80   # 모듈당 시그니처 상한 — 대형 모듈 토큰 폭증 차단


async def run_module_pre_analysis(
    modules: list[Module],
    code_nodes: list[CodeNode],
    strategy: Any,
    cost_budget: Optional[CostBudget],
    llm_slots: PrioritySemaphore,
    stream: Optional[NDJSONStream] = None,
) -> dict[str, str]:
    """모듈마다 LLM 1회 → ``{module_id: intent}``.

    intent 는 **내부 분석용 임시 통신값** — 코드노드 분석 3분할의 공통 context prefix
    로만 사용되고 끝. 영구 그래프 속성 X. Module 노드의 진짜 메타 (summary /
    stereotype / logical_name)는 step8 ``summarize_module``이 별도 채운다.

    이 strategy 에 ``module_pre_analysis.yaml`` 이 없으면 빈 dict 반환 (DBMS) —
    코드노드 분석은 intent 없이 진행.

    진행 가시성: ``step_processing_scope`` 진입 → ``_analyze_module`` 안 ``track_processing``
    이 자동 Module 단위 start/done/failed emit (sub_action=``pre_analysis``).
    """
    yaml_path = Path(strategy.prompts_dir) / f"{_RULE}.yaml"
    if not yaml_path.exists() or not modules:
        return {}

    started = time.monotonic()
    sigs_by_module = _signatures_by_module(code_nodes)
    targets = [
        m for m in modules
        if m.comment.strip() or m.annotations.strip() or sigs_by_module.get(m.id)
    ]
    if not targets:
        log_info(_STEP, "skip", reason="no header/signatures", modules=len(modules))
        return {}

    log_info(_STEP, "start", modules=len(targets), concurrency=llm_slots.capacity)

    with step_processing_scope(_STEP, stream):
        results = await gather_with_warmup(
            (
                _analyze_module(
                    m, sigs_by_module.get(m.id, []), strategy, cost_budget, llm_slots,
                )
                for m in targets
            ),
        )

    intents: dict[str, str] = {}
    for module, r in zip(targets, results):
        if r:
            intents[module.id] = r

    log_info(
        _STEP, "done",
        modules=len(targets), with_intent=len(intents),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return intents


def _signatures_by_module(code_nodes: list[CodeNode]) -> dict[str, list[dict]]:
    """코드노드 identity/signature/absolute range를 소속 module_id로 그룹핑."""
    out: dict[str, list[dict]] = defaultdict(list)
    stmt_labels = statement_labels()
    for fn in code_nodes:
        # statement 노드는 LLM 분석 대상이 아니다(_fw_statement 계약) — 모듈 프롬프트에
        # 수천 개의 RETURN/ASSIGNMENT 행이 실리는 것을 막는다.
        if fn.label in stmt_labels:
            continue
        if fn.owner_id:
            out[fn.owner_id].append({
                "node_id": fn.id,
                "signature": fn.signature,
                "source_range": f"{fn.start_line}-{fn.end_line}",
                "label": fn.label,
            })
    return out


async def _analyze_module(
    module: Module,
    signatures: list[dict],
    strategy: Any,
    cost_budget: Optional[CostBudget],
    llm_slots: PrioritySemaphore,
) -> str:
    """모듈 1개 LLM 호출 → intent. track_processing 이 start/done/failed 자동 emit."""
    try:
        async with track_processing("Module", module.id, sub_action="pre_analysis"):
            async with llm_slots:
                result: ModulePreAnalysisOutput = await run_yaml_rule(
                    prompts_dir=Path(strategy.prompts_dir),
                    rule_name=_RULE,
                    user_values={
                        "id": module.id,
                        "source_file": module.file_path,
                        "source_range": f"{module.start_line}-{module.end_line}",
                        "coordinate_system": "absolute-file-line",
                        "module_header": _format_header(module),
                        "code_signatures": _format_signatures(signatures),
                    },
                    response_model=ModulePreAnalysisOutput,
                    budget=cost_budget,
                )
    except ModelOutputError as error:
        finding = AnalysisFinding(
            concern="module_pre_analysis",
            status="unavailable",
            target_id=module.id,
            reasons=(str(error),),
            evidence_rule=error.rule_name,
        )
        record_analysis_findings([finding])
        log_warning(
            _STEP, "quality_finding",
            "모듈 사전 의도를 검증할 수 없어 이 입력만 격리하고 코드 분석을 계속함",
            module_id=module.id, error=str(error),
        )
        return ""
    return result.intent.strip()


def _format_header(module: Module) -> str:
    """헤더 주석 + 어노테이션 → LLM 입력 텍스트."""
    parts: list[str] = []
    if module.comment.strip():
        parts.append(module.comment.strip())
    if module.annotations.strip():
        parts.append(f"[어노테이션] {module.annotations.strip()}")
    return "\n".join(parts) if parts else "(헤더 주석 없음)"


def _format_signatures(signatures: list[dict]) -> str:
    """코드노드 identity/signature/range 목록 → compact JSON (상한 적용)."""
    if not signatures:
        return "(함수 없음)"
    return json.dumps(
        {
            "members": signatures[:_MAX_SIGNATURES],
            "omitted_count": max(0, len(signatures) - _MAX_SIGNATURES),
            "evidence_scope": "member-signature-only",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
