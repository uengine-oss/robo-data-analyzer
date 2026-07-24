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

_QUOTED = re.compile(r"['\"‘’“”]([^'\"‘’“”\n]{1,32})['\"‘’“”]")
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
    """산출물의 **도메인 코드값 리터럴** — 후보 전부를 그 함수의 소스 리터럴에 자가 앵커링한다.

    앵커링이 언어·산문 무관성을 만든다. 앵커 없이 "따옴표 안이면 코드값"으로 받으면 산출물
    산문의 인용구(``'간편결제'``·``'refund request'``)를 코드값으로 오인하고, 무인용 수치를
    받으면 "12자리" 같은 산문 수치가 섞인다. 그래서 **그 run 의 소스에 실제로 존재하는 값만**
    코드값으로 인정한다 — 어떤 자연어 목록도, 어떤 언어 지식도 필요 없다.

    (종전 구현은 "한글이 섞이면 코드값 아님" 규칙을 코드에 박고 있었다. 산출물이 한국어라는
    전제라서 다른 프로젝트·다른 산문 언어에서 그대로 깨진다.)
    """
    return {v for v in _candidates(text) if v in anchor}


def _candidates(text: str) -> set[str]:
    """인용된 값과 수치 — 앵커 판정 **전**의 원시 후보."""
    values = {m.group(1).strip().lower() for m in _QUOTED.finditer(text or "")}
    values |= {m.group(0) for m in _NUMERIC.finditer(text or "")}
    return {v for v in values if v}


def fabricated_values(text: str, anchor: frozenset[str], shapes: set[str]) -> set[str]:
    """소스 어디에도 없는데 **앵커된 값들과 같은 모양**인 후보 = 지어낸 코드값.

    모양 동질성이 언어 지식을 대신한다. 규칙이 ``'11','13','21'`` 을 열거하는데 ``'12'`` 가
    소스에 없으면, 그 값은 같은 집합의 일원 행세를 하는 날조다. 반대로 산문 인용구
    (``'간편결제'``·``'refund request'``)는 그 모양 집합에 속하지 않아 걸러진다.
    """
    return {v for v in _candidates(text) - anchor if _shape(v) in shapes}


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
        # 근거 분기 밖의 값 두 종류: 함수 어딘가엔 있으나 이 분기엔 없는 값 + 소스 어디에도 없는 값
        foreign = tuple(sorted(
            {v for v in values - known if _shape(v) in shapes}
            | fabricated_values(condition, inventory.source_values, shapes)
        ))
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
    anchor = inventory.source_values
    findings: list[GroundingFinding] = []
    for scope, condition in _conditions(rules_out):
        findings.extend(_check_value_set(scope, condition, inventory, anchor))
    return findings
