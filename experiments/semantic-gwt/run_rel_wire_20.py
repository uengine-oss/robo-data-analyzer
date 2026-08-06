# 13차 — 최종 후보 실측: 고정 표본 20 함수 × 운영 실물 프롬프트(system+user 그대로),
# 응답 계약만 탈바꿈: cond/then refs 유지 + nl 칸 → 파이썬 관계 등식(rel).
# json_schema 강제, 운영 GPU, record 당 3회.
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

HERE = Path(__file__).parent
OUT = HERE / "rel_wire_20_results.jsonl"
RECORDS = REPO_ROOT / "logs" / "llm-prompts" / "code_rules_examples.jsonl"
SAMPLE = json.loads((REPO_ROOT / r"_runs\ast-contract-20260805\l_sample_framework.json")
                    .read_text(encoding="utf-8"))
TARGET_IDS = {p["id"] for p in SAMPLE["picks"]}
REPEATS = 3

SCHEMA = {
    "name": "rules_rel",
    "schema": {
        "type": "object",
        "properties": {
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "row": {"type": "string"},
                        "cond": {"type": "array", "items": {"type": "string"}},
                        "then": {"type": "array", "items": {"type": "string"}},
                        "rel": {"type": "string"},
                    },
                    "required": ["row", "cond", "then", "rel"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["rules"],
        "additionalProperties": False,
    },
}

INSTR = """
────────────────────────────────────────
[이번 요청의 출력 형식 — 위 계약의 응답 형식 절만 다음으로 대체한다. 그 외 규칙은 전부 유지]

rows 의 각 행마다 하나의 객체를 출력한다:
{"row": "R<줄번호>", "cond": [조건 ref 배열], "then": [효과 ref 배열], "rel": "<파이썬 관계 등식>"}

- cond / then: 기존 계약 그대로 — 위 자료의 conditions/effects 보기에서 ref(C…/E…)만 고른다.
- rel: 기존의 nl(자연어 문장) 대신, 그 행의 의미를 **파이썬 관계 등식 한 줄**로 쓴다.
  · "무엇이 무엇과 같아지는가"를 == 로. 여러 결과는 and 로. 대입문(=) 금지.
  · 이전/이후 값 구분이 필요하면 _after 접미사만 허용.
  · 예: out.result_cd == "10" and ret == RC_ERR   /   bal_after == bal + in_amt - fee
  · 변수·상수·문자열은 위 자료(SOURCE·보기)에 등장하는 것만. 값 창작 금지.
  · 로그·디버그 출력성 호출은 rel 에 넣지 않는다.
  · 포인터 화살표 -> 는 . 으로 쓴다.
  · rel 은 파이썬 ast.parse 로 파싱 가능한 표현식 하나여야 한다.
- rows 의 모든 행을 하나도 빠짐없이, 순서대로 출력한다."""


def main() -> None:
    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    url = (cfg.api_base or "").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {SETTINGS.analysis_llm.api_key}"}
    lock = threading.Lock()
    OUT.write_text("", encoding="utf-8")

    jobs = []
    seq = 0
    with open(RECORDS, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("target_id") not in TARGET_IDS:
                continue
            seq += 1
            fn = rec["target_id"].split(":")[-1]
            messages = []
            if rec.get("system_prompt"):
                messages.append({"role": "system", "content": rec["system_prompt"]})
            messages.append({"role": "user", "content": rec["user_content"] + INSTR})
            # 정답지: 증거 rows/effects (채점용으로 기록)
            uc = rec["user_content"]
            i = uc.rfind('{"conditions"')
            ev = json.loads(uc[i:]) if i != -1 else {}
            for rep in range(1, REPEATS + 1):
                jobs.append((f"{fn}#r{seq}", rep, messages, ev))

    print(f"표본 함수 record 콜 수: {seq} × {REPEATS}회 = {len(jobs)}")

    def call(cid: str, rep: int, messages: list, ev: dict) -> None:
        body = {"model": cfg.model, "messages": messages,
                "temperature": cfg.sampling.temperature, "top_p": cfg.sampling.top_p,
                "max_tokens": 6144,
                "response_format": {"type": "json_schema", "json_schema": SCHEMA},
                **(cfg.extra_body or {})}
        t0 = time.time()
        try:
            resp = httpx.post(url, json=body, headers=headers,
                              timeout=cfg.timeout_seconds, verify=cfg.verify_ssl)
            resp.raise_for_status()
            data = resp.json()
            rec = {"id": cid, "rep": rep, "ok": True, "evidence": ev,
                   "seconds": round(time.time() - t0, 1),
                   "output_tokens": (data.get("usage") or {}).get("completion_tokens"),
                   "text": data["choices"][0]["message"]["content"]}
        except Exception as e:  # noqa: BLE001
            rec = {"id": cid, "rep": rep, "ok": False, "error": str(e)}
        with lock:
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"{cid} rep{rep} {'ok' if rec.get('ok') else 'FAIL'} "
                  f"{rec.get('seconds', '?')}s {rec.get('output_tokens', '?')}tok", flush=True)

    with ThreadPoolExecutor(max_workers=4) as ex:
        for cid, rep, messages, ev in jobs:
            ex.submit(call, cid, rep, messages, ev)
    print("완료")


if __name__ == "__main__":
    main()
