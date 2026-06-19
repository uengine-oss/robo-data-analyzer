"""step6 phase-0 — 모듈 사전분석 (Framework 전용).

코드노드 분석(Bottom-up DAG) 직전, 모듈마다 LLM 1회로 헤더 주석의 변경이력·작성자
노이즈를 제거해 짧은 **모듈 의도(intent, 최대 2줄)** 를 뽑는다. 그 intent 는 코드노드
3분할 분석의 공통 context prefix 로만 사용되고 끝 — 영구 그래프 속성 X.

strategy 게이트: ``<prompts_dir>/module_pre_analysis.yaml`` 이 있는 strategy 만
동작한다. DBMS 는 ``prompts/dbms`` 에 이 yaml 이 없어 자연히 skip.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from data_models.code_analysis_output import ModulePreAnalysisOutput
from data_models.graph_nodes import CodeNode, Module
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError, CostBudget
from shared.llm.fanout import gather_with_warmup
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.log.logger import log_error, log_info
from shared.log.ndjson_stream import NDJSONStream
from shared.log.processing_progress import step_processing_scope, track_processing

_STEP = "step6.module_pre_analysis"
_RULE = "module_pre_analysis"
_MAX_SIGNATURES = 80   # 모듈당 시그니처 상한 — 대형 모듈 토큰 폭증 차단


async def run_module_pre_analysis(
    modules: list[Module],
    code_nodes: list[CodeNode],
    strategy: Any,
    cost_budget: Optional[CostBudget],
    stream: Optional[NDJSONStream] = None,
) -> dict[str, str]:
    """모듈마다 LLM 1회 → ``{module_id: intent}``.

    intent 는 **내부 분석용 임시 통신값** — 코드노드 분석 3분할의 공통 context prefix
    로만 사용되고 끝. 영구 그래프 속성 X. Module 노드의 진짜 메타 (summary /
    stereotype / logical_name) 는 step7 ``summarize_module`` 이 별도 채운다.

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

    log_info(_STEP, "start", modules=len(targets), concurrency=ENV.pipeline.max_concurrency)

    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
    with step_processing_scope(_STEP, stream):
        results = await gather_with_warmup(
            (
                _analyze_module(m, sigs_by_module.get(m.id, []), strategy, cost_budget, semaphore)
                for m in targets
            ),
            return_exceptions=True,
        )

    intents: dict[str, str] = {}
    failed = 0
    for module, r in zip(targets, results):
        if isinstance(r, BudgetExceededError):
            raise r
        if isinstance(r, BaseException):
            failed += 1
            log_error(_STEP, "analyze_module", r, module_id=module.id)
            continue
        if r:
            intents[module.id] = r

    log_info(
        _STEP, "done",
        modules=len(targets), with_intent=len(intents), failed=failed or None,
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return intents


def _signatures_by_module(code_nodes: list[CodeNode]) -> dict[str, list[str]]:
    """코드노드 signature 를 소속 module_id 로 그룹핑."""
    out: dict[str, list[str]] = defaultdict(list)
    for fn in code_nodes:
        if fn.owner_id:
            out[fn.owner_id].append(fn.signature)
    return out


async def _analyze_module(
    module: Module,
    signatures: list[str],
    strategy: Any,
    cost_budget: Optional[CostBudget],
    semaphore: asyncio.Semaphore,
) -> str:
    """모듈 1개 LLM 호출 → intent. track_processing 이 start/done/failed 자동 emit."""
    async with track_processing("Module", module.id, sub_action="pre_analysis"):
        async with semaphore:
            result: ModulePreAnalysisOutput = await run_yaml_rule(
                prompts_dir=Path(strategy.prompts_dir),
                rule_name=_RULE,
                user_values={
                    "id": module.id,
                    "module_header": _format_header(module),
                    "code_signatures": _format_signatures(signatures),
                },
                response_model=ModulePreAnalysisOutput,
                budget=cost_budget,
            )
    return result.intent.strip()


def _format_header(module: Module) -> str:
    """헤더 주석 + 어노테이션 → LLM 입력 텍스트."""
    parts: list[str] = []
    if module.comment.strip():
        parts.append(module.comment.strip())
    if module.annotations.strip():
        parts.append(f"[어노테이션] {module.annotations.strip()}")
    return "\n".join(parts) if parts else "(헤더 주석 없음)"


def _format_signatures(signatures: list[str]) -> str:
    """코드노드 시그니처 목록 → LLM 입력 텍스트 (상한 적용)."""
    if not signatures:
        return "(함수 없음)"
    lines = [f"- {s}" for s in signatures[:_MAX_SIGNATURES]]
    if len(signatures) > _MAX_SIGNATURES:
        lines.append(f"... (외 {len(signatures) - _MAX_SIGNATURES}개)")
    return "\n".join(lines)
