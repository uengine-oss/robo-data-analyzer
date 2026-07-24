"""분기 대장 — ``control_subtree`` 줄범위 + 활성 소스를 결정론 사실로 환산한다.

게이트가 "그 규칙이 **어느 분기**에 근거하는가"를 물으려면 먼저 분기마다 (조건 코드값 ·
효과)를 알아야 한다. 이 모듈은 그것만 만들고 판정은 하지 않는다.

좌표 계약은 ``rules_fallback`` 과 같다: ``control_subtree`` 노드의 start/end 는 ``code_body``
기준 1-based 줄번호다(spec 069).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from pipeline.languages import source_lexicon
from pipeline.models.analysis_context import ControlSubtreeNode
from pipeline.quality_gates.lexing import SplitSource, atoms, literal_values, split_source

_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")


@dataclass
class Branch:
    """분기 노드 하나의 결정론 사실.

    ``cond_values`` 는 조건식의 **코드값 리터럴**만 담는다. 산출물이 보존하도록 요구받는 것이
    도메인 코드값이므로 대조축을 거기에 맞춘다 — 변수명은 산출물에서 금지된 토큰이라 대조축이
    될 수 없다.
    """

    node_id: str
    ntype: str
    start: int
    end: int
    parent: "Branch | None"
    condition: str = ""
    cond_values: frozenset[str] = frozenset()
    callees: frozenset[str] = frozenset()
    # 그 분기가 **만들어내는 것** — 본문 원자에서 조건 원자를 뺀 나머지(대입값·호출·반환 상수).
    # 언어별 대입/반환 문법을 몰라도 되게 "조건 밖에서 등장하는 것"으로 정의한다.
    effect_atoms: frozenset[str] = frozenset()
    children: list["Branch"] = field(default_factory=list)

    def ancestors(self) -> list["Branch"]:
        chain, node = [], self.parent
        while node is not None:
            chain.append(node)
            node = node.parent
        return chain


@dataclass
class BranchInventory:
    """함수 하나의 분기 대장."""

    lang: str
    roots: tuple[Branch, ...]
    all_branches: tuple[Branch, ...]
    callees: frozenset[str]
    source_values: frozenset[str]

    def dispatch_tables(self) -> list[dict[str, frozenset[str]]]:
        """SWITCH 마다 ``라벨 코드값 → 효과(호출 집합)`` 대응.

        본문 없는 CASE 는 언어 표(``case_fallthrough``)가 허용할 때만 다음 CASE 의 효과를
        물려받는다. 관습을 코드가 아니라 데이터로 받는다.
        """
        fallthrough = bool(source_lexicon(self.lang).get("case_fallthrough"))
        tables: list[dict[str, frozenset[str]]] = []
        for switch in self.all_branches:
            cases = [child for child in switch.children if child.ntype == "CASE"]
            if len(cases) < 2:
                continue
            effects: dict[str, frozenset[str]] = {}
            pending: list[Branch] = []
            for case in cases:
                pending.append(case)
                if case.callees or not fallthrough:
                    for waiting in pending:
                        for label in waiting.cond_values:
                            effects[label] = case.callees
                    pending = []
            for waiting in pending:
                for label in waiting.cond_values:
                    effects[label] = waiting.callees
            if effects:
                tables.append(effects)
        return tables

    def at_line(self, line: int) -> "Branch | None":
        """그 줄에서 시작하는 분기. 없으면 그 줄을 품는 가장 안쪽 분기."""
        starting = [b for b in self.all_branches if b.start == line]
        if starting:
            return starting[0]
        covering = [b for b in self.all_branches if b.start <= line <= b.end]
        return min(covering, key=lambda b: b.end - b.start) if covering else None

    def reachable_effects(self, branch: Branch) -> frozenset[str]:
        """그 분기와 그 안쪽 분기들이 만들어내는 것 전부."""
        found = set(branch.effect_atoms)
        for child in branch.children:
            found |= self.reachable_effects(child)
        return frozenset(found)

    def branch_index(self) -> str:
        """LLM 에게 보여줄 분기 목록 — ``줄번호: 조건식``. 고를 수 있는 후보를 못 박는다."""
        return "\n".join(
            f"{b.start}: {b.ntype} {' '.join(b.condition.split())[:110]}"
            for b in sorted(self.all_branches, key=lambda x: x.start)
        ) or "(분기 없음)"

    def best_match(self, values: set[str]) -> tuple[Branch | None, set[str]]:
        """항목이 근거로 삼은 분기 = 조건 코드값이 가장 많이 겹치는 분기."""
        best, best_hit = None, set()
        for branch in self.all_branches:
            hit = values & branch.cond_values
            if len(hit) > len(best_hit):
                best, best_hit = branch, hit
        return best, best_hit


def condition_text(body: str, ntype: str, lang: str) -> str:
    """분기의 조건식 텍스트.

    괄호 조건(``if (...)``)은 **여는 괄호부터 짝이 맞을 때까지** 읽는다. 줄 수로 자르면 여러
    줄에 걸친 코드값 열거(``a=="11" || a=="12" || ...``)가 잘려 뒤쪽 값이 통째로 "근거 없음"
    으로 오판된다 — 이 게이트에서 가장 비싼 오탐이라 좌표가 아니라 문법으로 끊는다.

    CASE 라벨의 끝 토큰과 예약어 목록은 언어 표에서 받는다(코드에 언어 지식을 두지 않는다).
    """
    if ntype == "CASE":
        head = body.split("\n", 1)[0]
        ends = [
            head.lower().find(token)
            for token in (source_lexicon(lang).get("case_label_end") or [])
            if head.lower().find(token) >= 0
        ]
        return head[: min(ends)] if ends else head
    # 여는 괄호는 **분기 키워드가 있는 첫 줄**에서만 찾는다. 본문 전체에서 찾으면 뒤쪽
    # 호출문의 괄호를 조건식으로 오인한다 — 괄호 없는 문법(python/sql)이 통째로 무력화된다.
    first_line = body.split("\n", 1)[0]
    open_paren = first_line.find("(")
    brace = first_line.find("{")
    if open_paren < 0 or (0 <= brace < open_paren):
        return first_line
    depth = 0
    for index in range(open_paren, len(body)):
        if body[index] == "(":
            depth += 1
        elif body[index] == ")":
            depth -= 1
            if depth == 0:
                return body[open_paren : index + 1]
    return body[open_paren:]


def _calls(text: str, lang: str) -> frozenset[str]:
    """``이름(`` 중 실제 호출만. 예약어 목록은 언어 표(데이터)에서 온다."""
    keywords = {k.lower() for k in (source_lexicon(lang).get("control_keywords") or [])}
    return frozenset(
        m.group(1).lower() for m in _CALL.finditer(text) if m.group(1).lower() not in keywords
    )


def build_inventory(
    code_body: str,
    control_subtree: Sequence[ControlSubtreeNode] | None,
    lang: str,
    split: SplitSource | None = None,
) -> BranchInventory:
    """분기 대장을 만든다. ``control_subtree`` 가 없으면 분기 0으로 본다."""
    split = split or split_source(code_body, lang)
    all_branches: list[Branch] = []

    def convert(node: ControlSubtreeNode, parent: Branch | None) -> Branch:
        body = split.live(node.start, node.end)
        condition = condition_text(body, node.ntype, lang)
        branch = Branch(
            node_id=node.node_id,
            ntype=node.ntype,
            start=node.start,
            end=node.end,
            parent=parent,
            condition=condition,
            cond_values=frozenset(literal_values(condition, lang)),
            callees=_calls(body, lang) - _calls(condition, lang),
            effect_atoms=frozenset(atoms(body, lang)) - frozenset(atoms(condition, lang)),
        )
        all_branches.append(branch)
        branch.children = [convert(child, branch) for child in node.children]
        return branch

    roots = tuple(convert(node, None) for node in (control_subtree or ()))
    return BranchInventory(
        lang=lang,
        roots=roots,
        all_branches=tuple(all_branches),
        callees=_calls(split.live(), lang),
        source_values=frozenset(literal_values(split.live(), lang)),
    )
