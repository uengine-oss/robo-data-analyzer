# 15차-v2 — 동일 조건(같은 record·스키마·3회), 지침만 실패 4유형 정조준 보강.
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

OUT = Path(__file__).parent / "gwt_semantic_v2_results.jsonl"
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

given 규칙:
- 그 행에 도달하는 상황의 의미. 파이썬 비교식(==, !=, and)으로. 무조건 도달이면 "True".
- switch 의 default(또는 else)는 앞선 case 조건들의 부정을 나열한다:
  예) case 가 RC_NRM, RC_NFD 였다면 → given: rc != RC_NRM and rc != RC_NFD
  절대 rc != rc 같은 자기 비교를 쓰지 마라.
- 특정 분기를 지나야만 도달하는 행(예: 어떤 case 만 통과해 내려오는 마지막 return)은
  그 통과 조건을 given 에 쓴다.

then 규칙:
- 그 행이 직접 만드는 지속 결과만, "무엇이 무엇과 같아지는가"의 등식(==)으로, and 연결.
- 반환은 ret == 값. 상태·구조체 저장은 대상 == 원천. 빠뜨리지 마라 —
  반환만 쓰고 저장을 빠뜨리는 것은 실패다.
- 다른 행의 결과를 끌어오지 마라. 그 행 범위 밖의 return 을 쓰는 것은 실패다.
- entry(진입) 행: 변수 초기화(rc = RC_NRM 등)는 쓰지 마라. 그 구간의 주 작업만 쓴다
  (조회 키 설정, DBIO 호출 결과가 rc 에 담김 등).
- 반복문 안의 누적: 중간 변수 등식을 나열하지 말고 한 회차의 누적 관계 하나로 요약한다.
  예) i_Sum_after == i_Sum + atoi(Bank_Param_st.c_Weight2[li]) * atoi(Bank_Param_st.c_Weight1[li])
- 로그·디버그 출력은 생략한다.

공통:
- 변수·상수·문자열은 위 자료에 등장하는 것만. 새 값·새 이름 금지(_after 만 허용).
- 포인터 화살표 -> 는 . 으로, C 캐스트 (char *) 는 쓰지 마라.
- 모든 식은 파이썬 ast.parse 로 파싱 가능해야 한다. rows 의 모든 행을 빠짐없이."""


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
