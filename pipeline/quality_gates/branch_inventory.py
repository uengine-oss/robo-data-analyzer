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

from pipeline.languages import control_role_of, source_lexicon
from pipeline.models.analysis_context import ControlSubtreeNode
from pipeline.quality_gates.lexing import SplitSource, atoms, literal_values, split_source

_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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
    children: list["Branch"] = field(default_factory=list)

    def ancestors(self) -> list["Branch"]:
        chain, node = [], self.parent
        while node is not None:
            chain.append(node)
            node = node.parent
        return chain

    def prompt_role(self, lang: str) -> str:
        """Return controller/decision/complement from AST role and source spelling.

        Condition length and atom count are deliberately irrelevant.
        """
        role = control_role_of(self.ntype)
        if role != "decision":
            return role
        head = self.condition.lstrip().lower()
        keywords = source_lexicon(lang).get("complement_keywords") or ()
        if any(
            head == keyword.lower()
            or head.startswith(f"{keyword.lower()}:")
            or head.startswith(f"{keyword.lower()} ")
            for keyword in keywords
        ):
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
    line_offset: int = 0

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

    def branch_index(self) -> str:
        """LLM 에게 보여줄 분기 목록 — 고를 수 있는 후보를 못 박는다.

        목록이 원문을 왜곡하면 앵커는 오히려 해가 된다. 실측으로 확인한 세 가지를 지킨다.

        1. **줄번호 유일** — ``else if`` 는 ELSE 와 IF 가 같은 줄에서 시작한다. 둘 다 실으면
           그 줄을 지목했을 때 어느 분기인지 확정되지 않는다(실측 74건).
        2. **조건식 절단 금지** — 110자로 자르니 여러 줄에 걸친 값 열거의 뒷부분이 사라져
           모델이 못 본 범위를 지어냈다(실측: `Bank_054` 의 과목코드 집합).
        3. **중첩 표시** — 부모·자식을 평면으로 나열하면 형제로 오해해 조건을 뒤집는다
           (실측: `cF10` 의 `cms_fb_req_yn` 안에 있는 `gc_send_ex_bank` 를 형제로 봄).
           들여쓰기로 소속을 드러내고, 조건식이 없는 분기(`else`)는 여집합임을 명시한다.
        """
        by_line: dict[int, list[Branch]] = {}
        for branch in self.all_branches:
            by_line.setdefault(branch.start, []).append(branch)
        chosen = {line: self._most_specific(group) for line, group in by_line.items()}

        rows = []
        for line in sorted(chosen):
            branch = chosen[line]
            depth = sum(1 for a in branch.ancestors() if a.start in chosen and chosen[a.start] is a)
            role = branch.prompt_role(self.lang)
            condition = " ".join(branch.condition.split())
            if role == "complement":
                condition = "(그 외 — 같은 층 다른 분기의 여집합)"
            rows.append(
                f"{'  ' * depth}{line + self.line_offset}: "
                f"[{role}] {branch.ntype} {condition}"
            )
        return "\n".join(rows) or "(분기 없음)"

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


def _loop_test(condition: str, lang: str) -> str:
    """계수형 반복문 머리에서 **판정절만** 남긴다.

    `for (i = 0; i < 11; i++)` 의 `0`·`11` 은 반복 제어 상수이지 업무 코드값이 아니다.
    이걸 조건 코드값으로 세면 "규칙이 이 값을 안 담았다"는 거짓 누락이 쏟아진다
    (실측: 미반영으로 잡힌 값의 다수가 루프 카운터였다).

    구분자는 언어 표에서 받는다. 그 문법이 없는 언어는 조건을 그대로 돌려준다.
    """
    sep = source_lexicon(lang).get("loop_header_parts") or ""
    if not sep:
        return condition
    parts = condition.split(sep)
    return parts[1] if len(parts) == 3 else condition


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


def build_inventory(
    code_body: str,
    control_subtree: Sequence[ControlSubtreeNode] | None,
    lang: str,
    split: SplitSource | None = None,
    declared_values: Sequence[str] = (),
    line_offset: int = 0,
) -> BranchInventory:
    """분기 대장을 만든다. ``control_subtree`` 가 없으면 분기 0으로 본다.

    ``declared_values`` 는 함수 **본문 밖**에서 선언된 상수의 값이다(``#define`` 등).
    프롬프트는 이 값을 증거 2순위로 인정하므로(``referenced_symbols`` 의
    ``immutable_constant.declared_value``) 근거 앵커도 같은 것을 인정해야 한다.
    본문만 앵커로 쓰면 정당하게 인용한 상수를 날조로 오판한다
    (실측: `#define ... "ZAPAE0021"` 을 인용한 규칙이 무근거로 잡혔다).
    """
    split = split or split_source(code_body, lang)
    all_branches: list[Branch] = []

    def convert(node: ControlSubtreeNode, parent: Branch | None) -> Branch:
        body = split.live(node.start, node.end)
        condition = condition_text(body, node.ntype, lang)
        tested = _loop_test(condition, lang)
        cond_values = _symbol_like(literal_values(tested, lang), body, lang)
        branch = Branch(
            node_id=node.node_id,
            ntype=node.ntype,
            start=node.start,
            end=node.end,
            parent=parent,
            condition=condition,
            cond_values=frozenset(cond_values),
            callees=_calls(body, lang) - _calls(condition, lang),
            effect_atoms=frozenset(atoms(body, lang)),
        )
        all_branches.append(branch)
        branch.children = [convert(child, branch) for child in node.children]
        return branch

    roots = tuple(convert(node, None) for node in (control_subtree or ()))
    declared = {value.strip().lower() for value in declared_values if value and value.strip()}
    declared |= {v for value in declared_values for v in literal_values(value or "", lang)}
    return BranchInventory(
        lang=lang,
        roots=roots,
        all_branches=tuple(all_branches),
        callees=_calls(split.live(), lang),
        source_values=frozenset(literal_values(split.live(), lang) | declared),
        line_offset=line_offset,
    )
