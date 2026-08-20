"""요구사항에서 근거가 있는 관련 코드 클러스터를 검색한다.

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
from search.ranking.fusion import infer_types, type_affinity
from search.ranking.recall import recall
from search.ranking.rerank import passage, predict_cached
from search.corpus.evidence_reader import read_rules
from llm import embedder
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import (
    AFFECTS_TABLE,
    HAS_CALL_OCCURRENCE,
    READS,
    WRITES,
)
from search.ranking.anchors import merge_clusters
from search.ranking.graph_access import (
    callees,
    call_neighbors,
    rules_for,
    table_neighbors,
)
from search.ranking.row_builder import table_row
from search.ranking.selection_policy import support_plan

_TABLE_RELS = (READS, WRITES, AFFECTS_TABLE)


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
            "MATCH (n {graph_owner:$graph_owner, generation_id:$generation_id}) "
            "WHERE n.id IN $ids AND n.embedding IS NOT NULL "
            "RETURN n.id AS id, n.embedding AS v",
            ids=list(missing), graph_owner=ANALYZER_GRAPH_OWNER,
        ).data()
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
        return callees(self.s, cid)

    def _call_neighbors(self, cid: str) -> list[dict]:
        return call_neighbors(self.s, cid)

    def _table_neighbors(self, cid: str) -> list[dict]:
        return table_neighbors(self.s, cid)

    def _rules_for(self, cid: str) -> list[dict]:
        return rules_for(self.s, cid)

    def _node_row(self, cid: str, cfg, qv, score: dict, callees: list[dict], role: str) -> dict:
        rec = self.s.run(
            "MATCH (x {id:$id, graph_owner:$graph_owner, generation_id:$generation_id}) "
            f"CALL {{ WITH x OPTIONAL MATCH (x)-[:{HAS_CALL_OCCURRENCE}]->(occ:CALL_OCCURRENCE) "
            "RETURN count(occ) AS call_total, "
            "sum(CASE WHEN occ.outcome = 'unresolved' THEN 1 ELSE 0 END) AS call_unresolved } "
            "OPTIONAL MATCH (x)-[r]->(t:TABLE {graph_owner:$graph_owner, generation_id:$generation_id}) "
            "WHERE type(r) IN $rels "
            "RETURN elementId(x) AS eid, collect({rel:type(r), t:t.name}) AS tb, "
            "call_total, call_unresolved",
            id=cid, rels=list(_TABLE_RELS), graph_owner=ANALYZER_GRAPH_OWNER,
        ).single()
        rw: dict[str, list] = {}
        for e in (rec["tb"] if rec else []):
            if e.get("rel") and e.get("t"):
                rw.setdefault(e["rel"], []).append(e["t"])
        calls = [c["name"] for c in callees if c["name"] and self._keep_neighbor(c["id"], cfg, qv)]
        n = self.c.by_id[cid]
        return {
            "id": cid, "element_id": rec["eid"] if rec else None,
            "name": n.get("name") or n.get("label") or "Unknown",
            "label": n.get("label", ""), "stereotype": n.get("stereotype", ""),
            "summary": (n.get("summary") or "").replace("\n", " "),
            "writes": sorted(set(rw.get("WRITES", []))),
            "reads": sorted(set(rw.get("READS", []) + rw.get("AFFECTS_TABLE", []))),
            "calls": calls,
            "call_resolution": {
                "status": "insufficient" if rec and rec["call_unresolved"] else "sufficient",
                "total": int(rec["call_total"] or 0) if rec else 0,
                "unresolved": int(rec["call_unresolved"] or 0) if rec else 0,
            },
            "rules": self._rules_for(cid),
            "relevance": round(score[cid][1], 3), "role": role,
        }

    @staticmethod
    def _table_row(table: dict, anchor_id: str, anchor_relevance: float) -> dict:
        return table_row(table, anchor_id, anchor_relevance)

    # retrieval pipeline

    # ── 파이프라인 ──
    def retrieve(self, query: str, cfg: Config, pool_k: int = 30,
                 on_progress=None, meta: dict | None = None) -> list[dict]:
        if cfg.cluster_selection_mode == "global_anchor":
            return self._retrieve_global_anchors(query, cfg, pool_k, on_progress, meta)
        if cfg.cluster_selection_mode == "legacy":
            return self._retrieve_legacy(query, cfg, pool_k, on_progress, meta)
        raise ValueError(
            "cluster_selection_mode must be 'global_anchor' or 'legacy': "
            f"{cfg.cluster_selection_mode!r}"
        )

    def _retrieve_legacy(self, query: str, cfg: Config, pool_k: int,
                         on_progress=None, meta: dict | None = None) -> list[dict]:
        """spec 044 기준선. spec 053 A/B가 끝날 때까지 삭제하지 않는다."""
        base = self._retrieve_one(query, cfg, pool_k, on_progress)
        if cfg.cluster_rewrite_mode != "novel_abbrev":
            return self._judge_prune(query, base, cfg, meta, on_progress)
        from search.query.rewrite import expand_abbrevs, rewrite_for_cluster  # lazy — LLM 경로

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

    def _hop_distances(self, anchor_ids: list[str], other_ids: list[str],
                       max_hops: int, cfg: Config | None = None) -> dict[str, int]:
        """앵커와의 CALLS 거리(무방향 BFS, ≤max_hops) — 단 **허브 비경유**.

        공용 유틸(fan_in ≥ hub_fanin, 예: SAFECPY_STR)을 경유하면 전 함수가 2홉이 되어
        거리가 무의미해진다(실측) — 확장은 비허브 노드를 통해서만 한다(도착 노드 자체는
        허브 여부와 무관하게 거리를 기록).
        """
        others = {cid for cid in other_ids if cid not in anchor_ids}
        if not anchor_ids or not others:
            return {}
        hub_fanin = cfg.cluster_hub_fanin if cfg else 20
        dist: dict[str, int] = {}
        frontier = set(anchor_ids)
        seen = set(anchor_ids)
        for hop in range(1, max_hops + 1):
            if not frontier:
                break
            rows = self.s.run(
                "MATCH (x {graph_owner:$graph_owner, generation_id:$generation_id})-[:CALLS]-"
                "(y {graph_owner:$graph_owner, generation_id:$generation_id}) "
                "WHERE x.id IN $ids AND y.id IS NOT NULL "
                "RETURN DISTINCT y.id AS id",
                ids=list(frontier), graph_owner=ANALYZER_GRAPH_OWNER,
            ).data()
            reached = {r["id"] for r in rows} - seen
            for cid in reached & others:
                dist.setdefault(cid, hop)
            seen |= reached
            frontier = {cid for cid in reached if self.c.fan_in.get(cid, 0) < hub_fanin}
        return dist

    @staticmethod
    def _support_plan(ranked: list[str], anchors: list[str], score: dict,
                      cfg: Config, dists: dict[str, int] | None = None,
                      ) -> tuple[list[str], list[str]]:
        return support_plan(ranked, anchors, score, cfg, dists)

    def _retrieve_global_anchors(self, query: str, cfg: Config, pool_k: int,
                                 on_progress=None, meta: dict | None = None) -> list[dict]:
        """변형별 후보를 먼저 합친 뒤 전역 앵커를 게이트로 고르고 딱 1홉만 evidence로 확장."""
        base = self._retrieve_one(query, cfg, pool_k, on_progress, expand=False)
        variants: list[tuple[str, str, list[dict]]] = [("original", query, base)]

        if cfg.cluster_rewrite_mode == "novel_abbrev":
            from search.query.rewrite import expand_abbrevs, rewrite_for_cluster

            ab = expand_abbrevs(query)
            if ab["terms"]:
                variants.append((
                    "abbrev", ab["expanded"],
                    self._retrieve_one(ab["expanded"], cfg, pool_k, None, expand=False),
                ))
                if meta is not None:
                    meta["expanded_query"] = ab["expanded"]
            rw = rewrite_for_cluster(query)
            if rw["ok"] and rw["rewritten"].strip() != query.strip() and _has_novel_terms(base, rw):
                variants.append((
                    "rewrite", rw["rewritten"],
                    self._retrieve_one(rw["rewritten"], cfg, pool_k, None, expand=False),
                ))
                if meta is not None:
                    meta["rewritten_query"] = rw["rewritten"]

        candidates: dict[str, dict] = {}
        sources: dict[str, set[str]] = {}
        for source, _variant_query, nodes in variants:
            for node in nodes:
                sources.setdefault(node["id"], set()).add(source)
                current = candidates.get(node["id"])
                if current is None or node["relevance"] > current["relevance"]:
                    candidates[node["id"]] = node
        if not candidates:
            return []

        # 전역 채점은 변형별 최종결과를 합치는 대신, 채택된 모든 질의 문맥으로 후보를 한 번
        # 다시 비교한다. 후보 수는 변형당 top_n의 합 이하라 기존 변형별 CE 총량보다 크지 않다.
        combined_query = "\n".join(dict.fromkeys(vq for _, vq, _ in variants))
        candidate_ids = list(candidates)
        qv = np.array(embedder.encode_texts([combined_query])[0], dtype=np.float32)
        self._ensure_vecs(candidate_ids)
        ce = self._ce(combined_query, candidate_ids, cfg)
        cos = {cid: self._cos(cid, qv) for cid in candidate_ids}
        alive = sum(1 for value in ce.values() if value >= cfg.cluster_ce_min) >= cfg.cluster_ce_quorum
        # CE와 bi-encoder는 실패 모드가 다르다. 전역 3개 선택에서 한 신호가 확실한 후보를
        # 다른 신호의 0점으로 죽이지 않도록 기존 합집합 게이트와 같은 max 결합을 쓴다.
        sem = {cid: max(ce.get(cid, 0.0), cos[cid]) for cid in candidate_ids} if alive else cos
        score = self._fuse(candidate_ids, sem, infer_types(query), cfg)
        ranked_candidates = sorted(
            (cid for cid in candidate_ids if not self._is_hub(cid, cfg)),
            key=lambda cid: -score[cid][1],
        )
        # spec 057: 점수절벽 게이트 — top1 과의 격차가 anchor_gap 초과면 앵커 제외.
        # 하한 1(top1 은 격차 0으로 항상 통과)·상한 anchor_top_n(기존). 상수는 골드 A/B 채택값.
        top_score = score[ranked_candidates[0]][1] if ranked_candidates else 0.0
        anchors = [
            cid for cid in ranked_candidates[:cfg.cluster_anchor_top_n]
            if score[cid][1] >= top_score - cfg.cluster_anchor_gap - 1e-9  # float 오차 허용
        ]
        if on_progress:
            on_progress("rerank", "관련도 전역 채점 완료",
                        f"통합 후보 {len(candidate_ids)}개 → 주요 앵커 {len(anchors)}개")

        result: dict[str, dict] = {}
        for cid in anchors:
            row = self._node_row(cid, cfg, qv, score, self._callees(cid), "anchor")
            row["candidate_sources"] = sorted(sources.get(cid, ()))
            row["evidence"] = []
            result[cid] = row

        if cfg.cluster_expand_hops >= 1:
            # 복합 흐름의 구현이 의미순위 4~12위에 흩어지는 것이 과거 골드에서 확인됐다 —
            # 그 재현율은 직행(direct) 자격으로 지키되, 낮은 순위 후보가 무조건 자기 1홉
            # 이웃까지 끌고 들어오던 증폭(spec 057 실측)은 정책으로 차단한다.
            cand_dists: dict[str, int] = {}
            if cfg.cluster_support_policy == "graph_gated":
                cand_dists = self._hop_distances(
                    anchors, ranked_candidates[:cfg.cluster_support_root_top_n],
                    cfg.cluster_hop_gate, cfg)
            support_roots, direct_ids = self._support_plan(
                ranked_candidates, anchors, score, cfg, cand_dists)
            raw_neighbors: dict[str, list[dict]] = {}
            evidence_links: dict[str, list[dict]] = {
                cid: [{
                    "anchor_id": "",
                    "relation": "SEMANTIC_SUPPORT",
                    "direction": "",
                    "support_root_id": cid,
                }]
                for cid in direct_ids
            }
            for support_id in support_roots:
                for neighbor in self._call_neighbors(support_id):
                    if neighbor["id"] in anchors:
                        continue
                    raw_neighbors.setdefault(neighbor["id"], []).append({
                        "anchor_id": support_id if support_id in anchors else "",
                        "relation": neighbor["relation"],
                        "direction": neighbor["direction"],
                        "support_root_id": support_id,
                    })
            self._ensure_vecs(raw_neighbors)
            kept = [cid for cid in raw_neighbors
                    if cid in candidates or self._keep_neighbor(cid, cfg, qv)]
            neighbor_sem = {cid: self._cos(cid, qv) for cid in kept}
            score.update(self._fuse(kept, neighbor_sem, infer_types(query), cfg))
            for cid, links in raw_neighbors.items():
                if cid in kept:
                    evidence_links.setdefault(cid, []).extend(links)

            evidence_ids = [cid for cid in evidence_links if cid not in anchors and cid in score]
            if cfg.cluster_support_policy == "graph_gated" and evidence_ids:
                # 최종 입장도 동일 이중신호 — 자격 뿌리의 1홉이라도 자신이 [점수 OR 거리 OR
                # 직행 자격]을 통과해야 한다(허브형 뿌리의 무관 이웃 재유입 차단).
                qualified = set(direct_ids)
                new_ids = [cid for cid in evidence_ids
                           if cid not in cand_dists and cid not in qualified]
                cand_dists.update(self._hop_distances(anchors, new_ids,
                                                      cfg.cluster_hop_gate, cfg))
                top_s = score[ranked_candidates[0]][1] if ranked_candidates else 0.0
                evidence_ids = [
                    cid for cid in evidence_ids
                    # 3홉은 의미 후보(직행 자격 판정을 거친 쪽)에만 허용 — 단순 1홉 이웃은
                    # 비허브 2홉 이내여야 한다(약신호 2개 합류 원칙).
                    if cid in qualified or cand_dists.get(cid, 99) <= 2
                    or score[cid][1] >= top_s - cfg.cluster_direct_gap - 1e-9
                ]
            evidence_ids.sort(key=lambda item: -score[item][1])
            evidence_ids = evidence_ids[:cfg.cluster_evidence_top_n]
            for cid in evidence_ids:
                row = self._node_row(cid, cfg, qv, score, self._callees(cid), "evidence")
                row["candidate_sources"] = sorted(sources.get(cid, ()))
                row["evidence"] = evidence_links[cid]
                result[cid] = row

            for anchor_id in anchors:
                for table in self._table_neighbors(anchor_id):
                    table_row = self._table_row(table, anchor_id, score[anchor_id][1])
                    existing = result.get(table_row["id"])
                    if existing is None:
                        result[table_row["id"]] = table_row
                    else:
                        existing.setdefault("evidence", []).extend(table_row["evidence"])

        if meta is not None:
            meta["selection"] = {
                "mode": "global_anchor",
                "candidates": len(candidate_ids),
                "anchors": len(anchors),
                "evidence": len(result) - len(anchors),
                "ranked_candidates": [
                    {
                        "rank": rank,
                        "id": cid,
                        "name": self.c.by_id[cid].get("name") or cid,
                        "score": round(score[cid][1], 3),
                        "sources": sorted(sources.get(cid, ())),
                    }
                    for rank, cid in enumerate(ranked_candidates, 1)
                ],
            }
        if on_progress:
            on_progress("expand", "앵커의 1단계 근거 연결 완료",
                        f"앵커 {len(anchors)}개 + 근거 {len(result) - len(anchors)}개")
        return sorted(result.values(), key=lambda node: (
            node["role"] != "anchor",
            node.get("label") == "TABLE",
            -node["relevance"],
        ))

    def _retrieve_one(self, query: str, cfg: Config, pool_k: int, on_progress,
                      *, expand: bool = True) -> list[dict]:
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
        if expand and cfg.cluster_expand_hops >= 1:
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
        from search.query.judge import judge  # lazy — LLM 경로

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
    blob = " ".join((x.get("summary") or "").lower() for x in base)
    subs = [(s.get("to") or "").strip().lower() for s in (rw.get("substitutions") or [])]
    return any(t and t not in blob for t in subs)
