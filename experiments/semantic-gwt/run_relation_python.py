# 10차 — "이해한 관계를 파이썬 등식으로" (사용자 의도 정조준).
# 코드의 계산·판정을 읽고, 자연어 설명 대신 결과 관계식(==, 산술)을 뱉게 한다.
# 시료 3종은 관계 정답을 사전 정의(정답지) — 기계 대조 가능. 운영 GPU, json_schema 강제, 각 3회.
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

OUT = Path(__file__).parent / "relation_python_results.jsonl"
REPEATS = 3

DISCOUNT = '''CREATE OR REPLACE FUNCTION apply_discount(p_order_id NUMBER) RETURN NUMBER IS
BEGIN
  SELECT total_amount, customer_type INTO v_amount, v_type
    FROM orders WHERE order_id = p_order_id;
  IF v_type = 'VIP' THEN
    v_final := v_amount * 0.8;
  ELSE
    v_final := v_amount;
  END IF;
  IF v_final < 0 THEN
    RETURN -1;
  END IF;
  UPDATE orders SET final_amount = v_final WHERE order_id = p_order_id;
  log_discount(p_order_id, v_amount - v_final);
  RETURN v_final;
END;'''

CHECKSUM = '''static long Check_Sum(long i_Len)
{
    long li; long i_Sum = 0; long i_Weight1; long i_Weight2;
    char c_Weight[2];
    for(li=0; li<=i_Len-1; li++)
    {
        strncpy(c_Weight, Bank_Param_st.c_Weight2+li, 1);
        i_Weight1 = atoi(c_Weight);
        strncpy(c_Weight, Bank_Param_st.c_Weight1+li, 1);
        i_Weight2 = atoi(c_Weight);
        i_Sum += i_Weight1 * i_Weight2;
    }
    return(i_Sum);
}'''

DEPOSIT = '''long deposit_proc(account_t *acct, long in_amt)
{
    long fee;
    if (in_amt <= 0)
        return RC_ERR;
    fee = in_amt / 100;              /* 수수료 1% (정수 나눗셈) */
    acct->balance = acct->balance + in_amt - fee;
    acct->dep_cnt = acct->dep_cnt + 1;
    return RC_NRM;
}'''

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
                        "case": {"type": "string"},
                        "given": {"type": "string"},
                        "then": {"type": "string"},
                    },
                    "required": ["case", "given", "then"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["rules"],
        "additionalProperties": False,
    },
}

GUIDE = """지침:
- 이 코드를 읽고 이해한 **결과 관계**를, 자연어 설명 대신 파이썬 등식/부등식으로 표현해라.
- 경우(case)마다 하나: given = 그 경우의 조건(파이썬 비교식), then = 그 경우의 결과 관계식.
- then 은 "무엇이 무엇과 같아지는가"를 == 와 산술(+,-,*,/)로 쓴 등식이다.
  예: 코드가 잔고에 입금액을 더한다면 → then: balance_after == balance + in_amt
  대입문(=)이 아니라 관계 등식(==)으로. 이전 값과 이후 값을 구분할 땐 _after 접미사를 써라.
- 구체 숫자 값을 지어내지 마라. 코드에 있는 상수(0.8, -1, 100 등)는 그대로 쓴다.
- 변수 이름은 코드의 이름을 쓴다 (새 의미 이름 발명 금지, _after 접미사만 허용).
- 부수 결과(반환값, 카운트 증가 등)도 등식으로: 예 ret == RC_NRM, dep_cnt_after == dep_cnt + 1
- 모든 식은 파이썬 ast.parse 로 파싱 가능해야 한다."""

CASES = [("discount", DISCOUNT, "PL/SQL"), ("checksum", CHECKSUM, "C"), ("deposit", DEPOSIT, "C")]


def main() -> None:
    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    url = (cfg.api_base or "").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {SETTINGS.analysis_llm.api_key}"}
    lock = threading.Lock()
    OUT.write_text("", encoding="utf-8")

    def call(cid: str, rep: int, prompt: str) -> None:
        body = {"model": cfg.model, "messages": [{"role": "user", "content": prompt}],
                "temperature": cfg.sampling.temperature, "top_p": cfg.sampling.top_p,
                "max_tokens": 2048,
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
        for cid, code, lang in CASES:
            prompt = f"다음 {lang} 코드의 각 경우(case)에 대한 결과 관계를 추출해라.\n\n{GUIDE}\n\n[코드]\n{code}"
            for rep in range(1, REPEATS + 1):
                ex.submit(call, cid, rep, prompt)
    print("완료")


if __name__ == "__main__":
    main()
