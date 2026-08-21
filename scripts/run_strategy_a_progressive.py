"""Run the sealed Strategy-A actual-data pilot without Analyzer or graph writes."""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx


REPO = Path(__file__).resolve().parents[1]
SPEC127 = REPO / "specs" / "127-cross-strategy-evidence-validation"
SPEC131 = REPO / "specs" / "131-cross-node-semantic-grounding"
DEFAULT_SEAL = SPEC131 / "evidence" / "strategy-a-progressive-pilot-seal-20260821.json"
ALLOWED_OUTPUT_ROOT = (SPEC131 / "_runs" / "framework" / "strategy-a").resolve()
HISTORICAL_RUNNER = SPEC127 / "run_strategy_a_v3.py"
HISTORICAL_RESULTS = SPEC127 / "evidence" / "strategy-a-v3-results.jsonl"
HISTORICAL_SUMMARY = SPEC127 / "evidence" / "strategy-a-v3-summary.json"

CONTROL_VARIANT = "historical_wire_control"
CANDIDATE_VARIANT = "lossless_candidate"
VARIANTS = (CONTROL_VARIANT, CANDIDATE_VARIANT)

HISTORICAL_HASHES = {
    HISTORICAL_RUNNER: "db5ea924ae3193644527d907b157e3f370d179ebcecbb9adf6099dd370a0aa58",
    HISTORICAL_RESULTS: "8c14d1928a6fea3afe4720a6cd343fe5eacff8b772f4d43bd9009a7b1d24f962",
    HISTORICAL_SUMMARY: "9c01436e3602da477555f66503ea7fc8faff9f7ecf6326a3c8b1858bcaf4c45d",
}

SYSTEM = """당신은 parser가 확정한 분기 증거를 검증 가능한 Python 관계식으로 옮기는 분석가다.
좌표·조건·직접 효과의 소유권은 입력 증거를 그대로 지키고, 이름·값·업무 뜻을 창작하지 않는다.
각 행은 Python 식으로 파싱 가능해야 하며 지정된 JSON schema 외에는 출력하지 않는다.
"""

