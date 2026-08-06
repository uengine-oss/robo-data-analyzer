# 11차 보정 — 결정론 후처리(고정 규칙) 적용 후 실질 파싱 실패율.
# 규칙(코드가 항상 동일 적용 → 검증 가능): '->' → '.', 속성 자리의 예약어 in → in_.
from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
RES = HERE / "relation_real_results.jsonl"

KEYWORDS = {"in", "if", "else", "and", "or", "not", "is", "for", "while", "return",
            "class", "def", "import", "from", "pass", "raise", "try", "except"}


def normalize(s: str) -> str:
    s = s.replace("->", ".")
    # 속성 자리의 예약어: ".in." / ".in " / ".in==" 등 → ".in_"
    for kw in KEYWORDS:
        s = re.sub(rf"\.{kw}\b", f".{kw}_", s)
    return s


def parse_ok(s):
    try:
        ast.parse(s, mode="eval")
        return True
    except SyntaxError:
        return False


stats = defaultdict(lambda: {"n": 0, "raw_fail": 0, "norm_fail": 0, "fail_kinds": Counter()})

for line in RES.read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    if not r.get("ok"):
        continue
    fn = r["id"].split("#")[0]
    try:
        d = json.loads(re.sub(r"<think>.*?</think>", "", r["text"], flags=re.S))
    except ValueError:
        continue
    for item in d.get("rules") or []:
        if not isinstance(item, dict):
            continue
        for s in (item.get("given"), item.get("then")):
            if not isinstance(s, str) or not s.strip():
                continue
            st = stats[fn]
            st["n"] += 1
            raw = parse_ok(s)
            norm = parse_ok(normalize(s))
            if not raw:
                st["raw_fail"] += 1
            if not norm:
                st["norm_fail"] += 1
                ns = normalize(s)
                if "=" in ns and "==" not in ns.replace("!=", "").replace(">=", "").replace("<=", ""):
                    st["fail_kinds"]["대입문"] += 1
                elif re.search(r"[가-힣]", ns):
                    st["fail_kinds"]["한글혼입"] += 1
                elif ";" in ns or "{" in ns:
                    st["fail_kinds"]["코드복사"] += 1
                else:
                    st["fail_kinds"]["기타"] += 1

for fn, st in stats.items():
    print(f"{fn}: 식 {st['n']}")
    print(f"  후처리 전 실패: {st['raw_fail']} ({st['raw_fail'] / st['n'] * 100:.0f}%)")
    print(f"  후처리 후 실패: {st['norm_fail']} ({st['norm_fail'] / st['n'] * 100:.0f}%)"
          f"  ← 고정 규칙('->'→'.', .in→.in_)만 적용")
    print(f"  잔여 실패 유형: {dict(st['fail_kinds'])}")
