"""build_glossary_step — 도메인 용어사전 종합 (spec 042, 결정론·LLM 0).

분할1이 노드마다 뽑아 둔 용어 조각(``CodeNode.domain_terms`` = "약어=뜻; …")을 전량
수집·병합해 사전 완성본을 ``:GLOSSARY`` 단일 노드(terms_json)로 저장한다. 검색기(놀이터)는
이 완성본을 읽기만 한다 — 검색 시점 LLM 사전 생성(재생성마다 별칭·뜻이 변하는 복권)을 소멸.

병합 규칙: 같은 용어(canonical, 대소문자 무시)에 쌓인 뜻들을 중복 제거하고, 서로 다른 뜻
(다의어·문맥차)은 " / " 로 나란히 보존(최대 3개) — 억지로 하나를 고르지 않는다(정보 손실 방지).
사용자 일상어 별칭은 저장하지 않는다 — 별칭 잇기는 질의 시점(글자→의미→AI 관문)의 몫.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from data_models.graph_nodes import CodeNode
from shared.log.logger import log_info, log_warning
from shared.neo4j.neo4j_client import Neo4jClient
from shared.workflow.step_utils import step_config

_STEP = "step7.build_glossary"
MAX_MEANINGS = 3    # 용어당 나란히 보존할 뜻 상한 (다의어 — 그 이상은 초기 등장 순)

_UPSERT_QUERY = """
UNWIND $items AS item
MERGE (g:GLOSSARY {id: "glossary"})
SET g.terms_json = item.terms_json, g.n_terms = item.n_terms
"""


def _merge_terms(code_nodes: list[CodeNode]) -> list[dict]:
    """노드별 "약어=뜻; …" 조각 → canonical 병합 용어 목록 (결정론, 등장 순 보존)."""
    merged: dict[str, dict] = {}    # lower(canonical) → {canonical, meanings[]}
    for fn in code_nodes:
        for piece in (fn.domain_terms or "").split(";"):
            term, _, meaning = piece.partition("=")
            term, meaning = term.strip(), meaning.strip()
            if not term or not meaning or len(term) > 60:
                continue
            slot = merged.setdefault(term.lower(), {"canonical": term, "meanings": []})
            if meaning not in slot["meanings"]:
                slot["meanings"].append(meaning)
    return [
        {"canonical": s["canonical"], "colloquial": [],
         "meaning": " / ".join(s["meanings"][:MAX_MEANINGS]), "category": ""}
        for s in merged.values()
    ]


async def build_glossary_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """domain_terms 조각 수집·병합 → :GLOSSARY 노드 저장. state 무변경(키 충돌 회피)."""
    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")

    code_nodes: list[CodeNode] = state.get("code_nodes", [])
    started = time.monotonic()
    terms = _merge_terms(code_nodes)
    if not terms:
        log_info(_STEP, "skip", reason="용어 조각 0 (domain_terms 빈 그래프)")
        return {}

    if neo is None:
        log_warning(_STEP, "config_missing", "neo_client 누락 — GLOSSARY 저장 skip")
        return {}

    payload = json.dumps({"terms": terms, "actions": []}, ensure_ascii=False)
    await neo.run_batch_unwind(_UPSERT_QUERY, [{"terms_json": payload, "n_terms": len(terms)}])
    log_info(
        _STEP, "done",
        terms=len(terms),
        multi_meaning=sum(1 for t in terms if " / " in t["meaning"]),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {}