CONTRACT = """# Strategy A v3 — 검증 가능한 Python 관계식

아래 evidence의 `rows` 각 키를 정확히 한 번 출력한다.

- `given`: `when`과 `conditions`가 입증한 도달 조건을 Python 비교식으로 쓴다. 조건이 없으면 `True`.
- `then_relation`: 그 행의 `direct`와 `effects`가 입증한 직접 결과만 Python 식으로 쓴다.
  대입은 `target == source`, 반환은 `ret == value`, 호출은 `called("callee")`, 여러 결과는 `and`로 잇는다.
- `semantic_status`: 모든 식을 증거만으로 정확히 쓸 수 있으면 `exact`, 실행 의미를 확정할 증거가
  부족하면 `needs_context`, 해당 경로가 제공된 값 흐름으로 명백히 불가능하면 `unreachable`.
- C의 `->`는 `.`, `&&`/`||`/`!`는 `and`/`or`/`not`으로만 정규화한다.
- 입력에 없는 변수·상수·문자열·수치·도메인 뜻을 만들지 않는다. 초기값을 하위 제어 실행 뒤 값으로
  접어 계산하지 않는다. `nested_control`의 자식 효과를 부모로 복사하지 않는다.
- `post_condition`은 본문 진입 조건이 아니다. 본문은 먼저 실행되고 그 조건은 다음 반복만 판정한다.
- 직접 결과를 관계식으로 손실 없이 표현할 수 없으면 억지 식을 만들지 말고 `needs_context`와 빈 문자열을 쓴다.

[동일 증거 payload]
"""

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_IDENTIFIER = re.compile(r"[^\W\d]\w*", re.UNICODE)
_NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?![\w.])")
_QUOTED_LITERAL = re.compile(r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''')
_LEAF_FIELDS = {"given", "then_relation", "semantic_status"}
_STATUSES = {"exact", "needs_context", "unreachable"}
_DSL_NAMES = {"called"}


@dataclass(frozen=True)
class SelectedRecord:
    ledger_ordinal: int
    record_key: str
    target_id: str
    function_name: str
    user_content: str
    evidence: dict[str, Any]
    rows: tuple[str, ...]
    prompt_sha256: str


@dataclass(frozen=True)
class PreparedPilot:
    seal_path: Path
    seal: dict[str, Any]
    records: tuple[SelectedRecord, ...]
    summary: dict[str, Any]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_json_no_duplicates(text: str) -> Any:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    return json.loads(text, object_pairs_hook=pairs_hook)


def _history_module() -> Any:
    spec = importlib.util.spec_from_file_location("_sealed_strategy_a_v3", HISTORICAL_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load historical runner: {HISTORICAL_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(record: dict[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
    original = str(record["user_content"])
    marker = "response_locale:"
    try:
        start = original.index(marker)
        evidence_text = original[start:]
        evidence_json = evidence_text.split("rule_evidence_json:\n", 1)[1]
    except ValueError as error:
        raise ValueError(f"prompt evidence marker missing for {record.get('target_id')}") from error
    evidence = parse_json_no_duplicates(evidence_json)
    rows_object = evidence.get("rows") if isinstance(evidence, dict) else None
    if not isinstance(rows_object, dict) or not rows_object:
        raise ValueError(f"prompt evidence rows missing for {record.get('target_id')}")
    rows = list(rows_object)
    return CONTRACT + evidence_text, evidence, rows


def build_schema(rows: Iterable[str], variant: str) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported Strategy A variant: {variant}")
    given: dict[str, Any] = {"type": "string"}
    relation: dict[str, Any] = {"type": "string"}
    if variant == CONTROL_VARIANT:
        given["maxLength"] = 500
        relation["maxLength"] = 1000
    leaf = {
        "type": "object",
        "properties": {
            "given": given,
            "then_relation": relation,
            "semantic_status": {
                "type": "string",
                "enum": ["exact", "needs_context", "unreachable"],
            },
        },
        "required": ["given", "then_relation", "semantic_status"],
        "additionalProperties": False,
    }
    row_list = list(rows)
    return {
        "name": "strategy_a_relations",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "rules": {
                    "type": "object",
                    "properties": {row: copy.deepcopy(leaf) for row in row_list},
                    "required": row_list,
                    "additionalProperties": False,
                }
            },
            "required": ["rules"],
            "additionalProperties": False,
        },
    }


def build_request(user_content: str, rows: Iterable[str], config: Any, variant: str) -> dict[str, Any]:
    extra = dict(config.extra_body or {})
    reserved = {"model", "messages", "temperature", "top_p", "max_tokens", "response_format"}
    collisions = sorted(reserved.intersection(extra))
    if collisions:
        raise ValueError(f"LLM extra_body overrides sealed request fields: {collisions}")
    max_tokens = 4096 if variant == CONTROL_VARIANT else config.max_tokens
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError(f"invalid output budget for {variant}: {max_tokens!r}")
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_content},
        ],
        "temperature": config.sampling.temperature,
        "top_p": config.sampling.top_p,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": build_schema(rows, variant)},
        **extra,
    }


def _record_manifest_hash(records: Iterable[dict[str, Any]]) -> str:
    hashes = sorted(
        _sha256_bytes((str(record["target_id"]) + "\0" + str(record["user_content"])).encode("utf-8"))
        for record in records
    )
    return _sha256_bytes("\n".join(hashes).encode("utf-8"))


def _repo_path(value: str) -> Path:
    path = (REPO / value).resolve()
    try:
        path.relative_to(REPO.resolve())
    except ValueError as error:
        raise ValueError(f"sealed path escapes repository: {value}") from error
    return path


def _prepare_pilot(seal_path: Path) -> PreparedPilot:
    seal_path = seal_path.resolve()
    seal = parse_json_no_duplicates(seal_path.read_text(encoding="utf-8"))
    if seal.get("selection_state") != "sealed_before_current_model_result_access":
        raise ValueError("pilot selection is not sealed before result access")

    source_contract = seal["source"]
    sample_path = _repo_path(source_contract["sample"])
    gold_path = _repo_path(source_contract["gold"])
    ledger_path = _repo_path(source_contract["prompt_ledger"])
    for path, expected in (
        (sample_path, source_contract["sample_sha256"]),
        (gold_path, source_contract["gold_sha256"]),
    ):
        actual = _sha256_file(path)
        if actual != expected:
            raise ValueError(f"sealed hash mismatch: {path} expected={expected} actual={actual}")

    sample = parse_json_no_duplicates(sample_path.read_text(encoding="utf-8"))
    gold = parse_json_no_duplicates(gold_path.read_text(encoding="utf-8"))
    source_root = Path(str(sample["source_root"]).replace("/", os.sep)).resolve()
    source_mismatches: list[dict[str, str]] = []
    for relative, expected in sample["source_files"].items():
        source_path = (source_root / relative).resolve()
        try:
            source_path.relative_to(source_root)
        except ValueError:
            source_mismatches.append({"path": relative, "reason": "escapes_source_root"})
            continue
        if not source_path.is_file():
            source_mismatches.append({"path": relative, "reason": "missing"})
            continue
        actual = _sha256_file(source_path)
        if actual != expected:
            source_mismatches.append({"path": relative, "reason": "hash_mismatch"})
    if source_mismatches:
        raise ValueError(f"sealed source mismatch: {source_mismatches}")

    expected_functions = {item["name"]: item for item in seal["functions"]}
    raw_records = [
        parse_json_no_duplicates(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected_raw = [
        record for record in raw_records
        if str(record.get("target_id") or "").rsplit(":", 1)[-1] in expected_functions
    ]
    ledger_hash = _record_manifest_hash(selected_raw)
    if ledger_hash != source_contract["selected_prompt_ledger_sha256"]:
        raise ValueError(
            "selected prompt ledger mismatch: "
            f"expected={source_contract['selected_prompt_ledger_sha256']} actual={ledger_hash}"
        )

    records: list[SelectedRecord] = []
    counts = {name: {"records": 0, "rows": 0} for name in expected_functions}
    for ordinal, raw in enumerate(raw_records):
        function_name = str(raw.get("target_id") or "").rsplit(":", 1)[-1]
        if function_name not in expected_functions:
            continue
        user_content, evidence, rows = _payload(raw)
        record_key = _sha256_bytes(
            (str(raw["target_id"]) + "\0" + str(raw["user_content"])).encode("utf-8")
        )
        records.append(SelectedRecord(
            ledger_ordinal=ordinal,
            record_key=record_key,
            target_id=str(raw["target_id"]),
            function_name=function_name,
            user_content=user_content,
            evidence=evidence,
            rows=tuple(rows),
            prompt_sha256=_sha256_bytes((SYSTEM + "\0" + user_content).encode("utf-8")),
        ))
        counts[function_name]["records"] += 1
        counts[function_name]["rows"] += len(rows)

    gold_by_function = {item["function"]: item for item in gold["functions"]}
    gold_cases = 0
    for name, expected in expected_functions.items():
        actual = counts[name]
        if actual["records"] != expected["records"] or actual["rows"] != expected["rows"]:
            raise ValueError(f"sealed function population mismatch for {name}: {actual} != {expected}")
        cases = len(gold_by_function[name]["cases"])
        if cases != expected["gold_cases"]:
            raise ValueError(f"sealed gold population mismatch for {name}: {cases} != {expected['gold_cases']}")
        gold_cases += cases

    totals = seal["totals"]
    logical_rows = sum(len(record.rows) for record in records)
    if len(records) != totals["logical_records"] or logical_rows != totals["logical_rows"]:
        raise ValueError("sealed total record/row population mismatch")
    if gold_cases != totals["gold_cases"]:
        raise ValueError("sealed total gold population mismatch")

    history_mismatches: list[str] = []
    for path, expected in HISTORICAL_HASHES.items():
        actual = _sha256_file(path) if path.is_file() else "missing"
        if actual != expected:
            history_mismatches.append(f"{path.name}:{actual}")
    if not history_mismatches:
        history = _history_module()
        if history.SYSTEM != SYSTEM:
            history_mismatches.append("system_bytes")
        if history.CONTRACT != CONTRACT:
            history_mismatches.append("contract_bytes")
        selected_by_key = {
            _sha256_bytes((str(raw["target_id"]) + "\0" + str(raw["user_content"])).encode("utf-8")): raw
            for raw in selected_raw
        }
        for record in records:
            raw = selected_by_key[record.record_key]
            old_user, old_evidence, old_rows = history._payload(raw)
            if old_user != record.user_content or old_evidence != record.evidence or old_rows != list(record.rows):
                history_mismatches.append(f"payload:{record.record_key}")
            if history._schema(list(record.rows)) != build_schema(record.rows, CONTROL_VARIANT):
                history_mismatches.append(f"schema:{record.record_key}")
    if history_mismatches:
        raise ValueError(f"historical control is not immutable: {history_mismatches}")

    summary = {
        "seal": seal_path.relative_to(REPO).as_posix(),
        "seal_sha256": _sha256_file(seal_path),
        "source_files": len(sample["source_files"]),
        "source_mismatches": source_mismatches,
        "logical_records": len(records),
        "logical_rows": logical_rows,
        "gold_cases": gold_cases,
        "selected_prompt_ledger_sha256": ledger_hash,
        "history_mismatches": history_mismatches,
        "variants": list(VARIANTS),
        "current_model_calls": len(records) * len(VARIANTS),
    }
    return PreparedPilot(seal_path=seal_path, seal=seal, records=tuple(records), summary=summary)


def load_preflight(seal_path: Path = DEFAULT_SEAL) -> dict[str, Any]:
    return copy.deepcopy(_prepare_pilot(seal_path).summary)


def _row_closure(evidence: dict[str, Any], row_key: str) -> Any:
    row = evidence.get("rows", {}).get(row_key, {})
    closure: dict[str, Any] = {"row": row}
    conditions = evidence.get("conditions") if isinstance(evidence.get("conditions"), dict) else {}
    effects = evidence.get("effects") if isinstance(evidence.get("effects"), dict) else {}
    closure["conditions"] = {
        key: conditions.get(key[1:] if isinstance(key, str) and key.startswith("!") else key)
        for key in row.get("when", [])
    }
    closure["effects"] = {key: effects.get(key) for key in row.get("direct", [])}
    return closure


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.append(str(key))
            result.extend(_string_values(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_string_values(item))
        return result
    if value is None:
        return []
    return [str(value)]


def _grounding_failures(expression: str, evidence: Any) -> list[dict[str, Any]]:
    tree = ast.parse(expression, mode="eval")
    strings = _string_values(evidence)
    identifiers = {token for value in strings for token in _IDENTIFIER.findall(value)}
    numbers = {token for value in strings for token in _NUMBER.findall(value)}
    decoded_literals: set[str] = set()
    for value in strings:
        for token in _QUOTED_LITERAL.findall(value):
            try:
                decoded = ast.literal_eval(token)
            except (SyntaxError, ValueError):
                continue
            if isinstance(decoded, str):
                decoded_literals.add(decoded)
    failures: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: Any) -> None:
        key = (kind, repr(value))
        if key not in seen:
            seen.add(key)
            failures.append({"kind": kind, "value": value})

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in identifiers and node.id not in _DSL_NAMES:
            add("identifier", node.id)
        elif isinstance(node, ast.Attribute) and node.attr not in identifiers:
            add("attribute", node.attr)
        elif isinstance(node, ast.Constant):
            if node.value is None or isinstance(node.value, bool):
                continue
            if isinstance(node.value, str):
                if node.value not in decoded_literals and not any(node.value in value for value in strings):
                    add("string_literal", node.value)
            elif isinstance(node.value, (int, float)) and str(node.value) not in numbers:
                add("number_literal", node.value)
    return failures


def validate_response(text: str, evidence: dict[str, Any], expected_rows: Iterable[str]) -> dict[str, Any]:
    expected = list(expected_rows)
    result: dict[str, Any] = {
        "json_valid": False,
        "json_error": None,
        "rules_object": False,
        "row_coverage": False,
        "missing_rows": expected.copy(),
        "extra_rows": [],
        "schema_failures": [],
        "python_parse_failures": [],
        "grounding_failures": [],
        "parsed_rows": 0,
    }
    try:
        parsed = parse_json_no_duplicates(text)
    except (json.JSONDecodeError, ValueError) as error:
        result["json_error"] = f"{type(error).__name__}: {error}"
        return result
    result["json_valid"] = True
    rules = parsed.get("rules") if isinstance(parsed, dict) else None
    if not isinstance(rules, dict):
        result["schema_failures"].append({"reason": "rules_not_object"})
        return result
    result["rules_object"] = True
    actual_keys = list(rules)
    result["missing_rows"] = sorted(set(expected) - set(actual_keys))
    result["extra_rows"] = sorted(set(actual_keys) - set(expected))
    result["row_coverage"] = not result["missing_rows"] and not result["extra_rows"]
    result["parsed_rows"] = len(set(expected).intersection(actual_keys))

    for row_key in expected:
        if row_key not in rules:
            continue
        leaf = rules[row_key]
        if not isinstance(leaf, dict):
            result["schema_failures"].append({"row": row_key, "reason": "leaf_not_object"})
            continue
        fields = set(leaf)
        if fields != _LEAF_FIELDS:
            result["schema_failures"].append({
                "row": row_key,
                "reason": "leaf_fields",
                "missing": sorted(_LEAF_FIELDS - fields),
                "extra": sorted(fields - _LEAF_FIELDS),
            })
        status = leaf.get("semantic_status")
        if status not in _STATUSES:
            result["schema_failures"].append({"row": row_key, "reason": "semantic_status", "value": status})
        closure = _row_closure(evidence, row_key)
        for field in ("given", "then_relation"):
            expression = leaf.get(field)
            if not isinstance(expression, str):
                result["schema_failures"].append({"row": row_key, "field": field, "reason": "not_string"})
                continue
            required = field == "given" or status == "exact"
            if not expression:
                if required:
                    result["python_parse_failures"].append({"row": row_key, "field": field, "reason": "empty"})
                continue
            try:
                ast.parse(expression, mode="eval")
            except (SyntaxError, ValueError) as error:
                result["python_parse_failures"].append({
                    "row": row_key, "field": field, "reason": f"{type(error).__name__}: {error}",
                })
                continue
            for failure in _grounding_failures(expression, closure):
                result["grounding_failures"].append({"row": row_key, "field": field, **failure})
    return result


def resolve_output_dir(root: Path, run_id: str) -> Path:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    root = root.resolve()
    output = (root / run_id).resolve()
    try:
        output.relative_to(root)
    except ValueError as error:
        raise ValueError(f"run id escapes sealed output root: {run_id}") from error
    if output.exists():
        raise FileExistsError(f"run output already exists: {output}")
    return output


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _git_state() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=REPO, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    return {"head": head, "dirty": bool(status), "dirty_paths": status}


def _history_summary(prepared: PreparedPilot) -> dict[str, Any]:
    by_prompt = {record.prompt_sha256: record for record in prepared.records}
    results = [
        parse_json_no_duplicates(line)
        for line in HISTORICAL_RESULTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = [result for result in results if result.get("prompt_sha256") in by_prompt]
    metrics = {
        "expected_results": len(prepared.records) * 3,
        "results": len(selected),
        "ok": 0,
        "json_valid": 0,
        "row_coverage": 0,
        "python_parse_clean": 0,
        "grounding_clean": 0,
        "expected_rows": 0,
        "parsed_rows": 0,
        "python_parse_failures": 0,
        "grounding_failures": 0,
    }
    for old in selected:
        record = by_prompt[old["prompt_sha256"]]
        metrics["expected_rows"] += len(record.rows)
        if old.get("ok"):
            metrics["ok"] += 1
        if "response" not in old:
            continue
        validation = validate_response(
            json.dumps(old["response"], ensure_ascii=False), record.evidence, record.rows,
        )
        metrics["json_valid"] += int(validation["json_valid"])
        metrics["row_coverage"] += int(validation["row_coverage"])
        metrics["python_parse_clean"] += int(not validation["python_parse_failures"])
        metrics["grounding_clean"] += int(not validation["grounding_failures"])
        metrics["parsed_rows"] += validation["parsed_rows"]
        metrics["python_parse_failures"] += len(validation["python_parse_failures"])
        metrics["grounding_failures"] += len(validation["grounding_failures"])
    metrics["result_residual"] = metrics["expected_results"] - metrics["results"]
    return metrics


def _variant_summary(results: list[dict[str, Any]], records: tuple[SelectedRecord, ...]) -> dict[str, Any]:
    expected_rows = sum(len(record.rows) for record in records)
    summary = {
        "expected_records": len(records),
        "records": len(results),
        "http_ok": 0,
        "finish_stop": 0,
        "record_valid": 0,
        "json_valid": 0,
        "row_coverage": 0,
        "expected_rows": expected_rows,
        "parsed_rows": 0,
        "schema_failures": 0,
        "python_parse_failures": 0,
        "grounding_failures": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "latency_ms": 0,
    }
    for result in results:
        summary["http_ok"] += int(result.get("http_ok", False))
        summary["finish_stop"] += int(result.get("finish_reason") == "stop")
        summary["record_valid"] += int(result.get("record_valid", False))
        validation = result.get("validation") or {}
        summary["json_valid"] += int(validation.get("json_valid", False))
        summary["row_coverage"] += int(validation.get("row_coverage", False))
        summary["parsed_rows"] += int(validation.get("parsed_rows", 0))
        summary["schema_failures"] += len(validation.get("schema_failures") or [])
        summary["python_parse_failures"] += len(validation.get("python_parse_failures") or [])
        summary["grounding_failures"] += len(validation.get("grounding_failures") or [])
        usage = result.get("usage") or {}
        summary["input_tokens"] += int(usage.get("prompt_tokens") or 0)
        summary["output_tokens"] += int(usage.get("completion_tokens") or 0)
        summary["latency_ms"] += int(result.get("latency_ms") or 0)
    summary["record_residual"] = summary["expected_records"] - summary["records"]
    summary["row_residual"] = summary["expected_rows"] - summary["parsed_rows"]
    return summary


def _invoke(
    record: SelectedRecord,
    variant: str,
    config: Any,
    url: str,
    api_key: str,
    verify_ssl: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    request = build_request(record.user_content, record.rows, config, variant)
    started = time.perf_counter()
    result: dict[str, Any] = {
        "schema": "strategy-a-progressive-result/v1",
        "variant": variant,
        "ledger_ordinal": record.ledger_ordinal,
        "record_key": record.record_key,
        "target_id": record.target_id,
        "function": record.function_name,
        "expected_rows": list(record.rows),
        "prompt_sha256": record.prompt_sha256,
        "system_sha256": _sha256_bytes(SYSTEM.encode("utf-8")),
        "user_sha256": _sha256_bytes(record.user_content.encode("utf-8")),
        "schema_sha256": _sha256_bytes(_canonical_json(request["response_format"]).encode("utf-8")),
        "system": SYSTEM,
        "user": record.user_content,
        "response_schema": request["response_format"],
        "request_parameters": {
            key: value for key, value in request.items()
            if key not in {"messages", "response_format"}
        },
    }
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=request,
            timeout=timeout_seconds,
            verify=verify_ssl,
        )
        result["http_status"] = response.status_code
        result["http_ok"] = response.is_success
        try:
            raw = response.json()
        except ValueError:
            raw = {"non_json_body": response.text}
        result["raw_response"] = raw
        if response.is_success:
            choices = raw.get("choices") if isinstance(raw, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices else {}
            message = choice.get("message") if isinstance(choice, dict) else {}
            content = message.get("content") if isinstance(message, dict) else None
            result["finish_reason"] = choice.get("finish_reason") if isinstance(choice, dict) else None
            result["usage"] = raw.get("usage") or {}
            if isinstance(content, str):
                result["raw_content"] = content
                result["validation"] = validate_response(content, record.evidence, record.rows)
            else:
                result["validation"] = {
                    "json_valid": False,
                    "json_error": "response content is not a string",
                    "rules_object": False,
                    "row_coverage": False,
                    "missing_rows": list(record.rows),
                    "extra_rows": [],
                    "schema_failures": [],
                    "python_parse_failures": [],
                    "grounding_failures": [],
                    "parsed_rows": 0,
                }
        else:
            result["error"] = f"HTTP {response.status_code}"
    except Exception as error:  # evidence preserves the exact single-attempt failure
        result["http_ok"] = False
        result["error"] = f"{type(error).__name__}: {error}"
    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
    validation = result.get("validation") or {}
    result["record_valid"] = bool(
        result.get("http_ok")
        and result.get("finish_reason") == "stop"
        and validation.get("json_valid")
        and validation.get("row_coverage")
        and not validation.get("schema_failures")
        and not validation.get("python_parse_failures")
        and not validation.get("grounding_failures")
    )
    return result


def execute_pilot(seal_path: Path, run_id: str, workers: int) -> Path:
    prepared = _prepare_pilot(seal_path)
    sealed_root = _repo_path(prepared.seal["output_root"])
    if sealed_root != ALLOWED_OUTPUT_ROOT:
        raise ValueError(f"sealed output root is not allowed: {sealed_root}")
    if workers <= 0:
        raise ValueError("workers must be positive")
    sealed_root.mkdir(parents=True, exist_ok=True)
    output = resolve_output_dir(sealed_root, run_id)
    output.mkdir()
    records_dir = output / "records"
    records_dir.mkdir()

    sys.path.insert(0, str(REPO))
    from llm.configs.loader import load_llm_config  # pylint: disable=import-outside-toplevel
    from shared.config.settings import SETTINGS  # pylint: disable=import-outside-toplevel

    config = load_llm_config(SETTINGS.analysis_llm.config)
    if not config.api_base or not SETTINGS.analysis_llm.api_key:
        raise RuntimeError("analysis LLM endpoint or key is unavailable")
    url = config.api_base.rstrip("/") + "/chat/completions"
    manifest = {
        "schema": "strategy-a-progressive-run/v1",
        "status": "running",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "preflight": prepared.summary,
        "git": _git_state(),
        "model": config.model,
        "provider": config.provider,
        "workers": min(workers, len(prepared.records) * len(VARIANTS)),
        "variants": list(VARIANTS),
        "timeout_seconds": config.timeout_seconds,
        "candidate_max_tokens": config.max_tokens,
    }
    _atomic_json(output / "manifest.json", manifest)
    jobs = [(record, variant) for variant in VARIANTS for record in prepared.records]
    results: list[dict[str, Any]] = []
    invoke: Callable[[SelectedRecord, str], dict[str, Any]] = lambda record, variant: _invoke(
        record,
        variant,
        config,
        url,
        SETTINGS.analysis_llm.api_key,
        config.verify_ssl,
        config.timeout_seconds,
    )
    with ThreadPoolExecutor(max_workers=manifest["workers"]) as executor:
        futures = {executor.submit(invoke, record, variant): (record, variant) for record, variant in jobs}
        for future in as_completed(futures):
            record, variant = futures[future]
            result = future.result()
            results.append(result)
            filename = f"{variant}-{record.ledger_ordinal:04d}-{record.record_key[:16]}.json"
            _atomic_json(records_dir / filename, result)
            print(
                f"{variant} {record.function_name} {record.record_key[:10]} "
                f"{'valid' if result['record_valid'] else 'FAILED'} {result['latency_ms']}ms",
                flush=True,
            )

    results.sort(key=lambda item: (VARIANTS.index(item["variant"]), item["ledger_ordinal"], item["record_key"]))
    (output / "results.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in results),
        encoding="utf-8",
    )
    by_variant = {
        variant: _variant_summary([item for item in results if item["variant"] == variant], prepared.records)
        for variant in VARIANTS
    }
    summary = {
        "schema": "strategy-a-progressive-summary/v1",
        "run_id": run_id,
        "preflight": prepared.summary,
        "historical_model_historical_wire": _history_summary(prepared),
        "current_model": by_variant,
        "candidate_automatic_gate_passed": all((
            by_variant[CANDIDATE_VARIANT]["record_residual"] == 0,
            by_variant[CANDIDATE_VARIANT]["row_residual"] == 0,
            by_variant[CANDIDATE_VARIANT]["records"] == by_variant[CANDIDATE_VARIANT]["http_ok"],
            by_variant[CANDIDATE_VARIANT]["records"] == by_variant[CANDIDATE_VARIANT]["finish_stop"],
            by_variant[CANDIDATE_VARIANT]["records"] == by_variant[CANDIDATE_VARIANT]["record_valid"],
            by_variant[CANDIDATE_VARIANT]["schema_failures"] == 0,
            by_variant[CANDIDATE_VARIANT]["python_parse_failures"] == 0,
            by_variant[CANDIDATE_VARIANT]["grounding_failures"] == 0,
        )),
        "direct_semantic_adjudication": "pending",
        "expansion_allowed": False,
    }
    _atomic_json(output / "summary.json", summary)
    manifest.update({
        "status": "completed_with_findings" if any(not item["record_valid"] for item in results) else "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "result_records": len(results),
        "results_sha256": _sha256_file(output / "results.jsonl"),
        "summary_sha256": _sha256_file(output / "summary.json"),
    })
    _atomic_json(output / "manifest.json", manifest)
    return output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seal", type=Path, default=DEFAULT_SEAL)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    run = subparsers.add_parser("run")
    run.add_argument("--run-id", required=True)
    run.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.command == "preflight":
        print(json.dumps(load_preflight(args.seal), ensure_ascii=False, indent=2))
        return
    output = execute_pilot(args.seal, args.run_id, args.workers)
    print(output)


if __name__ == "__main__":
    main()
