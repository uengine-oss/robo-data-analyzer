"""근거성 게이트 — 규칙·사례가 **어느 분기에** 근거하는지 결정론으로 판정한다 (spec 101).

측정 전용이다. 어떤 산출물도 바꾸지 않는다.

왜 "소스에 있나"가 아니라 "그 분기에 있나"인가: 프롬프트가 given/when/then 에 변수·메서드
토큰을 금지하고 도메인 코드값 리터럴만 남기므로, 단순 심볼 역매칭이 잡는 결함은 주석 유래
소수뿐이다(spec 101 §왜). 감사 29건의 지배 유형은 값집합 병합·날조와 분기 귀속 오류다.

**활성화한 검사는 두 종뿐이다.** gold set 실측에서 오탐이 낮은 것만 남겼다(spec 101 §경계면):
검사 7종을 다 켜면 재현 22/29 지만 **결함 0 함수의 항목 48% 를 플래그**해 제품에서 쓸 수 없다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pipeline.models.code_analysis_output import RulesExamplesOutput
from pipeline.quality_gates.branch_inventory import BranchInventory

_HANGUL = re.compile(r"[가-힣]")
_QUOTED = re.compile(r"['\"‘’“”]([^'\"‘’“”\n]{1,16})['\"‘’“”]")
_NUMERIC = re.compile(r"\d+")

CHECKS = ("delegation", "value_set")


@dataclass(frozen=True)
class GroundingFinding:
    """근거성 위반 1건 — 사람이 원문과 대조할 수 있게 좌표와 값을 함께 남긴다."""

    check: str
    scope: str          # "R3" (rule) / "R3E1" (example)
    values: tuple[str, ...]
    detail: str

    def as_dict(self) -> dict:
        return {
            "check": self.check,
            "scope": self.scope,
            "values": list(self.values),
            "detail": self.detail,
        }


def code_values(text: str, anchor: frozenset[str] = frozenset()) -> set[str]:
    """산출물의 **도메인 코드값 리터럴**.

    인용된 값 + *그 함수의 분기 조건에 실재하는* 무인용 수치. 무인용 수치를 무조건 받으면
    "3자리"·"12자리" 같은 산문 수치가 섞이므로, 후보를 그 run 의 AST 조건 리터럴로 자가
    앵커링한다(외부 어휘표 없음 — 범용성 계약).
    """
    values: set[str] = set()
    for match in _QUOTED.finditer(text or ""):
        value = match.group(1).strip()
        if value and not _HANGUL.search(value):
            values.add(value.lower())
    if anchor:
        values |= {m.group(0) for m in _NUMERIC.finditer(text or "") if m.group(0) in anchor}
    return values


def _shape(value: str) -> str:
    """값의 모양 서명 — 열거 집합의 동질성 판정용(그 run 의 값에서 도출)."""
    return f"d{len(value)}" if value.isdigit() else f"s{len(value)}"


def _conditions(rules_out: RulesExamplesOutput) -> list[tuple[str, str]]:
    """(scope, 조건절 텍스트) — 규칙 statement 와 사례 given/when."""
    items: list[tuple[str, str]] = []
    for rule_index, rule in enumerate(rules_out.rules):
        items.append((f"R{rule_index}", rule.statement or ""))
        for example_index, example in enumerate(rule.examples):
            items.append(
                (
                    f"R{rule_index}E{example_index}",
                    f"{example.given or ''} {example.when or ''}",
                )
            )
    return items


def _check_delegation(
    rules_out: RulesExamplesOutput, inventory: BranchInventory
) -> list[GroundingFinding]:
    """분기 0 + 호출뿐인 위임 함수는 ``rules:[]`` 가 정답이다(프롬프트 §5)."""
    if inventory.all_branches or not inventory.callees or not rules_out.rules:
        return []
    return [
        GroundingFinding(
            "delegation",
            f"R{index}",
            (),
            f"분기 노드 0 · 호출 {len(inventory.callees)}개인 위임 함수인데 규칙을 만들었다",
        )
        for index in range(len(rules_out.rules))
    ]


def _check_value_set(
    scope: str, condition: str, inventory: BranchInventory, anchor: frozenset[str]
) -> list[GroundingFinding]:
    """열거한 코드값이 (a) 근거 분기의 조건에 없거나 (b) 다른 효과의 라벨을 병합했는가."""
    values = code_values(condition, anchor)
    if len(values) < 2:
        return []
    findings: list[GroundingFinding] = []

    for table in inventory.dispatch_tables():
        hit = values & set(table)
        if len(hit) < 2 or len(hit) >= len(table):
            # 라벨을 **전부** 나열한 항목은 병합이 아니라 그 분기 전체(또는 그 여집합)를
            # 서술한 것이다 — 실측 오탐 원인이었다("길이가 10,11,12 가 아니면 오류").
            continue
        effects = {table[label] for label in hit}
        if len(effects) > 1:
            findings.append(
                GroundingFinding(
                    "value_set",
                    scope,
                    tuple(sorted(hit)),
                    f"한 항목이 서로 다른 {len(effects)}개 효과로 가는 라벨을 병합했다 "
                    f"(효과: {sorted(sorted(effect) for effect in effects)})",
                )
            )

    branch, overlap = inventory.best_match(values)
    if branch is not None and len(overlap) >= 2:
        known = set(branch.cond_values)
        for ancestor in branch.ancestors():
            known |= ancestor.cond_values
        for child in branch.children:
            known |= child.cond_values
        shapes = {_shape(value) for value in overlap}
        foreign = tuple(sorted(v for v in values - known if _shape(v) in shapes))
        if foreign:
            findings.append(
                GroundingFinding(
                    "value_set",
                    scope,
                    foreign,
                    f"열거한 코드값이 근거 분기({branch.ntype}@{branch.start})의 조건에 없다 "
                    f"(그 분기의 값 {sorted(overlap)})",
                )
            )
    return findings


def check_grounding(
    rules_out: RulesExamplesOutput | None, inventory: BranchInventory
) -> list[GroundingFinding]:
    """규칙·사례의 근거성 위반을 모은다. 산출물은 바꾸지 않는다."""
    if rules_out is None or not rules_out.rules:
        return []
    delegation = _check_delegation(rules_out, inventory)
    if delegation:
        return delegation
    anchor = frozenset().union(*(b.cond_values for b in inventory.all_branches)) \
        if inventory.all_branches else frozenset()
    findings: list[GroundingFinding] = []
    for scope, condition in _conditions(rules_out):
        findings.extend(_check_value_set(scope, condition, inventory, anchor))
    return findings
