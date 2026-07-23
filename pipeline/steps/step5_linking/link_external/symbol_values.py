"""코드가 내놓는 확정 문자열 — step2 의 REFERENCES/INIT_BY 를 그대로 소비한다 (spec 081).

"이 루틴이 어느 상수를 쓰는가"와 "그 상수가 어느 상수로부터 만들어지는가"는 step2 의
``reference_linker`` 가 이미 결정론으로 계산해 REFERENCES·INIT_BY 엣지로 내놓는다.
여기서 이름을 다시 찾지 않는다 — 같은 판단이 두 곳에 살면 한쪽만 고쳐져 어긋난다.

이 모듈이 남기는 책임은 **값 계산** 하나다: 확정된 상수 노드의 초기화식을 접어
단일 문자열을 만든다. 모든 피연산자가 확정 문자열일 때만 값을 내고, 하나라도
실행해야 아는 값이면 즉시 포기한다(과대근사 금지).

지역 변수는 step2 가 노드로 만들지 않으므로 이것만 본문에서 직접 읽는다.
"""
from __future__ import annotations

import re

from graph.schema.graph_edges import InitBy, References
from graph.schema.graph_nodes import Constant, Variable
from pipeline.steps.step5_linking.link_external.key_index import quoted_literals


_MAX_FOLD_DEPTH = 4
_MAX_VALUE_LENGTH = 240
_PLAIN_NAME = re.compile(r"[A-Za-z_]\w*")
_QUALIFIED_NAME = re.compile(r"(?:[A-Za-z_]\w*\.)+([A-Za-z_]\w*)")

# 지역 선언·대입은 언어마다 이름 위치가 다르므로 두 갈래로 읽는다.
#   Java/C   ``String k = "x";``               → ``=`` 바로 앞이 이름
#   PL/SQL   ``k CONSTANT VARCHAR2(60) := 'x';`` → 줄 첫 식별자가 이름, 뒤에 타입, 그다음 ``:=``
_ASSIGNMENT_PATTERNS = (
    re.compile(r"(?<![\w.])(?P<name>[A-Za-z_]\w*)[ \t]*=(?!=)[ \t]*(?P<value>[^;\n]+)"),
    re.compile(r"^[ \t]*(?P<name>[A-Za-z_]\w*)\b[^;\n]*?:=[ \t]*(?P<value>[^;\n]+)", re.MULTILINE),
)


def _immutable_variable(variable: Variable) -> bool:
    markers = f"{variable.label} {variable.annotations}".upper()
    if "FINAL" in markers or "IMMUTABLE" in markers or "CONSTANT" in markers:
        return True
    return bool(re.search(r"\b(?:final|const)\b", variable.code_text, re.IGNORECASE))


def _operands(expression: str) -> list[str] | None:
    """``a + b + …`` 를 피연산자로 나눈다. 문자열 안의 ``+`` 는 나누지 않는다."""
    body = expression.strip().rstrip(";").strip()
    if not body:
        return None
    masked = list(body)
    for occurrence in quoted_literals(body):
        for index in range(occurrence.start, occurrence.end):
            masked[index] = "\x00"
    parts: list[str] = []
    start = 0
    for index, char in enumerate(masked):
        if char == "+":
            parts.append(body[start:index])
            start = index + 1
    parts.append(body[start:])
    return [part.strip() for part in parts]


class SymbolTable:
    """step2 가 확정한 심볼 관계 위에서 값만 계산한다."""

    def __init__(
        self,
        variables: list[Variable],
        constants: list[Constant],
        references: list[References] | None = None,
        init_by: list[InitBy] | None = None,
    ) -> None:
        self._expression: dict[str, str] = {}
        self._name: dict[str, str] = {}
        for constant in constants:
            self._expression[constant.id] = constant.value
            self._name[constant.id] = constant.name
        for variable in variables:
            if _immutable_variable(variable):
                self._expression[variable.id] = variable.init_value
                self._name[variable.id] = variable.name
        # REFERENCES: 루틴 → 그 루틴이 쓰는 변수/상수. step2 가 import 범위까지 해석해 둔 사실.
        self._used_by_routine: dict[str, dict[str, str]] = {}
        for edge in references or []:
            name = self._name.get(edge.target_id)
            if name is not None:
                self._used_by_routine.setdefault(edge.source_id, {})[name] = edge.target_id
        # INIT_BY: 상수 → 정의 시 참조하는 다른 상수. 폴딩의 의존 관계가 이미 여기 있다.
        self._origins: dict[str, dict[str, str]] = {}
        for edge in init_by or []:
            name = self._name.get(edge.source_id)
            if name is not None:
                self._origins.setdefault(edge.from_id, {})[name] = edge.source_id
        self._folded: dict[str, str | None] = {}

    def node_for(self, routine_id: str, name: str) -> str | None:
        """이 루틴이 쓰는 이름 → 그 심볼 노드 id. step2 가 확정한 것만 답한다."""
        return self._used_by_routine.get(routine_id, {}).get(name)

    def value_of_node(self, node_id: str, depth: int = 0) -> str | None:
        """심볼 노드의 초기화식을 접어 확정 문자열로. 확정 불가면 None."""
        if depth > _MAX_FOLD_DEPTH:
            return None
        if node_id in self._folded:
            return self._folded[node_id]
        expression = self._expression.get(node_id)
        if expression is None:
            return None
        self._folded[node_id] = None            # 자기참조 순환 차단
        value = self._fold(expression, node_id, depth)
        self._folded[node_id] = value
        return value

    def _fold(self, expression: str, node_id: str, depth: int) -> str | None:
        operands = _operands(expression)
        if not operands:
            return None
        pieces: list[str] = []
        origins = self._origins.get(node_id, {})
        for operand in operands:
            if not operand:
                return None
            literals = quoted_literals(operand)
            if len(literals) == 1 and literals[0].start == 0 and literals[0].end == len(operand):
                pieces.append(literals[0].value)
                continue
            qualified = _QUALIFIED_NAME.fullmatch(operand)
            plain = _PLAIN_NAME.fullmatch(operand)
            if qualified is None and plain is None:
                return None                     # 호출·연산·리터럴 혼합 — 확정 불가
            name = qualified.group(1) if qualified else operand
            origin = origins.get(name)          # INIT_BY 가 지목한 출처 노드
            if origin is None:
                return None
            value = self.value_of_node(origin, depth + 1)
            if value is None:
                return None
            pieces.append(value)
        folded = "".join(pieces)
        return folded if 0 < len(folded) <= _MAX_VALUE_LENGTH else None

    @staticmethod
    def local_literal_values(code_text: str) -> dict[str, str]:
        """루틴 본문 안에서 리터럴 하나로만 정해지는 지역 이름들.

        step2 는 지역 변수를 노드로 만들지 않으므로 이것만 본문에서 직접 읽는다.
        같은 이름에 대입이 두 번 이상이면 값이 하나로 확정되지 않으므로 버린다.
        """
        assignments: dict[str, list[str | None]] = {}
        for pattern in _ASSIGNMENT_PATTERNS:
            for match in pattern.finditer(code_text):
                name, expression = match.group("name"), match.group("value").strip()
                literals = quoted_literals(expression)
                value = None
                if literals and literals[0].start == 0:
                    remainder = expression[literals[0].end:].strip().rstrip(";,)").strip()
                    if not remainder:
                        value = literals[0].value
                assignments.setdefault(name, []).append(value)
        return {
            name: values[0]
            for name, values in assignments.items()
            if len(values) == 1 and values[0] is not None and 0 < len(values[0]) <= _MAX_VALUE_LENGTH
        }
