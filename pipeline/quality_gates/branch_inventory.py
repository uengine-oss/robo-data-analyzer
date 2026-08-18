"""분기 대장 — ``control_subtree`` 줄범위 + 활성 소스를 결정론 사실로 환산한다.

게이트가 "그 규칙이 **어느 분기**에 근거하는가"를 물으려면 먼저 분기마다 (조건 코드값 ·
효과)를 알아야 한다. 이 모듈은 그것만 만들고 판정은 하지 않는다.

좌표 계약: ``control_subtree`` 노드의 start/end 는 ``code_body``
기준 1-based 줄번호다(spec 069).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from pipeline.languages import (
    alternative_clause_types,
    complement_owner_types,
    control_role_of,
    source_lexicon,
    statement_effect_ops,
)
from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.quality_gates.lexing import (
    SplitSource,
    atoms,
    literal_values,
    split_source,
)

_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_OCTAL = re.compile(r"^0[0-7]+$")
_HEX = re.compile(r"^0[xX][0-9A-Fa-f]+$")


@dataclass(frozen=True)
class StaticJumpResolution:
    """One parser-proven named jump and its unique target coordinate."""

    goto_line: int
    target: str
    label_line: int


def _active_statement_facts(
    code_body: str,
    lang: str,
    statements: Sequence[StatementFact],
    split: SplitSource | None = None,
) -> tuple[StatementFact, ...]:
    """Apply the same inactive-source boundary to every structural statement."""
    split = split or split_source(code_body, lang)

    def live(line: int) -> bool:
        index = line - 1
        return 0 <= index < len(split.live_lines) and bool(split.live_lines[index].strip())

    return tuple(fact for fact in statements if live(fact.start))


def validate_static_jump_targets(
    code_body: str,
    lang: str,
    statements: Sequence[StatementFact],
    *,
    split: SplitSource | None = None,
    line_offset: int = 0,
    target_id: str = "",
    source_ref: str = "",
) -> tuple[StaticJumpResolution, ...]:
    """Require every active named GOTO to resolve to one active parser LABEL.

    The parser owns both facts.  Recovering a missing label from source text would create a
    second structural producer and hide stale AST, so missing, blank, and duplicate targets
    are deterministic contract failures before any semantic model call.
    """
    active = _active_statement_facts(code_body, lang, statements, split)
    labels: dict[str, list[StatementFact]] = {}
    for fact in active:
        if fact.label == "LABEL" and fact.target:
            labels.setdefault(str(fact.target), []).append(fact)
    resolutions: list[StaticJumpResolution] = []
    defects: list[str] = []
    for fact in active:
        if fact.label != "GOTO":
            continue
        target = str(fact.target or "").strip()
        matches = labels.get(target, ()) if target else ()
        absolute_goto = fact.start + line_offset
        if not target or len(matches) != 1:
            defects.append(
                f"GOTO@{absolute_goto} target={target!r} LABEL count={len(matches)}"
            )
            continue
        resolutions.append(StaticJumpResolution(
            goto_line=absolute_goto,
            target=target,
            label_line=matches[0].start + line_offset,
        ))
    if defects:
        scope_parts = []
        if target_id:
            scope_parts.append(f"target={target_id}")
        if source_ref:
            scope_parts.append(f"source={source_ref}")
        scope = (" " + " ".join(scope_parts)) if scope_parts else ""
        raise ValueError(
            "static jump target contract failed:"
            f"{scope}; " + "; ".join(defects)
        )
    return tuple(resolutions)


def canonical_numeric(value: str) -> str | None:
    """C 계열 0 접두 정수의 십진 등가 — 다르면 십진 문자열, 아니면 None.

    `case 02` 를 모델이 `== 2` 로 옮기는 것은 수치 등가로 옳다(py1 실측 R-550).
    정답지가 등가값을 병기해야 채점이 이 표기 차이를 오답으로 만들지 않는다.
    """
    if _OCTAL.match(value):
        return str(int(value, 8))
    if _HEX.match(value):
        return str(int(value, 16))
    return None


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
    condition_timing: str = ""
    cond_values: frozenset[str] = frozenset()
    callees: frozenset[str] = frozenset()
    # 그 분기가 **만들어내는 것** — 본문에 등장하는 원자 전부(대입값·호출·반환 상수).
    # 조건 원자를 빼면 안 된다: `if(x=="Y") { f="Y"; }` 처럼 조건값이 결과값이기도 하면
    # 그 값이 통째로 사라져 "만들지 않는 결과"로 오판한다(실측 오탐 원인).
    effect_atoms: frozenset[str] = frozenset()
    direct_source: str = ""
    return_expressions: frozenset[str] = frozenset()
    assignment_pairs: tuple[tuple[str, str], ...] = ()
    # ontology statement_effect_ops 가 선언한 statement 효과 (op, 대상) — 예: GOTO 점프.
    statement_effects: tuple[tuple[str, str], ...] = ()
    direct_evidence_lines: tuple[int, ...] = ()
    direct_terminates: bool = False
    sequence_guard_lines: tuple[int, ...] = ()
    children: list["Branch"] = field(default_factory=list)

    def ancestors(self) -> list["Branch"]:
        chain, node = [], self.parent
        while node is not None:
            chain.append(node)
            node = node.parent
        return chain

    def prompt_role(self) -> str:
        """Return controller/decision/complement from AST role and source spelling.

        Condition length and atom count are deliberately irrelevant.

        ``control_roles`` 는 **의미 축**만 선언한다(decision = 조건으로 갈리는 자리).
        조건이 실제로 있는지는 데이터가 아니라 노드의 사실이므로, 조건이 빈 decision 은
        약속할 조건이 없어 ``controller`` 로 내린다 (spec 118 §2.1). 언어별 특례가 아니라
        규칙 하나다 — 같은 결함이 sql ``LOOP``·c ``for(;;)``·java ``FINALLY`` 에 함께 있었다
        (실측: DBMS findings 5 + framework findings 1 이 전부 이 사유였다).
        """
        role = control_role_of(self.ntype)
        if role != "decision":
            return role
        # grammar 사실: caseless 라벨(default)만 expression 이 없다 (spec 016).
        if self.ntype.upper() == "CASE" and not self.condition.strip():
            return "complement"
        if not self.condition.strip():
            return "controller"
        return "decision"


@dataclass(frozen=True)
class EffectRegion:
    """One linear direct-effect region between two sibling controls.

    A routine/branch row may own only its first linear region.  Every later region is
    anchored separately so effects from different execution times can never be folded
    into one entry/controller Rule.
    """

    start: int
    end: int
    source: str
    return_expressions: frozenset[str]
    assignment_pairs: tuple[tuple[str, str], ...]
    callees: frozenset[str]
    statement_effects: tuple[tuple[str, str], ...]
    evidence_lines: tuple[int, ...]
    path_anchor: int | None
    after_control: str
    at_label: str = ""
    fallthrough_false_of: int | None = None
    sequence_guard_lines: tuple[int, ...] = ()
    terminates: bool = False
    unresolved_reachability: bool = False


@dataclass(frozen=True)
class _LinearRegion:
    """A raw sequence range, optionally entered at an exact parser LABEL."""

    start: int
    end: int
    preceding: Branch | None
    entry_label: StatementFact | None = None


@dataclass
class BranchInventory:
    """함수 하나의 분기 대장."""

    lang: str
    roots: tuple[Branch, ...]
    all_branches: tuple[Branch, ...]
    callees: frozenset[str]
    source_values: frozenset[str]
    active_lines: frozenset[int]
    # 함수 밖 선언값의 원문 표기. ``source_values`` 는 과거 비교 계약 때문에 정규화된
    # 값도 담지만, 창작 검사는 대소문자와 전체 토큰을 보존한 이 집합을 사용한다.
    declared_values: frozenset[str] = frozenset()
    line_offset: int = 0
    # 종료문·핸들러 행 (상대 줄, "exit"|"handler", 한 줄 문장) — spec 116 1단계.
    # 분기 노드가 아니라 문장이지만, 종료 계약(default 반환·조기 return·오류 경로)이
    # 행으로 실체화돼야 절단·누락을 뺄셈으로 잡을 수 있다.
    termination_rows: tuple[tuple[int, str, str], ...] = ()
    # 소스에 직접 쓰인 반환 표현식 집합. 변수의 현재값을 해석하거나 객체 상태를
    # 추적하지 않는다. caller에 주는 callee 결과 힌트의 유일한 원천이다.
    return_expressions: frozenset[str] = frozenset()
    # [entry] 행 (상대 줄) — 분기 밖 최상위 **효과문**(대입·종결된 호출)의 첫 줄.
    # when=함수 실행이므로 "도달하면 항상"도 계약(cond=null 규칙 1개 의무)이다.
    # 선언·시그니처뿐이면 약속할 효과가 없으므로 None (spec 116).
    entry_line: "int | None" = None
    source_lines: tuple[str, ...] = ()
    # parser AST statement 계약 (spec 016) — 행별 직접 증거의 유일한 원천.
    ast_statements: tuple[StatementFact, ...] = ()
    # DBMS DML 효과 좌표 (routine 상대 줄, op, 테이블) — step5 링커 정본 (spec 119).
    # framework 는 제공하지 않으므로 빈 튜플이고 증거가 달라지지 않는다.
    ast_table_ops: tuple[tuple[int, str, str], ...] = ()
    ast_call_lines: tuple[tuple[int, str], ...] = ()
    entry_region: EffectRegion | None = None
    continuation_regions: tuple[EffectRegion, ...] = ()

    def continuation_at(self, absolute_line: int) -> EffectRegion | None:
        relative = absolute_line - self.line_offset
        return next(
            (region for region in self.continuation_regions if region.start == relative),
            None,
        )

    def _entry_direct_source(self) -> str:
        """Return only the first pre-control linear region owned by entry."""
        return self.entry_region.source if self.entry_region is not None else ""

    def direct_evidence(
        self, absolute_line: int,
    ) -> tuple[str, frozenset[str], tuple[tuple[str, str], ...]]:
        """행별 직접 source·return·대입 증거를 prompt 밖에서 제공한다."""
        relative = absolute_line - self.line_offset
        branch = self.at_line(absolute_line)
        if branch is not None and branch.start == relative:
            return branch.direct_source, branch.return_expressions, branch.assignment_pairs
        if self.entry_line is not None and relative == self.entry_line:
            region = self.entry_region
            if region is None:
                raise ValueError(f"entry region missing at line {absolute_line}")
            return region.source, region.return_expressions, region.assignment_pairs
        continuation = self.continuation_at(absolute_line)
        if continuation is not None:
            return (
                continuation.source,
                continuation.return_expressions,
                continuation.assignment_pairs,
            )
        if 1 <= relative <= len(self.source_lines):
            line = self.source_lines[relative - 1]
            returns, assignments, _calls_found, _effects = _ast_line_facts(
                self.ast_statements, self.ast_call_lines, relative, relative,
            )
            return line, returns, assignments
        return "", frozenset(), ()

    def direct_statement_effects(
        self, absolute_line: int,
    ) -> tuple[tuple[str, str], ...]:
        """행이 소유한 statement 효과 (op, 대상) — ontology statement_effect_ops 기반.

        분기 행은 자기 범위(자식 제외), entry 행은 분기 밖·종료문 밖 최상위,
        그 외 행은 자기 줄의 효과만 소유한다(direct_callees 와 같은 소유 규칙).
        """
        relative = absolute_line - self.line_offset
        branch = self.at_line(absolute_line)
        if branch is not None and branch.start == relative:
            return branch.statement_effects
        if self.entry_line is not None and relative == self.entry_line:
            if self.entry_region is None:
                raise ValueError(f"entry region missing at line {absolute_line}")
            return self.entry_region.statement_effects
        continuation = self.continuation_at(absolute_line)
        if continuation is not None:
            return continuation.statement_effects
        _returns, _assignments, _calls, effects = _ast_line_facts(
            self.ast_statements,
            self.ast_call_lines,
            relative,
            relative,
            table_ops=self.ast_table_ops,
        )
        return effects

    def direct_evidence_lines(self, absolute_line: int) -> tuple[int, ...]:
        """Return exact absolute AST/call coordinates owned by one rule row."""
        relative = absolute_line - self.line_offset
        branch = self.at_line(absolute_line)
        if branch is not None and branch.start == relative:
            lines = branch.direct_evidence_lines
        elif self.entry_line is not None and relative == self.entry_line:
            if self.entry_region is None:
                raise ValueError(f"entry region missing at line {absolute_line}")
            lines = self.entry_region.evidence_lines
        elif (continuation := self.continuation_at(absolute_line)) is not None:
            lines = continuation.evidence_lines
        else:
            lines = (relative,)
        return tuple(line + self.line_offset for line in lines)

    def case_fallthrough_to(self, absolute_line: int) -> int | None:
        """Return the next parser CASE label for an empty fall-through arm.

        This is a structural language fact and never copies the next arm's effects.
        """
        if not bool(source_lexicon(self.lang).get("case_fallthrough")):
            return None
        relative = absolute_line - self.line_offset
        branch = self.at_line(absolute_line)
        if (
            branch is None
            or branch.start != relative
            or branch.ntype.upper() != "CASE"
            or branch.end != branch.start
            or branch.children
            or branch.direct_evidence_lines
        ):
            return None
        siblings = tuple(
            child for child in (branch.parent.children if branch.parent else self.roots)
            if child.ntype.upper() == "CASE"
        )
        index = next(
            (position for position, sibling in enumerate(siblings) if sibling is branch),
            None,
        )
        if index is None:
            return None
        if index + 1 >= len(siblings):
            return None
        return siblings[index + 1].start + self.line_offset

    def direct_callees(self, absolute_line: int) -> frozenset[str]:
        """Return calls owned by one row, including the synthesized entry row."""
        relative = absolute_line - self.line_offset
        branch = self.at_line(absolute_line)
        if branch is not None and branch.start == relative:
            return branch.callees
        if self.entry_line is not None and relative == self.entry_line:
            if self.entry_region is None:
                raise ValueError(f"entry region missing at line {absolute_line}")
            return self.entry_region.callees
        continuation = self.continuation_at(absolute_line)
        if continuation is not None:
            return continuation.callees
        return frozenset(
            name.lower() for line, name in self.ast_call_lines if line == relative
        )

    def dispatch_tables(self) -> list[dict[str, frozenset[str]]]:
        """SWITCH 마다 ``라벨 코드값 → 효과(호출 집합)`` 대응.

        본문 없는 CASE 는 언어 표(``case_fallthrough``)가 허용할 때만 다음 CASE 의 효과를
        물려받는다. 관습을 코드가 아니라 데이터로 받는다.
        """
        tables: list[dict[str, frozenset[str]]] = []
        for switch in self.all_branches:
            cases = [child for child in switch.children if child.ntype == "CASE"]
            if len(cases) < 2:
                continue
            effects: dict[str, frozenset[str]] = {}
            pending: list[Branch] = []
            for case in cases:
                pending.append(case)
                if self.case_fallthrough_to(case.start + self.line_offset) is None:
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

    def siblings(self, branch: Branch) -> tuple[Branch, ...]:
        """같은 부모를 공유하는 형제 분기. ``else``/``default`` 는 형제 조건의 **부정**으로
        정의되므로, 그 형제들의 조건값은 그 분기의 정당한 어휘다."""
        pool = branch.parent.children if branch.parent else self.roots
        return tuple(b for b in pool if b is not branch)

    @staticmethod
    def _most_specific(group: list[Branch]) -> Branch:
        """같은 줄에서 시작하는 분기 중 **조건을 가진 쪽**을 대표로 삼는다.

        ``else if`` 는 ELSE 와 IF 두 노드가 같은 줄에서 시작한다. ELSE 는 컨테이너일 뿐이고
        실제 판정은 그 안의 IF 가 한다 — 컨테이너를 고르면 그 규칙이 무엇을 근거로 하는지
        알 수 없다(실측: 앵커 954건 중 74건이 이 중복 줄을 지목했다).

        판정 기준은 **깊이**다. 같은 줄에서 시작하는 둘 중 안쪽(자손)이 더 구체적인 결정을
        한다. 노드 타입 이름으로 고르지 않는다 — 언어마다 이름이 다르다.
        """
        return max(group, key=lambda b: (len(b.ancestors()), len(b.cond_values)))

    def at_line(self, line: int) -> "Branch | None":
        """그 줄에서 시작하는 분기. 없으면 그 줄을 품는 가장 안쪽 분기."""
        line -= self.line_offset
        starting = [b for b in self.all_branches if b.start == line]
        if starting:
            return self._most_specific(starting)
        covering = [b for b in self.all_branches if b.start <= line <= b.end]
        return min(covering, key=lambda b: b.end - b.start) if covering else None

    def reachable_effects(self, branch: Branch) -> frozenset[str]:
        """그 분기와 그 안쪽 분기들이 만들어내는 것 전부."""
        found = set(branch.effect_atoms)
        for child in branch.children:
            found |= self.reachable_effects(child)
        return frozenset(found)

    def has_nested_control(self, absolute_line: int) -> bool:
        """Return whether the row delegates behavior to nested control nodes.

        Direct rule evidence intentionally excludes child ranges so one effect has one
        owner.  Human NL still needs to distinguish an actually empty leaf from a
        controller whose effects are described by child rows.  This exposes only that
        structural boolean; it never copies child conditions or effects upward.
        """
        if self.continuation_at(absolute_line) is not None:
            return False
        relative = absolute_line - self.line_offset
        branch = self.at_line(absolute_line)
        return bool(
            branch is not None and branch.start == relative and branch.children
        )

    def _chosen_by_line(self) -> dict[int, Branch]:
        by_line: dict[int, list[Branch]] = {}
        for branch in self.all_branches:
            by_line.setdefault(branch.start, []).append(branch)
        return {line: self._most_specific(group) for line, group in by_line.items()}

    def _paired_decision(self, branch: Branch) -> "Branch | None":
        """complement가 부정하는 문법상 소유자.

        파서는 ``else`` 를 IF 의 **형제**로도(평면), IF 의 **자식**으로도(중첩) 내보낸다.
        구조 부모가 허용 타입이면 그것이 소유자다. 부모 본문 안의 별도 IF/LOOP를 먼저
        고르면 ``if A { if B {} } else if C``가 ``A and C``로 뒤집힌다(4,033행 전수 실측).
        부모가 없거나 비허용인 평면 파서 형상에서만 같은 층의 가장 가까운 선행 허용
        타입을 쓴다. C의 LOOP 같은 비허용 선행 형제도 후보가 아니다.
        허용 타입은 언어별 ontology 데이터이며 prompt role 추측이 아니다.
        """
        allowed = complement_owner_types(self.lang)
        parent = branch.parent
        if parent is not None and parent.ntype.upper() in allowed:
            return parent
        pool = branch.parent.children if branch.parent else self.roots
        preceding = [
            candidate
            for candidate in pool
            if candidate is not branch
            and candidate.start <= branch.start
            and candidate.ntype.upper() in allowed
        ]
        if preceding:
            return max(preceding, key=lambda candidate: candidate.start)
        return None

    def paired_decision(self, branch: Branch) -> "Branch | None":
        """Public complement-to-decision ownership used by deterministic materializers."""
        return self._paired_decision(branch)

    def negated_arms(self, branch: Branch) -> list[Branch]:
        """이 노드가 **대안 절**일 때, 도달하려면 부정돼야 하는 같은 층 판정들 (spec 118 §2.3).

        평면 형상(PL/SQL `IF … ELSIF … ELSE`)에서 `ELSIF`/`ELSE` 는 부모 `IF` 의 본문이
        아니라 **대안**이다. 따라서 부모 판정과 자기보다 앞선 대안 절 전부가 부정이어야
        도달한다. 이 사실이 없으면 조상 조건이 "성립"으로 상속돼 경로가 정반대가 된다
        (실측: `FN_EDIT_YN.sql:41` 이 `["C35","C41"]`, 옳게는 `["!C35","C41"]`).

        중첩 형상(C/Java/Python 의 `else { if … }`)은 대안 절 타입이 빈 목록이라 이
        경로를 타지 않으며 기존 조상 사슬 계산이 그대로 담당한다.
        """
        types = alternative_clause_types(self.lang)
        if not types or branch.ntype.upper() not in types:
            return []
        parent = branch.parent
        if parent is None or parent.prompt_role() != "decision":
            return []
        arms = [parent]
        arms.extend(
            sibling
            for sibling in parent.children
            if sibling is not branch
            and sibling.start < branch.start
            and sibling.ntype.upper() in types
        )
        return arms

    def _complement_context(
        self, branch: Branch, chosen: dict[int, Branch]
    ) -> dict:
        """complement 행의 의미 좌표 — 무엇의 부정이고 어떤 외곽이 성립/부정인가.

        ``else`` 는 외곽 decision 을 **어느 팔로 지나왔는가**에 따라 그 조건이 성립(본문
        중첩)일 수도, 부정(else 팔 경유)일 수도 있다. else-if 사슬에서 외곽 조건을
        "상속"으로 표기하면 정반대다(실측 결함 — spec 116 1단계): 사슬의 바깥 decision
        들은 전부 **부정**이다. 조상 사슬을 올라가며 complement 조상의 짝 decision 은
        부정으로, else 팔이 아닌 body 중첩의 decision 조상은 성립으로 기록한다.
        """
        parent = branch.parent
        controller = (
            parent
            if parent is not None and parent.prompt_role() == "controller"
            else None
        )
        direct = None if controller is not None else self._paired_decision(branch)

        outer: list[tuple[Branch, str]] = []
        current = branch
        for ancestor in branch.ancestors():
            role = ancestor.prompt_role()
            if role == "decision":
                if ancestor is direct:
                    pass  # 직접 짝 — direct 로 이미 표기
                elif (
                    current.prompt_role() == "complement"
                    and current.parent is ancestor
                    and self._paired_decision(current) is ancestor
                ):
                    # 자식 형상 else 팔 경유 — 그 decision 은 부정이다
                    outer.append((ancestor, "negated"))
                else:
                    outer.append((ancestor, "holding"))
            elif role == "complement":
                paired = self._paired_decision(ancestor)
                if paired is not None and paired is not direct and paired is not ancestor.parent:
                    # 형제 형상 else 를 경유 — 그 짝 decision 은 부정이다
                    outer.append((paired, "negated"))
            current = ancestor
        # controller 형상(switch default 등)의 도달 조건은 주제식의 부정이 아니라
        # **형제 판정 전부의 불성립**이다. 주제식을 부정하면 의미가 어긋난다(L-③ 실측).
        siblings = [
            child
            for child in (controller.children if controller is not None else ())
            if child is not branch
            and child.prompt_role() == "decision"
            and child.condition.strip()
        ]
        return {
            "controller": controller,
            "direct": direct,
            "outer": outer,
            "siblings": siblings,
        }

    def _complement_text(self, context: dict) -> str:
        state_word = {"negated": "부정", "holding": "성립"}
        parts = []
        if context["controller"] is not None:
            controller = context["controller"]
            parts.append(
                f"{controller.ntype}@{controller.start + self.line_offset}의 "
                "다른 분기 모두 불성립"
            )
        elif context["direct"] is not None:
            direct = context["direct"]
            parts.append(f"직접 decision @{direct.start + self.line_offset}의 부정")
        if context["outer"]:
            outer_text = ", ".join(
                f"@{ancestor.start + self.line_offset} {state_word[state]}"
                for ancestor, state in context["outer"]
            )
            parts.append(f"외곽 {outer_text}")
        if not parts:
            return "(그 외 — 같은 층 다른 분기의 여집합)"
        return f"(그 외 — {'; '.join(parts)})"

    def _case_canon_pairs(self, branch: Branch) -> list[tuple[str, str]]:
        """CASE 라벨의 0 접두 정수 → 십진 병기 쌍. 해당 언어가 아니면 빈 목록."""
        if branch.ntype != "CASE":
            return []
        if not source_lexicon(self.lang).get("octal_case_labels"):
            return []
        pairs = []
        for value in sorted(branch.cond_values):
            canon = canonical_numeric(value)
            if canon is not None and canon != value:
                pairs.append((value, canon))
        return pairs

    def _depth_at(self, line: int, chosen: dict[int, Branch]) -> int:
        return sum(
            1
            for branch in self.all_branches
            if branch.start <= line <= branch.end and chosen.get(branch.start) is branch
        )

    def _has_explicit_alternative(self, branch: Branch) -> bool:
        """Whether parser structure supplies an ELSE/default-style arm."""

        alternative_types = alternative_clause_types(self.lang)
        for candidate in self.all_branches:
            if candidate is branch:
                continue
            if (
                candidate.parent is branch
                and candidate.ntype.upper() in alternative_types
            ):
                return True
            if (
                candidate.prompt_role() == "complement"
                and self._paired_decision(candidate) is branch
            ):
                return True
        return False

    def branch_index(self) -> str:
        """LLM 에게 보여줄 분기 목록 — 고를 수 있는 후보를 못 박는다.

        목록이 원문을 왜곡하면 앵커는 오히려 해가 된다. 실측으로 확인한 것들을 지킨다.

        1. **줄번호 유일** — ``else if`` 는 ELSE 와 IF 가 같은 줄에서 시작한다. 둘 다 실으면
           그 줄을 지목했을 때 어느 분기인지 확정되지 않는다(실측 74건).
        2. **조건식 절단 금지** — 110자로 자르니 여러 줄에 걸친 값 열거의 뒷부분이 사라져
           모델이 못 본 범위를 지어냈다(실측: 여러 줄에 걸친 열거형 코드 집합).
        3. **중첩 표시** — 부모·자식을 평면으로 나열하면 형제로 오해해 조건을 뒤집는다
           (실측: `cF10` 의 `cms_fb_req_yn` 안에 있는 `gc_send_ex_bank` 를 형제로 봄).
           들여쓰기로 소속을 드러내고, 조건식이 없는 분기(`else`)는 여집합임을 명시한다.
        4. **부정 보존** — else 행은 직접 decision 의 부정과 외곽 decision 별 성립/부정을
           명시한다. else-if 사슬을 "조건 상속"으로 쓰면 정반대가 된다(spec 116 1단계).
        5. **종료문 행** — return/핸들러 진입 줄을 [exit]/[handler] 행으로 싣는다. 종료
           계약이 행이어야 절단·누락이 뺄셈으로 드러난다(48상한 절단 사고의 방어).
        """
        chosen = self._chosen_by_line()
        rows_by_line: dict[int, str] = {}
        for line, branch in chosen.items():
            depth = sum(
                1 for a in branch.ancestors() if a.start in chosen and chosen[a.start] is a
            )
            role = branch.prompt_role()
            if role == "complement":
                condition = self._complement_text(self._complement_context(branch, chosen))
            else:
                condition = " ".join(branch.condition.split())
                canon_pairs = self._case_canon_pairs(branch)
                if canon_pairs:
                    notes = ", ".join(f"{raw}={canon}" for raw, canon in canon_pairs)
                    condition += f" (수치 {notes})"
            if (
                role == "decision"
                and branch.ntype.upper() in complement_owner_types(self.lang)
                and not self._has_explicit_alternative(branch)
            ):
                condition += (
                    f" | [implicit_false] 명시적 대안 절 없음: 조건이 거짓이면 "
                    f"@{branch.start + self.line_offset}-{branch.end + self.line_offset} "
                    "본문을 건너뛰고 정상 진행(handler 진입 아님)"
                )
            if role == "handler":
                condition += (
                    " | [error_only] 이 handler는 실행 중 오류로만 진입; "
                    "정상 흐름이나 앞선 조건 거짓으로 진입하지 않음"
                )
            rows_by_line[line] = (
                f"{'  ' * depth}{line + self.line_offset}: "
                f"[{role}] {branch.ntype} {condition}"
            )
        for line, kind, text in self.termination_rows:
            if line in rows_by_line:
                continue
            depth = self._depth_at(line, chosen)
            rows_by_line[line] = (
                f"{'  ' * depth}{line + self.line_offset}: [{kind}] {text}"
            )
        if self.entry_line is not None and self.entry_line not in rows_by_line:
            rows_by_line[self.entry_line] = (
                f"{self.entry_line + self.line_offset}: [entry] "
                "(분기 밖 직접 효과 — 도달하면 항상 실행)"
            )
        for region in self.continuation_regions:
            line = region.start
            termination = next(
                (
                    (kind, text) for term_line, kind, text in self.termination_rows
                    if term_line == line
                ),
                None,
            )
            if line in rows_by_line:
                if termination is not None:
                    rows_by_line.pop(line)
                else:
                    raise ValueError(f"continuation row collides with branch row: {line}")
            if termination is not None:
                kind, text = termination
                rows_by_line[line] = (
                    f"{line + self.line_offset}: [{kind}] {text} | "
                    f"{region.at_label or region.after_control} 이후 직접 효과 소유"
                )
            else:
                rows_by_line[line] = (
                    f"{line + self.line_offset}: [continuation] "
                    f"{region.at_label or region.after_control} 구조 좌표에 도달한 직접 효과"
                )
        return (
            "\n".join(rows_by_line[line] for line in sorted(rows_by_line))
            or "(분기 없음)"
        )

    def table_rows(self) -> list[dict]:
        """분기표의 구조화 실체(JSON 직렬화 가능) — 채점기·run 산출물의 기계 소비용.

        ``branch_index()`` 와 같은 행 선택(줄번호 유일)에서 나오며, 채점기가 프롬프트
        문자열을 재파싱하지 않도록 조건원문·코드값(+수치 병기)·직접호출·효과원자·
        complement 의 부정/성립 좌표를 그대로 싣는다 (spec 116 1단계).
        """
        chosen = self._chosen_by_line()
        rows: list[dict] = []
        for line, branch in chosen.items():
            role = branch.prompt_role()
            row: dict = {
                "line": line + self.line_offset,
                "end_line": branch.end + self.line_offset,
                "role": role,
                "ntype": branch.ntype,
                "condition": " ".join(branch.condition.split()),
                "condition_timing": branch.condition_timing or "pre",
                # parser LOOP expression 은 이미 판정절만이라(spec 016) 조건과 동일하다.
                # 키는 소비자(rule_prompt_evidence) 계약 보존을 위해 유지한다.
                "test_condition": " ".join(branch.condition.split()),
                "cond_values": sorted(branch.cond_values),
                "callees": sorted(branch.callees),
                "effect_atoms": sorted(branch.effect_atoms),
            }
            canon_pairs = self._case_canon_pairs(branch)
            if canon_pairs:
                row["cond_values_canon"] = {raw: canon for raw, canon in canon_pairs}
            if role == "complement":
                context = self._complement_context(branch, chosen)
                row["complement_of"] = {
                    "controller": (
                        context["controller"].start + self.line_offset
                        if context["controller"] is not None
                        else None
                    ),
                    "direct_negated": (
                        context["direct"].start + self.line_offset
                        if context["direct"] is not None
                        else None
                    ),
                    "siblings": [
                        sibling.start + self.line_offset
                        for sibling in context["siblings"]
                    ],
                    "outer": [
                        {"line": ancestor.start + self.line_offset, "state": state}
                        for ancestor, state in context["outer"]
                    ],
                }
            if self.has_nested_control(line + self.line_offset):
                row["nested_control"] = True
            fallthrough_to = self.case_fallthrough_to(line + self.line_offset)
            if fallthrough_to is not None:
                row["fallthrough_to"] = fallthrough_to
            rows.append(row)
        if self.entry_line is not None and self.entry_line not in chosen:
            rows.append({"line": self.entry_line + self.line_offset, "role": "entry"})
        termination_by_line = {
            line: (kind, text) for line, kind, text in self.termination_rows
        }
        for region in self.continuation_regions:
            termination = termination_by_line.get(region.start)
            rows.append({
                "line": region.start + self.line_offset,
                "end_line": region.end + self.line_offset,
                "role": termination[0] if termination is not None else "continuation",
                "text": termination[1] if termination is not None else "",
                "path_anchor_line": (
                    region.path_anchor + self.line_offset
                    if region.path_anchor is not None else None
                ),
                "after_control": region.after_control,
                "at_label": region.at_label,
                "fallthrough_false_of": (
                    region.fallthrough_false_of + self.line_offset
                    if region.fallthrough_false_of is not None else None
                ),
                "sequence_guard_lines": [
                    line + self.line_offset for line in region.sequence_guard_lines
                ],
                "unresolved_reachability": region.unresolved_reachability,
            })
        for line, kind, text in self.termination_rows:
            if line in chosen or any(
                region.start == line for region in self.continuation_regions
            ):
                continue
            rows.append({
                "line": line + self.line_offset,
                "role": kind,
                "text": text,
                # 분기 안 종료문은 소속 분기 규칙의 then 이 담는다 — 규칙 의무는
                # 분기 밖(최상위·핸들러 경로)의 종료문 행에만 있다 (spec 116 §18-6).
                "in_branch": any(
                    branch.start <= line <= branch.end
                    for branch in self.all_branches
                ),
            })
        rows.sort(key=lambda row: row["line"])
        return rows

    def best_match(self, values: set[str]) -> tuple[Branch | None, set[str]]:
        """항목이 근거로 삼은 분기 = 조건 코드값이 가장 많이 겹치는 분기."""
        best, best_hit = None, set()
        for branch in self.all_branches:
            hit = values & branch.cond_values
            if len(hit) > len(best_hit):
                best, best_hit = branch, hit
        return best, best_hit


def _symbol_like(values: set[str], body: str, lang: str) -> set[str]:
    """인용값 중 **이 본문에 식별자로도 등장하는 것**을 뺀다.

    `strncmp("ordf_chk_iden_numb", itf->in.func_ident_id, LEN)` 의 인용 문자열은
    호출 대상·함수 이름이지 도메인 코드값이 아니다. 이름 목록을 코드에 박지 않고
    **그 실행의 본문**에서 같은 토큰이 식별자로 쓰였는지로 판정한다(범용).
    """
    quotes = source_lexicon(lang).get("string_quotes") or []
    bare = body
    for quote in quotes:
        escaped = re.escape(quote)
        bare = re.sub(rf"{escaped}[^{escaped}\\\n]{{0,64}}{escaped}", " ", bare)
    names = {m.group(0).lower() for m in _IDENT.finditer(bare)}
    return {v for v in values if v.lower() not in names}


def _calls(text: str, lang: str) -> frozenset[str]:
    """``이름(`` 중 실제 호출만. 예약어 목록은 언어 표(데이터)에서 온다."""
    keywords = {k.lower() for k in (source_lexicon(lang).get("control_keywords") or [])}
    return frozenset(
        m.group(1).lower() for m in _CALL.finditer(text) if m.group(1).lower() not in keywords
    )


# AST statement label → 의미 축 (parser spec 016). 언어별 라벨 차이는 ontology roles 가
# 소유하고, 여기는 언어 중립 canonical 라벨만 안다.
_AST_EXIT_LABELS = frozenset({"RETURN", "THROW", "RAISE"})
_AST_ASSIGN_LABEL = "ASSIGNMENT"
_SIMPLE_ASSIGN_OPERATORS = frozenset({"", "=", ":="})
_COMPOUND_ASSIGN_OPERATORS = {
    "+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%",
    "&=": "&", "|=": "|", "^=": "^", "<<=": "<<", ">>=": ">>",
    "**=": "**", "//=": "//", "@=": "@",
}


def _assignment_pair(fact: StatementFact) -> tuple[str, str]:
    """Preserve compound-assignment meaning in the canonical set effect.

    Parser evidence stores the RHS separately from the assignment operator.  Treating
    ``disc += 2000`` as ``disc = 2000`` silently changes business meaning, so the
    deterministic boundary expands only the grammar-defined compound operator.
    """
    target = " ".join(fact.target.split())
    expression = " ".join(fact.expression.split())
    operator = (fact.operator or "").strip()
    if operator in _SIMPLE_ASSIGN_OPERATORS:
        return target, expression
    binary = _COMPOUND_ASSIGN_OPERATORS.get(operator)
    if binary is None:
        raise ValueError(
            f"unsupported assignment operator {operator!r}: {fact.node_id}"
        )
    return target, f"({target}) {binary} ({expression})"


def _ast_termination_rows(
    statements: Sequence[StatementFact],
    call_lines: Sequence[tuple[int, str]],
    handler_markers: Sequence[str],
) -> tuple[tuple[int, str, str], ...]:
    """AST 정본에서 [exit]/[handler] 행을 만든다 — source 첫 토큰 재탐색을 대체한다.

    handler 마커(PFM_CATCH 류)는 프레임워크 사실이며 두 parser 표기를 인정한다:
    호출형은 FUNCTION_CALL 좌표, 라벨형은 LABEL statement 좌표다. source 정규식
    재탐색 폴백은 없다.
    """
    handlers = {str(marker) for marker in handler_markers if str(marker).strip()}
    rows: dict[int, tuple[int, str, str]] = {}
    for line, name in call_lines:
        if name in handlers and line not in rows:
            rows[line] = (line, "handler", name)
    labels = sorted(
        (
            fact for fact in statements
            if fact.label == "LABEL" and str(fact.target or "") in handlers
        ),
        key=lambda fact: (fact.start, fact.end, str(fact.target)),
    )
    for fact in labels:
        rows.setdefault(fact.start, (fact.start, "handler", str(fact.target)))
    all_label_lines = sorted(
        fact.start for fact in statements if fact.label == "LABEL"
    )

    def owned_by_handler_label(line: int) -> bool:
        preceding = next((fact for fact in reversed(labels) if fact.start < line), None)
        if preceding is None:
            return False
        next_label = next(
            (candidate for candidate in all_label_lines if candidate > preceding.start),
            None,
        )
        return next_label is None or line < next_label

    for fact in statements:
        if (
            fact.label not in _AST_EXIT_LABELS
            or fact.start in rows
            or owned_by_handler_label(fact.start)
        ):
            continue
        keyword = fact.label.lower()
        expression = " ".join((fact.expression or "").split())
        statement = f"{keyword} {expression}".strip()[:160]
        rows[fact.start] = (fact.start, "exit", statement)
    return tuple(rows[line] for line in sorted(rows))


def _ast_line_facts(
    statements: Sequence[StatementFact],
    call_lines: Sequence[tuple[int, str]],
    start: int,
    end: int,
    exclude: Sequence[tuple[int, int]] = (),
    table_ops: Sequence[tuple[int, str, str]] = (),
) -> tuple[
    frozenset[str],
    tuple[tuple[str, str], ...],
    frozenset[str],
    tuple[tuple[str, str], ...],
]:
    """[start,end] 범위(자식 범위 제외)의 (반환식, 대입쌍, 직접호출, statement 효과)."""
    def owned(line: int) -> bool:
        return start <= line <= end and not any(s <= line <= e for s, e in exclude)

    returns = frozenset(
        " ".join(fact.expression.split())
        for fact in statements
        if fact.label == "RETURN" and fact.expression and owned(fact.start)
    )
    assignments = tuple(
        _assignment_pair(fact)
        for fact in statements
        if fact.label == _AST_ASSIGN_LABEL and fact.target and fact.expression
        and owned(fact.start)
    )
    calls = frozenset(
        name.lower() for line, name in call_lines if owned(line)
    )
    effect_ops = statement_effect_ops()
    effects = tuple(
        (effect_ops[fact.label], " ".join((fact.target or "").split()))
        for fact in statements
        if fact.label in effect_ops and owned(fact.start)
        and not (fact.label == "RETURN" and fact.expression)
    )
    # DBMS DML 효과 (spec 119) — step5 링커가 이미 결정론으로 만든 (줄, op, 테이블).
    # statement 효과와 같은 (op, 대상) 모양이라 렌더·채점 경로를 그대로 공유한다.
    effects += tuple(
        (op, table) for line, op, table in table_ops if owned(line)
    )
    return returns, assignments, calls, tuple(dict.fromkeys(effects))


def _ast_entry_line(
    statements: Sequence[StatementFact],
    call_lines: Sequence[tuple[int, str]],
    branches: Sequence[Branch],
    termination_rows: Sequence[tuple[int, str, str]],
    table_ops: Sequence[tuple[int, str, str]] = (),
) -> "int | None":
    """분기 밖 최상위 효과문(대입·호출·DML)의 첫 줄 — AST/링커 정본.

    DBMS 는 최상위 효과가 대입이 아니라 DML 인 경우가 흔하다(rwis 트리거의 본문 첫 줄이
    ``INSERT INTO …``). DML 을 후보에서 빼면 그 효과를 약속할 행이 사라진다(spec 119).
    """
    termination_lines = {line for line, _, _ in termination_rows}
    candidates = [
        fact.start for fact in statements if fact.label == _AST_ASSIGN_LABEL
    ] + [line for line, _ in call_lines] + [line for line, _op, _t in table_ops]
    effect_lines = sorted(
        line for line in candidates
        if line not in termination_lines
        and not any(branch.start <= line <= branch.end for branch in branches)
    )
    return effect_lines[0] if effect_lines else None


def _linear_ranges(
    start: int,
    end: int,
    children: Sequence[Branch],
) -> list[_LinearRegion]:
    """Split one owner range at immediate child controls.

    The third item is the child control that must complete before the region can run.
    It is deliberately a coordinate fact, not an inferred business predicate.
    """
    ranges: list[_LinearRegion] = []
    cursor = start
    preceding: Branch | None = None
    for child in sorted(children, key=lambda item: (item.start, item.end, item.node_id)):
        if cursor <= child.start - 1:
            ranges.append(_LinearRegion(cursor, child.start - 1, preceding))
        cursor = max(cursor, child.end + 1)
        preceding = child
    if cursor <= end:
        ranges.append(_LinearRegion(cursor, end, preceding))
    return ranges


def _split_at_labels(
    ranges: Sequence[_LinearRegion],
    statements: Sequence[StatementFact],
) -> list[_LinearRegion]:
    """Split linear ownership at parser-proven named labels.

    A label has both normal fall-through and GOTO predecessors.  Effects before it must
    therefore never be folded into effects reached at the label.  This is a coordinate
    split only; no path predicate is inferred.
    """
    labels = sorted(
        (fact for fact in statements if fact.label == "LABEL" and fact.target),
        key=lambda fact: (fact.start, fact.end, str(fact.target)),
    )
    result: list[_LinearRegion] = []
    for region in ranges:
        owned = [
            fact for fact in labels
            if region.start <= fact.start <= region.end
        ]
        if not owned:
            result.append(region)
            continue
        cursor = region.start
        for index, label in enumerate(owned):
            if cursor < label.start:
                result.append(_LinearRegion(
                    cursor, label.start - 1, region.preceding,
                ))
            next_start = (
                owned[index + 1].start if index + 1 < len(owned) else region.end + 1
            )
            result.append(_LinearRegion(
                label.start, next_start - 1, None, label,
            ))
            cursor = next_start
    return result


def _sequence_guards_before(
    children: Sequence[Branch],
    coordinate: int,
    join_label_lines: Sequence[int],
) -> tuple[int, ...]:
    """Return prior sibling decisions whose true arm cannot fall through.

    Only parser-proven direct transfer (return/throw/raise/break/continue/goto) is
    used.  A named GOTO join resets earlier guards because both the normal and jump
    predecessors meet there.
    """
    labels = sorted(line for line in join_label_lines if line <= coordinate)
    guards: list[int] = []
    cursor = 0

    def reset_through(stop: int) -> None:
        nonlocal cursor
        if any(cursor <= line <= stop for line in labels):
            guards.clear()
        cursor = max(cursor, stop + 1)

    for child in sorted(children, key=lambda item: (item.start, item.end, item.node_id)):
        if child.start >= coordinate:
            break
        reset_through(child.start)
        if (
            child.end < coordinate
            and child.direct_terminates
            and child.prompt_role() == "decision"
            and child.condition.strip()
        ):
            guards.append(child.start)
        cursor = max(cursor, child.end + 1)
    reset_through(coordinate)
    return tuple(dict.fromkeys(guards))


def _build_effect_region(
    *,
    start: int,
    end: int,
    owner: Branch | None,
    preceding: Branch | None,
    split: SplitSource,
    statements: Sequence[StatementFact],
    call_lines: Sequence[tuple[int, str]],
    table_ops: Sequence[tuple[int, str, str]],
    termination_rows: Sequence[tuple[int, str, str]],
    lang: str,
    line_offset: int,
    entry_label: StatementFact | None,
    unresolved_reachability: bool,
    sequence_guard_lines: Sequence[int] = (),
) -> EffectRegion:
    """Materialize one exact linear source/effect region."""
    termination_lines = {
        line for line, _kind, _text in termination_rows
        if owner is None and start <= line <= end
    }
    exclusions = tuple((line, line) for line in sorted(termination_lines))
    returns, assignments, calls, effects = _ast_line_facts(
        statements,
        call_lines,
        start,
        end,
        exclusions,
        table_ops,
    )
    if owner is not None and start == owner.start:
        calls = calls - _calls(owner.condition, lang)
    source = "\n".join(
        line
        for number, line in enumerate(
            split.live_lines[start - 1 : end], start=start,
        )
        if number not in termination_lines
    )
    condition_calls = _calls(owner.condition, lang) if owner is not None else frozenset()
    effect_labels = set(statement_effect_ops()) | {_AST_ASSIGN_LABEL, *_AST_EXIT_LABELS}
    effect_lines = [
        fact.start
        for fact in statements
        if start <= fact.start <= end
        and fact.start not in termination_lines
        and fact.label in effect_labels
    ]
    effect_lines.extend(
        line for line, name in call_lines
        if start <= line <= end
        and line not in termination_lines
        and not (owner is not None and line == owner.start and name.lower() in condition_calls)
    )
    effect_lines.extend(
        line for line, _op, _table in table_ops
        if start <= line <= end and line not in termination_lines
    )
    anchor = entry_label.start if entry_label is not None else (
        min(effect_lines) if effect_lines else start
    )
    direct_jump = bool(
        preceding is not None
        and preceding.direct_terminates
    )
    terminates = bool(
        any(
            fact.label in _AST_EXIT_LABELS and start <= fact.start <= end
            and not any(s <= fact.start <= e for s, e in exclusions)
            for fact in statements
        )
        or any(op == "jump" for op, _target in effects)
    )
    return EffectRegion(
        start=anchor,
        end=end,
        source=source,
        return_expressions=returns,
        assignment_pairs=assignments,
        callees=calls,
        statement_effects=effects,
        evidence_lines=tuple(dict.fromkeys(effect_lines)),
        path_anchor=owner.start if owner is not None else None,
        after_control=(
            f"{preceding.ntype}@{preceding.start + line_offset}"
            if preceding is not None and not direct_jump else ""
        ),
        at_label=(
            f"{entry_label.target}@{entry_label.start + line_offset}"
            if entry_label is not None else ""
        ),
        fallthrough_false_of=(
            preceding.start
            if preceding is not None and direct_jump and entry_label is None
            else None
        ),
        sequence_guard_lines=tuple(sequence_guard_lines),
        terminates=terminates,
        unresolved_reachability=unresolved_reachability,
    )


def _region_has_effect(region: EffectRegion) -> bool:
    return bool(
        region.return_expressions
        or region.assignment_pairs
        or region.callees
        or region.statement_effects
    )


def build_inventory(
    code_body: str,
    control_subtree: Sequence[ControlSubtreeNode] | None,
    lang: str,
    *,
    statements: Sequence[StatementFact],
    call_lines: Sequence[tuple[int, str]] = (),
    table_ops: Sequence[tuple[int, str, str]] = (),
    split: SplitSource | None = None,
    declared_values: Sequence[str] = (),
    line_offset: int = 0,
    handler_markers: Sequence[str] = (),
) -> BranchInventory:
    """분기 대장을 만든다. ``control_subtree`` 가 없으면 분기 0으로 본다.

    구조 구문 사실(종료행·반환식·대입·entry·직접호출·조건식)의 생산자는 parser AST 다
    (spec 016). ``statements`` 는 필수다 — statement 가 없는 routine 도 "없음"을 빈
    시퀀스로 명시해야 하며, 이 함수는 source 를 재탐색하는 폴백을 갖지 않는다.

    ``declared_values`` 는 함수 **본문 밖**에서 선언된 상수의 값이다(``#define`` 등).
    프롬프트는 이 값을 증거 2순위로 인정하므로(``referenced_symbols`` 의
    ``immutable_constant.declared_value``) 근거 앵커도 같은 것을 인정해야 한다.
    본문만 앵커로 쓰면 정당하게 인용한 상수를 날조로 오판한다
    (실측: `#define ... "ZAPAE0021"` 을 인용한 규칙이 무근거로 잡혔다).
    """
    split = split or split_source(code_body, lang)

    def _live_line(line: int) -> bool:
        index = line - 1
        return 0 <= index < len(split.live_lines) and bool(split.live_lines[index].strip())

    # 전처리 비활성 구간(#if 0 등)의 statement/call 은 실행 의무가 아니다 — 분기(convert)
    # 와 같은 live/blank 판정 경계를 공유한다(framework 실측: zapamcom10110.c 1282~1353
    # `#if 0` 블록의 return 이 [exit] 행으로 승격되던 결함).
    ast_statements = _active_statement_facts(code_body, lang, statements, split)
    validate_static_jump_targets(
        code_body,
        lang,
        ast_statements,
        split=split,
        line_offset=line_offset,
    )
    ast_calls: tuple[tuple[int, str], ...] = tuple(
        (line, name) for line, name in call_lines if _live_line(line)
    )
    # DML 효과 좌표도 같은 live/blank 경계를 공유한다 — 비활성 구간의 DML 은 의무가 아니다.
    ast_table_ops: tuple[tuple[int, str, str], ...] = tuple(
        (line, op, table) for line, op, table in table_ops if _live_line(line)
    )
    all_branches: list[Branch] = []

    def convert(node: ControlSubtreeNode, parent: Branch | None) -> Branch | None:
        body = split.live(node.start, node.end)
        # Parser artifacts can still contain coordinates from ontology-declared inactive
        # regions (for example conditional-compilation history).  An empty live range is not
        # an executable branch obligation.  The language spelling is owned by split_source;
        # inventory only consumes its live/blank verdict.
        if not body.strip():
            return None
        # Parser ranges can include nested/else child ranges. Direct row effects must not
        # absorb child calls or atoms; otherwise a parent rule can copy a sibling/child action
        # and still pass the "direct callees" grader. Ownership comes from AST coordinates,
        # not language keywords or function-name patterns.
        child_ranges = [(child.start, child.end) for child in node.children]
        direct_body = "\n".join(
            line
            for line_number, line in enumerate(
                split.live_lines[node.start - 1 : node.end], start=node.start
            )
            if not any(start <= line_number <= end for start, end in child_ranges)
        )
        # parser 소유 조건식 (spec 016) — LOOP expression 은 이미 판정절만이고,
        # grammar 가 조건을 두지 않는 노드(ELSE/default/TRY/CATCH)는 빈 조건이다.
        condition = node.expression or ""
        cond_values = _symbol_like(literal_values(condition, lang), body, lang)
        # 조건식에 인용된 호출은 효과가 아니다 — parser 소유 조건 문자열 안의
        # 이름만 제외한다(소스 재탐색 아님).
        returns, assignments, call_names, statement_effects = _ast_line_facts(
            ast_statements, ast_calls, node.start, node.end, child_ranges,
            ast_table_ops,
        )
        branch = Branch(
            node_id=node.node_id,
            ntype=node.ntype,
            start=node.start,
            end=node.end,
            parent=parent,
            condition=condition,
            condition_timing=node.condition_timing,
            cond_values=frozenset(cond_values),
            callees=call_names - _calls(condition, lang),
            effect_atoms=frozenset(atoms(direct_body, lang)),
            direct_source=direct_body,
            return_expressions=returns,
            assignment_pairs=assignments,
            statement_effects=statement_effects,
        )
        all_branches.append(branch)
        branch.children = [
            converted
            for child in node.children
            if (converted := convert(child, branch)) is not None
        ]
        return branch

    roots = tuple(
        converted
        for node in (control_subtree or ())
        if (converted := convert(node, None)) is not None
    )
    declared = {value.strip().lower() for value in declared_values if value and value.strip()}
    declared |= {v for value in declared_values for v in literal_values(value or "", lang)}
    termination = _ast_termination_rows(ast_statements, ast_calls, handler_markers)
    function_returns = frozenset(
        " ".join(fact.expression.split())
        for fact in ast_statements
        if fact.label == "RETURN" and fact.expression
    )
    # Direct effects are owned by linear sequence regions, not by a whole routine/branch.
    # This prevents one entry/controller Rule from merging effects that occur before and
    # after nested controls (spec 128).
    effect_ops = statement_effect_ops()
    labels_by_name: dict[str, list[StatementFact]] = {}
    for fact in ast_statements:
        if fact.label == "LABEL" and fact.target:
            labels_by_name.setdefault(str(fact.target), []).append(fact)
    unresolved_jump = any(
        fact.label == "GOTO"
        and len(labels_by_name.get(str(fact.target or ""), ())) != 1
        for fact in ast_statements
    )
    goto_targets = {
        str(fact.target) for fact in ast_statements
        if fact.label == "GOTO" and fact.target
    }
    join_label_lines = tuple(
        fact.start for fact in ast_statements
        if fact.label == "LABEL" and str(fact.target or "") in goto_targets
    )
    chosen_by_line: dict[int, list[Branch]] = {}
    for branch in all_branches:
        chosen_by_line.setdefault(branch.start, []).append(branch)
    chosen_ids = {
        BranchInventory._most_specific(group).node_id
        for group in chosen_by_line.values()
    }
    continuations: list[EffectRegion] = []
    ranges_by_branch: dict[str, list[_LinearRegion]] = {}
    # First materialize every controller's own prefix.  Continuation fall-through
    # depends on whether the preceding child ends in a direct jump, so all children
    # must have their facts before any continuation is built.
    for branch in all_branches:
        ranges = _split_at_labels(
            _linear_ranges(branch.start, branch.end, branch.children),
            ast_statements,
        )
        ranges_by_branch[branch.node_id] = ranges
        first = (
            _build_effect_region(
                start=ranges[0].start, end=ranges[0].end, owner=branch,
                preceding=ranges[0].preceding, split=split, statements=ast_statements,
                call_lines=ast_calls, table_ops=ast_table_ops,
                termination_rows=termination, lang=lang, line_offset=line_offset,
                entry_label=ranges[0].entry_label,
                unresolved_reachability=unresolved_jump,
            )
            if ranges else None
        )
        branch.direct_source = first.source if first is not None else ""
        branch.return_expressions = (
            first.return_expressions if first is not None else frozenset()
        )
        branch.assignment_pairs = first.assignment_pairs if first is not None else ()
        branch.callees = first.callees if first is not None else frozenset()
        branch.statement_effects = first.statement_effects if first is not None else ()
        branch.direct_evidence_lines = first.evidence_lines if first is not None else ()
        branch.direct_terminates = first.terminates if first is not None else False
        branch.effect_atoms = frozenset(
            atoms(branch.direct_source, lang) if branch.direct_source else ()
        )

    def assign_sequence_guards(children: Sequence[Branch]) -> None:
        for child in children:
            child.sequence_guard_lines = _sequence_guards_before(
                children, child.start, join_label_lines,
            )
            assign_sequence_guards(child.children)

    assign_sequence_guards(roots)

    for branch in all_branches:
        if branch.node_id not in chosen_ids:
            continue
        ranges = ranges_by_branch[branch.node_id]
        for sequence in ranges[1:]:
            region = _build_effect_region(
                start=sequence.start, end=sequence.end, owner=branch,
                preceding=sequence.preceding, split=split, statements=ast_statements,
                call_lines=ast_calls, table_ops=ast_table_ops,
                termination_rows=termination, lang=lang, line_offset=line_offset,
                entry_label=sequence.entry_label,
                unresolved_reachability=unresolved_jump,
                sequence_guard_lines=_sequence_guards_before(
                    branch.children, sequence.start, join_label_lines,
                ),
            )
            if _region_has_effect(region):
                continuations.append(region)

    routine_ranges = _split_at_labels(
        _linear_ranges(1, len(split.live_lines), roots),
        ast_statements,
    )
    entry_region: EffectRegion | None = None
    if routine_ranges:
        first_sequence = routine_ranges[0]
        first_region = _build_effect_region(
            start=first_sequence.start, end=first_sequence.end, owner=None,
            preceding=first_sequence.preceding, split=split, statements=ast_statements,
            call_lines=ast_calls, table_ops=ast_table_ops,
            termination_rows=termination, lang=lang, line_offset=line_offset,
            entry_label=first_sequence.entry_label,
            unresolved_reachability=unresolved_jump,
            sequence_guard_lines=_sequence_guards_before(
                roots, first_sequence.start, join_label_lines,
            ),
        )
        if _region_has_effect(first_region) and first_sequence.entry_label is None:
            entry_region = first_region
        elif _region_has_effect(first_region):
            continuations.append(first_region)
        for sequence in routine_ranges[1:]:
            region = _build_effect_region(
                start=sequence.start, end=sequence.end, owner=None,
                preceding=sequence.preceding, split=split, statements=ast_statements,
                call_lines=ast_calls, table_ops=ast_table_ops,
                termination_rows=termination, lang=lang, line_offset=line_offset,
                entry_label=sequence.entry_label,
                unresolved_reachability=unresolved_jump,
                sequence_guard_lines=_sequence_guards_before(
                    roots, sequence.start, join_label_lines,
                ),
            )
            if _region_has_effect(region):
                continuations.append(region)
    entry = entry_region.start if entry_region is not None else None
    function_callees = frozenset(name.lower() for _line, name in ast_calls)
    return BranchInventory(
        lang=lang,
        roots=roots,
        all_branches=tuple(all_branches),
        callees=function_callees,
        source_values=frozenset(literal_values(split.live(), lang) | declared),
        active_lines=frozenset(
            index + 1 + line_offset
            for index, line in enumerate(split.live_lines)
            if line.strip()
        ),
        declared_values=frozenset(
            value.strip() for value in declared_values if value and value.strip()
        ),
        line_offset=line_offset,
        termination_rows=termination,
        return_expressions=function_returns,
        entry_line=entry,
        source_lines=tuple(split.live_lines),
        ast_statements=ast_statements,
        ast_call_lines=ast_calls,
        ast_table_ops=ast_table_ops,
        entry_region=entry_region,
        continuation_regions=tuple(sorted(
            continuations, key=lambda region: (region.start, region.end),
        )),
    )
