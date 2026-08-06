# 8차 채점 — 봉투(강제 효과)·내용(표기별)·코드복사 후퇴·값 창작·커버리지·재현성 + 표본.
from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
REPO = Path(r"D:\work\robo\project\robo-data-analyzer")
RES = HERE / "semantic_tri_results.jsonl"
SRC = REPO / r"_runs\ast-contract-20260805\framework-data\source"

SRC_TEXT = {
    "b100_change_name_chk": (SRC / "zapamcom10010.c").read_text(encoding="utf-8", errors="replace"),
    "b140_get_drw_dt": (SRC / "zapamcom20010.c").read_text(encoding="utf-8", errors="replace"),
}

by_id = defaultdict(list)
for line in RES.read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    by_id[r["id"]].append(r)
for v in by_id.values():
    v.sort(key=lambda r: r["rep"])

COPY_MARKERS = [";", "{", "%s", "%ld", "strncpy", "memset", "PFM_ERRC(", "PFM_MSGE(", "mpfmdbio("]


def py_ok(s):
    try:
        ast.parse(s, mode="eval")
        return True
    except SyntaxError:
        return False


def literals(s: str):
    for m in re.finditer(r'"([^"]*)"|\'([^\']*)\'|(?<![\w.])(\d+(?:\.\d+)?)', s):
        v = m.group(1) or m.group(2) or m.group(3)
        if v:
            yield v


agg = defaultdict(lambda: {"calls": 0, "env_fail": 0, "req": 0, "got": 0,
                           "expr_n": 0, "py_fail": 0, "copy": 0, "arrow": 0,
                           "inv": set(), "digests": defaultdict(list)})
samples = defaultdict(dict)

for cid, rows in sorted(by_id.items()):
    notation, rest = cid.split(":", 1)
    fn = rest.split("#")[0]
    key = (notation, fn)
    a = agg[key]
    for r in rows:
        a["calls"] += 1
        batch = set(r.get("batch") or [])
        a["req"] += len(batch)
        if not r.get("ok"):
            a["env_fail"] += 1
            continue
        try:
            d = json.loads(re.sub(r"<think>.*?</think>", "", r["text"], flags=re.S))
            arr = d.get("rules") if isinstance(d, dict) else d
            assert isinstance(arr, list)
        except (ValueError, AssertionError):
            a["env_fail"] += 1
            a["digests"][cid].append("ENV_FAIL")
            continue
        got = set()
        for item in arr:
            if not isinstance(item, dict):
                continue
            ln = item.get("line")
            if ln in batch:
                got.add(ln)
            for s in (item.get("given"), item.get("then")):
                if not isinstance(s, str):
                    continue
                a["expr_n"] += 1
                if notation == "pysem" and not py_ok(s):
                    a["py_fail"] += 1
                if any(mk in s for mk in COPY_MARKERS):
                    a["copy"] += 1
                if "->" in s:
                    a["arrow"] += 1
                for v in literals(s):
                    if v not in SRC_TEXT[fn]:
                        a["inv"].add(v)
            if r["rep"] == 1 and isinstance(ln, int):
                samples[(notation, fn)][ln] = (item.get("given"), item.get("then"))
        a["got"] += len(got)
        a["digests"][cid].append(
            hashlib.sha1(json.dumps(arr, sort_keys=True, ensure_ascii=False).encode())
            .hexdigest()[:8])

for (notation, fn), a in sorted(agg.items()):
    print("=" * 74)
    print(f"{notation} / {fn}   (콜 {a['calls']}, 봉투 실패 {a['env_fail']})")
    print(f"  행 커버 {a['got']}/{a['req']} | 식 {a['expr_n']}개")
    if notation == "pysem":
        print(f"  파이썬 파싱실패: {a['py_fail']}/{a['expr_n']}")
    print(f"  코드복사 마커 포함 식: {a['copy']} | '->' 포함: {a['arrow']}")
    print(f"  값 창작: {len(a['inv'])}종 {sorted(a['inv'])[:6]}")
    same = sum(1 for d in a["digests"].values() if len(set(d)) == 1 and len(d) == 3)
    print(f"  배치 3회 동일: {same}/{len(a['digests'])}")

print()
print("=" * 74)
print("실물 표본 (rep1) — 각 표기 b100 L272(default) + b140 L1119(지저분)")
print("=" * 74)
for notation in ("pysem", "ocl", "feel"):
    for fn, ln in (("b100_change_name_chk", 272), ("b140_get_drw_dt", 1119)):
        gt = samples.get((notation, fn), {}).get(ln)
        if gt:
            print(f"[{notation} {fn} L{ln}]")
            print(f"  given: {gt[0]}")
            print(f"  then : {gt[1]}")
