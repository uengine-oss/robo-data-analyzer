"""External Format Agent 의 결정론 후보 도구.

``list_structure_candidates`` — 구조 마이너(deep-research-2, Drain 원리 자체 구현)가
합성한 후보 규칙 목록을 돌려준다. 에이전트의 1차 책임은 규칙 발명이 아니라
**이 후보들의 검토·채택·명명**이다 (후보가 없거나 전부 부적합할 때만 직접 설계).
"""
from __future__ import annotations

from langchain.tools import tool

from pipeline.steps.step4_scan_project.structure_miner import mine_rule_candidates


_CANDIDATE_PAGE_SIZE = 8


def make_structure_candidate_tool(samples: list[str]):
    """samples 를 묶은 후보 조회 도구를 만든다 (마이닝은 1회 수행 후 캐시)."""
    cached: list | None = None

    @tool
    def list_structure_candidates(offset: int = 0) -> str:
        """결정론 마이너가 모든 sample에서 합성한 구조 후보를 page로 반환한다.

        각 후보는 index, kind, pattern, 근거 수, key 표본을 가진다. 후보는 제안일
        뿐이므로 채택할 pattern은 모두 `verify_external_rule`로 검증한다.
        `next_offset`이 null이 될 때까지 이어 읽는다.
        """
        nonlocal cached
        if cached is None:
            cached = mine_rule_candidates(samples)
        if not cached:
            return "후보 없음 — 반복 구조를 결정론으로 찾지 못했다. 직접 관찰·설계하라."
        if offset < 0:
            return "INVALID_OFFSET: offset must be non-negative"
        end = min(offset + _CANDIDATE_PAGE_SIZE, len(cached))
        next_offset = end if end < len(cached) else None
        lines = [
            f"PAGE total={len(cached)} "
            f"next_offset={next_offset if next_offset is not None else 'null'}"
        ]
        for index in range(offset, end):
            candidate = cached[index]
            keys = ", ".join(candidate.sample_keys[:4])
            lines.append(
                f"[{index}] kind={candidate.kind} · 근거 {candidate.evidence_lines}건 · {candidate.note}\n"
                f"    pattern: {candidate.pattern}\n"
                f"    key 표본: {keys}"
            )
        return "\n".join(lines)

    return list_structure_candidates
