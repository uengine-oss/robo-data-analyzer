"""search/retriever — 요구사항 → 노이즈 없는 관련 코드 뭉치 (놀이터 cluster_retriever 이식).

흐름: 의미+어휘 리콜 → core 선별(CE 1차·신호부재 시 코사인 폴백·+타입 가점) → 허브 컷
      → 관계확장 1홉(코사인 게이트) → 구조화 뭉치.
★per-request DB: 전역 `.npz`/드라이버 대신 요청 session·corpus 를 받는다. 후보 코사인(_cos)은
  전량 행렬이 아니라 요청 DB 에서 **후보 벡터만** 조회(FR-004 — 전량 로드 없음).
판단 LLM(rewrite·judge)은 기본 off(config) — 켤 때만 lazy import(base 경로는 LLM 불필요).
progress 콜백은 side-channel(코어 반환 불변) — 게이트/eval 은 None 으로 부른다.
"""
from __future__ import annotations

import numpy as np

from search.config import Config
from search.fusion import infer_types, type_affinity
from search.recall import recall
from search.rerank import passage, predict_cached
from shared.llm import embedder

_TABLE_RELS = ("READS", "WRITES", "AFFECTS_TABLE")


def merge_clusters(a: list[dict], b: list[dict]) -> list[dict]:
    """뭉치 병합 — id 기준, core 우선·relevance 큰 쪽. calls 는 사본 간 합집합(구조 증거 보존)."""
    out: dict[str, dict] = {}
    for x in a + b:
        cur = out.get(x["id"])
        if cur is None:
            out[x["id"]] = x
            continue
        merged_calls = list(dict.fromkeys(cur.get("calls", []) + x.get("calls", [])))
        if (x["role"] == "core" and cur["role"] != "core") \
                or (x["role"] == cur["role"] and x["relevance"] > cur["relevance"]):
            out[x["id"]] = x
        out[x["id"]]["calls"] = merged_calls
    return sorted(out.values(), key=lambda x: (x["role"] != "core", -x["relevance"]))


