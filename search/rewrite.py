"""search/rewrite — 용어집 기반 질의 보강 (놀이터 engine/query_rewrite 이식, spec 006/008).

약어 병기(expand_abbrevs, 결정론) + 근거제약 번역(rewrite_for_cluster, LLM 연결판단만) — 둘 다
원질의를 대체하지 않고 병합 재료를 만든다. 치환은 용어집 실재 canonical 만 허용, 실패=원질의 폴백.
LLM = search.llm(ROBO_SEARCH_LLM_CONFIG, 기본 Qwen/GPU). 문자열 조립은 코드가 결정론으로.
"""
from __future__ import annotations

import os
import re

from search import llm
from search.glossary import build as build_glossary, render_for_prompt
from search.recall import tokenize

# 의미 대조 임계(spec 008) — 0 = off(글자 겹침만). >0 이면 용어카드 임베딩 대조(사내 임베더).
GLOSSARY_SEM_MIN = float(os.environ.get("ROBO_GLOSSARY_SEM_MIN", "0"))
_ccache: dict = {}
_ABBREV = re.compile(r"^[A-Za-z][A-Za-z0-9&-]+$")


def _semantic_scores(q: str, terms: list[dict]) -> list[float]:
    """질의↔용어카드(표준어+별칭+뜻) 코사인 — 사내 임베더(질의·노드와 동일 모델)."""
    import numpy as np

    from llm.client import embedder
    cards = [" ".join([t.get("canonical", ""), *(t.get("colloquial") or []), t.get("meaning", "")])
             for t in terms]
    vecs = np.array(embedder.encode_texts(cards), dtype=np.float32)
    qv = np.array(embedder.encode_texts([q])[0], dtype=np.float32)
    return (vecs @ qv).tolist()


def _shortlist(q: str, top: int = 8) -> list[dict]:
    """용어집 유력후보 — 글자 겹침 우선 + (임계>0 시) 의미 유사 보강."""
    glo = build_glossary()
    terms = glo.get("terms", [])
    qt = set(tokenize(q))
    lex = []
    for i, t in enumerate(terms):
        text = " ".join([t.get("canonical", "")] + (t.get("colloquial") or []) + [t.get("meaning", "")])
        ov = len(qt & set(tokenize(text)))
        if ov:
            lex.append((ov, i))
    lex.sort(key=lambda x: -x[0])
    picked = [i for _, i in lex[:top]]
    if GLOSSARY_SEM_MIN > 0 and len(picked) < top and terms:
        sims = _semantic_scores(q, terms)
        sem = [(s, i) for i, s in enumerate(sims) if s >= GLOSSARY_SEM_MIN and i not in set(picked)]
        sem.sort(key=lambda x: -x[0])
        picked += [i for _, i in sem[: top - len(picked)]]
    return [terms[i] for i in picked]


