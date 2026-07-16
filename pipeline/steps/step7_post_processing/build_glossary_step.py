"""build_glossary_step — 도메인 용어사전 종합 (spec 042 수집 + spec 043 AI 종합).

흐름 (수집·조립·확정은 결정론, 판단만 LLM — 헌법 I):
1. 수집 — 노드별 ``CodeNode.domain_terms`` dict를 표제어 단위로 병합하며
   출처 노드를 기록한다.
2. 문맥 조립 — 표제어마다 [전체 표제어 목록(모든 콜 공유 접두 — KV 캐시)] + [출처 노드들의
   summary·search_text] + [그 노드들의 모듈 요약]을 묶는다. 출처 노드 상한 초과분은 컷하되
   로그로 남긴다(조용한 절단 금지 — 헌법 IV).
3. AI 종합 — 용어당 1콜 병렬(``glossary_synthesis`` rule): 뜻 종합 + 같은 실체 표기 변종
   지목(aliases). 실패 = 그 용어 원본 유지(개선 전용, 사전을 악화시킬 수 없음).
4. 수용(결정론) — evidence 가 입력 문맥의 실제 부분열이 아니면 종합 기각, aliases 는 전체
   표제어 목록에 실재하는 표기만 인정(LLM 창작 원천 차단).
5. 그룹 확정(결정론 union-find) — 대표 = 최다 표본 표제어, 나머지는 colloquial 로. 검색기
   호환 스키마(terms_json: canonical/colloquial/meaning) 그대로 ``:GLOSSARY`` 저장.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from typing import Optional

from langchain_core.runnables import RunnableConfig
from pathlib import Path

from pipeline.data_models.code_analysis_output import GlossarySynthesisOutput
from graph.schema.graph_nodes import CodeNode, Module
from system.env_catalog import ENV
from llm.client.usage.cost_budget import BudgetExceededError
from llm.client.llm_fanout import gather_with_warmup
from llm.client.runners.yaml_llm_runner import run_yaml_rule
from log.logger import log_error, log_info, log_warning
from graph.neo4j_client import Neo4jClient
from pipeline.execution.step_config import step_config

from pipeline.steps.step7_post_processing.summarize_module_step import _match_code_to_modules

_STEP = "step7.build_glossary"
MAX_MEANINGS = 3      # 용어당 나란히 보존할 뜻 상한 (다의어 — 그 이상은 초기 등장 순)
MAX_CTX_NODES = 6     # 종합 문맥에 넣을 출처 노드 상한 (초과분 컷은 로그)

_UPSERT_QUERY = """
UNWIND $items AS item
MERGE (g:GLOSSARY {id: "glossary"})
SET g.terms_json = item.terms_json, g.n_terms = item.n_terms
"""


# ── 1. 수집 (결정론) ──────────────────────────────────────

def _collect_terms(code_nodes: list[CodeNode]) -> dict[str, dict]:
    """노드별 표제어→뜻 dict를 lower(표제어) 키로 병합하고 출처 노드를 기록한다."""
    merged: dict[str, dict] = {}
    for fn in code_nodes:
        for term, meaning in fn.domain_terms.items():
            term, meaning = term.strip(), meaning.strip()
            if not term or not meaning or len(term) > 60:
                continue
            slot = merged.setdefault(
                term.lower(), {"canonical": term, "meanings": [], "src": []}
            )
            if meaning not in slot["meanings"]:
                slot["meanings"].append(meaning)
            if fn not in slot["src"]:
                slot["src"].append(fn)
    return merged


# ── 2. 문맥 조립 (결정론) ─────────────────────────────────

def _node_context(fn: CodeNode, module_summary: str) -> str:
    parts = [f"[{fn.name}]"]
    if fn.summary:
        parts.append(fn.summary.replace("\n", " ").strip())
    if fn.search_text:
        parts.append(fn.search_text.replace("\n", " ").strip())
    if module_summary:
        parts.append(f"(모듈 요약) {module_summary}")
    return "\n".join(parts)


def _build_contexts(
    terms: dict[str, dict], code_nodes: list[CodeNode], modules: list[Module]
) -> dict[str, str]:
    """표제어 → 종합 문맥 텍스트. 모듈 요약은 summarize_module 의 매칭 로직 재사용."""
    fn_by_module = _match_code_to_modules(code_nodes, modules)
    module_of: dict[str, str] = {}
    for m in modules:
        summary = (getattr(m, "summary", None) or "").replace("\n", " ").strip()[:300]
        for fn in fn_by_module.get(m.id, []):
            module_of[fn.id] = summary

    contexts: dict[str, str] = {}
    cut = 0
    for key, slot in terms.items():
        src = slot["src"]
        if len(src) > MAX_CTX_NODES:
            cut += 1
            src = src[:MAX_CTX_NODES]
        contexts[key] = "\n".join(_node_context(fn, module_of.get(fn.id, "")) for fn in src)
    if cut:
        log_info(_STEP, "context_cut", terms=cut, cap=MAX_CTX_NODES)
    return contexts


# ── 4. 수용 (결정론 — LLM 창작 차단) ──────────────────────

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s or "").strip().lower()


def _accept(
    out: GlossarySynthesisOutput, context: str, catalog: dict[str, str], self_key: str
) -> dict | None:
    """수용 규칙: evidence ⊂ 문맥 실문장, aliases ∈ 표제어 목록(자기 제외). 위반은 기각/정화."""
    if not (out.meaning or "").strip():
        return None
    if _norm(out.evidence) and _norm(out.evidence) not in _norm(context):
        return None                        # 근거를 지어냄 → 종합 기각(원본 유지)
    aliases = []
    for a in (out.aliases or "").split(","):
        a = a.strip()
        if a.lower() in catalog and a.lower() != self_key:
            aliases.append(a)              # 목록 실재 표기만 인정(그 항목만 정화)
    return {"meaning": out.meaning.strip(), "aliases": aliases}


# ── 5. 그룹 확정 (결정론 — 상호 지목만 파괴적 병합) ────────
# 노이즈 간선 위 추이적 합집합은 오지목 하나로 사전 전체가 한 덩어리로 붕괴한다(실측:
# 164→1). 파괴적 병합(항목 흡수)은 **양방향 상호 지목**일 때만. 일방 지목은 항목을
# 보존한 채 지목자의 colloquial 에 상대 표기를 병기(검색 재료) — 목적은 검색 다리다.

def _union_groups(terms: dict[str, dict], accepted: dict[str, dict]) -> list[dict]:
    nominate: dict[str, set[str]] = {
        k: {a.lower() for a in accepted.get(k, {}).get("aliases", [])} for k in terms
    }
    parent: dict[str, str] = {k: k for k in terms}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in terms:
        for b in nominate[a]:
            if b in terms and a in nominate.get(b, set()):    # 상호 지목만
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

    groups: dict[str, list[str]] = defaultdict(list)
    for k in terms:
        groups[find(k)].append(k)

    out: list[dict] = []
    for members in groups.values():
        # 대표 = 표본(수집된 뜻 조각) 최다 — 코퍼스가 가장 많이 증언한 표기
        rep = max(members, key=lambda k: (len(terms[k]["meanings"]), -members.index(k)))
        rep_slot = terms[rep]
        res = accepted.get(rep)
        meaning = res["meaning"] if res else " / ".join(rep_slot["meanings"][:MAX_MEANINGS])
        colloquial = [terms[m]["canonical"] for m in members if m != rep]
        # 일방 지목은 비파괴 병기 — 그룹 밖 표기를 검색 재료로만 싣는다
        for m in members:
            for b in nominate[m]:
                cross = terms.get(b, {}).get("canonical")
                if cross and find(b) != find(rep) and cross not in colloquial:
                    colloquial.append(cross)
        out.append({
            "canonical": rep_slot["canonical"],
            "colloquial": colloquial,
            "meaning": meaning,
            "category": "",
        })
    return out


# ── 메인 step ─────────────────────────────────────────────

async def build_glossary_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """수집 → 문맥 조립 → AI 종합(용어당 1콜 병렬) → 수용 → 그룹 확정 → 저장. state 무변경."""
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")

    code_nodes: list[CodeNode] = state.get("code_nodes", [])
    modules: list[Module] = state.get("modules", [])
    started = time.monotonic()

    terms = _collect_terms(code_nodes)
    if not terms:
        log_info(_STEP, "skip", reason="용어 조각 0 (domain_terms 빈 그래프)")
        return {}
    if strategy is None or neo is None:
        log_warning(_STEP, "config_missing", "strategy/neo_client 누락 — GLOSSARY 저장 skip")
        return {}

    contexts = _build_contexts(terms, code_nodes, modules)
    catalog = {k: v["canonical"] for k, v in terms.items()}      # lower → 원표기
    term_catalog = "\n".join(f"- {v['canonical']}" for v in terms.values())

    prompts_dir = Path(strategy.prompts_dir)
    semaphore = cfg.get("llm_semaphore") or asyncio.Semaphore(ENV.pipeline.max_concurrency)

    async def _synthesize_one(key: str) -> tuple[str, GlossarySynthesisOutput]:
        slot = terms[key]
        async with semaphore:
            result: GlossarySynthesisOutput = await run_yaml_rule(
                prompts_dir=prompts_dir,
                rule_name="glossary_synthesis",
                user_values={
                    "term_catalog": term_catalog,
                    "term": slot["canonical"],
                    "meanings": " / ".join(slot["meanings"]),
                    "context": contexts[key],
                },
                response_model=GlossarySynthesisOutput,
                budget=cost_budget,
            )
        return key, result

    keys = list(terms)
    results = await gather_with_warmup(
        [_synthesize_one(k) for k in keys], return_exceptions=True,
    )

    accepted: dict[str, dict] = {}
    failed = rejected = 0
    for key, result in zip(keys, results):
        if isinstance(result, BudgetExceededError):
            raise result
        if isinstance(result, BaseException):
            failed += 1
            log_error(_STEP, "synthesis_call", result, term=terms[key]["canonical"])
            continue
        _, out = result
        res = _accept(out, contexts[key], catalog, key)
        if res is None:
            rejected += 1                  # 근거 불충분 → 원본 유지 (기각 자체는 정상 경로)
            continue
        accepted[key] = res

    final_terms = _union_groups(terms, accepted)
    payload = json.dumps({"terms": final_terms, "actions": []}, ensure_ascii=False)
    await neo.run_batch_unwind(_UPSERT_QUERY, [{"terms_json": payload, "n_terms": len(final_terms)}])
    log_info(
        _STEP, "done",
        collected=len(terms), synthesized=len(accepted), rejected=rejected, failed=failed,
        groups=len(final_terms), grouped_variants=len(terms) - len(final_terms),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {}
