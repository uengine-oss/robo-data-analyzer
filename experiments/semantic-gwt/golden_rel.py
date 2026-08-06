# rel 골든셋 파일럿 — 사람(분석자)이 소스를 읽고 작성한 정답 관계식 vs 13차 모델 rel 결정론 대조.
# 표기 규약: 13차 지침과 동일 (== 등식, and 연결, _after 접미사, 소스 이름, 로그성 생략).
from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
RES = HERE / "rel_wire_20_results.jsonl"

# ── 골든셋 (분석자 수제 — 소스 판독 근거를 주석으로 병기) ──────────────
GOLDEN = {
    "b100_change_name_chk": {
        # 242~257: 구조체 초기화, svc_mgmt_num 설정, dbio 조회
        "R242": 'zord_svc_tmp_s0003_in.svc_mgmt_num == itf.in.svc_mgmt_num and rc == mpfmdbio("zord_svc_tmp_s0003")',
        # 261: 조회됨(명의변경중) → result_cd "10", RC_ERR 반환
        "R261": 'itf.out.result_cd == "10" and ret == RC_ERR',
        # 269: RC_NFD → 아무 결과 없이 통과
        "R269": 'True',
        # 272: default → result_cd "11", RC_ERR 반환
        "R272": 'itf.out.result_cd == "11" and ret == RC_ERR',
        # 279: 정상 종료
        "R279": 'ret == RC_NRM',
    },
    "e700_check_ksnet_proc_yn": {
        # 1175~1189: comm_cd_id/val 설정 후 dbio 조회
        "R1175": ('zngm_comm_cd_dtl_s0330_in.comm_cd_id == "ZAPA_P_KSNET_PROC" and '
                  'zngm_comm_cd_dtl_s0330_in.comm_cd_val == "Y" and '
                  'rc == mpfmdbio("zngm_comm_cd_dtl_s0330")'),
        # 1191: 조회 실패 → ctx에 "N" 저장하고 정상 반환
        "R1191": 'ctx.gc_ksnet_proc_yn == "N"',
        "R1194": 'ctx.gc_ksnet_proc_yn == "N" and ret == RC_NRM',
        # 1199~1201: 조회값 저장 후 정상 반환
        "R1201": 'ctx.gc_ksnet_proc_yn == zngm_comm_cd_dtl_s0330_out.cast_yn and ret == RC_NRM',
    },
    "Check_Sum": {
        "R5901": 'li == 0 and i_Sum == 0 and i_Weight1 == 0 and i_Weight2 == 0',
        # 5907 루프: 자릿수 가중치 곱 합산
        "R5907": ('i_Sum_after == i_Sum + '
                  'atoi(Bank_Param_st.c_Weight2[li]) * atoi(Bank_Param_st.c_Weight1[li])'),
        "R5920": 'ret == i_Sum',
    },
    "z000_norm_exit_proc": {
        "R4095": 'ret == RC_NRM',
    },
}

KEYWORDS = {"in", "if", "else", "and", "or", "not", "is", "for", "while", "return"}


def normalize_text(s: str) -> str:
    s = s.replace("->", ".")
    for kw in KEYWORDS:
        s = re.sub(rf"\.{kw}\b", f".{kw}_", s)
    return s


def canon(s: str):
    """정규형: and 항 정렬 + AST dump. 파싱 불가면 None."""
    try:
        tree = ast.parse(normalize_text(s), mode="eval").body
    except SyntaxError:
        return None
    terms = tree.values if isinstance(tree, ast.BoolOp) and isinstance(tree.op, ast.And) else [tree]
    return tuple(sorted(ast.dump(t) for t in terms))


def symbols(s: str) -> set:
    try:
        tree = ast.parse(normalize_text(s), mode="eval")
    except SyntaxError:
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
    return out - {"True_", "True"}


# ── 13차 모델 rel 수집 (rep1 기준, 함수별) ───────────────────────────────
model_rels = defaultdict(dict)   # fn -> row -> [rep1, rep2, rep3 rel]
for line in RES.read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    if not r.get("ok"):
        continue
    fn = r["id"].split("#")[0]
    if fn not in GOLDEN:
        continue
    try:
        d = json.loads(re.sub(r"<think>.*?</think>", "", r["text"], flags=re.S))
    except ValueError:
        continue
    for item in d.get("rules") or []:
        if isinstance(item, dict) and isinstance(item.get("row"), str):
            model_rels[fn].setdefault(item["row"], {})[r["rep"]] = item.get("rel")

# ── 판정표 ───────────────────────────────────────────────────────────────
exact = equiv_sym = mismatch = absent = 0
print("행 | 정답(골든) | 모델 rel(rep1) | 기계 판정")
print("-" * 100)
for fn, rows in GOLDEN.items():
    print(f"\n### {fn}")
    for row, gold in rows.items():
        reps = model_rels.get(fn, {}).get(row)
        if not reps:
            absent += 1
            print(f"[{row}] 모델 응답에 없음 (record 분할로 다른 콜 소속일 수 있음)")
            continue
        m = reps.get(1) or next(iter(reps.values()))
        cg, cm = canon(gold), canon(m)
        sg, sm = symbols(gold), symbols(m)
        if cg is not None and cg == cm:
            verdict = "정규형 완전 일치"
            exact += 1
        elif sg and sg == sm:
            verdict = "심볼 집합 일치 (표현 상이)"
            equiv_sym += 1
        else:
            verdict = f"불일치 — 심볼 차: 정답에만 {sorted(sg - sm)[:4]} / 모델에만 {sorted(sm - sg)[:4]}"
            mismatch += 1
        print(f"[{row}]")
        print(f"  정답: {gold}")
        print(f"  모델: {m}")
        print(f"  판정: {verdict}")

total = exact + equiv_sym + mismatch
print("\n" + "=" * 60)
print(f"기계 대조 결과 (표본 {total}행): 정규형 일치 {exact} · 심볼 일치 {equiv_sym} · "
      f"불일치 {mismatch} · 모델 미응답 {absent}")
