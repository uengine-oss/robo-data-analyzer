# 8차 — 의미 표기 3종(파이썬/OCL/FEEL) × 운영 동일 json_schema 출력 강제 + 강화 프롬프트.
# 코드 변환 금지 — "의미를 자연어로 해석한 것을 해당 표기법으로 표현"만. 운영 GPU, 각 3회.
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

OUT = Path(__file__).parent / "semantic_tri_results.jsonl"
SRC = REPO_ROOT / r"_runs\ast-contract-20260805\framework-data\source"
REPEATS = 3
BATCH = 10

TARGETS = [
    ("zapamcom10010.c", "b100_change_name_chk"),
    ("zapamcom20010.c", "b140_get_drw_dt"),
]

SCHEMA = {
    "name": "semantic_rules",
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

COMMON = """절대 규칙 (위반은 실패):
- C 코드를 그대로 옮기지 마라. 함수 호출문·매크로·세미콜론·중괄호·포맷 문자열("%s" 등)을
  출력에 복사하는 것을 금지한다.
- 각 분기를 사람이 이해한 의미로 먼저 해석하고, 그 의미를 아래 표기법 문법으로만 표현하라.
- given = 그 분기에 도달하는 상황의 의미. then = 그 분기가 만드는 결과의 의미.
- 숫자·문자열 값은 코드에 실제로 등장하는 것만 사용한다. 새 값 창작 금지.
- 이름은 코드 변수명 또는 의미를 담은 명사(예: 이전_처리_실패, 시스템에러_반환)를 쓴다."""

NOTATIONS = {
    "pysem": COMMON + """
표기법 = 파이썬 표현식. given/then 각각 ast.parse 로 파싱되는 표현식 한 줄.
예: given: rc != RC_NRM and rc != RC_NFD
    then : 오류로그_출력() and result_cd == "11" and 시스템에러_반환()""",
    "ocl": COMMON + """
표기법 = OMG OCL. given 은 사전조건 식, then 은 사후조건 식 (context/pre:/post: 접두어 없이
식 본문만). OCL 문법만 사용: and/or/not, =, <>, self.속성, 불리언 식.
예: given: rc <> RC_NRM and rc <> RC_NFD
    then : 오류로그출력 and self.result_cd = '11' and result = RC_ERR""",
    "feel": COMMON + """
표기법 = DMN FEEL 표현식. FEEL 문법만 사용: and/or/not, =, !=, 이름에 공백 허용, "문자열".
예: given: rc != RC_NRM and rc != RC_NFD
    then : 오류로그 출력 and result_cd = "11" and 시스템에러 반환""",
}


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
        for notation, guide in NOTATIONS.items():
            for bi, batch in enumerate(batches, 1):
                prompt = (f"다음 C 소스의 지정된 분기 줄번호 각각에 대해 의미 규칙을 추출해라.\n"
                          f"대상 줄번호(전부, 순서대로): {batch}\n\n{guide}\n\n[소스]\n{numbered}")
                for rep in range(1, REPEATS + 1):
                    jobs.append((f"{notation}:{fn}#b{bi}", rep, prompt, batch))

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
            detail = e.response.text[:200] if isinstance(e, httpx.HTTPStatusError) else ""
            rec = {"id": cid, "rep": rep, "ok": False, "batch": batch,
                   "error": f"{type(e).__name__}: {e} {detail}"}
        with lock:
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"{cid} rep{rep} {'ok' if rec.get('ok') else 'FAIL: ' + rec.get('error', '')[:100]} "
                  f"{rec.get('seconds', '?')}s {rec.get('output_tokens', '?')}tok", flush=True)

    with ThreadPoolExecutor(max_workers=4) as ex:
        for cid, rep, prompt, batch in jobs:
            ex.submit(call, cid, rep, prompt, batch)
    print("완료")


if __name__ == "__main__":
    main()
