"""yaml 기반 LLM 호출 헬퍼 — 단순 LLM 호출 (Agent 외부).

용도: ``prompts/<strategy>/<name>.yaml`` 의 룰 로딩 + LLM 호출 + Pydantic 응답.
사용처:
- ``analysis_flow/step6_analyze_function`` (function_summary / function_table_insights /
  function_rules_examples)
- ``analysis_flow/step7_post_processing`` (module_summary / table_description)
- ``analysis_flow/step5_build_context/large_function_one_liner``

yaml 형식 (단순)::

    system: |
      <system prompt — byte-identical 보존>
    user_template: |
      <user 메시지 template (str.format 치환)>
    model: <옵션 — None 이면 현재 llm_config 의 model 사용>

yaml 형식 (DRY — 분할 그룹의 KV 캐시 prefix 통일)::

    # `prompts/<strategy>/_common.yaml` — 단일 진실
    system: |
      <분할 공통 system 본문>
    user_template_inputs: |
      <분할 공통 user_template 입력값 영역>

    # `prompts/<strategy>/<split_name>.yaml` — 분할 고유만
    extends: _common
    user_template_specific: |
      <이 분할 고유 user_template 지침>

``extends`` 가 있으면 ``_common`` 의 ``system`` 을 그대로 + ``user_template_inputs`` 를
prefix 로, ``user_template_specific`` 을 그 뒤에 붙여 최종 prompt 조립. 분할 그룹의
system 본문은 글자 단위 동일이 강제됨 (KV 캐시 prefix 적중 보장). 자세한 원칙: [[kv-cache]].

``llm.with_structured_output(response_model, include_raw=True)`` 사용 →
usage_metadata 동시 추출 + Pydantic 자동 파싱.

**Plan §4 M0 핵심**: Agent 외부 호출도 ``usage.jsonl`` 적재 (측정 사각지대 차단).
``CostTrackingMiddleware`` 와 같은 헬퍼 ``shared.llm.usage_jsonl_writer.append_usage_record``
사용.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, TypeVar

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from shared.config.env_catalog import ENV
from shared.llm.cost_budget import CostBudget
from shared.llm.llm_client import build_llm_client
from shared.llm.llm_config_loader import load_llm_config
from shared.llm.llm_pricing import compute_cost
from shared.llm.token_usage import track_usage
from shared.llm.usage_jsonl_writer import append_usage_record
from shared.logging_and_stream.logger import log_info, log_warning
from shared.logging_and_stream.message_filters import apply_cache_friendly_filters
from shared.logging_and_stream.prompt_logger import write_rule_record


T = TypeVar("T", bound=BaseModel)


def _load_rule(prompts_dir: Path, rule_name: str) -> dict:
    """yaml 룰 로딩 + ``extends`` 처리 → ``{system, user_template, model}`` 반환.

    ``extends: <name>`` 가 있으면 ``<name>.yaml`` 의 ``system`` + ``user_template_inputs`` 를
    공통 prefix 로 합쳐 분할 그룹의 system 본문 byte-identical 을 강제한다 (KV 캐시
    prefix 적중 보장). ``model`` 은 분할 고유 > 공통 순으로 우선.
    """
    rule = yaml.safe_load((prompts_dir / f"{rule_name}.yaml").read_text(encoding="utf-8"))

    extends = rule.get("extends")
    if extends is None:
        return rule

    common = yaml.safe_load((prompts_dir / f"{extends}.yaml").read_text(encoding="utf-8"))
    return {
        "system": common.get("system", ""),
        "user_template": (
            common.get("user_template_inputs", "") + rule.get("user_template_specific", "")
        ),
        "model": rule.get("model") or common.get("model"),
    }


def _resolve_usage_log_path() -> Path:
    """``ENV.log.dir/analysis.run/latest/usage.jsonl`` — Agent 미들웨어와 동일 경로."""
    return ENV.log.dir / "analysis.run" / "latest" / "usage.jsonl"


async def run_yaml_rule(
    prompts_dir: Path,
    rule_name: str,
    user_values: dict,
    response_model: type[T],
    budget: Optional[CostBudget] = None,
    model_override: Optional[str] = None,
) -> T:
    """yaml 룰 로딩 + LLM 호출 → Pydantic 응답.

    Args:
        prompts_dir: ``strategy.prompts_dir`` (예: ``Path("prompts/framework")``).
        rule_name: yaml 파일 이름 (확장자 X — "function_summary" 식).
        user_values: user_template 의 ``{placeholder}`` 치환 값.
        response_model: 응답 Pydantic 클래스.
        budget: ``CostBudget`` — 호출 후 ``add_sync(cost)``. 임계 초과 시 raise.
        model_override: yaml 의 ``model`` 보다 우선. ``ROBO_LLM_MODEL_SMALL`` 등 전달 가능.

    Returns:
        ``response_model`` 인스턴스.
    """
    rule = _load_rule(prompts_dir, rule_name)

    system_prompt = rule.get("system", "")
    user_template = rule.get("user_template", "")
    model = model_override or rule.get("model")

    user_content = user_template.format(**user_values)

    # 현재 LLM config — provider 정보를 cache friendly filter 에 전달
    cfg = load_llm_config(ENV.llm.config)
    llm = build_llm_client(model=model)
    # include_raw=True — Pydantic 결과 + usage_metadata 동시 획득
    structured = llm.with_structured_output(response_model, include_raw=True)

    # 캐시 친화 필터 — 단순 LLM 호출에도 미들웨어와 동일 로직 적용
    messages = apply_cache_friendly_filters(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ],
        provider=cfg.provider,
    )

    # silent 차단 — cost_tracking 미들웨어 안 거치는 진입점이라 직접 wrap.
    # 예외 (401 / 429 / 400 / timeout / network) 시 명시 로그 + raise.
    start = time.monotonic()
    log_info("llm.call", "start", agent=f"rule:{rule_name}")
    try:
        result = await structured.ainvoke(messages)
    except Exception as e:  # noqa: BLE001 — silent 차단 목적
        elapsed_ms = int((time.monotonic() - start) * 1000)
        log_warning(
            "shared.llm.yaml_llm_runner",
            "model_error",
            f"LLM 호출 실패 (silent 차단) — {type(e).__name__}: {e}",
            rule_name=rule_name,
            elapsed_ms=elapsed_ms,
        )
        raise
    latency_ms = int((time.monotonic() - start) * 1000)

    raw_msg = result.get("raw")
    parsed = result.get("parsed")

    # 비용 추적 — Agent 외부라 미들웨어 안 붙음. 직접 처리 + usage.jsonl 적재.
    metadata: dict = {"latency_ms": latency_ms}
    if raw_msg is not None:
        response_meta = getattr(raw_msg, "response_metadata", {}) or {}
        model_name = response_meta.get("model_name") or response_meta.get("model")
        if not model_name:
            model_name = model or "unknown"
            log_warning(
                "shared.llm.yaml_llm_runner",
                "track_usage",
                "response_metadata 에서 model 이름 추출 실패 — yaml model 또는 'unknown' 으로 적재됨",
                rule_name=rule_name,
            )

        record = track_usage(raw_msg, model=model_name, latency_ms=latency_ms)
        cost = compute_cost(record)

        # usage.jsonl 적재 (Plan §4 M0 — Agent 미들웨어와 동일 형식).
        append_usage_record(
            usage_log_path=_resolve_usage_log_path(),
            agent_name=f"rule:{rule_name}",
            record=record,
            cost_usd=cost,
        )

        # analyzer.log 한 줄 — `tail -f` 로 LLM 호출 진행 보임.
        log_info(
            "llm.call",
            "done",
            agent=f"rule:{rule_name}",
            model=model_name,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cached_tokens=record.cached_tokens or None,
            cost_usd=f"{cost:.6f}",
            elapsed_ms=latency_ms,
        )

        if budget is not None:
            budget.add_sync(cost)        # 임계 초과 시 BudgetExceededError

        metadata.update({
            "model": model_name,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cached_tokens": record.cached_tokens,
            "cost_usd": cost,
        })

    # prompt + 응답 logs/llm_prompts/latest/<rule_name>.json 적재.
    # 같은 rule 호출이 한 .json 의 records 배열에 누적 (소스 라인 순 정렬).
    # 로깅 실패는 분석 자체 막지 X (graceful) — silent skip 금지, 사유 명시.
    try:
        target_id, sort_key = _extract_target_and_sort(user_values)
        write_rule_record(
            rule_name=rule_name,
            target_id=target_id,
            system_prompt=system_prompt,
            user_content=user_content,
            response=parsed if parsed is not None else (raw_msg.content if raw_msg else ""),
            metadata=metadata,
            sort_key=sort_key,
        )
    except Exception as e:
        log_warning(
            "shared.llm.yaml_llm_runner",
            "write_rule_record",
            "프롬프트 로그 적재 실패 — 호출 자체는 성공 (디버깅 보조만 누락)",
            rule_name=rule_name,
            error=f"{type(e).__name__}: {e}",
        )

    if parsed is None:
        raise RuntimeError(f"LLM 응답 파싱 실패: rule={rule_name}")
    return parsed


# user_values 의 어떤 키가 target_id / sort_key 로 적합한지 — 우선순위.
# rule 별 user_template 의 placeholder 이름과 일치하면 자동 매핑.
_TARGET_ID_KEYS = (
    "function_id", "procedure_id", "module_id", "table_fqn",
    "case_id", "name", "file",
)


def _extract_target_and_sort(user_values: dict) -> tuple[str | None, str | None]:
    """``user_values`` 에서 target_id + sort_key 자동 추출.

    target_id: ctrl+F 검색 / 가독성용. 첫 매칭 키 값.
    sort_key: 한 파일 안 records 정렬용 — file 이 있으면 ``<file>:<start_line>`` 권장.
    """
    target_id: str | None = None
    for k in _TARGET_ID_KEYS:
        v = user_values.get(k)
        if v:
            target_id = str(v)
            break

    file_v = user_values.get("file") or user_values.get("file_path")
    start_v = user_values.get("start_line") or user_values.get("line")
    if file_v and start_v:
        return target_id, f"{file_v}:{start_v}"
    return target_id, target_id
