# 15차 — GWT 전체를 의미 수준 파이썬으로: 행마다 given(조건의 의미)·then(결과의 의미).
# 골든셋 4함수의 운영 record(증거 동봉) 사용, json_schema 강제, 각 3회.
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

OUT = Path(__file__).parent / "gwt_semantic_results.jsonl"
RECORDS = REPO_ROOT / "logs" / "llm-prompts" / "code_rules_examples.jsonl"
REPEATS = 3

TARGET_FNS = {
    "code:zapamcom10010.c:b100_change_name_chk",
    "code:apascrtu0010t02.c:e700_check_ksnet_proc_yn",
    "code:zapamcom10020.c:Check_Sum",
    "code:apascrtu0010t02.c:z000_norm_exit_proc",
}

SCHEMA = {
    "name": "gwt_rules",
    "schema": {
        "type": "object",
        "properties": {
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "row": {"type": "string"},
                        "given": {"type": "string"},
                        "then": {"type": "string"},
                    },
                    "required": ["row", "given", "then"],
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
[이번 요청의 출력 지시 — 위 자료의 기존 출력 형식 절은 무시한다]

rows 의 각 행에 대해, 그 분기의 의미를 GWT 형태의 파이썬 표현으로 써라:
{"row": "R<줄번호>", "given": "<파이썬 비교식>", "then": "<파이썬 등식>"}

- given = 그 행에 도달하는 상황의 의미. 파이썬 비교식(==, !=, and)으로.
  무조건 도달이면 "True".
- then = 그 행이 만드는 결과의 의미. "무엇이 무엇과 같아지는가"의 등식(==)들을 and 로.
  · 반환은 ret == 값 으로. 상태 저장은 대상 == 원천 으로. 누적은 _after 접미사로
    (예: i_Sum_after == i_Sum + x).
  · entry(진입) 행은 초기화 나열이 아니라 그 구간의 주 작업(키 설정·조회 호출 등)을 쓴다.
  · 로그·디버그 출력은 생략한다.
- 변수·상수·문자열은 위 자료에 등장하는 것만. 새 값·새 이름 금지(_after 만 허용).
- 포인터 화살표 -> 는 . 으로. 모든 식은 파이썬 ast.parse 로 파싱 가능해야 한다.
- rows 의 모든 행을 빠짐없이 출력한다."""


def main() -> None:
    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    url = (cfg.api_base or "").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {SETTINGS.analysis_llm.api_key}"}
    lock = threading.Lock()
    OUT.write_text("", encoding="utf-8")

    jobs = []
    with open(RECORDS, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("target_id") not in TARGET_FNS:
                continue
            fn = rec["target_id"].split(":")[-1]
            messages = []
            if rec.get("system_prompt"):
                messages.append({"role": "system", "content": rec["system_prompt"]})
            messages.append({"role": "user", "content": rec["user_content"] + INSTR})
            for rep in range(1, REPEATS + 1):
                jobs.append((fn, rep, messages))

    print(f"record 수: {len(jobs) // REPEATS}")

    def call(cid: str, rep: int, messages: list) -> None:
        body = {"model": cfg.model, "messages": messages,
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
            rec = {"id": cid, "rep": rep, "ok": True,
                   "seconds": round(time.time() - t0, 1),
                   "text": data["choices"][0]["message"]["content"]}
        except Exception as e:  # noqa: BLE001
            rec = {"id": cid, "rep": rep, "ok": False, "error": str(e)}
        with lock:
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"{cid} rep{rep} {'ok' if rec.get('ok') else 'FAIL'}", flush=True)

    with ThreadPoolExecutor(max_workers=4) as ex:
        for cid, rep, messages in jobs:
            ex.submit(call, cid, rep, messages)
    print("완료")


if __name__ == "__main__":
    main()
