"""step6 phase-0 — 모듈 사전분석 (Framework 전용).

함수 분석(Bottom-up DAG) 직전, 모듈마다 LLM 1회로 헤더 주석의 변경이력·작성자
노이즈를 제거해 짧은 **모듈 의도(intent)** 를 뽑는다. 그 intent 는 함수 3분할
분석의 공통 context 에 주입돼 함수마다 모듈 맥락을 짧게 제공한다.

strategy 게이트: ``<prompts_dir>/module_pre_analysis.yaml`` 이 있는 strategy 만
동작한다. DBMS 는 ``prompts/dbms`` 에 이 yaml 이 없어 자연히 skip — 헤더주석 개념이
달라 별도 판단 대상 (plan §M9). ``if strategy`` 분기 없이 prompt 존재로 게이트.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from data_models.graph_nodes import Function, Module
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError, CostBudget
from shared.llm.llm_fanout import gather_with_warmup
from shared.llm.llm_output_models import ModulePreAnalysisOutput
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.logging_and_stream.logger import log_error, log_info
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.module import upsert_modules

_STEP = "step6.module_pre_analysis"
_RULE = "module_pre_analysis"
_MAX_SIGNATURES = 80   # 모듈당 시그니처 상한 — 대형 모듈 토큰 폭증 차단


async def run_module_pre_analysis(
    modules: list[Module],
    functions: list[Function],
    strategy: Any,
    cost_budget: Optional[CostBudget],
    neo: Optional[Neo4jClient],
) -> dict[str, str]:
    """모듈마다 LLM 1회 → ``{module_id: intent}``.

    이 strategy 에 ``module_pre_analysis.yaml`` 이 없으면 빈 dict 반환 (DBMS) —
    함수 분석은 모듈 intent 없이 그대로 진행. intent 는 MODULE 노드에 영속화
    (노드 자체는 step2_load_ast 가 이미 생성).
    """
    yaml_path = Path(strategy.prompts_dir) / f"{_RULE}.yaml"
    if not yaml_path.exists() or not modules:
        return {}

    started = time.monotonic()
    sigs_by_module = _signatures_by_module(functions)
    targets = [
        m for m in modules
        if m.comment.strip() or m.annotations.strip() or sigs_by_module.get(m.module_id)
    ]
    if not targets:
        log_info(_STEP, "skip", reason="no header/signatures", modules=len(modules))
        return {}

    log_info(_STEP, "start", modules=len(targets), concurrency=ENV.pipeline.max_concurrency)

    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
    # 첫 모듈 1건으로 prefix 캐시 워밍 → 나머지 병렬 (cold 동시요청 캐시 미스 방지).
    results = await gather_with_warmup(
        (
            _analyze_module(m, sigs_by_module.get(m.module_id, []), strategy, cost_budget, semaphore)
            for m in targets
        ),
        return_exceptions=True,
    )

    intents: dict[str, str] = {}
    updated: list[Module] = []
    failed = 0
    for module, r in zip(targets, results):
        if isinstance(r, BudgetExceededError):
            raise r        # 회로 차단기 즉시 전파
        if isinstance(r, BaseException):
            failed += 1
            log_error(_STEP, "analyze_module", r, module_id=module.module_id)
            continue
        intent = r
        updated.append(replace(module, intent=intent or None))
        if intent:
            intents[module.module_id] = intent

    if neo is not None and updated:
        await upsert_modules(neo, updated)

    log_info(
        _STEP, "done",
        modules=len(targets), with_intent=len(intents), failed=failed or None,
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return intents


def _signatures_by_module(functions: list[Function]) -> dict[str, list[str]]:
    """함수 signature 를 소속 module_id 로 그룹핑."""
    out: dict[str, list[str]] = defaultdict(list)
    for fn in functions:
        if fn.module_id:
            out[fn.module_id].append(fn.signature)
    return out


async def _analyze_module(
    module: Module,
    signatures: list[str],
    strategy: Any,
    cost_budget: Optional[CostBudget],
    semaphore: asyncio.Semaphore,
) -> str:
    """모듈 1개 LLM 호출 → intent. 회로 차단기는 그대로 raise."""
    async with semaphore:
        result: ModulePreAnalysisOutput = await run_yaml_rule(
            prompts_dir=Path(strategy.prompts_dir),
            rule_name=_RULE,
            user_values={
                "module_id": module.module_id,
                "module_header": _format_header(module),
                "function_signatures": _format_signatures(signatures),
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
    """함수 시그니처 목록 → LLM 입력 텍스트 (상한 적용)."""
    if not signatures:
        return "(함수 없음)"
    lines = [f"- {s}" for s in signatures[:_MAX_SIGNATURES]]
    if len(signatures) > _MAX_SIGNATURES:
        lines.append(f"... (외 {len(signatures) - _MAX_SIGNATURES}개)")
    return "\n".join(lines)