class Retriever:
    """요청 1건용 리트리버 — session·corpus 주입(전역 싱글턴 아님). 후보 벡터는 per-retrieve 캐시."""

    def __init__(self, session, corpus):
        self.s = session
        self.c = corpus
        self._vecs: dict[str, np.ndarray] = {}

    # ── 후보 벡터 (요청 DB 에서 필요분만) ──
    def _ensure_vecs(self, ids) -> None:
        missing = [i for i in ids if i not in self._vecs]
        if not missing:
            return
        rows = self.s.run(
            "MATCH (n) WHERE n.id IN $ids AND n.embedding IS NOT NULL "
            "RETURN n.id AS id, n.embedding AS v", ids=list(missing)).data()
        for r in rows:
            self._vecs[r["id"]] = np.array(r["v"], dtype=np.float32)

    def _cos(self, cid: str, qv) -> float:
        v = self._vecs.get(cid)
        return float(v @ qv) if v is not None else 0.0

    # ── 신호 ──
    def _ce(self, query: str, cids: list, cfg) -> dict[str, float]:
        cids = [c for c in cids if c in self.c.by_id]
        return dict(zip(cids, predict_cached(
            cfg.ce_model, [(query, passage(self.c.by_id[c])) for c in cids])))

    def _is_hub(self, cid: str, cfg) -> bool:
        return self.c.fan_in.get(cid, 0) >= cfg.cluster_hub_fanin

    def _fuse(self, cids, sem: dict, wanted: set, cfg) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        for cid in cids:
            if cid not in self.c.by_id:
                continue
            bonus = cfg.cluster_type_weight if type_affinity(self.c.by_id[cid], wanted) else 0.0
            out[cid] = (sem[cid], sem[cid] + bonus)
        return out

    def _keep_neighbor(self, cid: str, cfg, qv) -> bool:
        return (cid in self.c.by_id and not self._is_hub(cid, cfg)
                and self._cos(cid, qv) >= cfg.cluster_cos_min)

    # ── 그래프 조회 ──
    def _callees(self, cid: str) -> list[dict]:
        return self.s.run(
            "MATCH (x {id:$id})-[:CALLS]->(b) WHERE b.id IS NOT NULL "
            "RETURN b.id AS id, coalesce(b.name,'') AS name", id=cid).data()

    def _node_row(self, cid: str, cfg, qv, score: dict, callees: list[dict], role: str) -> dict:
        rec = self.s.run(
            "MATCH (x {id:$id}) "
            "OPTIONAL MATCH (x)-[r]->(t:TABLE) WHERE type(r) IN $rels "
            "RETURN elementId(x) AS eid, collect({rel:type(r), t:t.name}) AS tb",
            id=cid, rels=list(_TABLE_RELS)).single()
        rw: dict[str, list] = {}
        for e in (rec["tb"] if rec else []):
            if e.get("rel") and e.get("t"):
                rw.setdefault(e["rel"], []).append(e["t"])
        calls = [c["name"] for c in callees if c["name"] and self._keep_neighbor(c["id"], cfg, qv)]
        n = self.c.by_id[cid]
        return {
            "id": cid, "element_id": rec["eid"] if rec else None,
            "name": n.get("name") or cid.split(":")[0],
            "label": n.get("label", ""), "stereotype": n.get("stereotype", ""),
            "summary": (n.get("summary") or "").replace("\n", " "),
            "search_text": (n.get("search_text") or "").replace("\n", " "),
            "writes": sorted(set(rw.get("WRITES", []))),
            "reads": sorted(set(rw.get("READS", []) + rw.get("AFFECTS_TABLE", []))),
            "calls": calls,
            "relevance": round(score[cid][1], 3), "role": role,
        }

    # ── 파이프라인 ──
    def retrieve(self, query: str, cfg: Config, pool_k: int = 30,
                 on_progress=None, meta: dict | None = None) -> list[dict]:
        base = self._retrieve_one(query, cfg, pool_k, on_progress)
        if cfg.cluster_rewrite_mode != "novel_abbrev":
            return self._judge_prune(query, base, cfg, meta, on_progress)
        from search.rewrite import expand_abbrevs, rewrite_for_cluster  # lazy — LLM 경로

        out = base
        ab = expand_abbrevs(query)
        if ab["terms"]:
            out = merge_clusters(out, self._retrieve_one(ab["expanded"], cfg, pool_k, None))
            if meta is not None:
                meta["expanded_query"] = ab["expanded"]
        rw = rewrite_for_cluster(query)
        if rw["ok"] and rw["rewritten"].strip() != query.strip() and _has_novel_terms(base, rw):
            out = merge_clusters(out, self._retrieve_one(rw["rewritten"], cfg, pool_k, None))
            if meta is not None:
                meta["rewritten_query"] = rw["rewritten"]
        return self._judge_prune(query, out, cfg, meta, on_progress)

    def _retrieve_one(self, query: str, cfg: Config, pool_k: int, on_progress) -> list[dict]:
        wanted = infer_types(query)
        qv = np.array(embedder.encode_texts([query])[0], dtype=np.float32)
        cands = recall(self.s, self.c, [query], [qv], cfg)
        pool = [c.node["id"] for c in cands[:pool_k] if c.node["id"] in self.c.by_id]
        if on_progress:
            on_progress("recall", "관련 코드 후보 찾는 중", f"후보 {len(pool)}개")
        self._ensure_vecs(pool)

        ce_pool = pool[: cfg.cluster_ce_top]   # CE 는 RRF 상위 N만(CPU 절감) — 꼬리는 코사인 축 담당
        if on_progress:
            on_progress("rerank", "관련도 채점 중", f"상위 {len(ce_pool)}개 정밀 채점 (후보 {len(pool)})")
        ce = self._ce(query, ce_pool, cfg)
        cos = {cid: self._cos(cid, qv) for cid in pool}
        alive = sum(1 for v in ce.values() if v >= cfg.cluster_ce_min) >= cfg.cluster_ce_quorum
        # CE 미채점 꼬리는 CE=0 취급 — 합집합 게이트의 코사인 축(cos≥cos_min)으로만 통과 가능.
        sem = {c: ce.get(c, 0.0) for c in pool} if alive else cos
        score = self._fuse(pool, sem, wanted, cfg)
        if alive:
            passes = lambda c: score[c][0] >= cfg.cluster_ce_min or cos[c] >= cfg.cluster_cos_min
        else:
            passes = lambda c: score[c][0] >= cfg.cluster_cos_min
        core = sorted((c for c in pool if passes(c) and not self._is_hub(c, cfg)),
                      key=lambda c: -score[c][1])[: cfg.cluster_top_n]

        callees = {cid: self._callees(cid) for cid in core}
        related: list[str] = []
        if cfg.cluster_expand_hops >= 1:
            cand = {c["id"] for cs in callees.values() for c in cs} - set(core)
            self._ensure_vecs(cand)
            related = [b for b in cand if self._keep_neighbor(b, cfg, qv)]
            score.update(self._fuse(related, {b: self._cos(b, qv) for b in related}, wanted, cfg))
            related.sort(key=lambda b: -score[b][1])
            for bid in related:
                callees[bid] = self._callees(bid)
        if on_progress:
            on_progress("expand", "호출관계로 뭉치 넓히는 중", f"+{len(related)}개")

        result: dict[str, dict] = {}
        for cid in core:
            result[cid] = self._node_row(cid, cfg, qv, score, callees[cid], "core")
        for bid in related:
            result[bid] = self._node_row(bid, cfg, qv, score, callees[bid], "related")
        return sorted(result.values(), key=lambda x: (x["role"] != "core", -x["relevance"]))

    def _judge_prune(self, query: str, out: list[dict], cfg: Config,
                     meta: dict | None, on_progress) -> list[dict]:
        if cfg.cluster_judge_mode != "p15" or len(out) < cfg.cluster_judge_min:
            return out
        from search.judge import judge  # lazy — LLM 경로

        if on_progress:
            on_progress("judge", "관련 없는 것 걸러내는 중",
                        f"상위 {min(len(out), cfg.cluster_judge_top)}개 심판 (전체 {len(out)})")
        head = out[: cfg.cluster_judge_top]
        verdict = judge(query, head)
        if verdict is None:
            return out
        pruned = [n for i, n in enumerate(head) if i in verdict] or head
        calls = {name for n in pruned for name in n.get("calls", [])}
        pruned += [n for n in out[cfg.cluster_judge_top:] if n["name"] in calls]
        if meta is not None:
            meta["judged"] = f"{len(out)}→{len(pruned)}"
        return pruned


def _has_novel_terms(base: list[dict], rw: dict) -> bool:
    """번역의 치환 표준어가 기존 결과(이름+요약)에 없는 새 어휘인가 — 병합 근거(novelty 게이트)."""
    blob = " ".join((x["name"] + " " + x["summary"]).lower() for x in base)
    subs = [(s.get("to") or "").strip().lower() for s in (rw.get("substitutions") or [])]
    return any(t and t not in blob for t in subs)
