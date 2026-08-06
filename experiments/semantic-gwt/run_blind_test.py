# 표기법 blind 실측 (질문지 그대로) + LLM 값 실행 실측 — 운영과 같은 GPU·샘플링.
# 원칙: 표기법 문항에는 스펙·예시 주입 금지(blind). 각 문항 3회 반복. 결과는 JSONL 즉시 기록.
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

OUT = Path(__file__).parent / "notation_blind_results.jsonl"
REPEATS = 3
MAX_TOKENS = 2048

SAMPLE = '''CREATE OR REPLACE FUNCTION apply_discount(p_order_id NUMBER) RETURN NUMBER IS
  v_amount   NUMBER;
  v_type     VARCHAR2(10);
  v_final    NUMBER;
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

CHECKSUM_C = '''static long Check_Sum(long i_Len)
{
    long li = 0; long i_Sum = 0; long i_Weight1 = 0; long i_Weight2 = 0;
    char c_Weight[1+1];
    /* 전역: Bank_Param_st.c_Weight2 = "123", Bank_Param_st.c_Weight1 = "456" */
    for(li=0; li<=i_Len-1; li++)
    {
        memset(c_Weight, 0x00, sizeof(c_Weight));
        strncpy((char *)c_Weight, (char *)Bank_Param_st.c_Weight2+li, 1);
        i_Weight1 = atoi((char *)c_Weight);
        memset(c_Weight, 0x00, sizeof(c_Weight));
        strncpy((char *)c_Weight, (char *)Bank_Param_st.c_Weight1+li, 1);
        i_Weight2 = atoi((char *)c_Weight);
        i_Sum += i_Weight1 * i_Weight2;
    }
    return(i_Sum);
}'''

VALUE_JSON_INSTR = ('아래 형식의 JSON만 출력하고 다른 말은 하지 마: '
                    '{"return": 숫자, "update_final_amount": 숫자 또는 null(UPDATE 미실행 시), '
                    '"log_args": [숫자, 숫자] 또는 null(호출 미실행 시)}')

PROMPTS: list[dict] = []


def add(notation: str, q: str, prompt: str) -> None:
    PROMPTS.append({"id": f"{notation}:{q}", "notation": notation, "q": q, "prompt": prompt})


# ── 실험 A: LLM 값 실행 (머릿속 실행) ─────────────────────────────────
add("value", "vip", f"다음 PL/SQL 함수를 머릿속으로 실행해라. 입력: p_order_id=1, "
    f"orders 테이블의 해당 행은 total_amount=100, customer_type='VIP'.\n{VALUE_JSON_INSTR}\n\n{SAMPLE}")
add("value", "normal", f"다음 PL/SQL 함수를 머릿속으로 실행해라. 입력: p_order_id=2, "
    f"orders 테이블의 해당 행은 total_amount=250, customer_type='NORMAL'.\n{VALUE_JSON_INSTR}\n\n{SAMPLE}")
add("value", "negative", f"다음 PL/SQL 함수를 머릿속으로 실행해라. 입력: p_order_id=3, "
    f"orders 테이블의 해당 행은 total_amount=-50, customer_type='VIP'.\n{VALUE_JSON_INSTR}\n\n{SAMPLE}")
add("value", "checksum", "다음 C 함수를 머릿속으로 실행해라. 호출: Check_Sum(3). "
    '전역 값은 주석에 있다. {"return": 숫자} 형식의 JSON만 출력하라.\n\n' + CHECKSUM_C)

# ── 실험 B: 표기법 blind (스펙 주입 금지) ─────────────────────────────
add("fuml", "q1", "OMG fUML 표준에서 정의하는 액션(Action) 종류를 아는 대로 나열하고, "
    "각각이 무엇을 하는지 한 줄씩 설명해줘. 스펙에 실제로 존재하는 이름만 말해줘.")
add("fuml", "q2", f"다음 PL/SQL 함수를 OMG Alf(Action Language for fUML) 문법으로 변환해줘.\n\n{SAMPLE}")
add("fuml", "q3", "다음이 어떤 표기법인지 알아보고 무슨 뜻인지 설명해줘: "
    "activity ApplyDiscount(in orderId: Integer): Integer { ... }")

add("kdm", "q1", "OMG KDM(Knowledge Discovery Metamodel)의 Action 패키지에서 정의하는 "
    "마이크로 액션 종류를 나열해줘. ISO 표준 번호도 알면 말해줘.")
add("kdm", "q2", f"다음 PL/SQL 함수를 KDM Action 요소들로 표현한 XMI(또는 JSON 근사)로 변환해줘.\n\n{SAMPLE}")
add("kdm", "q3", "KDM과 ASTM의 역할 차이를 설명해줘.")

add("ocl", "q1", "OCL로 사전조건/사후조건/불변식을 쓰는 문법 키워드를 알려줘.")
add("ocl", "q2", f'다음 함수의 사후조건 "반환값은 0 이상이거나 -1이다"를 OCL로 써줘.\n\n{SAMPLE}')
add("ocl", "q3", "context Order inv: self.finalAmount >= 0 이 무슨 뜻인지 설명해줘.")

add("sbvr", "q1", "OMG SBVR의 Structured English에서 쓰는 4가지 텍스트 스타일(폰트 구분)과 "
    "의무/가능 표현 키워드를 설명해줘.")
add("sbvr", "q2", '"VIP 고객의 주문 금액에는 20% 할인이 적용되어야 한다"를 SBVR Structured English로 써줘.')

add("dmn", "q1", "DMN 결정 테이블의 hit policy 종류를 전부 나열하고 각각 설명해줘.")
add("dmn", "q2", f"다음 PL/SQL의 할인 로직을 DMN 결정 테이블(마크다운 표)과 FEEL 표현식으로 변환해줘.\n\n{SAMPLE}")
add("dmn", "q3", "S-FEEL과 Full FEEL의 차이를 설명해줘.")

add("drl", "q1", "Drools DRL 파일의 기본 구조(키워드)를 설명해줘.")
add("drl", "q2", f"다음 PL/SQL을 Drools DRL 규칙으로 변환해줘.\n\n{SAMPLE}")

add("gherkin", "q1", "Gherkin의 예약 키워드를 전부 나열해줘.")
add("gherkin", "q2", "다음 PL/SQL 함수의 동작을 Gherkin 시나리오 3개(VIP/일반/음수금액)로 써줘. "
    f"단, Given의 값은 반드시 코드에 등장하는 리터럴('VIP', 0.8, -1)과 경계값만 써.\n\n{SAMPLE}")

add("pysub", "q1", "다음 PL/SQL의 각 분기를 {\"cond\": <파이썬 표현식 문자열>, \"then\": <파이썬 표현식 문자열>} "
    "JSON 배열로 변환해줘. 표현식에는 비교·산술·and/or/not만 허용, 함수 호출·if식 금지. "
    f"JSON 배열만 출력해.\n\n{SAMPLE}")
add("pysub", "q2", "v_type == 'VIP' and v_final >= 0 을 자연어로 설명해줘.")

add("llvm", "q1", "다음 로직과 동등한 LLVM IR을 작성해줘.\n\n"
    "IF v_type = 'VIP' THEN\n  v_final := v_amount * 0.8;\nELSE\n  v_final := v_amount;\nEND IF;\n"
    "IF v_final < 0 THEN\n  RETURN -1;\nEND IF;")

SCHEMA_V2 = ('{"rules":[{"anchor_line":정수,"cond":"파이썬 표현식 문자열 또는 빈 문자열",'
             '"then":["효과 표현식 문자열",...],"nl":"한 문장 설명"}]}')
add("customjson", "q1", f"다음 스키마에 정확히 맞는 JSON만 출력해라. 스키마: {SCHEMA_V2}\n"
    f"각 분기(IF/ELSE/RETURN)를 rules 배열의 한 항목으로. cond는 파이썬 비교/불리언 표현식, "
    f"then의 효과는 set(변수, 식)/ret(식)/update(테이블)/call(함수명) 형태 문자열로.\n\n{SAMPLE}")


def main() -> None:
    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    url = (cfg.api_base or "").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {SETTINGS.analysis_llm.api_key}"}
    lock = threading.Lock()
    done = [0]
    total = len(PROMPTS) * REPEATS

    def call(task: dict, rep: int) -> None:
        body = {
            "model": cfg.model,
            "messages": [{"role": "user", "content": task["prompt"]}],
            "temperature": cfg.sampling.temperature,
            "top_p": cfg.sampling.top_p,
            "max_tokens": MAX_TOKENS,
            **(cfg.extra_body or {}),
        }
        t0 = time.time()
        try:
            resp = httpx.post(url, json=body, headers=headers,
                              timeout=cfg.timeout_seconds, verify=cfg.verify_ssl)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            rec = {"id": task["id"], "rep": rep, "ok": True, "seconds": round(time.time() - t0, 1),
                   "output_tokens": usage.get("completion_tokens"), "text": text}
        except Exception as e:  # noqa: BLE001 — 실측 기록: 실패도 데이터다
            rec = {"id": task["id"], "rep": rep, "ok": False, "error": f"{type(e).__name__}: {e}"}
        with lock:
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done[0] += 1
            print(f"[{done[0]}/{total}] {task['id']} rep{rep} "
                  f"{'ok' if rec.get('ok') else 'FAIL'}", flush=True)

    OUT.write_text("", encoding="utf-8")
    jobs = [(t, r) for t in PROMPTS for r in range(1, REPEATS + 1)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for t, r in jobs:
            ex.submit(call, t, r)
    print("완료:", OUT)


if __name__ == "__main__":
    main()
