"""구조화 Rule·Example 파이썬 leaf 어휘의 단일 정의처 (spec 116, 헌법 III).

cond/then 문자열 leaf 가 쓸 수 있는 함수 어휘와 형태 제약(regex pattern)을 여기서만
정의한다. Pydantic 스키마(형태 강제)·프롬프트(어휘 안내)·채점기(ast 후검증)가 전부
이 모듈을 import 한다 — 세 곳의 어휘가 갈라지면 드리프트 테스트가 잡는다.

형태와 의미의 분업:
- **스키마(pattern)** 는 개행·길이만 막는다 — xgrammar 가 강제하는 결정론 끝 경계
  (폭주 방어). 파이썬 문법 검증을 regex 로 하려 들지 않는다.
- **ast 후검증** 은 채점기의 의미 책임이다. schema는 구조·좌표를 강제하고, AST는
  파이썬 문법·폐쇄 어휘·효과 shape를 행별로 검증한다. 틀린 모델 후보는 finding으로
  격리되고 독립적으로 검증된 행은 보존된다.
"""
from __future__ import annotations

import ast

# then 효과 호출문의 닫힌 어휘 — 이 밖의 최상위 호출은 채점기가 거절한다.
# jump: 이름 있는 제어 이동(goto 류), exit_loop/commit/rollback: target 없는 control.
# ontology statement_effect_ops 가 생산을 선언한다.
EFFECT_FUNCTIONS: tuple[str, ...] = (
    "call", "ret", "set", "raise_error", "jump", "exit_loop", "commit", "rollback",
    "select", "insert", "update", "delete", "upsert",
)

# cond 안의 기본 도우미 호출. ontology가 source 의미와 AST 동등성을 증명한 canonical
# intrinsic(예: strlen → len)은 채점기가 실행별로 이 집합에 더한다.
COND_HELPERS: tuple[str, ...] = (
    "F", "else_of", "after_control", "at_label", "len",
)

# 구조 leaf의 형태 제약. Rule cond/then과 EXAMPLE은 parser-owned facts에서 결정론 생성되며,
# source predicate나 statement를 임의 길이에서 자르거나 버리면 의미가 달라진다. 과거
# LLM-generated Example의 폭주를 막던 별도 500자 상한은 Example이 LLM 0이 된 뒤에는
# 정확한 조건을 조용히 누락시키므로 적용하지 않는다.
RULE_LEAF_PATTERN = r"^[^\n]+$"
EXAMPLE_LEAF_PATTERN = RULE_LEAF_PATTERN


class LeafSyntaxError(ValueError):
    """leaf 가 파이썬 표현식으로 파싱되지 않음 — 행 단위 거절 사유."""


class LeafVocabularyError(ValueError):
    """leaf 가 닫힌 어휘 밖의 함수를 호출함 — 행 단위 거절 사유."""


def parse_condition(cond: str) -> ast.Expression:
    """cond leaf → ast. 문법 오류는 LeafSyntaxError 로 행 단위 거절 사유가 된다."""
    try:
        return ast.parse(cond, mode="eval")
    except SyntaxError as error:
        raise LeafSyntaxError(f"cond 파이썬 파싱 실패: {error}") from error


def parse_effect(statement: str) -> ast.Call:
    """then 효과 호출문 → 계약을 만족하는 ``ast.Call``.

    최상위 함수명만 확인하면 ``call("fn", invented)`` 나
    ``set(F("x"), atoi(F("y")))`` 같은 계약 밖 표현이 통과한다. 효과마다 인자 수와
    AST 형태를 여기서 함께 고정해 prompt·grader·serializer가 한 문법을 쓴다.
    복잡한 원문 식은 부분 AST로 재작성하지 않고 ``F("원문 식")`` 하나로 보존한다.
    """
    try:
        tree = ast.parse(statement, mode="eval")
    except SyntaxError as error:
        raise LeafSyntaxError(f"then 파이썬 파싱 실패: {error}") from error
    node = tree.body
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        raise LeafVocabularyError(f"then 은 효과 함수 호출문이어야 한다: {statement!r}")
    if node.func.id not in EFFECT_FUNCTIONS:
        raise LeafVocabularyError(
            f"then 어휘 밖 함수 {node.func.id!r} — 허용: {EFFECT_FUNCTIONS}"
        )
    if node.keywords:
        raise LeafVocabularyError(
            f"then 효과 {node.func.id} 에 키워드 인자를 사용할 수 없음"
        )
    _validate_effect_arguments(node, statement)
    return node


def _is_string(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_f_reference(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "F"
        and not node.keywords
        and len(node.args) == 1
        and _is_string(node.args[0])
    )


def _is_scalar(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and (
        node.value is None or isinstance(node.value, (str, int, float, bool))
    )


def _validate_effect_arguments(node: ast.Call, statement: str) -> None:
    if any(isinstance(part, ast.IfExp) for part in ast.walk(node)):
        raise LeafVocabularyError(
            "파이썬 if식 금지 — 삼항식은 원문 전체를 F()로 보존하라"
        )
    name = node.func.id
    args = node.args
    if name == "call":
        valid = (
            len(args) == 3
            and _is_string(args[0])
            and (
                isinstance(args[1], ast.Constant) and args[1].value is None
                or _is_f_reference(args[1])
            )
            and isinstance(args[2], ast.List)
            and all(_is_f_reference(item) for item in args[2].elts)
        )
        expected = 'call("함수명", F("수신자")/None, [F("인자"), ...])'
    elif name == "jump":
        valid = len(args) == 1 and _is_string(args[0])
        expected = 'jump("라벨명")'
    elif name == "ret":
        valid = len(args) == 1 and (_is_scalar(args[0]) or _is_f_reference(args[0]))
        expected = 'ret("값") 또는 ret(F("원문 반환식"))'
    elif name == "set":
        valid = (
            len(args) == 2
            and _is_f_reference(args[0])
            and (_is_scalar(args[1]) or _is_f_reference(args[1]))
        )
        expected = 'set(F("대상"), "값") 또는 set(F("대상"), F("원문 우변식"))'
    elif name == "raise_error":
        # 오류 발생이라는 의미만 보존한다. 언어/프레임워크마다 메시지 ID가 없거나
        # 포맷 문자열뿐인 경우가 있어 텍스트 인자는 요약·창작을 강요한다.
        valid = not args
        expected = "raise_error()"
    elif name in {"exit_loop", "commit", "rollback"}:
        valid = not args
        expected = f"{name}()"
    else:  # select / insert / update / delete / upsert
        valid = len(args) == 1 and _is_string(args[0])
        expected = f'{name}("테이블")'
    if not valid:
        raise LeafVocabularyError(
            f"then 효과 인자 계약 위반: {statement!r} — 허용 형태: {expected}"
        )


def condition_call_names(tree: ast.AST) -> set[str]:
    """cond ast 안의 이름 호출 집합 — 기본 helper+증명된 intrinsic 어휘 검사용."""
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
