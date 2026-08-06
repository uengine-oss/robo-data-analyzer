# 11차 채점 — 실코드 관계 등식: 파싱률·-> 잔존·값 창작·커버리지·재현성 + 지저분 분기 표본.
from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
REPO = Path(r"D:\work\robo\project\robo-data-analyzer")
RES = HERE / "relation_real_results.jsonl"
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


def parse_ok(s):
    if not isinstance(s, str) or not s.strip():
        return False
    try:
        ast.parse(s, mode="eval")
        return True
    except SyntaxError:
        return False


def invented(s, src):
    out = []
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError:
        return out
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and not isinstance(n.value, bool) and n.value is not None:
            v = n.value
            text = str(int(v)) if isinstance(v, float) and float(v).is_integer() else str(v)
            if text and text not in src:
                out.append(repr(v))
    return out


agg = defaultdict(lambda: {"req": 0, "got": 0, "n": 0, "pf": 0, "arrow": 0,
                           "inv": set(), "digests": defaultdict(list)})
samples = defaultdict(dict)

for cid, rows in sorted(by_id.items()):
    fn = cid.split("#")[0]
    a = agg[fn]
    for r in rows:
        batch = set(r.get("batch") or [])
        a["req"] += len(batch)
        if not r.get("ok"):
            continue
        try:
            d = json.loads(re.sub(r"<think>.*?</think>", "", r["text"], flags=re.S))
            arr = d.get("rules") or []
        except ValueError:
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
                a["n"] += 1
                if not parse_ok(s):
                    a["pf"] += 1
                else:
                    a["inv"].update(invented(s, SRC_TEXT[fn]))
                if "->" in s:
                    a["arrow"] += 1
            if r["rep"] == 1 and isinstance(ln, int):
                samples[fn][ln] = (item.get("given"), item.get("then"))
        a["got"] += len(got)
        norm = []
        for item in arr:
            if isinstance(item, dict) and parse_ok(item.get("given") or "") \
                    and parse_ok(item.get("then") or ""):
                norm.append((item.get("line"), item.get("given"), item.get("then")))
        import hashlib
        a["digests"][cid].append(hashlib.sha1(json.dumps(sorted(norm, key=str),
                                 ensure_ascii=False).encode()).hexdigest()[:8])

for fn, a in agg.items():
    print("=" * 74)
    print(f"{fn}")
    print(f"  행 커버 {a['got']}/{a['req']}")
    print(f"  파싱실패 {a['pf']}/{a['n']} ({a['pf'] / a['n'] * 100:.0f}%) | '->' 잔존 {a['arrow']}")
    print(f"  값 창작 {len(a['inv'])}종 {sorted(map(str, a['inv']))[:6]}")
    same = sum(1 for d in a["digests"].values() if len(set(d)) == 1 and len(d) == 3)
    print(f"  배치 3회 동일: {same}/{len(a['digests'])}")

print()
print("실물 표본 — b140의 지저분 분기 (rep1):")
for ln in (1091, 1099, 1104, 1119, 1231, 1296):
    gt = samples["b140_get_drw_dt"].get(ln)
    if gt:
        print(f"[L{ln}] given: {gt[0]}")
        print(f"{'':7}then : {gt[1]}")