def _cluster_system(q: str) -> str:
    cand = "\n".join(
        f"- 「{t['canonical']}」← 현장: {', '.join((t.get('colloquial') or [])[:6])}"
        + (f"  · {t.get('meaning', '')}" if t.get("meaning") else "")
        for t in _shortlist(q)) or "(없음)"
    glo_text = render_for_prompt(build_glossary())
    return f"""너는 레거시 코드 검색의 **질의 번역기**다. 사용자의 현장 말투 질의를,
코드그래프 요약문이 쓰는 표준어로 번역한다. 도메인은 아래 용어집에서 스스로 파악하라.

[왜] 질의와 코드 요약문의 어휘가 다르면 검색이 실패한다. 용어집 근거로 현장말투를
표준어로 치환해 요약문 어휘에 근접시키는 것이 네 일이다.

[★이 질의와 표현이 겹치는 유력 후보 — 치환은 여기서 우선 검토하라]
{cand}

{glo_text}

[규칙]
1) 치환·병기의 대상은 **반드시 위 용어집에 실재하는 canonical** 이어야 한다(새 단어 창작 금지).
   단 연결 판단에는 네 세상지식을 써라 — 질의의 일상어가 별칭 목록에 없어도, canonical 의
   실체(시스템·망·프로토콜·테이블)가 질의 개념과 같은 것을 가리키면 병기로 제안하라.
   용어집에 그 실체 자체가 없으면 원문 그대로 둔다.
2) **영문 약어·시스템명 canonical**(대문자·하이픈 표기)은 검색 변별력이 가장 크다 —
   후보에 있으면 반드시 채택해 원 표현과 병기하라(예: "배송망(CJ-EXP)"). 일반 개념어는
   약어가 없을 때만. ★질의어가 범주(망·시스템 종류·수단)를 가리키고 그 범주에 속하는
   canonical 이 여럿이면 하나만 고르지 말고 전부 병기하라(예: "배송망(CJ-EXP·HJ-LOG)").
3) 사용자가 준 구체 엔터티(고유명사·숫자·영문 식별자·기간)는 **그대로 보존**한다.
4) 질의의 의도(조회/변경/흐름 질문)를 바꾸지 말고, 사용자가 말하지 않은 개념을 추가하지 마라.
5) 번역할 게 없으면 원문 그대로 반환한다(substitutions 빈 배열).
6) **substitutions 에는 용어집 근거 치환만 기재하라** — 어순·조사·동의어 다듬기는 rewritten 에만.
7) 한 문장, 군더더기 없이.

[출력] 오직 단일 JSON 객체 1개. 마크다운/설명 금지.
{{"intent": "한 줄 의도", "substitutions": [{{"from": "현장말투", "to": "표준어"}}], "rewritten": "번역된 질의 한 문장"}}

[예시 — 무관 도메인(병원·물류). 패턴만 배우고 단어를 베끼지 마라]
입력: "환자 몸무게 재는 데가 어디"   (용어집에 「계측」←현장:"재는" 이 있다고 가정)
출력: {{"intent": "환자 체중 계측 위치", "substitutions": [{{"from": "재는", "to": "계측"}}], "rewritten": "환자 체중을 계측하는 로직"}}"""


def _harvest_subs(q: str, out: dict) -> list[dict]:
    """모델 출력에서 쓸 수 있는 치환만 수확 — 용어집 실재 to + 질의에 실재하는 from."""
    glo = build_glossary()
    canon = {(t.get("canonical") or "").strip().lower() for t in glo.get("terms", [])}
    canon |= {(a.get("canonical") or "").strip().lower() for a in glo.get("actions", [])}
    canon.discard("")
    subs = []
    for s in out.get("substitutions") or []:
        frm = (s.get("from") or "").strip()
        to = (s.get("to") or "").strip()
        if not frm or not to or to == frm or to in q or frm not in q:
            continue
        tl = to.lower()
        if tl not in canon and not any(tl in c or c in tl for c in canon):
            continue
        subs.append({"from": frm, "to": to})
    return subs


def _apply_subs(q: str, subs: list[dict]) -> str:
    """결정론 병기 조립 — 원문 보존하고 frm(to1·to2…) 형태로 삽입(범주어는 전부 병기)."""
    by: dict[str, list[str]] = {}
    for s in subs:
        tos = by.setdefault(s["from"], [])
        if s["to"] not in tos:
            tos.append(s["to"])
    rew = q
    for frm, tos in by.items():
        rew = rew.replace(frm, f"{frm}({'·'.join(tos)})", 1)
    return rew


def rewrite_for_cluster(q: str) -> dict:
    """질의 1개 번역. LLM 은 연결 판단(from→to)만, 조립은 코드. 반환 {rewritten, substitutions, intent, ok}."""
    system = _cluster_system(q)
    k = hash((system, q))
    if k in _ccache:
        return _ccache[k]
    out = llm.chat_json(q, system=system, max_tokens=1024)
    subs = _harvest_subs(q, out) if isinstance(out, dict) else []
    if subs:
        res = {"rewritten": _apply_subs(q, subs), "substitutions": subs,
               "intent": (out.get("intent") or ""), "ok": True}
    else:
        res = {"rewritten": q, "substitutions": [], "intent": "", "ok": False}
    _ccache[k] = res
    return res


def expand_abbrevs(q: str) -> dict:
    """결정론 질의 보강 — 질의와 겹치는 용어집의 영문 약어/시스템명 canonical 병기. LLM 없음."""
    hits = [t["canonical"] for t in _shortlist(q)
            if _ABBREV.match((t.get("canonical") or "").strip())]
    if not hits:
        return {"expanded": q, "terms": []}
    return {"expanded": f"{q} (관련 표준어: {', '.join(hits[:3])})", "terms": hits[:3]}
