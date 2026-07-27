"""큰 코드노드 사전 압축 — callee 본문 통째 노출 차단.

용도: 본문 토큰 > ``ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS`` (기본 1000) 인
코드노드만 LLM 1회 호출로 한 줄 요약 → ``AnalysisContext.one_liner`` 에 박음.

strategy 무관 (본문만 보고 압축). 작은 모델 (``ROBO_LLM_MODEL_SMALL``) 사용.

다른 코드노드 의존 X — Bottom-up DAG와 무관하게 step6 시점에 미리 실행 가능.
"""
from __future__ import annotations

from typing import Optional

from graph.schema.graph_nodes import CodeNode
from llm import LLM_PACKAGE_DIR
from shared.config.settings import SETTINGS
from llm.usage.cost_budget import BudgetExceededError, CostBudget
from llm.configs.loader import load_llm_config
from llm.runners.yaml_llm_runner import run_yaml_rule
from shared.events.runtime_logger import log_error, log_warning
from shared.files.source_excerpt import number_text

# 출력 계약: 한 문장, 최대 120자 (초과분은 압축 목적상 결정론 절단).
_MAX_ONE_LINER_CHARS = 120


# strategy 무관이라 ``llm/prompts/common/`` 에 yaml
_PROMPTS_COMMON = LLM_PACKAGE_DIR / "prompts" / "common"


def estimate_tokens(text: str) -> int:
    """단순 토큰 추정 — 영문 4 자/토큰, 한글 약 1.5 자/토큰."""
    n_ascii = sum(1 for c in text if ord(c) < 128)
    n_other = len(text) - n_ascii
    return n_ascii // 4 + int(n_other / 1.5)


async def compute_one_liner(
    fn: CodeNode,
    body_text: str,
    cost_budget: Optional[CostBudget] = None,
) -> Optional[str]:
    """본문 토큰 > 임계 시 한 줄 요약, 아니면 None.

    Returns:
        압축 결과 또는 None (임계 미만 또는 LLM 호출 실패).
    """
    token_count = estimate_tokens(body_text)
    if token_count < SETTINGS.analysis_pipeline.big_context_threshold_tokens:
        return None

    # 작은 모델 결정 — llm_config 의 model_small 필드 (빈 값이면 model 사용)
    small_model = load_llm_config(SETTINGS.analysis_llm.config).resolve_small_model()
    numbered_body = number_text(body_text, fn.start_line)

    try:
        # 평문 텍스트 모드 — 스칼라 1개 출력에 JSON 문법 강제를 걸면 공백 폭주가
        # 가능하다(run7 실측 3만 토큰). 형식(한 줄·길이)은 아래 결정론 코드가 집행.
        raw = await run_yaml_rule(
            prompts_dir=_PROMPTS_COMMON,
            rule_name="large_code_one_liner",
            user_values={
                "id": fn.id,
                "signature": fn.signature,
                "source_file": fn.file_path,
                "source_range": f"{fn.start_line}-{fn.end_line}",
                "coordinate_system": "absolute-file-line",
                "code_body": numbered_body,
            },
            response_model=None,
            budget=cost_budget,
            model_override=small_model,
        )
        one_liner = " ".join(str(raw).split())    # 개행·연속 공백 → 한 줄 정규화
        if not one_liner:
            log_warning(
                "pipeline.steps.step6_build_context.large_code_one_liner",
                "empty_response",
                "one_liner 응답이 비어 있음 — 본문 폴백 사용",
                code_id=fn.id,
            )
            return None
        return one_liner[:_MAX_ONE_LINER_CHARS]
    except BudgetExceededError:
        raise        # 회로 차단기 상위로 전파
    except Exception as e:
        # 큰 코드노드 압축은 graceful degradation 가능 (없으면 본문 일부 fallback).
        # 그러나 사유는 반드시 로깅 (silent return 금지).
        log_error(
            "pipeline.steps.step6_build_context.large_code_one_liner",
            "compute_one_liner",
            e,
            code_id=fn.id,
        )
        return None
