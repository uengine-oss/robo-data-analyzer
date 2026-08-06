# 13차 채점 — 운영 동일 결정론 채점(cond 경로·필수 ret) + rel 품질(파싱·창작·재현).
from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(r"D:\work\robo\project\robo-data-analyzer")
HERE = Path(__file__).parent
RES = HERE / "rel_wire_20_results.jsonl"
RECORDS = REPO_ROOT / "logs" / "llm-prompts" / "code_rules_examples.jsonl"
SAMPLE = json.loads((REPO_ROOT / r"_runs\ast-contract-20260805\l_sample_framework.json")
                    .read_text(encoding="utf-8"))
TARGET_IDS = {p["id"] for p in SAMPLE["picks"]}

# seq → user_content (창작 검사용 어휘 원천)
uc_by_seq = {}
seq = 0
with open(RECORDS, encoding="utf-8") as f:
    for line in f:
        rec = json.loads(line)
        if rec.get("target_id") not in TARGET_IDS:
            continue
        seq += 1
        uc_by_seq[seq] = rec["user_content"]

KEYWORDS = {"in", "if", "else", "and", "or", "not", "is", "for", "while", "return"}


def normalize(s: str) -> str:
    s = s.replace("->", ".")
    for kw in KEYWORDS:
        s = re.sub(rf"\.{kw}\b", f".{kw}_", s)
    return s


def parse_ok(s: str) -> bool:
    try:
        ast.parse(normalize(s), mode="eval")
        return True
    except SyntaxError:
        return False


def invented(s: str, vocab: str):
    out = []
    try:
        tree = ast.parse(normalize(s), mode="eval")
    except SyntaxError:
        return out
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and not isinstance(n.value, bool) and n.value is not None:
            v = n.value
            text = str(int(v)) if isinstance(v, float) and float(v).is_integer() else str(v)
            if text and text not in vocab:
                out.append(repr(v))
    return out


tot = {"calls": 0, "env_fail": 0, "rows_req": 0, "rows_got": 0,
       "cond_ok": 0, "cond_n": 0, "ret_ok": 0, "ret_n": 0,
       "rel_n": 0, "rel_pf": 0, "rel_inv": set()}
rel_norms = defaultdict(lambda: defaultdict(dict))
samples = []

for line in RES.read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    tot["calls"] += 1
    if not r.get("ok"):
        tot["env_fail"] += 1
        continue
    ev = r.get("evidence") or {}
    rows_ev = ev.get("rows") or {}
    effects = ev.get("effects") or {}
    tot["rows_req"] += len(rows_ev)
    m = re.match(r".*#r(\d+)$", r["id"])
    vocab = uc_by_seq.get(int(m.group(1)) if m else -1, "")
    try:
        d = json.loads(re.sub(r"<think>.*?</think>", "", r["text"], flags=re.S))
        arr = d.get("rules") or []
    except ValueError:
        tot["env_fail"] += 1
        continue
    got = {}
    for item in arr:
        if isinstance(item, dict) and isinstance(item.get("row"), str):
            got[item["row"]] = item
    for rk, evrow in rows_ev.items():
        item = got.get(rk)
        if item is None:
            continue
        tot["rows_got"] += 1
        # cond 채점 — 운영 규칙: 정답 경로(when)와 정확 일치
        when = evrow.get("when")
        if when is not None:
            tot["cond_n"] += 1
            if sorted(item.get("cond") or []) == sorted(when):
                tot["cond_ok"] += 1
        # then 채점 — direct 중 op==ret 인 효과는 반드시 포함
        req_ret = [e for e in (evrow.get("direct") or [])
                   if isinstance(effects.get(e), dict) and effects[e].get("op") == "ret"]
        if req_ret:
            tot["ret_n"] += 1
            if set(req_ret) <= set(item.get("then") or []):
                tot["ret_ok"] += 1
        # rel 채점
        rel = item.get("rel")
        if isinstance(rel, str) and rel.strip():
            tot["rel_n"] += 1
            if not parse_ok(rel):
                tot["rel_pf"] += 1
            else:
                tot["rel_inv"].update(invented(rel, vocab))
                rel_norms[r["id"]][rk][r["rep"]] = ast.dump(
                    ast.parse(normalize(rel), mode="eval"))
            if r["rep"] == 1 and len(samples) < 12 and (evrow.get("when") or req_ret):
                samples.append((r["id"].split("#")[0], rk, rel))

print(f"콜 {tot['calls']} (봉투/호출 실패 {tot['env_fail']})")
print(f"행 응답률: {tot['rows_got']}/{tot['rows_req']}")
print(f"cond 정답 경로 일치: {tot['cond_ok']}/{tot['cond_n']} "
      f"({tot['cond_ok'] / tot['cond_n'] * 100:.1f}%)" if tot["cond_n"] else "cond 없음")
print(f"필수 ret 포함: {tot['ret_ok']}/{tot['ret_n']} "
      f"({tot['ret_ok'] / tot['ret_n'] * 100:.1f}%)" if tot["ret_n"] else "ret 없음")
print(f"rel 파싱실패(후처리 포함): {tot['rel_pf']}/{tot['rel_n']} "
      f"({tot['rel_pf'] / tot['rel_n'] * 100:.1f}%)")
print(f"rel 값 창작: {len(tot['rel_inv'])}종 {sorted(map(str, tot['rel_inv']))[:8]}")
same = total = 0
for cid, rows in rel_norms.items():
    for rk, reps in rows.items():
        if len(reps) == 3:
            total += 1
            if len(set(reps.values())) == 1:
                same += 1
print(f"rel AST 정규형 3회 동일(행 단위): {same}/{total} "
      f"({same / total * 100:.0f}%)" if total else "재현 표본 없음")
print()
print("rel 실물 표본:")
for fn, rk, rel in samples:
    print(f"  [{fn} {rk}] {rel}")
