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
        """결정론 구조 마이너가 samples 전체에서 합성한 후보 규칙 목록.

        **언제 사용**: 관찰 직후 — 규칙을 직접 발명하기 전에 반드시 확인한다.
        결과에 next_offset 이 있으면 그 offset 으로 다음 페이지를 계속 읽을 수 있다.

        **결과 읽는 법**: 각 후보의 pattern 은 이미 samples 에서 실측 검증된
        것이다. `verify_external_rule` 로 재확인한 뒤 맞는 후보를 그대로(또는
        최소 수정으로) 제출하라. 큰 단위(block/section)와 줄 단위(line_record)가
        같이 있으면 의미상 올바른 단위를 골라라. **'접두 일반화 변형'은 base 의
        대안이다 — 같은 군집에서 둘을 동시에 채택하면 span 이 겹쳐 실격되니
        반드시 하나만 고른다** (샘플이 적어 접두가 상수인지 불확실할 땐 변형이
        보통 안전하다). **key 표본이 숫자뿐이면 거의 항상 잘못이다** — 코드는
        접두를 포함한 전체 식별자로 참조하므로, 인접 상수 접두(문자 run)를
        key 에 포함한 변형을 채택하라. 후보가 비었을 때만 직접 설계한다.
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
            f"PAGE offset={offset} returned={end - offset} total={len(cached)} "
            f"next_offset={next_offset if next_offset is not None else 'null'} "
            "(전부 samples 실측 검증 통과)"
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
