# blind 실측 기계 채점 — P(파싱)/값 정답/재현율/금지구문/키워드. 지식 문항은 수동 판정용 발췌 출력.
from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path

RES = Path(__file__).parent / "notation_blind_results.jsonl"

by_id: dict[str, list[dict]] = defaultdict(list)
for line in RES.read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    by_id[r["id"]].append(r)
for v in by_id.values():
    v.sort(key=lambda r: r["rep"])

VALUE_TRUTH = {
    "value:vip": {"return": 80, "update_final_amount": 80, "log_args": [1, 20]},
    "value:normal": {"return": 250, "update_final_amount": 250, "log_args": [2, 0]},
    "value:negative": {"return": -1, "update_final_amount": None, "log_args": None},
    "value:checksum": {"return": 32},
}


def first_json(text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    for m in re.finditer(r"[\[{]", text):
        try:
            return json.loads(text[m.start():_balance(text, m.start())])
        except (ValueError, IndexError):
            continue
    return None


def _balance(text: str, start: int) -> int:
    open_c = text[start]
    close_c = "}" if open_c == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
    raise IndexError


def num_eq(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


print("=" * 72)
print("실험 A — LLM 값 실행 (합격선: 3/3 정확)")
print("=" * 72)
for vid, truth in VALUE_TRUTH.items():
    rows = by_id.get(vid, [])
    verdicts = []
    for r in rows:
        if not r.get("ok"):
            verdicts.append("호출실패")
            continue
        d = first_json(r["text"])
        if not isinstance(d, dict):
            verdicts.append("JSON불가")
            continue
        ok = num_eq(d.get("return"), truth["return"])
        if "update_final_amount" in truth:
            ok = ok and num_eq(d.get("update_final_amount"), truth["update_final_amount"])
            la, lt = d.get("log_args"), truth["log_args"]
            if lt is None:
                ok = ok and la in (None, [])
            else:
                ok = ok and isinstance(la, list) and len(la) == 2 and num_eq(la[0], lt[0]) and num_eq(la[1], lt[1])
        verdicts.append("정답" if ok else f"오답({json.dumps(d, ensure_ascii=False)[:90]})")
    print(f"{vid:18} {' | '.join(verdicts)}")

print()
print("=" * 72)
print("실험 B — 기계 채점 가능 항목")
print("=" * 72)

BANNED = (ast.Call, ast.IfExp)


def py_ok(expr: str) -> str:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return "파싱실패"
    for n in ast.walk(tree):
        if isinstance(n, BANNED):
            return "금지구문"
    return "ok"


# pysub q1: 파싱 + 금지 + 재현율
rows = by_id.get("pysub:q1", [])
rep_shapes = []
for r in rows:
    if not r.get("ok"):
        rep_shapes.append(("호출실패", []))
        continue
    arr = first_json(r["text"])
    if not isinstance(arr, list):
        rep_shapes.append(("JSON불가", []))
        continue
    results = []
    conds = []
    for item in arr:
        for k in ("cond", "then"):
            v = item.get(k) if isinstance(item, dict) else None
            if isinstance(v, str) and v.strip():
                results.append(py_ok(v))
        if isinstance(item, dict) and isinstance(item.get("cond"), str):
            conds.append(item["cond"].strip())
    status = "전부ok" if results and all(x == "ok" for x in results) else \
        f"{results.count('파싱실패')}파싱실패/{results.count('금지구문')}금지"
    rep_shapes.append((status, conds))
print("pysub:q1  ", " | ".join(s for s, _ in rep_shapes))
cond_sets = [tuple(c) for _, c in rep_shapes if c]
print("pysub:q1 재현율:", "3회 동일" if len(set(cond_sets)) == 1 and len(cond_sets) == 3
      else f"상이 {len(set(cond_sets))}종/{len(cond_sets)}회")
for i, cs in enumerate(cond_sets, 1):
    print(f"   rep{i} conds: {list(cs)}")

# gherkin q1 키워드 / q2 키워드+값 규약
GK = ["Feature", "Scenario", "Given", "When", "Then", "And", "But", "Background", "Examples", "Scenario Outline"]
for qid in ("gherkin:q1", "gherkin:q2"):
    outs = []
    for r in by_id.get(qid, []):
        if not r.get("ok"):
            outs.append("호출실패")
            continue
        text = r["text"]
        hits = sum(1 for k in GK if k in text)
        if qid == "gherkin:q2":
            nums = set(re.findall(r"-?\d+(?:\.\d+)?", re.sub(r"<think>.*?</think>", "", text, flags=re.S)))
            allowed = {"-1", "0", "0.8", "20", "80", "100", "3", "1", "2"}
            free = sorted(nums - allowed)[:6]
            outs.append(f"키워드{hits} 자유값{len(nums - allowed)}{free}")
        else:
            outs.append(f"키워드 {hits}/10")
    print(f"{qid:11}", " | ".join(outs))

# ocl q2 / drl q2 / dmn q2 / customjson / llvm — 형태 검사
def probe(qid: str, checks: dict[str, str]) -> None:
    outs = []
    for r in by_id.get(qid, []):
        if not r.get("ok"):
            outs.append("호출실패")
            continue
        text = re.sub(r"<think>.*?</think>", "", r["text"], flags=re.S)
        outs.append("+".join(name for name, pat in checks.items() if re.search(pat, text)) or "형태없음")
    print(f"{qid:14}", " | ".join(outs))


probe("ocl:q2", {"context": r"context\s", "post": r"post\s*:", "result": r"\bresult\b"})
probe("drl:q2", {"rule": r"\brule\b", "when": r"\bwhen\b", "then": r"\bthen\b", "end": r"\bend\b"})
probe("dmn:q2", {"표": r"\|.*\|.*\|", "FEEL-if": r"\bif\b.*\bthen\b.*\belse\b",
                 "범위밖인지": r"범위 밖|밖입니다|다루지 않|표현할 수 없|cannot|not part of DMN|부수효과"})
probe("llvm:q1", {"define": r"\bdefine\b", "br": r"\bbr\b", "reg": r"%\w+"})

# customjson: 스키마 적합 + cond 파싱
rows = by_id.get("customjson:q1", [])
outs = []
for r in rows:
    if not r.get("ok"):
        outs.append("호출실패")
        continue
    d = first_json(r["text"])
    rules = d.get("rules") if isinstance(d, dict) else None
    if not isinstance(rules, list) or not rules:
        outs.append("스키마불일치")
        continue
    conds = [x.get("cond") for x in rules if isinstance(x, dict) and (x.get("cond") or "").strip()]
    bad = [c for c in conds if py_ok(c) != "ok"]
    outs.append(f"rules{len(rules)} cond파싱 {len(conds)-len(bad)}/{len(conds)}")
print("customjson:q1 ", " | ".join(outs))

print()
print("=" * 72)
print("수동 판정용 발췌 — 지식 문항 (rep1, 앞 500자)")
print("=" * 72)
for qid in ("fuml:q1", "fuml:q2", "kdm:q1", "kdm:q2", "ocl:q1", "sbvr:q1", "sbvr:q2",
            "dmn:q1", "drl:q1", "pysub:q2", "fuml:q3", "kdm:q3", "dmn:q3", "ocl:q3"):
    rows = by_id.get(qid, [])
    if not rows:
        continue
    r = rows[0]
    text = re.sub(r"<think>.*?</think>", "", r.get("text", ""), flags=re.S).strip()
    print(f"\n### {qid}\n{text[:500]}")
