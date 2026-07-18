"""Policy for selecting expansion roots and direct retrieval candidates."""
from __future__ import annotations

from search.config import Config


def support_plan(
    ranked: list[str],
    anchors: list[str],
    score: dict,
    cfg: Config,
    dists: dict[str, int] | None = None,
) -> tuple[list[str], list[str]]:
    policy = cfg.cluster_support_policy
    if policy == "all_candidates":
        roots = ranked[:cfg.cluster_support_root_top_n]
        return roots, [code_id for code_id in roots if code_id not in anchors]
    if policy == "anchors_only":
        return list(anchors), []
    if policy in ("gated_direct", "graph_gated"):
        top = score[ranked[0]][1] if ranked else 0.0
        near = dists or {}
        direct: list[str] = []
        for index, code_id in enumerate(ranked[:cfg.cluster_support_root_top_n]):
            if code_id in anchors:
                continue
            by_score = score[code_id][1] >= top - cfg.cluster_direct_gap - 1e-9
            by_distance = policy == "graph_gated" and code_id in near
            by_rank = policy == "graph_gated" and index < cfg.cluster_rank_floor
            if by_score or by_distance or by_rank:
                direct.append(code_id)
        roots = list(anchors) + (
            [code_id for code_id in direct if code_id in near]
            if policy == "graph_gated"
            else []
        )
        return roots, direct
    raise ValueError(f"unknown cluster_support_policy: {policy!r}")
