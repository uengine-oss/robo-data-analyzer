"""Run the sealed Strategy-A relation experiment without invoking Analyzer.

The source of truth is the immutable Framework run prompt ledger.  This script reuses
the same 20 targets, evidence payloads, model and sampling configuration as Strategy B;
only the requested semantic representation changes.  It never writes graph state.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

from llm.configs.loader import load_llm_config  # noqa: E402
from shared.config.settings import SETTINGS  # noqa: E402

SPEC = Path(__file__).resolve().parent
SAMPLE = SPEC / "gold" / "semantic-sample-20.json"
LEDGER = REPO / "logs" / "20260810_100517_3f4850ec3def" / "llm-prompts" / "code_rules_examples.jsonl"
OUT_DIR = SPEC / "evidence"
OUT = OUT_DIR / "strategy-a-v3-results.jsonl"
REPEATS = 3
WORKERS = 16

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


def _schema(rows: list[str]) -> dict:
    leaf = {
        "type": "object",
        "properties": {
            "given": {"type": "string", "maxLength": 500},
            "then_relation": {"type": "string", "maxLength": 1000},
            "semantic_status": {
                "type": "string",
                "enum": ["exact", "needs_context", "unreachable"],
            },
        },
        "required": ["given", "then_relation", "semantic_status"],
        "additionalProperties": False,
    }
    return {
        "name": "strategy_a_relations",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "rules": {
                    "type": "object",
                    "properties": {row: leaf for row in rows},
                    "required": rows,
                    "additionalProperties": False,
                }
            },
            "required": ["rules"],
            "additionalProperties": False,
        },
    }


def _payload(record: dict) -> tuple[str, dict, list[str]]:
    user = str(record["user_content"])
    marker = "response_locale:"
    start = user.index(marker)
    evidence_text = user[start:]
    evidence = json.loads(evidence_text.split("rule_evidence_json:\n", 1)[1])
    rows = list(evidence["rows"])
    return CONTRACT + evidence_text, evidence, rows


def _parse_verdict(parsed: dict, rows: list[str]) -> dict:
    failures: list[dict] = []
    output_rows = parsed.get("rules") if isinstance(parsed, dict) else None
    if not isinstance(output_rows, dict):
        return {"row_coverage": False, "python_parse": False,
                "failures": [{"reason": "rules_not_object"}]}
    coverage = set(output_rows) == set(rows)
    for row in rows:
        leaf = output_rows.get(row)
        if not isinstance(leaf, dict):
            failures.append({"row": row, "field": "leaf", "reason": "missing"})
            continue
        for field in ("given", "then_relation"):
            expression = str(leaf.get(field) or "")
            if field == "then_relation" and not expression:
                if leaf.get("semantic_status") != "exact":
                    continue
            try:
                ast.parse(expression, mode="eval")
            except (SyntaxError, ValueError) as error:
                failures.append({"row": row, "field": field, "reason": str(error)})
    return {"row_coverage": coverage, "python_parse": not failures, "failures": failures}


def main() -> None:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    selected = {item["function"] for item in sample["samples"]}
    records: list[dict] = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if str(record.get("target_id") or "").rsplit(":", 1)[-1] in selected:
            records.append(record)
    present = {str(record["target_id"]).rsplit(":", 1)[-1] for record in records}
    if present != selected:
        raise SystemExit(f"sealed target mismatch: missing={sorted(selected - present)}")

    config = load_llm_config(SETTINGS.analysis_llm.config)
    url = (config.api_base or "").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {SETTINGS.analysis_llm.api_key}"}
    jobs = []
    for record_index, record in enumerate(records):
        user_content, evidence, rows = _payload(record)
        schema = _schema(rows)
        manifest = {
            row: {
                "when": list(evidence["rows"][row].get("when") or ()),
                "direct": list(evidence["rows"][row].get("direct") or ()),
            }
            for row in rows
        }
        for repeat in range(1, REPEATS + 1):
            jobs.append((record_index, record, repeat, user_content, rows, schema, manifest))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text("", encoding="utf-8")
    lock = threading.Lock()

    def invoke(job: tuple) -> None:
        record_index, source, repeat, user_content, rows, schema, manifest = job
        body = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_content},
            ],
            "temperature": config.sampling.temperature,
            "top_p": config.sampling.top_p,
            "max_tokens": 4096,
            "response_format": {"type": "json_schema", "json_schema": schema},
            **(config.extra_body or {}),
        }
        started = time.perf_counter()
        result = {
            "record_index": record_index,
            "target_id": source["target_id"],
            "repeat": repeat,
            "rows": rows,
            "evidence_manifest": manifest,
            "system_bytes": len(SYSTEM.encode("utf-8")),
            "user_bytes": len(user_content.encode("utf-8")),
            "schema_bytes": len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            "prompt_sha256": hashlib.sha256((SYSTEM + "\0" + user_content).encode("utf-8")).hexdigest(),
        }
        try:
            response = httpx.post(
                url, headers=headers, json=body,
                timeout=config.timeout_seconds, verify=config.verify_ssl,
            )
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            parsed = json.loads(text)
            usage = data.get("usage") or {}
            result.update({
                "ok": True,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "response": parsed,
                "validation": _parse_verdict(parsed, rows),
            })
        except Exception as error:  # noqa: BLE001 - evidence must retain exact transport failure
            result.update({
                "ok": False,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "error": f"{type(error).__name__}: {error}",
            })
        with lock:
            with OUT.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
            print(
                f"{record_index:02d} {source['target_id'].rsplit(':', 1)[-1]} "
                f"r{repeat} {'ok' if result['ok'] else 'FAIL'} {result['latency_ms']}ms",
                flush=True,
            )

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        list(executor.map(invoke, jobs))


if __name__ == "__main__":
    main()
