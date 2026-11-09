# -*- coding: utf-8 -*-
"""세 그룹 동일 규칙 전수 재계산. 모델 재호출 0. 저장된 raw 만 사용."""
import ast, json, sys, pathlib, collections, importlib.util
ROOT = pathlib.Path(r"D:\work\robo\project\robo-data-analyzer")
spec = importlib.util.spec_from_file_location("runner", ROOT/"scripts"/"run_strategy_a_progressive.py")
runner = importlib.util.module_from_spec(spec); sys.modules["runner"]=runner; spec.loader.exec_module(runner)
RUN = ROOT/"specs/131-cross-node-semantic-grounding/_runs/framework/strategy-a/strategy-a-stage1-current-comparable-20260821-r1"

prepared = runner._prepare_pilot(runner.DEFAULT_SEAL)
by_prompt = {r.prompt_sha256: r for r in prepared.records}
by_key    = {r.record_key: r for r in prepared.records}
FIELDS = ("given", "then_relation")

def load_hist():
    out=[]
    for line in runner.HISTORICAL_RESULTS.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        d=json.loads(line)
        if d.get("prompt_sha256") in by_prompt and "response" in d:
            out.append((int(d["repeat"]), by_prompt[d["prompt_sha256"]], d["response"]))
    return out

def load_cur(variant):
    out=[]
    for line in (RUN/"results.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        d=json.loads(line)
        if d["variant"]!=variant: continue
        try: resp=runner.parse_json_no_duplicates(d.get("raw_content") or "")
        except Exception: resp=None
        out.append((int(d["repeat"]), by_key[d["record_key"]], resp))
    return out

def leaves_of(resp, rec):
    rules=(resp or {}).get("rules") if isinstance(resp,dict) else None
    rules=rules if isinstance(rules,dict) else {}
    return {row: (rules.get(row) if isinstance(rules.get(row),dict) else None) for row in rec.rows}

def measure(entries):
    m=collections.Counter()
    m["row_instances"]=0
    syn_rows=set(); syn_hits=0
    emp_rows=set(); emp_hits=0
    gnd_rows=set(); gnd_hits=0
    ruleA_ok=ruleB_ok=gnd_ok=0
    per_repeat=collections.defaultdict(lambda: collections.Counter())
    syn_examples=[]
    for repeat, rec, resp in entries:
        L=leaves_of(resp, rec)
        for row in rec.rows:
            rk=(rec.record_key,row)
            m["row_instances"]+=1
            per_repeat[repeat]["rows"]+=1
            leaf=L[row]
            fa=fb=fg=False
            if leaf is None:
                fa=fb=fg=True
            else:
                status=leaf.get("semantic_status")
                closure=runner._row_closure(rec.evidence, row)
                for f in FIELDS:
                    e=leaf.get(f)
                    if not isinstance(e,str):
                        fa=fb=True; continue
                    if not e:
                        if status=="exact" or f=="given":
                            fb=True; emp_rows.add(rk); emp_hits+=1
                        continue
                    try:
                        ast.parse(e, mode="eval")
                    except (SyntaxError, ValueError) as err:
                        fa=fb=True; syn_rows.add(rk); syn_hits+=1
                        if len(syn_examples)<40:
                            syn_examples.append({"func":rec.function_name,"row":row,"repeat":repeat,
                                                 "field":f,"expr":e,"error":f"{type(err).__name__}: {err}"})
                        continue
                    g=runner._grounding_failures(e, closure)
                    if g:
                        fg=True; gnd_rows.add(rk); gnd_hits+=len(g)
            ruleA_ok+=int(not fa); ruleB_ok+=int(not fb); gnd_ok+=int(not fg)
            per_repeat[repeat]["A"]+=int(not fa); per_repeat[repeat]["B"]+=int(not fb)
            per_repeat[repeat]["G"]+=int(not fg)
    return {
        "row_instances": m["row_instances"],
        "ruleA_ok": ruleA_ok, "ruleB_ok": ruleB_ok, "grounding_ok": gnd_ok,
        "syntax_error_hits": syn_hits, "syntax_error_rows": len(syn_rows),
        "empty_required_hits": emp_hits, "empty_required_rows": len(emp_rows),
        "grounding_hits": gnd_hits, "grounding_rows": len(gnd_rows),
        "per_repeat": {str(k): dict(v) for k,v in sorted(per_repeat.items())},
        "syntax_examples": syn_examples,
    }

def identity(entries):
    """3회 반복 동일성: 텍스트 동일 / AST 정규화 동일"""
    groups=collections.defaultdict(dict)
    recs={}
    for repeat, rec, resp in entries:
        groups[rec.record_key][repeat]=leaves_of(resp, rec); recs[rec.record_key]=rec
    logical=text_same=norm_same=status_same=0
    for key, reps in groups.items():
        rec=recs[key]
        if len(reps)!=3: continue
        for row in rec.rows:
            ls=[reps[r].get(row) for r in sorted(reps)]
            if any(l is None for l in ls): continue
            logical+=1
            status_same+=int(len({l.get("semantic_status") for l in ls})==1)
            text_same+=int(all(len({l.get(f) for l in ls})==1 for f in FIELDS))
            ok=True
            for f in FIELDS:
                v=[runner._expression_fingerprint(l.get(f), normalize_relation=True) for l in ls]
                ok = ok and None not in v and len(set(v))==1
            norm_same+=int(ok)
    return {"logical_rows":logical,"status_identical":status_same,
            "text_identical":text_same,"relation_normalized_identical":norm_same}

groups={
 "이전 모델 + 옛 wire": load_hist(),
 "현재 모델 + 옛 prompt": load_cur("historical_wire_control"),
 "현재 모델 + 개선 prompt": load_cur("evidence_closed_prompt_candidate"),
}
out={}
for name, e in groups.items():
    out[name]={"responses":len(e), **measure(e), "repeat_identity":identity(e)}
print(json.dumps(out, ensure_ascii=False, indent=2))
