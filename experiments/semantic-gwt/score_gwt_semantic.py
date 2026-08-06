# 15차 채점 — given·then 양쪽을 골든셋과 정규형/심볼 대조. 판정표 출력.
from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path

import sys

HERE = Path(__file__).parent
RES = HERE / (sys.argv[1] if len(sys.argv) > 1 else "gwt_semantic_results.jsonl")

# 골든셋 — 분석자가 소스를 읽고 작성 (given·then 모두). 사용자 검토 대상.
GOLDEN = {
    "b100_change_name_chk": {
        "R242": {"given": "True",
                 "then": 'zord_svc_tmp_s0003_in.svc_mgmt_num == itf.in.svc_mgmt_num and rc == mpfmdbio("zord_svc_tmp_s0003")'},
        "R261": {"given": "rc == RC_NRM",
                 "then": 'itf.out.result_cd == "10" and ret == RC_ERR'},
        "R269": {"given": "rc == RC_NFD", "then": "True"},
        "R272": {"given": "rc != RC_NRM and rc != RC_NFD",
                 "then": 'itf.out.result_cd == "11" and ret == RC_ERR'},
        "R279": {"given": "rc == RC_NFD", "then": "ret == RC_NRM"},
    },
    "e700_check_ksnet_proc_yn": {
        "R1175": {"given": "True",
                  "then": ('zngm_comm_cd_dtl_s0330_in.comm_cd_id == "ZAPA_P_KSNET_PROC" and '
                           'zngm_comm_cd_dtl_s0330_in.comm_cd_val == "Y" and '
                           'rc == mpfmdbio("zngm_comm_cd_dtl_s0330")')},
        "R1191": {"given": "rc != RC_NRM", "then": 'ctx.gc_ksnet_proc_yn == "N"'},
        "R1194": {"given": "rc != RC_NRM", "then": 'ctx.gc_ksnet_proc_yn == "N" and ret == RC_NRM'},
        "R1201": {"given": "rc == RC_NRM",
                  "then": 'ctx.gc_ksnet_proc_yn == zngm_comm_cd_dtl_s0330_out.cast_yn and ret == RC_NRM'},
    },
    "Check_Sum": {
        "R5901": {"given": "True",
                  "then": "li == 0 and i_Sum == 0 and i_Weight1 == 0 and i_Weight2 == 0"},
        "R5907": {"given": "li <= i_Len - 1",
                  "then": ("i_Sum_after == i_Sum + "
                           "atoi(Bank_Param_st.c_Weight2[li]) * atoi(Bank_Param_st.c_Weight1[li])")},
        "R5920": {"given": "True", "then": "ret == i_Sum"},
    },
    "z000_norm_exit_proc": {
        "R4095": {"given": "True", "then": "ret == RC_NRM"},
    },
}

KEYWORDS = {"in", "if", "else", "and", "or", "not", "is", "for", "while", "return"}


def norm_text(s: str) -> str:
    s = s.replace("->", ".")
    for kw in KEYWORDS:
        s = re.sub(rf"\.{kw}\b", f".{kw}_", s)
    return s


def canon(s):
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        tree = ast.parse(norm_text(s), mode="eval").body
    except SyntaxError:
        return None
    terms = tree.values if isinstance(tree, ast.BoolOp) and isinstance(tree.op, ast.And) else [tree]
    return tuple(sorted(ast.dump(t) for t in terms))


def syms(s):
    try:
        tree = ast.parse(norm_text(s), mode="eval")
    except (SyntaxError, TypeError):
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
    return out - {"True", "True_"}


model = defaultdict(dict)
for line in RES.read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    if not r.get("ok"):
        continue
    try:
        d = json.loads(re.sub(r"<think>.*?</think>", "", r["text"], flags=re.S))
    except ValueError:
        continue
    for item in d.get("rules") or []:
        if isinstance(item, dict) and isinstance(item.get("row"), str):
            model[r["id"]].setdefault(item["row"], {})[r["rep"]] = item


def judge(gold: str, got: str) -> str:
    if canon(gold) is not None and canon(gold) == canon(got):
        return "일치"
    if syms(gold) and syms(gold) == syms(got):
        return "심볼일치"
    return "불일치"


counts = {"given": defaultdict(int), "then": defaultdict(int)}
print("판정표 — 골든셋(분석자 작성) vs 모델 (rep1)")
for fn, rows in GOLDEN.items():
    print(f"\n### {fn}")
    for row, gold in rows.items():
        reps = model.get(fn, {}).get(row)
        if not reps:
            print(f"[{row}] 모델 미응답")
            counts["given"]["미응답"] += 1
            counts["then"]["미응답"] += 1
            continue
        it = reps.get(1) or next(iter(reps.values()))
        vg = judge(gold["given"], it.get("given"))
        vt = judge(gold["then"], it.get("then"))
        counts["given"][vg] += 1
        counts["then"][vt] += 1
        print(f"[{row}]")
        print(f"  given 정답: {gold['given']}")
        print(f"        모델: {it.get('given')}   → {vg}")
        print(f"  then  정답: {gold['then']}")
        print(f"        모델: {it.get('then')}   → {vt}")

print("\n" + "=" * 60)
for axis in ("given", "then"):
    c = counts[axis]
    total = sum(v for k, v in c.items() if k != "미응답")
    ok = c["일치"] + c["심볼일치"]
    print(f"{axis}: 일치 {c['일치']} + 심볼일치 {c['심볼일치']} = {ok}/{total} "
          f"({ok / total * 100:.0f}%) · 불일치 {c['불일치']} · 미응답 {c['미응답']}")
