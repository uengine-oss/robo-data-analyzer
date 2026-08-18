"""구조화 Rule·Example 행 단위 결정론 채점기 (spec 116 4단계 — LLM 0).

정답지(분기표)와 모델 출력을 행 단위로 대조한다. 판정 원칙:

- **확실한 위반만 거절한다**: 행 echo 위반(창작·중복·할당 밖), 파이썬 문법 오류,
  닫힌 어휘 밖 함수, 소스에 없는 값(창작), 모순 AND, else_of 형식·참조 위반.
- **불일치 의심은 findings 로 표면화**한다(then ↔ 분기표 효과 대조 등) — 근거가
  확률적인 것을 거절 사유로 쓰지 않는다.
- 누락은 뺄셈이다: 할당 행 − 출력 행. 축퇴(행 1개만 출력)도 같은 뺄셈이 잡는다.

하네스(grade_py) 오탐 2종을 여기서 수리한다(py1 실측):
- ``else_of("IF@512")`` 인자를 리터럴 창작으로 오판 → else 참조는 형식·실재 검사로 분리.
- ``F("atoi(gc_Bank_Cd)")`` 같은 F() 안 함수식을 리터럴 검사 → F() 원문은 소스
  부분문자열 검사로 분리.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Iterable

from pipeline.models.code_analysis_output import RulesExamplesOutput, RuleItem
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.rule_expression import (
    COND_HELPERS,
    LeafSyntaxError,
    LeafVocabularyError,
    NL_MAX_CHARS,
    condition_call_names,
    parse_condition,
    parse_effect,
)
from pipeline.quality_gates.branch_inventory import BranchInventory
from pipeline.quality_gates.rule_prompt_evidence import expected_rule_condition
from pipeline.quality_gates.evidence_matching import (
    resolve_qualified_name,
    source_fragment_present,
)

_ELSE_REF = re.compile(r"^[A-Z_]+@(\d+)$")
_CONTROL_REF = re.compile(r"^[A-Z_]+@(\d+)$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 소수 포함 — `v_rate := 0.8` 의 0.8 을 0/8 로 쪼개 창작으로 오판하지 않게 (sql 시료 실측).
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
# 200자: 소스의 긴 문자열 값을 정당하게 인용해도 창작으로 오판하지 않게 (도메인
# 코드값은 보통 수 자라 실측 사고 0건이지만, 한도가 곧 오탐 경계라 넉넉히 둔다).
_QUOTED = re.compile(r"['\"]([^'\"\n]{1,200})['\"]")


# ── ast 정규화 — 표기 차이를 의미 동치로 접는다 ──────────────────────────


def normalize(node: ast.AST) -> ast.AST:
    """교환법칙 정렬·이중부정 해소·``not (a == b)`` → ``a != b``·비교 방향 통일.

    자식을 먼저 정규화하고 **교체 결과를 부모에 반영**한 뒤 자기 변환을 한다 —
    반영하지 않으면 ``not (not x)`` 에서 안쪽 부정이 먼저 접혀 의미가 뒤집힌다
    (반증 테스트로 잡힌 실결함).
    """
    for field_name, value in ast.iter_fields(node):
        if isinstance(value, list):
            setattr(
                node,
                field_name,
                [
                    normalize(item) if isinstance(item, ast.AST) else item
                    for item in value
                ],
            )
        elif isinstance(value, ast.AST):
            setattr(node, field_name, normalize(value))

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = node.operand
        if isinstance(inner, ast.UnaryOp) and isinstance(inner.op, ast.Not):
            return inner.operand
        if isinstance(inner, ast.Compare) and len(inner.ops) == 1:
            flip = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
                    ast.In: ast.NotIn, ast.NotIn: ast.In,
                    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
                    ast.Gt: ast.LtE, ast.LtE: ast.Gt}
            op_type = type(inner.ops[0])
            if op_type in flip:
                inner.ops = [flip[op_type]()]
                return inner
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        # 상수를 오른쪽으로 — `2 == F("x")` 와 `F("x") == 2` 를 같은 형태로.
        if isinstance(node.left, ast.Constant) and not isinstance(
            node.comparators[0], ast.Constant
        ):
            mirror = {ast.Eq: ast.Eq, ast.NotEq: ast.NotEq,
                      ast.Lt: ast.Gt, ast.Gt: ast.Lt,
                      ast.LtE: ast.GtE, ast.GtE: ast.LtE}
            op_type = type(node.ops[0])
            if op_type in mirror:
                node.left, node.comparators[0] = node.comparators[0], node.left
                node.ops = [mirror[op_type]()]
    if isinstance(node, ast.BoolOp):
        # 같은 연산자 중첩 평탄화 후 표기 순 정렬 (교환법칙).
        flat: list[ast.AST] = []
        for value in node.values:
            if isinstance(value, ast.BoolOp) and type(value.op) is type(node.op):
                flat.extend(value.values)
            else:
                flat.append(value)
        node.values = sorted(flat, key=ast.dump)
    return node


def canonical_form(expression: str) -> str:
    """표현식의 정규화 dump — 동치 비교의 열쇠."""
    return ast.dump(normalize(parse_condition(expression).body))


def conditions_equal(left: str, right: str) -> bool:
    return canonical_form(left) == canonical_form(right)


# ── 행 판정 ──────────────────────────────────────────────────────────────


@dataclass
class RowVerdict:
    """행 하나의 판정 — reasons 가 비어 있으면 통과."""

    anchor: int
    reasons: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)   # 거절 아닌 표면화

    @property
    def accepted(self) -> bool:
        return not self.reasons


@dataclass
class GradeReport:
    """함수(또는 배치) 하나의 채점 결과."""

    verdicts: list[RowVerdict]
    missing: list[int]            # 할당 행 − 출력 행 (누락·축퇴)
    fabricated_rows: list[int]    # 분기표·종료행 어디에도 없는 anchor
    duplicated_rows: list[int]
    out_of_assignment: list[int]  # 배칭 할당 밖 행

    @property
    def rejected(self) -> list[RowVerdict]:
        return [verdict for verdict in self.verdicts if not verdict.accepted]

    def failure_message(self) -> str:
        """실패 사유 — 행 번호·위반 내용을 빠짐없이 기록한다."""
        lines: list[str] = []
        rejected_anchors = {verdict.anchor for verdict in self.rejected}
        for verdict in self.rejected:
            for reason in verdict.reasons:
                lines.append(f"행 {verdict.anchor}: {reason}")
        for line in self.missing:
            if line in rejected_anchors:
                # 이미 구체 사유를 준 행이다. "누락됐다"고 다시 말하면 모순 신호가 되어
                # 모델이 무엇을 고쳐야 하는지 흐려진다(run 실측: 누락 메시지 878건 중
                # 상당수가 거절 행의 그림자였다).
                continue
            lines.append(f"행 {line}: 규칙이 누락됨 — 이 행의 규칙을 출력하라")
        for line in self.fabricated_rows:
            lines.append(f"행 {line}: 분기표에 없는 행 번호 — 출력하지 말라")
        for line in self.duplicated_rows:
            lines.append(f"행 {line}: 같은 행에 규칙이 중복됨 — 정확히 1개만")
        for line in self.out_of_assignment:
            lines.append(f"행 {line}: 이번 호출의 할당 밖 행 — 출력하지 말라")
        return "\n".join(lines)


def partition_rule_candidates(
    output: RulesExamplesOutput,
    report: GradeReport,
    *,
    assigned: set[int],
    evidence_rule: str,
) -> tuple[list[RuleItem], list[AnalysisFinding]]:
    """Split a grade report into trusted rules and explicit quarantined findings.

    This is the single producer boundary shared by framework and DBMS.  A candidate cannot
    be both unavailable and rejected, and duplicated coordinates can never leak into trusted
    downstream Rule/Example construction.
    """
    invalid_anchors = (
        set(report.fabricated_rows)
        | set(report.duplicated_rows)
        | set(report.out_of_assignment)
    )
    accepted: list[RuleItem] = []
    findings: list[AnalysisFinding] = []
    for rule, verdict in zip(output.rules, report.verdicts):
        candidate = rule.model_dump()
        if rule.anchor_line in invalid_anchors or verdict.reasons:
            reasons = list(verdict.reasons)
            if rule.anchor_line in report.duplicated_rows:
                reasons.append("같은 anchor_line 후보가 중복됨")
            if rule.anchor_line in report.fabricated_rows:
                reasons.append("분기표/활성 source에 없는 anchor_line")
            if rule.anchor_line in report.out_of_assignment:
                reasons.append("이번 호출에 할당되지 않은 anchor_line")
            findings.append(AnalysisFinding(
                concern="rules",
                status="rejected",
                target_id=f"R{rule.anchor_line}",
                coordinates=(rule.anchor_line,),
                reasons=tuple(reasons),
                candidate=candidate,
                evidence_rule=evidence_rule,
            ))
        elif verdict.findings:
            findings.append(AnalysisFinding(
                concern="rules",
                status="unverified",
                target_id=f"R{rule.anchor_line}",
                coordinates=(rule.anchor_line,),
                reasons=tuple(verdict.findings),
                candidate=candidate,
                evidence_rule=evidence_rule,
            ))
        elif rule.anchor_line in assigned:
            accepted.append(rule)

    candidate_anchors = {rule.anchor_line for rule in output.rules}
    for row in report.missing:
        if row in candidate_anchors:
            continue
        findings.append(AnalysisFinding(
            concern="rules",
            status="unavailable",
            target_id=f"R{row}",
            coordinates=(row,),
            reasons=("할당 행의 검증 통과 규칙이 없음",),
            evidence_rule=evidence_rule,
        ))

    accepted_anchors = [rule.anchor_line for rule in accepted]
    if len(accepted_anchors) != len(set(accepted_anchors)):
        raise RuntimeError("채점 불변식 위반: trusted 규칙 anchor가 중복됨")
    findings.sort(key=lambda finding: (finding.coordinates, finding.status, finding.target_id))
    return accepted, findings


def _unescape(value: str) -> str:
    """이스케이프 표기 차이를 접는다 — 소스 ``'\\0'`` 과 모델 ``'\\x00'`` 은 같은 값이다."""
    try:
        return value.encode("utf-8", "surrogatepass").decode("unicode_escape")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return value


def _source_value_pool(inventory: BranchInventory, source: str) -> set[str]:
    """정확한 소스 리터럴·선언값 풀(대소문자와 선행 0 보존)."""
    values: set[str] = set()
    quoted = [match.group(1) for match in _QUOTED.finditer(source)]
    values |= set(quoted)
    # 이스케이프를 푼 표기도 같은 값이다(소스 `'\0'` ↔ 모델 `'\x00'` — 실측 32건 오탐).
    values |= {_unescape(text) for text in quoted}
    # 포맷 문자열 안의 비영숫자 구분자는 독립 효과값이 될 수 있다
    # (소스 `"%s|%s"` 의 `'|'` — 실측 139건 오탐). 영숫자까지 잘라 넣으면
    # `"03"`을 근거로 잘못된 `"3"`도 통과하므로 허용하지 않는다.
    values |= {
        char for text in quoted for char in text
        if not char.isalnum() and not char.isspace()
    }
    values |= {match.group(0) for match in _NUMBER.finditer(source)}
    for declared in inventory.declared_values:
        values.add(declared)
        matches = [match.group(1) for match in _QUOTED.finditer(declared)]
        values.update(matches)
        values.update(_unescape(text) for text in matches)
    return values


def _string_constant_ok(value: str, pool: set[str], source: str) -> bool:
    """값은 정확 리터럴/선언 근거나 완전한 식별자 토큰일 때만 허용한다."""
    if not value:
        return True
    if value in pool:
        return True
    if _unescape(value) in pool:
        return True
    return bool(_IDENT.fullmatch(value) and source_fragment_present(value, source))


def _atomic_value_variants(values: Iterable[str]) -> set[str]:
    """외부 표현식 중 leaf 값으로 쓸 수 있는 원자 표기만 남긴다."""
    atoms: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if _IDENT.fullmatch(value) or _NUMBER.fullmatch(value):
            atoms.add(value)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            atoms.add(value[1:-1])
    return atoms


def _return_value_pool(inventory: BranchInventory, extra_values: Iterable[str]) -> set[str]:
    """ret의 문자열 인자는 실제 return 원자 또는 외부 근거 원자만 허용한다."""
    values = _atomic_value_variants(extra_values)
    for expression in inventory.return_expressions:
        value = expression.strip()
        if _IDENT.fullmatch(value) or _NUMBER.fullmatch(value):
            values.add(value)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            values.add(value[1:-1])
    return values


def _inventory_source_fragments(inventory: BranchInventory) -> set[str]:
    """Return exact parser facts that may be normalized across physical source lines."""
    fragments: set[str] = set()
    for row in inventory.table_rows():
        for key in ("test_condition", "condition"):
            value = str(row.get(key) or "").strip()
            if value:
                fragments.add(value)
        line = int(row["line"])
        _source, returns, assignments = inventory.direct_evidence(line)
        fragments.update(value.strip() for value in returns if value.strip())
        for target, value in assignments:
            if target.strip():
                fragments.add(target.strip())
            if value.strip():
                fragments.add(value.strip())
    fragments.update(
        branch.condition.strip()
        for branch in inventory.all_branches
        if branch.condition.strip()
    )
    fragments.update(_normalized_source_expression(value) for value in tuple(fragments))
    return fragments


def _check_leaf_tree(
    tree: ast.AST,
    *,
    pool: set[str],
    source: str,
    table_lines: set[int],
    callees: set[str],
    allowed_tables: set[str],
    read_tables: set[str] | None,
    write_tables: set[str] | None,
    table_ops: dict[str, str] | None,
    table_aliases: dict[str, str] | None,
    return_values: set[str],
    inventory_fragments: set[str],
    reasons: list[str],
) -> None:
    """cond/then/given 공용 leaf 검사 — 어휘·F() 실재·창작·else 참조·모순."""
    for node in ast.walk(tree):
        # 파이썬 if식 = 소스 삼항식 직역(교차 언어 실측 회귀 — JS 삼항식이 if식으로
        # 스며듦). 분기 논리가 leaf 에 숨으면 분기표 대조가 무력화된다 — 확실 위반.
        if isinstance(node, ast.IfExp):
            reasons.append(
                "파이썬 if식 금지 — 삼항식은 원문을 F()로 보존하라"
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name == "F":
                if (
                    len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    original = node.args[0].value
                    if (
                        original not in inventory_fragments
                        and _normalized_source_expression(original)
                        not in inventory_fragments
                        and not source_fragment_present(original, source)
                    ):
                        reasons.append(f'F({original!r}) 원문이 SOURCE에 없음')
                else:
                    reasons.append("F() 인자는 원문 문자열 1개여야 함")
            elif name == "else_of":
                if (
                    len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    reference = node.args[0].value
                    matched = _ELSE_REF.match(reference)
                    if not matched:
                        reasons.append(
                            f"else_of 참조 형식 위반: {reference!r} (예: IF@512)"
                        )
                    elif int(matched.group(1)) not in table_lines:
                        reasons.append(
                            f"else_of 가 분기표에 없는 행을 참조: {reference!r}"
                        )
                else:
                    reasons.append("else_of 인자는 행 참조 문자열 1개여야 함")
            elif name in ("after_control", "at_label"):
                if (
                    len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    reference = node.args[0].value
                    matched = _CONTROL_REF.match(reference)
                    if not matched:
                        reasons.append(
                            f"{name} 참조 형식 위반: {reference!r} "
                            "(예: IF@512)"
                        )
                    elif int(matched.group(1)) not in table_lines:
                        reasons.append(
                            f"{name} 이 분기표에 없는 구조행을 참조: "
                            f"{reference!r}"
                        )
                else:
                    reasons.append(
                        f"{name} 인자는 구조행 참조 문자열 1개여야 함"
                    )

    # 특수 의미 인자는 일반 값 풀이 아니라 각 결정론 증거와 대조한다.
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("F", "else_of", "after_control", "at_label")
            and node.args
        ):
            excluded.add(id(node.args[0]))
    if isinstance(tree, ast.Call) and isinstance(tree.func, ast.Name) and tree.args:
        effect = tree.func.id
        first = tree.args[0]
        first_value = first.value if isinstance(first, ast.Constant) else None
        if effect == "call" and isinstance(first_value, str):
            excluded.add(id(first))
            if first_value.casefold() not in callees:
                reasons.append(f"call 대상이 SOURCE의 정확한 callee가 아님: {first_value!r}")
        elif effect in {"select", "insert", "update", "delete", "upsert"} and isinstance(
            first_value, str
        ):
            excluded.add(id(first))
            resolved_table = resolve_qualified_name(
                first_value, allowed_tables, table_aliases,
            )
            if resolved_table is None:
                reasons.append(
                    f"{effect} 테이블이 결정론 R/W 집합에 없음: {first_value!r}"
                )
            else:
                direction_pool = read_tables if effect == "select" else write_tables
                if (
                    direction_pool is not None
                    and resolve_qualified_name(
                        first_value, direction_pool, table_aliases,
                    ) is None
                ):
                    direction = "READ" if effect == "select" else "WRITE"
                    reasons.append(
                        f"{effect} 테이블이 결정론 {direction} 집합에 없음: "
                        f"{first_value!r}"
                    )
                authoritative = str((table_ops or {}).get(resolved_table) or "").upper()
                expected_effect = {
                    "SELECT": "select",
                    "READ": "select",
                    "INSERT": "insert",
                    "UPDATE": "update",
                    "DELETE": "delete",
                    "MERGE": "upsert",
                }.get(authoritative)
                if expected_effect is not None and effect != expected_effect:
                    reasons.append(
                        f"{effect} 효과가 결정론 table op {authoritative}와 다름: "
                        f"{first_value!r}"
                    )
        elif effect == "ret" and isinstance(first_value, str):
            excluded.add(id(first))
            if first_value not in return_values:
                reasons.append(
                    f"ret 값이 실제 return 표현식/외부 근거에 없음: {first_value!r}"
                )
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in excluded
        ):
            if not _string_constant_ok(node.value, pool, source):
                reasons.append(f"소스에 없는 값(창작): {node.value!r}")

    # 모순 AND — 같은 F() 필드가 서로 다른 단일값과 동치 (OR→AND 반전 검출).
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            seen: dict[str, object] = {}
            for value in node.values:
                if (
                    isinstance(value, ast.Compare)
                    and len(value.ops) == 1
                    and isinstance(value.ops[0], ast.Eq)
                    and isinstance(value.left, ast.Call)
                    and isinstance(value.left.func, ast.Name)
                    and value.left.func.id == "F"
                    and value.left.args
                    and isinstance(value.left.args[0], ast.Constant)
                    and isinstance(value.comparators[0], ast.Constant)
                ):
                    field_name = value.left.args[0].value
                    constant = value.comparators[0].value
                    if field_name in seen and seen[field_name] != constant:
                        reasons.append(
                            f"모순 AND: {field_name!r} 가 {seen[field_name]!r} 와 "
                            f"{constant!r} 에 동시에 같을 수 없음 (OR 반전 의심)"
                        )
                    seen[field_name] = constant


def grade_rules(
    output: RulesExamplesOutput,
    inventory: BranchInventory,
    source: str,
    assigned: set[int] | None = None,
    extra_values: Iterable[str] = (),
    allowed_tables: Iterable[str] = (),
    read_tables: Iterable[str] | None = None,
    write_tables: Iterable[str] | None = None,
    table_ops: dict[str, str] | None = None,
    table_aliases: dict[str, str] | None = None,
    require_exact_conditions: bool = False,
) -> GradeReport:
    """행 단위 채점. ``assigned`` 는 배칭 시 이번 호출의 할당 행(절대 줄) 집합.

    ``extra_values`` 는 프롬프트에 증거로 주입한 값 문자열(callee 반환집합·객체 쓰기
    값·API 계약값 — spec 116 §19). 모델에게 준 증거를 "소스에 없는 창작"으로 오탐
    거절하지 않도록 창작 검사 풀에만 합류한다 — F() 필드 원문 검사는 소스 전용 불변.
    """
    rows = inventory.table_rows()
    rule_rows = {
        row["line"]
        for row in rows
        if row["role"] in ("decision", "complement", "entry", "continuation")
        or (row["role"] in ("exit", "handler") and not row.get("in_branch"))
    }
    # 분기 안 종료문 행 — anchor 로는 정당하지만 규칙 의무(누락 뺄셈 대상)는 아니다.
    optional_rows = {
        row["line"]
        for row in rows
        if row["role"] in ("exit", "handler") and row.get("in_branch")
    }
    table_lines = {row["line"] for row in rows}
    expected = set(assigned) if assigned is not None else set(rule_rows)
    pool = _source_value_pool(inventory, source)
    external_values = tuple(value for value in extra_values if value)
    pool.update(_atomic_value_variants(external_values))
    return_values = _return_value_pool(inventory, external_values)
    inventory_fragments = _inventory_source_fragments(inventory)
    callees = {name.casefold() for name in inventory.callees}
    table_names = {str(name) for name in allowed_tables}
    read_names = None if read_tables is None else {str(name) for name in read_tables}
    write_names = None if write_tables is None else {str(name) for name in write_tables}

    verdicts: list[RowVerdict] = []
    seen_anchors: set[int] = set()
    duplicated: list[int] = []
    fabricated_rows: list[int] = []
    out_of_assignment: list[int] = []

    for rule in output.rules:
        anchor = rule.anchor_line
        verdict = RowVerdict(anchor=anchor)
        source_row = next((row for row in rows if row["line"] == anchor), None)
        if anchor in seen_anchors:
            duplicated.append(anchor)
        seen_anchors.add(anchor)
        if anchor not in table_lines and anchor not in inventory.active_lines:
            fabricated_rows.append(anchor)
        elif assigned is not None and anchor not in expected and anchor in rule_rows:
            out_of_assignment.append(anchor)

        if source_row is not None:
            role = source_row.get("role")
            owner = inventory.at_line(anchor)
            is_post_test = (
                role == "decision"
                and owner is not None
                and owner.condition_timing == "post"
            )
            if role in ("decision", "complement") and not rule.cond and not is_post_test:
                verdict.reasons.append(f"{role} 행은 cond가 null일 수 없음")
            # A handler marker identifies entry into an exception block; the marker
            # itself is not necessarily an executable effect.  Requiring ``then``
            # here rejected correct model output such as "예외 처리 블록 진입".
            # Entry/exit rows, unlike handler markers, are created from direct
            # executable statements and still require their direct effect.
            if role in ("entry", "continuation", "exit") and not rule.then:
                verdict.reasons.append(f"{role} 행은 직접 효과 then이 비어 있을 수 없음")
            if role == "continuation" and source_row.get("unresolved_reachability"):
                verdict.findings.append(
                    "비구조 jump가 있는 routine의 continuation 도달성은 "
                    "구조 좌표만으로 완전 증명할 수 없음"
                )
            if require_exact_conditions:
                expected_cond = expected_rule_condition(inventory, anchor)
                if expected_cond is None:
                    if rule.cond is not None:
                        verdict.reasons.append(
                            "cond가 분기표의 무조건 경로와 다름"
                        )
                elif not rule.cond:
                    verdict.reasons.append(
                        "cond가 분기표의 정확한 구조 경로와 다름"
                    )
                else:
                    try:
                        matches = conditions_equal(rule.cond, expected_cond)
                    except LeafSyntaxError:
                        matches = False
                    if not matches:
                        verdict.reasons.append(
                            "cond가 분기표의 정확한 구조 경로와 다름"
                        )
        _grade_rule_content(
            rule,
            verdict,
            projected_from_semantic_frame=output.semantic_frame is not None,
            pool=pool,
            source=source,
            table_lines=table_lines,
            callees=callees,
            allowed_tables=table_names,
            read_tables=read_names,
            write_tables=write_names,
            table_ops=table_ops,
            table_aliases=table_aliases,
            return_values=return_values,
            inventory_fragments=inventory_fragments,
        )
        _compare_then_with_branch_callees(rule, verdict, rows)
        _compare_then_with_direct_effects(rule, verdict, inventory, source_row)
        verdicts.append(verdict)

    accepted_anchors = {
        verdict.anchor for verdict in verdicts if verdict.accepted
    }
    missing = sorted(
        line for line in expected
        if line not in accepted_anchors and line not in optional_rows
    )
    return GradeReport(
        verdicts=verdicts,
        missing=missing,
        fabricated_rows=sorted(set(fabricated_rows)),
        duplicated_rows=sorted(set(duplicated)),
        out_of_assignment=sorted(set(out_of_assignment)),
    )


def _compare_then_with_branch_callees(
    rule: RuleItem, verdict: RowVerdict, rows: list[dict]
) -> None:
    """then 의 call(...) ↔ 분기표 행의 직접호출(callees) 대조 — findings 축.

    거절이 아니라 표면화다: 분기 본문이 직접 부르지 않는 함수 호출을 then 에 쓴 것은
    소유권 위반 의심(다른 분기·callee 내부 복사)이지만, 근거가 확률적이라(같은 줄
    범위 밖 호출 표기 변형 등) 확실 위반 축과 분리한다.
    """
    row = next(
        (r for r in rows
         if r["line"] == rule.anchor_line and r["role"] in ("decision", "complement")),
        None,
    )
    if row is None:
        return
    callees = {str(name).lower() for name in (row.get("callees") or ())}
    for statement in rule.then:
        try:
            call = parse_effect(statement)
        except (LeafSyntaxError, LeafVocabularyError):
            continue          # 문법·어휘 위반은 거절 축이 이미 다룸
        if call.func.id != "call" or not call.args:
            continue
        first = call.args[0]
        name = getattr(first, "value", None)
        if isinstance(name, str) and name.lower() not in callees:
            verdict.findings.append(
                f"call({name!r}) 는 이 분기 본문의 직접호출 목록에 없음 — 소유권 확인"
            )


def _normalized_source_expression(value: str) -> str:
    value = " ".join(value.strip().rstrip(";").split())
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        encloses_all = False
        quote: str | None = None
        escaped = False
        for index, char in enumerate(value):
            if quote is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in "'\"":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    encloses_all = index == len(value) - 1
                    break
        if not encloses_all:
            break
        value = value[1:-1].strip()
    return value


def _effect_value_matches(node: ast.AST, source_expression: str) -> bool:
    source_value = _normalized_source_expression(source_expression)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "F"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return _normalized_source_expression(node.args[0].value) == source_value
    if not isinstance(node, ast.Constant):
        return False
    try:
        parsed_source = ast.literal_eval(source_value)
    except (SyntaxError, ValueError):
        parsed_source = object()
    if (
        parsed_source is None
        or isinstance(parsed_source, (str, int, float, bool))
    ):
        # The materializer emits Python scalar notation so system comparisons can
        # be semantic. Source spellings such as C ``0x00`` and Python ``0`` are
        # the same integer; opaque/non-Python expressions remain exact F leaves.
        return (
            type(node.value) is type(parsed_source)
            and node.value == parsed_source
        )
    if isinstance(node.value, str):
        if node.value == source_value:
            return True
        quoted = _QUOTED.fullmatch(source_value)
        return quoted is not None and _unescape(quoted.group(1)) == _unescape(node.value)
    return ast.unparse(node) == source_value


def _compare_then_with_direct_effects(
    rule: RuleItem,
    verdict: RowVerdict,
    inventory: BranchInventory,
    source_row: dict | None,
) -> None:
    """행별 return·대입 귀속을 증명하고 미지원 효과 의미는 격리한다."""
    if source_row is None:
        return
    direct_source, returns, assignments = inventory.direct_evidence(rule.anchor_line)
    targetless_ret = any(
        op == "ret" and not target
        for op, target in inventory.direct_statement_effects(rule.anchor_line)
    )
    parsed: list[ast.Call] = []
    for statement in rule.then:
        try:
            parsed.append(parse_effect(statement))
        except (LeafSyntaxError, LeafVocabularyError):
            continue

    ret_calls = [call for call in parsed if call.func.id == "ret"]
    if (returns or targetless_ret) and not ret_calls:
        verdict.reasons.append("이 행의 직접 return 효과가 then에서 누락됨")
    for call in ret_calls:
        is_targetless = (
            bool(call.args)
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value is None
        )
        if is_targetless and targetless_ret:
            continue
        if not returns:
            verdict.reasons.append("ret 효과가 이 행의 직접 source에 없음")
        elif not any(_effect_value_matches(call.args[0], value) for value in returns):
            verdict.reasons.append(
                f"ret 값이 이 행의 직접 return 표현식과 다름: {sorted(returns)}"
            )

    set_calls = [call for call in parsed if call.func.id == "set"]
    matched_targets: set[str] = set()
    for call in set_calls:
        target = call.args[0].args[0].value
        matching = [
            (left, right) for left, right in assignments
            if source_fragment_present(target, left)
        ]
        if matching:
            matched_targets.add(target)
            if not any(_effect_value_matches(call.args[1], right) for _, right in matching):
                verdict.reasons.append(
                    f"set({target!r}) 우변이 이 행의 직접 대입과 다름: "
                    f"{[right for _, right in matching]}"
                )
        elif source_fragment_present(target, direct_source):
            verdict.findings.append(
                f"set({target!r}) 는 직접 source에 있으나 대입/복사 의미를 결정론으로 증명할 수 없음"
            )
        else:
            verdict.reasons.append(f"set 대상 {target!r} 이 이 행의 직접 source에 없음")

    for left, _right in assignments:
        if not any(source_fragment_present(target, left) for target in matched_targets):
            verdict.findings.append(f"이 행의 직접 대입 효과가 then에서 누락됨: {left}")

    for call in parsed:
        if call.func.id in {"select", "insert", "update", "delete", "upsert"}:
            table = call.args[0].value
            if not source_fragment_present(table, direct_source):
                verdict.findings.append(
                    f"{call.func.id}({table!r}) 를 이 행의 직접 source에서 증명할 수 없음"
                )
        elif call.func.id == "raise_error":
            verdict.findings.append(
                "raise_error 의미는 이 행의 직접 source와 수동/LLM 의미 대조 필요"
            )


def _grade_rule_content(
    rule: RuleItem,
    verdict: RowVerdict,
    *,
    projected_from_semantic_frame: bool,
    pool: set[str],
    source: str,
    table_lines: set[int],
    callees: set[str],
    allowed_tables: set[str],
    read_tables: set[str] | None,
    write_tables: set[str] | None,
    table_ops: dict[str, str] | None,
    table_aliases: dict[str, str] | None,
    return_values: set[str],
    inventory_fragments: set[str],
) -> None:
    if rule.cond is not None and "\n" in rule.cond:
        verdict.reasons.append("cond는 한 줄 Python 표현식이어야 함")
    if any("\n" in statement for statement in rule.then):
        verdict.reasons.append("then 효과는 각각 한 줄 Python 표현식이어야 함")
    if "\n" in rule.nl or (
        not projected_from_semantic_frame and len(rule.nl) > NL_MAX_CHARS
    ):
        verdict.reasons.append(
            f"nl은 개행 없는 {NL_MAX_CHARS}자 이하 한 문장이어야 함"
        )
    if rule.cond:
        try:
            tree = parse_condition(rule.cond)
            if isinstance(tree.body, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
                verdict.reasons.append(
                    "cond는 collection이 아니라 단일 Python boolean 표현식이어야 함"
                )
            outside = condition_call_names(tree) - set(COND_HELPERS)
            if outside:
                verdict.reasons.append(
                    f"cond 어휘 밖 함수 {sorted(outside)} — 허용: {list(COND_HELPERS)}"
                )
            _check_leaf_tree(
                tree, pool=pool, source=source,
                table_lines=table_lines, callees=callees,
                allowed_tables=allowed_tables, read_tables=read_tables,
                write_tables=write_tables, table_ops=table_ops,
                table_aliases=table_aliases,
                return_values=return_values,
                inventory_fragments=inventory_fragments,
                reasons=verdict.reasons,
            )
        except LeafSyntaxError as error:
            verdict.reasons.append(str(error))
    for statement in rule.then:
        try:
            call = parse_effect(statement)
            _check_leaf_tree(
                call, pool=pool, source=source,
                table_lines=table_lines, callees=callees,
                allowed_tables=allowed_tables, read_tables=read_tables,
                write_tables=write_tables, table_ops=table_ops,
                table_aliases=table_aliases,
                return_values=return_values,
                inventory_fragments=inventory_fragments,
                reasons=verdict.reasons,
            )
        except (LeafSyntaxError, LeafVocabularyError) as error:
            verdict.reasons.append(str(error))
    # 예시는 채점 대상이 아니다 — LLM 이 내지 않고 cond/then 에서 결정론 합성하므로
    # (spec 116 §21) 검사할 모델 산출물이 없다. 합성 결과의 정합은 합성기 테스트가 진다.
