"""GPU(SGLang) 서버에 실제 분석 페이로드를 보내는 진단 스크립트.

목적 (2026-07-30 폭주 대응 보고의 운영자 검증 절차 지원):

1. ``info``   — 서버의 grammar_backend / constrained_json_disable_any_whitespace
   실제 활성값 확인 (절차 2단계).
2. ``probe``  — 작은 constrained JSON 호출로 "문자열 내부 공백 보존 + 구조 사이
   공백 차단" 여부 확인 (절차 3단계).
3. ``replay`` — 실행 기록(``<run-dir>/llm-prompts/<rule>.jsonl``)에 남은 **실제
   프롬프트 전문**을 실제 호출 경로 그대로 재전송하고 공백 폭주를 관측 (절차 4단계의
   단건 재현). 합성 예제가 아니라 파이프라인이 보낸 것과 동일한 시스템·유저 메시지,
   동일 Pydantic 스키마, 동일 sampling/extra_body, 동일 rule max_tokens, 동일 폭주 가드를 사용한다.
   ``--current-contract``를 주면 저장된 가변 evidence는 유지하되 해당 rule의 현재 system/user
   고정 지시와 변경된 dynamic wire assignment/schema를 한 계약으로 교체한다.
   ``--current-summary-contract``는 과거 summary wire schema 이관을 위한 호환 옵션이다.

실제 호출 경로 재사용 범위 — ``run_yaml_rule`` 의 전송 구간과 글자 단위 동일:
``build_llm_client(streaming=...)`` → provider canonical JSON Schema 구조화 출력 →
내부 Pydantic 검증
→ ``apply_cache_friendly_filters`` → ``ainvoke(config={"callbacks":[RepetitionGuardCallback]})``.
단, 진단 스크립트이므로 usage.jsonl 적재·응답 캐시·rule record 적재는 하지 않는다
(운영 통계를 오염시키지 않기 위해 의도적으로 생략 — 결과는 stdout 으로만 보고).

실행 (저장소 루트에서, 저장소 venv 로):

    .venv\\Scripts\\python.exe scripts\\send_real_prompt_to_gpu.py info
    .venv\\Scripts\\python.exe scripts\\send_real_prompt_to_gpu.py probe
    .venv\\Scripts\\python.exe scripts\\send_real_prompt_to_gpu.py replay ^
        --run-dir D:\\work\\robo\\_runs\\analyzer-semantic20-20260730-context-closed-rerun3 ^
        --rule code_summary --index 0

필요 env (.env): ``ROBO_LLM_CONFIG``, ``ROBO_LLM_API_KEY``.
endpoint / model / sampling / extra_body 는 ``llm/configs/<ROBO_LLM_CONFIG>.yaml`` 이
단일 진실 — 이 스크립트는 아무것도 하드코딩하지 않는다.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import httpx
import jsonschema
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from shared.config.settings import SETTINGS
from llm.client import build_llm_client
from llm.configs.loader import load_llm_config
from llm.filters import apply_cache_friendly_filters
from llm.runners.prompt_rules import _load_rule
from llm.runners.prompt_values import _safe_format
from llm.repetition_guard import RepetitionGuardCallback, RepetitionLoopAborted
from llm.structured_output import bind_structured_output
from llm.tracing import load_rule_records

# 가드레일 임계 (llm/repetition_guard.py 와 같은 의미 축) — 보고용 기준선
WHITESPACE_FLOOD = 32
POST_ROOT_WHITESPACE_FLOOD = 8


def _configure_utf8_console(*streams: Any) -> None:
    """Make diagnostic output lossless on Windows legacy console code pages."""
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def _print_call_header(
    cfg: Any,
    streaming: bool,
    schema_name: str,
    max_tokens: Optional[int],
    structured_method: str,
) -> None:
    print("── 전송 구성 (yaml 단일 진실, 하드코딩 없음) " + "─" * 20)
    print(f"  endpoint      : {cfg.api_base}")
    print(f"  provider      : {cfg.provider}")
    print(f"  model         : {cfg.model}")
    print(f"  temperature   : {cfg.sampling.temperature} / top_p {cfg.sampling.top_p}")
    print(f"  max_tokens    : {max_tokens if max_tokens is not None else cfg.max_tokens} (비상 브레이크)")
    print(f"  extra_body    : {json.dumps(cfg.extra_body, ensure_ascii=False)}")
    print(f"  streaming     : {streaming} (폭주 가드 = 스트리밍 수신 전제)")
    print(f"  structured    : {structured_method}")
    print(f"  response_model: {schema_name}")
    print("─" * 66)


def _extract_raw_text(raw_msg: Any) -> str:
    """raw AIMessage 에서 모델이 실제 생성한 JSON 원문 추출.

    json_schema 경로는 ``content`` 에, function-calling 경로는 tool_calls 의
    ``arguments`` 문자열에 원문이 있다 — 어느 쪽이든 공백 분석 대상은 이 원문이다.
    """
    if raw_msg is None:
        return ""
    content = getattr(raw_msg, "content", "") or ""
    if isinstance(content, str) and content.strip():
        return content
    for tc in (getattr(raw_msg, "additional_kwargs", None) or {}).get("tool_calls", []):
        args = (tc.get("function") or {}).get("arguments")
        if isinstance(args, str) and args.strip():
            return args
    # LangChain may normalize the provider payload into AIMessage.tool_calls and
    # remove the original OpenAI-shaped additional_kwargs entry.  A dict here is
    # semantically complete but no longer byte-exact, so serialize it canonically
    # for schema/whitespace diagnostics instead of reporting a false "raw 없음".
    for tc in getattr(raw_msg, "tool_calls", None) or []:
        args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
        if isinstance(args, str) and args.strip():
            return args
        if isinstance(args, dict):
            return json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    return ""


def _analyze_whitespace(text: str) -> dict[str, Any]:
    """JSON 원문의 공백 분포 분석 — 가드레일과 같은 축 (문자열 안/밖, 루트 종료 후).

    - ``max_ws_run_outside_strings``: JSON 문자열 밖 연속 공백 최대 길이
      (가드 중단 임계 32)
    - ``post_root_ws``: 루트 객체가 닫힌 뒤 꼬리 공백 수 (가드 중단 임계 8)
    - ``string_values_with_spaces``: 내부 공백을 가진 문자열 값 수
      (disable_any_whitespace 가 이 공백까지 지우면 안 됨 — 보존 확인용)
    """
    in_string = False
    escaped = False
    depth = 0
    root_closed = False
    ws_run = 0
    max_ws_run = 0
    post_root_ws = 0
    current_string_has_space = False
    strings_with_spaces = 0
    total_strings = 0

    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                total_strings += 1
                if current_string_has_space:
                    strings_with_spaces += 1
            elif ch in " \t\n\r":
                current_string_has_space = True
            continue
        if ch in " \t\n\r":
            ws_run += 1
            max_ws_run = max(max_ws_run, ws_run)
            if root_closed:
                post_root_ws += 1
            continue
        ws_run = 0
        if ch == '"':
            in_string = True
            current_string_has_space = False
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                root_closed = True

    return {
        "chars_total": len(text),
        "max_ws_run_outside_strings": max_ws_run,
        "post_root_ws": post_root_ws,
        "string_values_with_spaces": strings_with_spaces,
        "total_string_tokens": total_strings,
    }


def _report_whitespace(stats: dict[str, Any]) -> None:
    print("── 공백 분석 (가드레일과 같은 축) " + "─" * 30)
    print(f"  전체 문자 수                  : {stats['chars_total']}")
    ws = stats["max_ws_run_outside_strings"]
    verdict = "폭주 (임계 이상)" if ws >= WHITESPACE_FLOOD else "정상"
    print(f"  문자열 밖 최대 연속 공백      : {ws} (임계 {WHITESPACE_FLOOD}) → {verdict}")
    pr = stats["post_root_ws"]
    verdict = "폭주 (임계 이상)" if pr >= POST_ROOT_WHITESPACE_FLOOD else "정상"
    print(f"  루트 종료 후 꼬리 공백        : {pr} (임계 {POST_ROOT_WHITESPACE_FLOOD}) → {verdict}")
    print(
        f"  내부 공백 보존 문자열 값      : {stats['string_values_with_spaces']}"
        f" / {stats['total_string_tokens']}"
        "  (disable_any_whitespace 적용 후에도 0 이 되면 안 됨)"
    )
    print("─" * 66)


def _format_parsed_payload(parsed: Any) -> str:
    """Render a validated diagnostic payload without losing non-ASCII text."""
    dump = parsed.model_dump() if hasattr(parsed, "model_dump") else parsed
    return json.dumps(dump, ensure_ascii=False, indent=2)


def _format_compact_parsed_payload(parsed: Any) -> str:
    """Render row-owned slot results without hiding semantic review fields."""
    dump = parsed.model_dump() if hasattr(parsed, "model_dump") else parsed
    if not isinstance(dump, dict) or not isinstance(dump.get("slots"), dict):
        return json.dumps(dump, ensure_ascii=False, separators=(",", ":"))
    compact = [
        {
            "wire_id": wire_id,
            "status": value.get("status"),
            "meaning": value.get("meaning"),
            "missing_context": value.get("missing_context"),
        }
        for wire_id, value in dump["slots"].items()
    ]
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


async def _send(
    system_prompt: str,
    user_content: str,
    response_model: Optional[type[BaseModel]],
    response_schema: dict | None,
    label: str,
    max_tokens: Optional[int],
    use_guard: bool,
    structured_method: str | None = None,
    compact_payload: bool = False,
) -> str:
    """run_yaml_rule 의 전송 구간과 동일한 경로로 1회 호출하고 결과를 보고."""
    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    structured_method = structured_method or cfg.structured_output_method
    guard_on = use_guard and SETTINGS.analysis_llm.repetition_guard
    structured = response_schema is not None or response_model is not None
    provider_schema = (
        response_schema
        if response_schema is not None
        else response_model.model_json_schema() if response_model is not None else None
    )
    validation_schema = provider_schema
    schema_name = (
        "canonical JSON Schema"
        if response_schema is not None
        else response_model.__name__ if response_model else "(없음 — raw)"
    )
    _print_call_header(
        cfg,
        streaming=guard_on,
        schema_name=schema_name,
        max_tokens=max_tokens,
        structured_method=structured_method,
    )

    llm = build_llm_client(max_tokens=max_tokens, streaming=guard_on)
    runnable = (
        llm if not structured
        else bind_structured_output(
            llm, provider_schema, method=structured_method
        )
    )
    messages = apply_cache_friendly_filters(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_content)],
        provider=cfg.provider,
    )
    invoke_config = (
        {"callbacks": [RepetitionGuardCallback(f"gpu-diag:{label}")]} if guard_on else None
    )

    print(f"[{label}] 전송 시작 — system {len(system_prompt)}자 / user {len(user_content)}자")
    start = time.monotonic()
    try:
        result = await runnable.ainvoke(messages, config=invoke_config)
    except RepetitionLoopAborted as e:
        elapsed = time.monotonic() - start
        print(f"[{label}] ❌ 폭주 가드 중단 ({elapsed:.1f}s): {e}")
        print("  → GPU 서버에서 반복/공백 폭주가 재현됨. 부분 결과는 채택하지 않는다.")
        return ""
    except Exception as e:  # noqa: BLE001 — 진단 스크립트: 종류 표시 후 종료
        elapsed = time.monotonic() - start
        print(f"[{label}] ❌ 호출 실패 ({elapsed:.1f}s): {type(e).__name__}: {e}")
        raise SystemExit(1)
    elapsed = time.monotonic() - start

    if not structured:
        raw_msg, parsed = result, None
    else:
        raw_msg, parsed = result.get("raw"), result.get("parsed")
        parsing_error = result.get("parsing_error")
        try:
            if parsing_error is not None:
                raise parsing_error
            raw_content = getattr(raw_msg, "content", None)
            if isinstance(raw_content, str) and raw_content.strip():
                complete_payload = json.loads(raw_content)
                if complete_payload != parsed:
                    raise ValueError(
                        "provider parsed payload differs from complete raw JSON"
                    )
                parsed = complete_payload
            if validation_schema is not None:
                schema_payload = (
                    parsed.model_dump() if hasattr(parsed, "model_dump") else parsed
                )
                jsonschema.validate(instance=schema_payload, schema=validation_schema)
            if response_model is not None:
                parsed = response_model.model_validate(parsed)
        except Exception as error:  # noqa: BLE001 — 진단은 실패 종류를 그대로 보고 종료
            print(f"[{label}] ❌ 구조화 응답 검증 실패: {type(error).__name__}: {error}")
            raise SystemExit(1) from error

    usage = getattr(raw_msg, "usage_metadata", None) or {}
    print(f"[{label}] ✅ 완료 {elapsed:.1f}s — "
          f"input {usage.get('input_tokens', '?')} tok / output {usage.get('output_tokens', '?')} tok")
    print(f"  usage: {json.dumps(usage, ensure_ascii=False, separators=(',', ':'))}")

    raw_text = _extract_raw_text(raw_msg)
    if raw_text:
        print(
            "  raw: "
            f"chars={len(raw_text)} sha256={hashlib.sha256(raw_text.encode('utf-8')).hexdigest()}"
        )
        _report_whitespace(_analyze_whitespace(raw_text))
    else:
        print("  (raw 원문 없음 — 공백 분석 생략)")

    if parsed is not None:
        dump = parsed.model_dump() if hasattr(parsed, "model_dump") else parsed
        print(f"  파싱 결과 키: {list(dump)[:10] if isinstance(dump, dict) else type(dump).__name__}")
        print("  파싱 결과:")
        print(
            _format_compact_parsed_payload(parsed)
            if compact_payload
            else _format_parsed_payload(parsed)
        )
    elif structured:
        print("  ⚠ 스키마 파싱 실패 (parsed=None) — raw 원문을 직접 확인할 것")
    return raw_text


# ─────────────────────────────────────────────────────────────
# 서브커맨드
# ─────────────────────────────────────────────────────────────


def cmd_info(args: argparse.Namespace) -> None:
    """SGLang /get_server_info — 공백 폭주 관련 서버 인자 실제 활성값 확인."""
    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    base = (cfg.api_base or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    url = f"{base}/get_server_info"
    print(f"GET {url}")
    headers = {}
    if SETTINGS.analysis_llm.api_key:
        headers["Authorization"] = f"Bearer {SETTINGS.analysis_llm.api_key}"
    resp = httpx.get(url, timeout=30, verify=cfg.verify_ssl, headers=headers)
    resp.raise_for_status()
    info = resp.json()

    interesting = [
        "grammar_backend",
        "constrained_json_disable_any_whitespace",
        "model_path",
        "served_model_name",
        "max_running_requests",
        "context_length",
        "version",
    ]
    print("── 폭주 대응 관련 서버 활성값 " + "─" * 34)
    found = False
    for key in interesting:
        if key in info:
            print(f"  {key}: {info[key]}")
            found = True
    if not found:
        print("  (관심 키가 최상위에 없음 — --full 로 전체 확인)")
    print("─" * 66)
    if args.full:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(f"(전체 {len(info)}개 키 — 전부 보려면 --full)")


class WhitespaceProbeOutput(BaseModel):
    """probe 전용 소형 스키마 — 문자열 내부 공백 보존 확인이 목적."""

    echo: str
    words: list[str]
    count: int


def cmd_probe(args: argparse.Namespace) -> None:
    """작은 constrained JSON 호출 — 운영자 절차 3단계.

    disable_any_whitespace 적용 후에도 문자열 값 내부 공백('주문 상태 코드' 의
    띄어쓰기)이 보존되고, 구조 토큰 사이 formatting 공백만 사라지는지 확인한다.
    """
    system = (
        "너는 JSON 출력 테스트 도구다. 지시된 값을 정확히 그대로 스키마에 담아 반환한다. "
        "값을 변형하거나 번역하지 않는다."
    )
    user = (
        "다음을 그대로 반환하라.\n"
        "- echo: 주문 상태 코드는 접수 완료 상태다\n"
        "- words: [\"주문 상태\", \"접수 완료\", \"a b c\"]\n"
        "- count: 3"
    )
    asyncio.run(
        _send(
            system_prompt=system,
            user_content=user,
            response_model=WhitespaceProbeOutput,
            response_schema=None,
            label="probe",
            max_tokens=1024,
            use_guard=not args.no_guard,
        )
    )


def _validate_raw_against_schema(raw_text: str, schema: dict) -> dict:
    """Strictly validate an unconstrained replay against its recorded schema."""
    try:
        payload = json.loads(raw_text)
        jsonschema.validate(instance=payload, schema=schema)
    except (json.JSONDecodeError, jsonschema.ValidationError, jsonschema.SchemaError) as error:
        raise SystemExit(
            f"unconstrained replay가 recorded schema를 위반함: "
            f"{type(error).__name__}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise SystemExit("unconstrained replay root는 object여야 한다")
    return payload


def _select_replay_record(
    records: list[dict],
    *,
    target: str | None,
    node_key: str | None,
    index: int,
) -> dict:
    """Select one replay record; semantic selectors must be unambiguous."""
    if target or node_key:
        matched = records
        if target:
            matched = [
                record for record in matched
                if target in (record.get("target_id") or "")
            ]
        if node_key:
            narrowed: list[dict] = []
            for record in matched:
                user_content = record.get("user_content") or ""
                key_match = re.search(
                    r"^required_node_keys:\s*([^\r\n]+)$",
                    user_content,
                    flags=re.MULTILINE,
                )
                keys = {
                    value.strip()
                    for value in key_match.group(1).split(",")
                } if key_match else set()
                if node_key in keys:
                    narrowed.append(record)
            matched = narrowed
        if len(matched) != 1:
            raise SystemExit(
                "replay semantic selector 결과가 유일하지 않음: "
                f"{len(matched)}건 (target={target!r}, node_key={node_key!r})"
            )
        return matched[0]
    if not (0 <= index < len(records)):
        raise SystemExit(f"index {index} 범위 밖 (records {len(records)}건)")
    return records[index]


def cmd_replay(args: argparse.Namespace) -> None:
    """실행 기록의 실제 프롬프트 1건을 실제 호출 경로로 재전송 — 절차 4단계 단건판."""
    record_path = Path(args.run_dir) / "llm-prompts" / f"{args.rule}.jsonl"
    if not record_path.exists():
        raise SystemExit(f"기록 파일 없음: {record_path}")
    records = load_rule_records(record_path)
    if not records:
        raise SystemExit(f"records 비어 있음: {record_path}")

    record = _select_replay_record(
        records,
        target=args.target,
        node_key=args.node_key,
        index=args.index,
    )

    system_prompt = record.get("system_prompt") or ""
    user_content = record.get("user_content") or ""
    if not system_prompt or not user_content:
        raise SystemExit("record 에 system_prompt/user_content 전문이 없음 — 다른 rule/record 선택")

    if args.current_contract and args.current_summary_contract:
        raise SystemExit("--current-contract와 --current-summary-contract는 함께 쓸 수 없음")
    if args.current_contract:
        _require_current_contract_rule(args.rule)
        system_prompt = _current_system_contract(args.rule)
        current_fixed = _current_fixed_contract(args.rule)
        recorded_sha256 = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
        user_content = _replace_recorded_fixed_contract(user_content, current_fixed)
        user_content = _upgrade_recorded_input_markers(user_content)
        current_sha256 = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
        print(
            "고정 지시 prefix 제한 교체: "
            f"recorded_user_sha256={recorded_sha256} "
            f"replay_user_sha256={current_sha256}"
        )

    if args.current_summary_contract:
        current_contract = (
            REPO_ROOT / "llm" / "prompts" / "common" / "summary_contract.md"
        ).read_text(encoding="utf-8")
        recorded_sha256 = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
        user_content = _replace_recorded_summary_contract(
            user_content, current_contract,
        )
        if args.rule == "code_summary":
            user_content = _replace_framework_summary_envelope(user_content)
        current_sha256 = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
        print(
            "summary 계약 제한 교체: "
            f"recorded_user_sha256={recorded_sha256} "
            f"replay_user_sha256={current_sha256}"
        )

    response_schema = None if args.no_schema else (
        _current_summary_response_schema(args.rule, user_content)
        if args.current_summary_contract
        else _current_contract_response_schema(
            args.rule, record, user_content=user_content,
        )
        if args.current_contract
        else _recorded_response_schema(record)
    )
    structured_method = _resolve_replay_method(
        record, override=args.method, no_schema=args.no_schema,
    )
    max_tokens = args.max_tokens
    if max_tokens is None and args.current_contract:
        max_tokens = _current_rule_max_tokens(args.rule)

    print(f"재전송 대상: {record_path.name} → target_id={record.get('target_id')!r} "
          f"(records {len(records)}건 중 1건)")
    orig_meta = record.get("metadata") or {}
    if orig_meta:
        print(f"원 호출 기록: input {orig_meta.get('input_tokens')} tok / "
              f"output {orig_meta.get('output_tokens')} tok / {orig_meta.get('latency_ms')}ms")
        print(f"원 구조화 출력 방식: {orig_meta.get('structured_output_method')!r}")

    raw_text = asyncio.run(
        _send(
            system_prompt=system_prompt,
            user_content=user_content,
            response_model=None,
            response_schema=response_schema,
            label=f"replay:{args.rule}",
            max_tokens=max_tokens,
            use_guard=not args.no_guard,
            structured_method=structured_method,
            compact_payload=args.compact_payload,
        )
    )
    if args.no_schema and raw_text:
        try:
            _validate_raw_against_schema(raw_text, _recorded_response_schema(record))
        except SystemExit:
            # Unconstrained diagnostics exist to expose the provider's exact failure
            # shape.  Hiding the payload here made a malformed JSON result
            # indistinguishable from a prompt/content problem.
            print("── raw response (schema validation failed) " + "─" * 24)
            print(raw_text)
            print("─" * 66)
            raise
        print("  client-side recorded schema 검증: 통과")


def _recorded_response_schema(record: dict) -> dict:
    """Return an integrity-checked exact provider schema from one evidence record."""
    evidence = record.get("response_schema")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("schema"), dict):
        raise SystemExit("record 에 canonical response_schema evidence가 없음")
    schema = evidence["schema"]
    canonical = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    actual_sha256 = hashlib.sha256(canonical).hexdigest()
    if evidence.get("sha256") != actual_sha256 or evidence.get("bytes") != len(canonical):
        raise SystemExit("record response_schema fingerprint/bytes 불일치")
    return schema


def _require_current_contract_rule(rule: str) -> None:
    """Reject current replays for provider concerns removed from production."""
    if rule in {
        "code_rules_examples",
        "code_rules_examples_batch",
        "code_gate_rules_batch",
    }:
        raise SystemExit(
            "Rule 모델 계약은 폐기됐다. 저장된 historical record는 원형 replay만 "
            "가능하며 --current-contract로 되살릴 수 없다."
        )


def _current_contract_response_schema(
    rule: str,
    record: dict,
    *,
    user_content: str = "",
) -> dict:
    """Align a fixed-prefix replay with current wire changes for supported concerns."""
    recorded = _recorded_response_schema(record)
    if rule not in {"code_table_insights", "code_table_insights_batch"}:
        raise SystemExit(
            f"--current-contract schema 이관이 지원되지 않는 rule: {rule}"
        )
    # Column meaning moved to the final TABLE/COLUMN concern.  Dynamic node/table keys and
    # closed role enums stay byte-for-byte from the record; only the retired intermediate
    # ``columns`` field and its now-unreferenced map definitions are removed.
    current = json.loads(json.dumps(recorded))
    definitions = current.get("$defs") or {}
    changed = 0
    for definition in definitions.values():
        title = str(definition.get("title") or "")
        if not title.startswith("AssignedTableInsightValue_"):
            continue
        properties = definition.get("properties") or {}
        if "columns" in properties:
            properties.pop("columns")
            definition["required"] = [
                key for key in definition.get("required", []) if key != "columns"
            ]
            changed += 1
    for name in list(definitions):
        if name.startswith("AssignedColumnUsageMap_"):
            definitions.pop(name)
    if changed == 0:
        raise SystemExit("recorded TABLE usage schema에서 교체할 columns 필드를 찾지 못함")
    jsonschema.Draft202012Validator.check_schema(current)
    return current


def _current_fixed_contract(rule_name: str) -> str:
    """Render the current rule-owned fixed prefix without touching volatile INPUT."""
    candidates: list[str] = []
    for strategy in ("framework", "dbms"):
        prompts_dir = REPO_ROOT / "llm" / "prompts" / strategy
        if not (prompts_dir / f"{rule_name}.yaml").is_file():
            continue
        rule = _load_rule(prompts_dir, rule_name)
        template = str(rule.get("user_template") or "")
        match = re.search(r"(?m)^# \[INPUT[^\r\n]*\]\r?$", template)
        if match is None or re.search(
            r"(?m)^# \[INPUT[^\r\n]*\]\r?$", template[match.end():],
        ):
            raise SystemExit(f"현재 {strategy}/{rule_name} INPUT 경계가 정확히 1개가 아님")
        candidates.append(_safe_format(template[:match.start()], rule.get("static_values") or {}))
    if not candidates:
        raise SystemExit(f"현재 prompt rule을 찾지 못함: {rule_name}")
    if any(candidate != candidates[0] for candidate in candidates[1:]):
        raise SystemExit(
            f"framework/dbms의 {rule_name} 고정 계약이 다름 — strategy가 유일한 rule로 replay할 것"
        )
    return candidates[0]


def _current_system_contract(rule_name: str) -> str:
    """Render the current exact system prompt for the recorded rule owner."""
    candidates: list[str] = []
    for strategy in ("framework", "dbms"):
        prompts_dir = REPO_ROOT / "llm" / "prompts" / strategy
        if not (prompts_dir / f"{rule_name}.yaml").is_file():
            continue
        rule = _load_rule(prompts_dir, rule_name)
        system_prompt = _safe_format(
            str(rule.get("system") or ""), rule.get("static_values") or {},
        )
        if not system_prompt.strip():
            raise SystemExit(f"현재 {strategy}/{rule_name} system prompt가 비어 있음")
        candidates.append(system_prompt)
    if not candidates:
        raise SystemExit(f"현재 prompt rule을 찾지 못함: {rule_name}")
    if any(candidate != candidates[0] for candidate in candidates[1:]):
        raise SystemExit(
            f"framework/dbms의 {rule_name} system 계약이 다름 — strategy가 유일한 rule로 replay할 것"
        )
    return candidates[0]


def _current_rule_max_tokens(rule_name: str) -> int:
    """Resolve the exact current YAML token ceiling for a uniquely owned rule."""
    candidates: list[int] = []
    for strategy in ("framework", "dbms"):
        prompts_dir = REPO_ROOT / "llm" / "prompts" / strategy
        if not (prompts_dir / f"{rule_name}.yaml").is_file():
            continue
        value = _load_rule(prompts_dir, rule_name).get("max_tokens")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SystemExit(f"현재 {strategy}/{rule_name} max_tokens가 양의 정수가 아님")
        candidates.append(value)
    if not candidates:
        raise SystemExit(f"현재 prompt rule을 찾지 못함: {rule_name}")
    if any(candidate != candidates[0] for candidate in candidates[1:]):
        raise SystemExit(
            f"framework/dbms의 {rule_name} max_tokens 계약이 다름 — strategy가 유일한 rule로 replay할 것"
        )
    return candidates[0]


def _replace_recorded_fixed_contract(
    recorded_user_content: str,
    current_fixed_contract: str,
) -> str:
    """Replace only the rule-fixed prefix and preserve recorded volatile evidence bytes."""
    matches = list(re.finditer(
        r"(?m)^# \[INPUT[^\r\n]*\]\r?$", recorded_user_content,
    ))
    if len(matches) != 1:
        raise SystemExit("recorded user prompt의 INPUT 경계가 정확히 1개가 아님")
    if not current_fixed_contract.strip() or re.search(
        r"(?m)^# \[INPUT[^\r\n]*\]\r?$", current_fixed_contract,
    ):
        raise SystemExit("current fixed contract가 비었거나 INPUT을 포함함")
    return current_fixed_contract + recorded_user_content[matches[0].start():]


def _upgrade_recorded_input_markers(user_content: str) -> str:
    """Upgrade deterministic evidence markers when replaying the current contract.

    Fixed-prefix replacement preserves recorded target evidence. Marker syntax is also part of
    the producer/consumer contract, so an old child summary must not keep masquerading as source
    after production marks that same payload as a non-source candidate.
    """
    return re.sub(
        r"\[CONTEXT_SUMMARY child_range=([^\s\]]+) ",
        r"[CHILD_SUMMARY_CANDIDATE child_range=\1 authority=non_source ",
        user_content,
    )


def _current_summary_response_schema(rule: str, user_content: str) -> dict:
    """Build the current summary schema while keeping replay evidence fixed.

    The current contract owns new sufficiency fields, so combining it with the
    historical schema would create an impossible prompt/schema pair.  Only the
    summary boundary is supported; every other concern must keep its recorded
    integrity-checked schema.
    """
    from pipeline.models.summary_response_schema import (
        batch_summary_response_model,
        summary_response_model,
    )

    if rule == "code_summary":
        return summary_response_model("framework").model_json_schema()
    if rule == "code_summary_batch":
        key_match = re.search(
            r"^required_node_keys:\s*([^\r\n]+)$",
            user_content,
            flags=re.MULTILINE,
        )
        if key_match is None:
            raise SystemExit("현재 DBMS summary schema에 필요한 required_node_keys가 없음")
        keys = tuple(
            value.strip() for value in key_match.group(1).split(",") if value.strip()
        )
        if not keys or len(keys) != len(set(keys)):
            raise SystemExit("required_node_keys가 비었거나 중복됨")
        return batch_summary_response_model(keys, "dbms").model_json_schema()
    raise SystemExit(
        "--current-summary-contract는 code_summary/code_summary_batch만 지원함"
    )


def _replace_recorded_summary_contract(
    recorded_user_content: str,
    current_contract: str,
) -> str:
    """Replace exactly one recorded summary section without mutating evidence."""
    historical_headings = (
        "## summary — 목적 헤더 계약",
        "## summary + evidence_status + missing_context — 전체 흐름 의미 계약",
        "## [INPUT GUIDE — 각 값의 뜻·권한·허용 판정]",
    )
    current_heading = historical_headings[-1]
    next_headings = (
        "\n## stereotype — 역할 분류 계약",
        "\n## [DECISION — stereotype 역할 분류]",
    )
    current = current_contract.strip()
    if not current.startswith(current_heading) or current.count(current_heading) != 1:
        raise SystemExit("current summary contract heading이 정확하지 않음")
    if any(heading in current for heading in next_headings):
        raise SystemExit("current summary contract가 다음 concern 절까지 포함함")
    candidates = [
        heading for heading in historical_headings
        if recorded_user_content.count(heading) == 1
    ]
    if len(candidates) != 1:
        raise SystemExit("recorded summary contract heading이 정확히 1개가 아님")
    recorded_heading = candidates[0]
    start = recorded_user_content.index(recorded_heading)
    boundaries = [
        position for heading in next_headings
        if (position := recorded_user_content.find(
            heading, start + len(recorded_heading)
        )) >= 0
    ]
    if not boundaries:
        raise SystemExit("recorded summary contract의 stereotype 경계를 찾지 못함")
    end = min(boundaries)
    return recorded_user_content[:start] + current + "\n" + recorded_user_content[end:]


def _replace_framework_summary_envelope(user_content: str) -> str:
    """Align a historical single-summary replay with the current wire envelope."""
    old = "이 요청은 `summary`, `stereotype`, `questions` 세 필드만 출력한다."
    new = (
        "이 요청은 최상위 `result` 하나를 출력하고, 그 안에 `summary`, `stereotype`, "
        "`evidence_status`, `missing_context`, `questions` 다섯 필드만 출력한다."
    )
    old_count = user_content.count(old)
    current_prefix = "이 요청은 최상위 `result` 하나를 출력하고"
    current_suffix = "`missing_context`, `questions` 다섯 필드만 출력한다."
    current_count = min(
        user_content.count(current_prefix), user_content.count(current_suffix),
    )
    if old_count == 1 and current_count == 0:
        return user_content.replace(old, new, 1)
    if old_count == 0 and current_count == 1:
        return user_content
    raise SystemExit("recorded Framework summary 출력 envelope 문구가 정확히 1개가 아님")


def _resolve_replay_method(
    record: dict,
    *,
    override: str | None,
    no_schema: bool,
) -> str | None:
    """Resolve the replay channel without pretending current config is history."""
    if override is not None:
        return override
    if no_schema:
        return None
    metadata = record.get("metadata") or {}
    method = metadata.get("structured_output_method")
    if method not in {"json_schema", "function_calling"}:
        raise SystemExit(
            "record 에 structured_output_method evidence가 없음 — "
            "동일 채널을 추정하지 말고 --method로 명시할 것"
        )
    return method


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="SGLang 서버 활성 설정 확인 (읽기 전용)")
    p_info.add_argument("--full", action="store_true", help="server info 전체 JSON 출력")
    p_info.set_defaults(func=cmd_info)

    p_probe = sub.add_parser("probe", help="작은 constrained JSON 로 공백 동작 확인")
    p_probe.add_argument("--no-guard", action="store_true", help="폭주 가드 없이 전송")
    p_probe.set_defaults(func=cmd_probe)

    p_replay = sub.add_parser("replay", help="실행 기록의 실제 프롬프트 재전송")
    p_replay.add_argument("--run-dir", required=True, help="_runs/<run> 디렉터리")
    p_replay.add_argument("--rule", required=True, help="recorded rule 이름")
    p_replay.add_argument("--index", type=int, default=0, help="records 배열 인덱스 (기본 0)")
    p_replay.add_argument("--target", help="target_id 부분 일치로 record 선택 (index 대신)")
    p_replay.add_argument(
        "--node-key",
        help="required_node_keys의 정확한 key로 record를 좁힘 (--target과 조합 권장)",
    )
    p_replay.add_argument("--max-tokens", type=int, default=None,
                          help="비상 브레이크 override (current-contract 기본: rule YAML)")
    p_replay.add_argument("--no-schema", action="store_true",
                          help="constrained JSON 없이 raw 재전송 (문법 제약 효과 분리용)")
    p_replay.add_argument(
        "--current-summary-contract", action="store_true",
        help="저장 user prompt의 summary 계약과 응답 schema만 현재 계약으로 교체",
    )
    p_replay.add_argument(
        "--current-contract", action="store_true",
        help="저장 가변 INPUT은 유지하고 해당 rule의 현재 고정 지시 prefix만 교체",
    )
    p_replay.add_argument(
        "--method", choices=("json_schema", "function_calling"),
        default=None, help="recorded provider 강제 방식 override (기본: record metadata)",
    )
    p_replay.add_argument("--no-guard", action="store_true", help="폭주 가드 없이 전송")
    p_replay.add_argument(
        "--compact-payload", action="store_true",
        help="semantic review 필드는 보존하고 parsed payload를 한 줄 slot 목록으로 출력",
    )
    p_replay.set_defaults(func=cmd_replay)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    _configure_utf8_console(sys.stdout, sys.stderr)
    main()
