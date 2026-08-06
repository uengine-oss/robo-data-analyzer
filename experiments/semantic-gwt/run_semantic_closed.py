# 9차 — 의미-파이썬 + 닫힌 어휘(소스 심볼만·동사 3종·==/!=·and) + json_schema 강제.
# 목표 측정: 재량 제거로 "표현 유일·결정론 비교"가 달성되는가. 운영 GPU, 각 3회.
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(r"D:\work\robo\project\robo-data-analyzer")
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from shared.config.settings import SETTINGS  # noqa: E402
from llm.configs.loader import load_llm_config  # noqa: E402

OUT = Path(__file__).parent / "semantic_closed_results.jsonl"
SRC = REPO_ROOT / r"_runs\ast-contract-20260805\framework-data\source"
REPEATS = 3
BATCH = 10

TARGETS = [
    ("zapamcom10010.c", "b100_change_name_chk"),
    ("zapamcom20010.c", "b140_get_drw_dt"),
]

SCHEMA = {
    "name": "closed_rules",
    "schema": {
        "type": "object",
        "properties": {
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "line": {"type": "integer"},
                        "given": {"type": "string"},
                        "then": {"type": "string"},
                    },
                    "required": ["line", "given", "then"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["rules"],
        "additionalProperties": False,
    },
}

GUIDE = """표기 규칙 (전부 강제 — 위반은 실패):
- given = 그 줄에 도달하는 조건, then = 그 분기의 효과. 각각 파이썬 표현식 한 줄.
- 사용할 수 있는 이름: 소스 코드에 실제로 등장하는 변수명·상수명·함수명뿐이다.
  새 이름을 만들지 마라 (한글 이름·의미 요약 이름 금지).
- 사용할 수 있는 문법: 비교 == 와 != , 결합 and , 괄호. 그 외 연산자 금지.
- then 은 다음 3가지 함수 표기만으로 쓴다:
  ret(반환값심볼) / call(함수명심볼) / set(대상심볼, 원천심볼)
  여러 효과는 and 로 잇는다. 예: call(pfm_errc) and set(result_cd, "10") and ret(RC_ERR)
- 문자열·숫자 리터럴은 소스에 등장하는 것만.
- 항의 순서는 소스에 등장하는 순서를 따른다.
- given 이 무조건이면 "True" 를 쓴다."""


def main() -> None:
    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    url = (cfg.api_base or "").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {SETTINGS.analysis_llm.api_key}"}
    lock = threading.Lock()
    OUT.write_text("", encoding="utf-8")

    jobs = []
    for file, fn in TARGETS:
        bt = json.loads((REPO_ROOT / "logs" / "branch-tables" / f"code_{file}_{fn}.json")
                        .read_text(encoding="utf-8"))
        rows = sorted({r["line"] for r in bt if r["role"] in ("decision", "complement", "exit")})
        lo = min(r["line"] for r in bt) - 2
        hi = max(r.get("end_line") or r["line"] for r in bt) + 2
        src_lines = (SRC / file).read_text(encoding="utf-8", errors="replace").split("\n")
        numbered = "\n".join(f"{i}| {src_lines[i - 1]}" for i in range(lo, min(hi, len(src_lines)) + 1))
        batches = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
        for bi, batch in enumerate(batches, 1):
            prompt = (f"다음 C 소스의 지정된 분기 줄번호 각각에 대해 규칙을 추출해라.\n"
                      f"대상 줄번호(전부, 순서대로): {batch}\n\n{GUIDE}\n\n[소스]\n{numbered}")
            for rep in range(1, REPEATS + 1):
                jobs.append((f"{fn}#b{bi}", rep, prompt, batch))

    def call(cid: str, rep: int, prompt: str, batch: list[int]) -> None:
        body = {"model": cfg.model, "messages": [{"role": "user", "content": prompt}],
                "temperature": cfg.sampling.temperature, "top_p": cfg.sampling.top_p,
                "max_tokens": 4096,
                "response_format": {"type": "json_schema", "json_schema": SCHEMA},
                **(cfg.extra_body or {})}
        t0 = time.time()
        try:
            resp = httpx.post(url, json=body, headers=headers,
                              timeout=cfg.timeout_seconds, verify=cfg.verify_ssl)
            resp.raise_for_status()
            data = resp.json()
            rec = {"id": cid, "rep": rep, "ok": True, "batch": batch,
                   "seconds": round(time.time() - t0, 1),
                   "output_tokens": (data.get("usage") or {}).get("completion_tokens"),
                   "text": data["choices"][0]["message"]["content"]}
        except Exception as e:  # noqa: BLE001
            rec = {"id": cid, "rep": rep, "ok": False, "batch": batch, "error": str(e)}
        with lock:
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"{cid} rep{rep} {'ok' if rec.get('ok') else 'FAIL'} "
                  f"{rec.get('seconds', '?')}s {rec.get('output_tokens', '?')}tok", flush=True)

    with ThreadPoolExecutor(max_workers=4) as ex:
        for cid, rep, prompt, batch in jobs:
            ex.submit(call, cid, rep, prompt, batch)
    print("완료")


if __name__ == "__main__":
    main()
