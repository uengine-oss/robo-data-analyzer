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

CHECKS = ("value_set", "anchor_values", "anchor_effect")


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


def _check_anchor(
    rule_index: int, rule, inventory: BranchInventory, anchor: frozenset[str]
) -> list[GroundingFinding]:
    """규칙이 **스스로 지목한 분기**에 대고 검증한다 — 추정이 아니라 확정 대조다.

    ``best_match`` 추정은 조건값이 겹치는 분기를 고르므로, 규칙이 애초에 엉뚱한 분기를
    말하고 있으면 그 오류를 볼 수 없다(추정이 오류를 따라간다). 규칙이 직접 줄을 대면
    "네가 말한 그 분기에 그 값·그 결과가 있느냐"를 물을 수 있다.
    """
    line = getattr(rule, "anchor_line", 0) or 0
    if line <= 0:
        return []
    branch = inventory.at_line(line)
    if branch is None:
        # 무조건 변환·위임·I/O·반환은 분기가 아니라 그 효과를 수행하는 활성 SOURCE 줄을
        # 가리킨다. 의미를 추론하지 않고 실제 활성 좌표인지까지만 검증한다.
        if line in inventory.active_lines:
            return []
        return [
            GroundingFinding(
                "anchor_values", f"R{rule_index}", (str(line),),
                f"지목한 줄 {line} 에 활성 소스 근거가 없다",
            )
        ]

    findings: list[GroundingFinding] = []
    scope = f"R{rule_index}"

    known = set(branch.cond_values)
    for ancestor in branch.ancestors():
        known |= ancestor.cond_values
    for child in branch.children:
        known |= child.cond_values
    # ``else``/``default`` 는 형제 조건의 부정으로 정의된다 — 형제의 조건값은 정당한 어휘다.
    # (실측 오탐: `default:` 에 앵커한 "11,12,13,14 가 아니면" 규칙이 전부 위반으로 잡혔다)
    for sibling in inventory.siblings(branch):
        known |= sibling.cond_values

    # statement 는 "<조건>이면 <결과>" 구조라 **결과값도 섞여 있다**. 결과값을 조건값 대조에
    # 넣으면 정상 규칙이 무더기로 걸린다(실측 오탐 최대 원인: `gubun='1' 로 조회`·`납부주기를
    # '4' 로 변경`). 조건절을 산문으로 가르는 대신, **예시의 then 에 나온 값은 결과로 보고
    # 조건 대조에서 뺀다** — 필드 구조로 판정하므로 산문 언어를 몰라도 된다.
    produced_by_rule: set[str] = set()
    for example in rule.examples:
        produced_by_rule |= code_values(example.then or "", anchor)
    claimed = code_values(rule.statement or "", anchor) - produced_by_rule
    # 소스 어디에도 없는데 지목 분기 조건값과 **같은 모양**인 값 = 그 조건 자리에 지어낸 값.
    # (실측: 코드가 `x=="Y"` 인데 규칙이 "'N' 이면" 이라 쓴 매핑 반전. 'N' 은 소스에 없어
    #  앵커링만으로는 코드값으로 뽑히지 않으므로 이 경로가 없으면 통째로 놓친다.)
    shapes = {_shape(value) for value in branch.cond_values}
    fabricated = fabricated_values(rule.statement or "", anchor, shapes) - produced_by_rule
    missing = tuple(sorted((claimed - known) | fabricated))
    if missing:
        findings.append(
            GroundingFinding(
                "anchor_values", scope, missing,
                f"지목 분기({branch.ntype}@{branch.start})의 조건에 없는 코드값 "
                f"(그 분기의 값 {sorted(branch.cond_values)})",
            )
        )

    effects = set(inventory.reachable_effects(branch))
    for ancestor in branch.ancestors():
        effects |= ancestor.effect_atoms
    # 예시가 여럿인 규칙은 **여러 경로를 함께 서술**한다(`A면 정상, 아니면 거절`). 그때는
    # 형제 분기의 산물도 정당하다. 예시가 하나면 한 경로만 말하는 것이므로 그 분기가 실제로
    # 만드는 결과여야 한다 — 이 구분이 없으면 형제 결과를 갖다 쓴 반전 결함을 놓친다.
    if len(rule.examples) > 1:
        for sibling in inventory.siblings(branch):
            effects |= inventory.reachable_effects(sibling)
    absent = tuple(sorted(v for v in produced_by_rule - effects if v in anchor))
    if absent:
        findings.append(
            GroundingFinding(
                "anchor_effect", scope, absent,
                f"지목 분기({branch.ntype}@{branch.start})가 만들지 않는 결과값",
            )
        )
    return findings


def check_grounding(
    rules_out: RulesExamplesOutput | None, inventory: BranchInventory
) -> list[GroundingFinding]:
    """규칙·사례의 근거성 위반을 모은다. 산출물은 바꾸지 않는다."""
    if rules_out is None or not rules_out.rules:
        return []
    anchor = inventory.source_values
    findings: list[GroundingFinding] = []
    for scope, condition in _conditions(rules_out):
        findings.extend(_check_value_set(scope, condition, inventory, anchor))
    for rule_index, rule in enumerate(rules_out.rules):
        findings.extend(_check_anchor(rule_index, rule, inventory, anchor))
    return findings
