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
        """
        role = control_role_of(self.ntype)
        if role != "decision":
            return role
        # grammar 사실: caseless 라벨(default)만 expression 이 없다 (spec 016).
        if self.ntype.upper() == "CASE" and not self.condition.strip():
            return "complement"
        return "decision"


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
    ast_call_lines: tuple[tuple[int, str], ...] = ()

    def _entry_direct_source(self) -> str:
        """Return every top-level effect line owned by the single entry obligation."""
        if self.entry_line is None:
            return ""
        termination_lines = {line for line, _, _ in self.termination_rows}
        lines = []
        for line, source in enumerate(self.source_lines, start=1):
            if line < self.entry_line or line in termination_lines:
                continue
            if any(branch.start <= line <= branch.end for branch in self.all_branches):
                continue
            lines.append(source)
        return "\n".join(lines)

    def direct_evidence(
        self, absolute_line: int,
    ) -> tuple[str, frozenset[str], tuple[tuple[str, str], ...]]:
        """행별 직접 source·return·대입 증거를 prompt 밖에서 제공한다."""
        relative = absolute_line - self.line_offset
        branch = self.at_line(absolute_line)
        if branch is not None and branch.start == relative:
            return branch.direct_source, branch.return_expressions, branch.assignment_pairs
        if self.entry_line is not None and relative == self.entry_line:
            # entry 행은 분기 밖·종료문 밖 최상위 효과만 소유한다.
            source = self._entry_direct_source()
            branch_ranges = [(b.start, b.end) for b in self.all_branches]
            termination_lines = {line for line, _, _ in self.termination_rows}

            def entry_owned(line: int) -> bool:
                return line not in termination_lines and not any(
                    s <= line <= e for s, e in branch_ranges
                )

            returns = frozenset(
                " ".join(fact.expression.split())
                for fact in self.ast_statements
                if fact.label in _AST_EXIT_LABELS and fact.expression
                and entry_owned(fact.start)
            )
            assignments = tuple(
                (" ".join(fact.target.split()), " ".join(fact.expression.split()))
                for fact in self.ast_statements
                if fact.label == _AST_ASSIGN_LABEL and fact.target and fact.expression
                and entry_owned(fact.start)
            )
            return source, returns, assignments
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
        effect_ops = statement_effect_ops()
        if not effect_ops:
            return ()
        relative = absolute_line - self.line_offset
        branch = self.at_line(absolute_line)
        if branch is not None and branch.start == relative:
            return branch.statement_effects
        if self.entry_line is not None and relative == self.entry_line:
            branch_ranges = [(b.start, b.end) for b in self.all_branches]
            termination_lines = {line for line, _, _ in self.termination_rows}
            return tuple(
                (effect_ops[fact.label], " ".join(fact.target.split()))
                for fact in self.ast_statements
                if fact.label in effect_ops and fact.target
                and fact.start not in termination_lines
                and not any(s <= fact.start <= e for s, e in branch_ranges)
            )
        return tuple(
            (effect_ops[fact.label], " ".join(fact.target.split()))
            for fact in self.ast_statements
            if fact.label in effect_ops and fact.target and fact.start == relative
        )

    def direct_callees(self, absolute_line: int) -> frozenset[str]:
        """Return calls owned by one row, including the synthesized entry row."""
        relative = absolute_line - self.line_offset
        branch = self.at_line(absolute_line)
        if branch is not None and branch.start == relative:
            return branch.callees
        if self.entry_line is not None and relative == self.entry_line:
            branch_ranges = [(b.start, b.end) for b in self.all_branches]
            termination_lines = {line for line, _, _ in self.termination_rows}
            return frozenset(
                name.lower()
                for line, name in self.ast_call_lines
                if line not in termination_lines
                and not any(s <= line <= e for s, e in branch_ranges)
            )
        return frozenset(
            name.lower() for line, name in self.ast_call_lines if line == relative
        )

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
        return {"controller": controller, "direct": direct, "outer": outer}

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
                    "outer": [
                        {"line": ancestor.start + self.line_offset, "state": state}
                        for ancestor, state in context["outer"]
                    ],
                }
            rows.append(row)
        if self.entry_line is not None and self.entry_line not in chosen:
            rows.append({"line": self.entry_line + self.line_offset, "role": "entry"})
        for line, kind, text in self.termination_rows:
            if line in chosen:
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


def _handler_label_lines(
    split: SplitSource, handler_markers: Sequence[str],
) -> tuple[tuple[int, str], ...]:
    """live 소스에서 라벨형 handler 마커(`PFM_CATCH:`) 줄을 찾는다.

    마커는 skill 이 선언한 프레임워크 사실이라 표기가 둘이다 — 호출형
    `PFM_CATCH();` 는 parser FUNCTION_CALL 좌표로 오지만, 라벨형 `PFM_CATCH:` 는
    C 라벨 구문이라 현재 parser 가 노드를 내보내지 않는다(2026-08-05 K run에서
    handler 행 67개 소실로 실측). 라벨 emit 은 parser 개선 항목으로 남기고,
    여기서는 마커 이름 exact + 콜론 형태만 인정한다(정규식 구조 추정 아님).
    """
    found: list[tuple[int, str]] = []
    markers = [str(m) for m in handler_markers if str(m).strip()]
    for marker in markers:
        pattern = re.compile(rf"^\s*{re.escape(marker)}\s*:")
        for index, line in enumerate(split.live_lines, start=1):
            if pattern.match(line):
                found.append((index, marker))
    return tuple(found)


def _ast_termination_rows(
    statements: Sequence[StatementFact],
    call_lines: Sequence[tuple[int, str]],
    handler_markers: Sequence[str],
    label_lines: Sequence[tuple[int, str]] = (),
) -> tuple[tuple[int, str, str], ...]:
    """AST 정본에서 [exit]/[handler] 행을 만든다 — source 첫 토큰 재탐색을 대체한다.

    handler 마커(PFM_CATCH 류)는 프레임워크 사실이며 두 표기를 인정한다:
    호출형은 parser FUNCTION_CALL 좌표(call_lines), 라벨형은 live 소스의
    마커 라벨 줄(label_lines — ``_handler_label_lines``)이다.
    """
    handlers = {str(marker) for marker in handler_markers if str(marker).strip()}
    rows: dict[int, tuple[int, str, str]] = {}
    for line, name in call_lines:
        if name in handlers and line not in rows:
            rows[line] = (line, "handler", name)
    for line, name in label_lines:
        if line not in rows:
            rows[line] = (line, "handler", name)
    for fact in statements:
        if fact.label not in _AST_EXIT_LABELS or fact.start in rows:
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
        if fact.label in _AST_EXIT_LABELS and fact.expression and owned(fact.start)
    )
    assignments = tuple(
        (" ".join(fact.target.split()), " ".join(fact.expression.split()))
        for fact in statements
        if fact.label == _AST_ASSIGN_LABEL and fact.target and fact.expression
        and owned(fact.start)
    )
    calls = frozenset(
        name.lower() for line, name in call_lines if owned(line)
    )
    effect_ops = statement_effect_ops()
    effects = tuple(
        (effect_ops[fact.label], " ".join(fact.target.split()))
        for fact in statements
        if fact.label in effect_ops and fact.target and owned(fact.start)
    )
    return returns, assignments, calls, effects


def _ast_entry_line(
    statements: Sequence[StatementFact],
    call_lines: Sequence[tuple[int, str]],
    branches: Sequence[Branch],
    termination_rows: Sequence[tuple[int, str, str]],
) -> "int | None":
    """분기 밖 최상위 효과문(대입 또는 호출 statement)의 첫 줄 — AST 정본."""
    termination_lines = {line for line, _, _ in termination_rows}
    candidates = [
        fact.start for fact in statements if fact.label == _AST_ASSIGN_LABEL
    ] + [line for line, _ in call_lines]
    effect_lines = sorted(
        line for line in candidates
        if line not in termination_lines
        and not any(branch.start <= line <= branch.end for branch in branches)
    )
    return effect_lines[0] if effect_lines else None


def build_inventory(
    code_body: str,
    control_subtree: Sequence[ControlSubtreeNode] | None,
    lang: str,
    *,
    statements: Sequence[StatementFact],
    call_lines: Sequence[tuple[int, str]] = (),
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
    ast_statements: tuple[StatementFact, ...] = tuple(
        fact for fact in statements if _live_line(fact.start)
    )
    ast_calls: tuple[tuple[int, str], ...] = tuple(
        (line, name) for line, name in call_lines if _live_line(line)
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
        )
        branch = Branch(
            node_id=node.node_id,
            ntype=node.ntype,
            start=node.start,
            end=node.end,
            parent=parent,
            condition=condition,
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
    termination = _ast_termination_rows(
        ast_statements, ast_calls, handler_markers,
        _handler_label_lines(split, handler_markers),
    )
    function_returns = frozenset(
        " ".join(fact.expression.split())
        for fact in ast_statements
        if fact.label in _AST_EXIT_LABELS and fact.expression
    )
    entry = _ast_entry_line(ast_statements, ast_calls, all_branches, termination)
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
    )
