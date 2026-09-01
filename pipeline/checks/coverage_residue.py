"""커버리지 잔차 감사 — "결정론 사실이 딛지 않는 live 소스 줄"의 표면화 (골키퍼 그물2).

파서가 emit 하지 않고 분석기도 모르는 구문은 어떤 그물에도 안 걸린다 — 이 감사가
그 부재 자체를 잡는다(실측: `PFM_CATCH:` 라벨 67행이 어떤 사실에도 안 덮인 채
소실됐던 사고 계열). 언어·구문 이름을 모르는 부재 감지라 새 언어에도 코드 불변.

- 덮인 줄 = 분기 범위 ∪ 종료/handler 행 ∪ entry 행 ∪ statement 좌표 ∪ 호출 좌표
- 잔차 = live(주석·비활성 제외) 비공백 줄 − 덮인 줄 − 구두점 전용 줄 − 루틴 머리
- 잔차는 shape(식별자→I, 수→N 정규화)로 묶여 미지 구문이 무리로 드러난다.
- ontology ``unsupported_constructs``(값 추적 금지 구문)는 같은 감사가 장부로 센다.
"""
from __future__ import annotations

import re

from pipeline.source_languages import unsupported_construct_patterns
from pipeline.checks.branch_inventory import BranchInventory

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"\b\d+\b")
_PUNCT_ONLY = re.compile(r"^[\s{}();,]*$")


def _shape(live_line: str) -> str:
    """잔차 줄의 구문 모양 서명 — 같은 미지 구문이 무리로 보이게 한다."""
    normalized = _IDENT.sub("I", live_line.strip())
    normalized = _NUMBER.sub("N", normalized)
    return " ".join(normalized.split())


def coverage_residue(inventory: BranchInventory) -> dict:
    """함수 하나의 잔차 보고 — 순수 함수(그래프·파일 접근 없음)."""
    lines = inventory.source_lines
    total = len(lines)
    covered: set[int] = set()
    for branch in inventory.all_branches:
        covered.update(range(branch.start, branch.end + 1))
    covered.update(line for line, _, _ in inventory.termination_rows)
    if inventory.entry_line is not None:
        covered.add(inventory.entry_line)
    for fact in inventory.ast_statements:
        covered.update(range(fact.start, fact.end + 1))
    covered.update(line for line, _ in inventory.effect_call_positions)

    # 루틴 머리(시그니처)는 사실이 아니라 정의부다 — 첫 여는 중괄호 줄까지 머리로 본다.
    header_end = 1
    for index, line in enumerate(lines, start=1):
        header_end = index
        if "{" in line:
            break
        if index >= total:
            break

    residue = []
    live_count = 0
    for index, line in enumerate(lines, start=1):
        text = line.strip()
        if not text:
            continue
        live_count += 1
        if index in covered or index <= header_end:
            continue
        if _PUNCT_ONLY.match(text):
            continue
        residue.append({
            "line": index + inventory.line_offset,
            "text": text[:80],
            "shape": _shape(line),
        })

    unsupported = []
    for kind, pattern in unsupported_construct_patterns(inventory.lang).items():
        matcher = re.compile(pattern)
        for index, line in enumerate(lines, start=1):
            if matcher.search(line):
                unsupported.append({
                    "kind": kind,
                    "line": index + inventory.line_offset,
                    "text": line.strip()[:80],
                })

    return {
        "live": live_count,
        "covered": live_count - len(residue),
        "ratio": (live_count - len(residue)) / live_count if live_count else 1.0,
        "residue": residue,
        "unsupported": sorted(unsupported, key=lambda u: u["line"]),
        "unsupported_total": len(unsupported),
    }
