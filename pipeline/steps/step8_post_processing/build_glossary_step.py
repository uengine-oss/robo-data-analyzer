"""build_glossary_step — 도메인 용어사전 종합 (spec 042 수집 + spec 043 AI 종합).

흐름 (수집·조립·확정은 결정론, 판단만 LLM — 헌법 I):
1. 수집 — 노드별 ``CodeNode.domain_terms`` dict를 표제어 단위로 병합하며
   출처 노드를 기록한다.
2. 문맥 조립 — 표제어마다 [별칭 후보(결정론 추림 — 닫힌 선택)] + [출처 노드의 원문
   주석·활성 본문에서 표제어 주변 구절]을 묶는다. 다른 LLM 필드(summary/search/module)는
   뜻의 증거로 재사용하지 않는다. 출처 노드 상한 초과분은 로그로 남긴다.
3. AI 종합 — 용어당 1콜 병렬(``glossary_synthesis`` rule): 뜻 종합 + 별칭 후보 중
   같은 실체 표기 선택(aliases). 전체 목록 열린 나열은 반복 폭주를 유발해 폐기
   (run9 실측). 실패·근거 기각 = 그 용어를 최종 사전에 싣지 않는다.
4. 수용(결정론) — evidence 가 입력 문맥의 실제 부분열이 아니면 종합 기각, aliases 는
   별칭 후보에 실재하는 표기만 인정(LLM 창작 원천 차단).
5. 그룹 확정(결정론 union-find) — 대표 = 최다 표본 표제어, 나머지는 colloquial 로. 검색기
   호환 스키마(terms_json: canonical/colloquial/meaning) 그대로 ``:GLOSSARY`` 저장.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Optional

from langchain_core.runnables import RunnableConfig
from pathlib import Path

from pipeline.models.code_analysis_output import GlossarySynthesisOutput
from graph.schema.graph_nodes import CodeNode
from llm.usage.cost_budget import BudgetExceededError
from llm.fanout import gather_with_warmup
from llm.runners.yaml_llm_runner import run_yaml_rule
from shared.events.runtime_logger import log_error, log_info, log_warning
from shared.files.source_excerpt import number_text, raw_node_text
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from pipeline.execution.analysis_handoff import (
    POSTPROCESS_PRIORITY,
    AnalysisHandoffAborted,
    handoff_of,
)
from pipeline.execution.step_config import step_config
from pipeline.execution.pause_controller import PauseController
from pipeline.steps.step8_post_processing.handoff_skip import log_handoff_skip

_STEP = "step8.build_glossary"
MAX_MEANINGS = 3      # 용어당 나란히 보존할 뜻 상한 (다의어 — 그 이상은 초기 등장 순)
MAX_CTX_NODES = 6     # 종합 문맥에 넣을 출처 노드 상한 (초과분 컷은 로그)
MAX_SOURCE_EXCERPT_CHARS = 1200

_UPSERT_QUERY = f"""
UNWIND $items AS item
MERGE (g:ANALYZER_OWNED:GLOSSARY {{id: "glossary", graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
SET g.terms_json = item.terms_json,
    g.n_terms = item.n_terms
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

def _node_context(fn: CodeNode, term: str) -> str:
    """원문에서 표제어 주변 구절만 잘라 glossary 근거로 제공한다.

    summary/search/module 같은 생성 필드를 뜻 증거로 다시 쓰면 한 번의 환각이 사전에서
    자기 자신을 증명한다. 그래서 source comment + code_text만 읽는다.
    """
    source = raw_node_text(fn)
    if not source:
        return f"[source {fn.file_path}:{fn.start_line}-{fn.end_line}]\n(원문 없음)"
    lines = source.splitlines()
    needle = term.casefold()
    hits = [index for index, line in enumerate(lines) if needle in line.casefold()][:3]
    windows: list[tuple[int, int]] = []
    for index in hits or [0]:
        start = max(0, index - 2)
        end = min(len(lines), index + 3)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    blocks: list[str] = []
    used = 0
    for start, end in windows:
        block = number_text("\n".join(lines[start:end]), fn.start_line + start)
        remaining = MAX_SOURCE_EXCERPT_CHARS - used
        if remaining <= 0:
            break
        blocks.append(block[:remaining])
        used += len(block)
    excerpt = "\n... [non-contiguous source lines omitted] ...\n".join(blocks)
    comment = (getattr(fn, "comment", "") or "").strip()
    comment_block = (
        "\n[node_comment scope=non-executable-metadata coordinate_system=unavailable]\n"
        f"{comment}"
        if comment else ""
    )
    return (
        f"[source file={fn.file_path} range={fn.start_line}-{fn.end_line} "
        "coordinate_system=absolute-file-line]\n"
        f"{excerpt}{comment_block}"
    )


def _build_contexts(
    terms: dict[str, dict],
) -> dict[str, str]:
    """표제어 → 원문 근거 문맥. 생성된 다른 의미 필드는 포함하지 않는다."""
    contexts: dict[str, str] = {}
    cut = 0
    for key, slot in terms.items():
        src = slot["src"]
        if len(src) > MAX_CTX_NODES:
            cut += 1
            src = src[:MAX_CTX_NODES]
        contexts[key] = "\n".join(
            _node_context(fn, slot["canonical"]) for fn in src
        )
    if cut:
        log_info(_STEP, "context_cut", terms=cut, cap=MAX_CTX_NODES)
    return contexts


# ── 4. 수용 (결정론 — LLM 창작 차단) ──────────────────────

_WS = re.compile(r"\s+")
_FLAT = re.compile(r"[\s_\-]+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s or "").strip().lower()


def _flat(s: str) -> str:
    """표기 변종 비교용 정규화 — 공백·하이픈·언더스코어 제거 + 소문자."""
    return _FLAT.sub("", (s or "").lower())


def _alias_candidates(self_key: str, terms: dict[str, dict], cap: int = 30) -> list[str]:
    """Return only exact normalized typography variants.

    Substring overlap proves neither identity nor compatible entity kind. A
    longer business phrase, SQL operation key, type, function, or table name
    never becomes an alias merely because it contains a shorter spelling.
    Uncertain terms stay separate.
    """
    base = _flat(terms[self_key]["canonical"])
    if len(base) < 3:
        return []
    hits: list[str] = []
    for k, v in terms.items():
        if k == self_key:
            continue
        other = _flat(v["canonical"])
        if base == other:
            hits.append(v["canonical"])
    return sorted(hits, key=str.casefold)[:cap]


def _accept(
    out: GlossarySynthesisOutput, context: str, candidates: set[str], self_key: str
) -> dict | None:
    """수용 규칙: evidence ⊂ 문맥 실문장, aliases ∈ 별칭 후보(자기 제외). 위반은 기각/정화."""
    if not (out.meaning or "").strip():
        return None
    evidence = _norm(out.evidence)
    if not evidence or evidence not in _norm(context):
        return None                        # 근거를 지어냄 → 종합 기각
    aliases = []
    for a in (out.aliases or "").split(","):
        a = a.strip()
        if a.lower() in candidates and a.lower() != self_key:
            aliases.append(a)              # 후보 실재 표기만 인정(그 항목만 정화)
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
        # 최종 meaning은 glossary synthesis LLM의 근거 통과 결과만 사용한다. 거부/실패한
        # 그룹을 domain_terms의 후보 meaning으로 채우면 서로 다른 의미 필드 복사가 된다.
        accepted_members = [member for member in members if member in accepted]
        if not accepted_members:
            continue
        # 대표 = 수용된 항목 중 표본 최다 — 코퍼스가 가장 많이 증언한 표기
        rep = max(
            accepted_members,
            key=lambda k: (len(terms[k]["meanings"]), -members.index(k)),
        )
        rep_slot = terms[rep]
        meaning = accepted[rep]["meaning"]
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
    pause: PauseController = cfg.get("pause") or PauseController()

    # 용어 후보는 step7 #1 산물이지만 최종 뜻의 증거는 원문만 사용한다. 전 대상 #1이
    # 확정된 뒤 시작하면 후보 집합은 완전하고, 모듈 요약을 기다릴 이유는 없다.
    handoff = handoff_of(cfg)
    try:
        await handoff.summaries()
        code_nodes: list[CodeNode] = handoff.merge(state.get("code_nodes", []))
    except AnalysisHandoffAborted:
        log_handoff_skip(_STEP, "용어사전", pause)
        return {}
    started = time.monotonic()

    terms = _collect_terms(code_nodes)
    if not terms:
        log_info(_STEP, "skip", reason="용어 조각 0 (domain_terms 빈 그래프)")
        return {}
    if strategy is None or neo is None:
        log_warning(_STEP, "config_missing", "strategy/neo_client 누락 — GLOSSARY 저장 skip")
        return {}

    contexts = _build_contexts(terms)
    # 용어별 별칭 후보 — 결정론 추림. LLM 은 이 안에서 고르기만 한다 (닫힌 선택).
    candidates_of = {k: _alias_candidates(k, terms) for k in terms}

    prompts_dir = Path(strategy.prompts_dir)
    # run 공유 슬롯 — step7 과 같은 세마포어를 낮은 우선순위로 쓴다 (spec 096).
    slots = handoff.slots

    async def _synthesize_one(key: str) -> tuple[str, GlossarySynthesisOutput]:
        slot = terms[key]
        async with slots.at(POSTPROCESS_PRIORITY):
            result: GlossarySynthesisOutput = await run_yaml_rule(
                prompts_dir=prompts_dir,
                rule_name="glossary_synthesis",
                user_values={
                    "alias_candidates": (
                        "\n".join(f"- {c}" for c in candidates_of[key]) or "(없음)"
                    ),
                    "term": slot["canonical"],
                    "meanings": " / ".join(slot["meanings"]),
                    "context": contexts[key],
                },
                response_model=GlossarySynthesisOutput,
                budget=cost_budget,
            )
        return key, result

    keys = list(terms)
    async def _run_fanout() -> list:
        return await gather_with_warmup(
            [_synthesize_one(k) for k in keys], return_exceptions=True,
        )

    results = await pause.run_restartable_during_pause(_run_fanout)

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
        res = _accept(out, contexts[key], {c.lower() for c in candidates_of[key]}, key)
        if res is None:
            rejected += 1                  # 근거 불충분 → 최종 사전 제외 (기각 자체는 정상 경로)
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
