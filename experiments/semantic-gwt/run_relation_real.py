# 11차 — 관계 등식 지침을 실코드(b100/b140)에. 행 배치 + json_schema 강제, 운영 GPU, 각 3회.
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

OUT = Path(__file__).parent / "relation_real_results.jsonl"
SRC = REPO_ROOT / r"_runs\ast-contract-20260805\framework-data\source"
REPEATS = 3
BATCH = 10

TARGETS = [
    ("zapamcom10010.c", "b100_change_name_chk"),
    ("zapamcom20010.c", "b140_get_drw_dt"),
]

SCHEMA = {
    "name": "relations",
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

GUIDE = """지침:
- 각 대상 줄번호(분기·return)에 대해, 코드를 읽고 이해한 **결과 관계**를 자연어 설명 대신
  파이썬 등식/부등식으로 표현해라.
- given = 그 줄에 도달하는 조건(파이썬 비교식), then = 그 경우의 결과 관계식들(and 로 연결).
- then 은 "무엇이 무엇과 같아지는가"의 등식(==)이다. 대입문(=) 금지.
  이전/이후 값을 구분할 땐 _after 접미사만 허용.
  예: balance_after == balance + in_amt - fee / ret == RC_ERR / out.result_cd == "10"
- 포인터 화살표 -> 는 . 으로 쓴다. 절대 -> 를 출력하지 마라.
- 구체 숫자·문자열은 코드에 등장하는 것만. 값 창작 금지.
- 변수 이름은 코드의 이름을 쓴다 (_after 접미사 외 새 이름 발명 금지).
- 로그·출력성 호출은 관계가 아니므로 생략해도 된다. 상태·반환·DB에 남는 결과만 등식으로.
- 모든 식은 파이썬 ast.parse 로 파싱 가능해야 한다."""


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
            prompt = (f"다음 C 소스의 지정된 줄번호 각각에 대해 결과 관계를 추출해라.\n"
                      f"대상 줄번호(전부): {batch}\n\n{GUIDE}\n\n[소스]\n{numbered}")
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
