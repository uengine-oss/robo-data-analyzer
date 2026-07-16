"""검색 질의 재작성과 심판에 쓰는 생성 LLM 클라이언트.

분석용 LLM(`ROBO_LLM_CONFIG`)과 **별도** `ROBO_SEARCH_LLM_CONFIG` 를 쓴다(FR-007). 기본은
사내 Qwen(`qwen36_sglang_local`) = GPU 통일(임베딩·심판·번역 모두 사내 서버, 기밀 유지·과금 0).
gpt-5 계열로 바꾸려면 env 만 교체(`ROBO_SEARCH_LLM_CONFIG=gpt54_mini_openai`).

LLM 죽으면 chat_json 이 None → 호출부(judge/rewrite)가 폴백(원결과 유지 — 헌법 IV 는 검색
품질 저하가 아니라 조용한 오류 금지가 목적이므로, 판단 실패는 원결과 보존이 올바른 폴백).
"""
from __future__ import annotations

import json
import time

import httpx

from system.env_catalog import ENV
from llm.client.llm_config_loader import load_llm_config
from llm.client.usage.pricing import compute_cost
from llm.client.usage.token_usage import TokenUsageRecord
from llm.client.usage.usage_jsonl_writer import append_usage_record
from log.logger import log_warning

_STEP = "search.query.query_llm"
_cfg_cache: dict = {}


def _config():
    """검색 LLM config + 키(캐시). 키는 ROBO_SEARCH_LLM_API_KEY, 없으면 분석 키 폴백(사내 공유)."""
    name = ENV.search.llm_config
    if name not in _cfg_cache:
        cfg = load_llm_config(name)
        key = ENV.search.llm_api_key or ENV.llm.api_key
        _cfg_cache[name] = (cfg, key)
    return _cfg_cache[name]


def _is_openai_reasoning(cfg) -> bool:
    return cfg.provider == "openai" and "gpt-5" in cfg.model


def chat(prompt: str, *, system: str = "", max_tokens: int = 1024, temperature: float = 0.0) -> str | None:
    """단발 chat. 실패 시 None(상위 폴백). Qwen thinking 은 config 의 extra_body 로 끔.

    토큰·비용은 ``_record_usage`` 가 ``usage.jsonl`` 에 남긴다 (spec 046 FR-006) — 종전엔
    검색 LLM(질의마다 심판·재작성 호출) 의 비용이 **어디에도 안 잡혔다**.
    """
    cfg, key = _config()
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    body = {"model": cfg.model, "messages": messages, **cfg.extra_body}
    if _is_openai_reasoning(cfg):
        body["max_completion_tokens"] = max(max_tokens, 2048)   # gpt-5: reasoning+출력 합
        body["temperature"] = temperature
    else:
        body["max_tokens"] = max_tokens
        body["temperature"] = temperature
    started = time.monotonic()
    try:
        r = httpx.post(f"{cfg.api_base.rstrip('/')}/chat/completions",
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                       json=body, timeout=cfg.timeout_seconds)
        r.raise_for_status()
        payload = r.json()
        _record_usage(payload, cfg.model, int((time.monotonic() - started) * 1000))
        content = payload["choices"][0]["message"].get("content")
        return content.strip() if content else None
    except Exception as exc:  # noqa: BLE001 — 폴백 사유 로그 후 None
        log_warning(_STEP, "chat_failed", f"검색 LLM 호출 실패(폴백): {exc!r}", model=cfg.model)
        return None


def _record_usage(payload: dict, model: str, latency_ms: int) -> None:
    """OpenAI 호환 응답의 ``usage`` → ``usage.jsonl`` 적재 (spec 046 FR-006).

    검색 LLM 은 LangChain 미들웨어 스택을 안 타는 raw httpx 경로라, 비용추적이 **전무**했다.
    여기서 표준 기록 경로(``append_usage_record``)에 편입해 **모든 LLM 호출이 계측**되게 한다.

    적재 실패가 검색을 막아선 안 되므로 예외는 경고로만 남긴다 (검색 결과는 그대로 반환).
    """
    try:
        usage = payload.get("usage") or {}
        if not usage:
            return
        record = TokenUsageRecord(
            model=model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int(
                (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            ),
            total_tokens=int(usage.get("total_tokens") or 0),
            latency_ms=latency_ms,
        )
        append_usage_record(
            ENV.log.dir / "search" / "usage.jsonl",
            agent_name="search",
            record=record,
            cost_usd=compute_cost(record),
        )
    except Exception as exc:  # noqa: BLE001 — 계측 실패가 검색을 막지 않는다
        log_warning(_STEP, "usage_record_failed", f"검색 LLM 사용량 적재 실패: {exc!r}")


def chat_json(prompt: str, *, system: str = "", max_tokens: int = 1024,
              temperature: float = 0.0) -> dict | list | None:
    """JSON 출력 강제 파싱. 실패 시 None."""
    raw = chat(prompt, system=system, max_tokens=max_tokens, temperature=temperature)
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
    s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        l, r = s.find("{"), s.rfind("}")
        lb, rb = s.find("["), s.rfind("]")
        for a, b in ((l, r), (lb, rb)):
            if 0 <= a < b:
                try:
                    return json.loads(s[a:b + 1])
                except json.JSONDecodeError:
                    continue
        log_warning(_STEP, "json_parse_failed", "검색 LLM 응답을 JSON으로 파싱하지 못해 폴백")
        return None
