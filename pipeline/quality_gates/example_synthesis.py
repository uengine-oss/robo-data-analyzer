"""Rule → Example 결정론 합성 (spec 116 §21 — LLM 0, 헌법 I).

전체 run 실측(2026-08-03)이 판정 근거다: LLM 이 낸 EXAMPLE 3,319건 중 **71.9% 는
cond/then 의 기계적 재진술**(새 정보 0)이었고, "새 정보"로 분류된 나머지도 실제로는
① ``else_of`` 여집합의 부정(형제 규칙이 이미 아는 사실)이거나 ② 소스에 없는 값을
지어낸 것(창작 — 우리가 금지한 바로 그것)이었다. 결정론으로 되는 일을 LLM 에 시키는
것은 결함이라는 헌법 I 에 따라 코드가 만든다.

합성 규칙(언어·도메인 무관):

- ``cond`` 가 없으면(무조건 규칙) 예시도 없다 — 기존 계약 그대로.
- satisfy: cond 를 만족시키는 **하나의 경로**를 등식 목록으로 편다.
  AND 는 모든 항, OR 는 첫 항, ``else_of("IF@N")`` 은 그 행 규칙 cond 의 부정.
- 효과는 규칙의 ``then`` 을 그대로 쓴다 — 예시가 규칙에 없는 정책을 추가하지 않는다.

여기서 나오는 leaf 는 전부 이미 채점을 통과한 cond 에서 파생되므로 창작이 될 수 없다.
"""
from __future__ import annotations

import ast

_ELSE_OF = "else_of"
# 부정 시 뒤집는 비교 — 그 외 형태는 not(...) 로 감싼다.
_FLIP = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}


def _row_reference(node: ast.AST) -> int | None:
    """``else_of("IF@512")`` → 512. 그 형태가 아니면 None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return None
    if node.func.id != _ELSE_OF or not node.args:
        return None
    argument = node.args[0]
    if not (isinstance(argument, ast.Constant) and isinstance(argument.value, str)):
        return None
    _, _, line = argument.value.partition("@")
    return int(line) if line.isdigit() else None


def _negate(expression: str) -> str:
    """표현식의 부정 — 단일 비교는 연산자를 뒤집고, 그 외는 not(...) 로 감싼다."""
    try:
        tree = ast.parse(expression, mode="eval").body
    except SyntaxError:
        return f"not ({expression})"
    if isinstance(tree, ast.Compare) and len(tree.ops) == 1:
        flipped = _FLIP.get(type(tree.ops[0]))
        if flipped is not None:
            tree.ops = [flipped()]
            return ast.unparse(tree)
    if isinstance(tree, ast.UnaryOp) and isinstance(tree.op, ast.Not):
        return ast.unparse(tree.operand)
    return f"not ({expression})"


def _split_inherited(node: ast.AST) -> tuple[list[ast.AST], list[ast.AST]]:
    """조건을 (물려받은 여집합 문맥, 그 행 자신의 조건)으로 가른다.

    ``else_of("IF@512") and (x == 1)`` 은 "512 를 지나온 상태에서 x == 1" 이다.
    else-if 체인의 다음 행은 앞 문맥을 **그대로 물려받고** 자기 조건만 부정한다.
    """
    values = node.values if isinstance(node, ast.BoolOp) and isinstance(
        node.op, ast.And
    ) else [node]
    inherited = [value for value in values if _row_reference(value) is not None]
    own = [value for value in values if _row_reference(value) is None]
    return inherited, own


def _resolved_tree(
    reference: int, cond_by_anchor: dict[int, str]
) -> ast.AST | None:
    referenced = (cond_by_anchor.get(reference) or "").strip()
    if not referenced:
        return None
    try:
        return ast.parse(referenced, mode="eval").body
    except SyntaxError:
        return None


def _ordered_unique(terms: list[str]) -> list[str]:
    """Preserve semantic order while collapsing inherited path facts."""
    return list(dict.fromkeys(terms))


def _satisfy_terms(
    node: ast.AST,
    cond_by_anchor: dict[int, str],
    visiting: set[int],
    memo: dict[tuple[str, int], tuple[str, ...]],
) -> list[str]:
    """이 조건을 **참**으로 만드는 한 경로의 항 목록 (항들의 AND)."""
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            terms: list[str] = []
            for value in node.values:
                terms.extend(_satisfy_terms(value, cond_by_anchor, visiting, memo))
            return _ordered_unique(terms)
        # OR 는 한 항만 참이면 된다 — 첫 항을 대표 시나리오로 쓴다.
        return (
            _satisfy_terms(node.values[0], cond_by_anchor, visiting, memo)
            if node.values else []
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _falsify_terms(node.operand, cond_by_anchor, visiting, memo)

    reference = _row_reference(node)
    if reference is not None:
        # 여집합 행에 **도달**하는 조건 = 앞 문맥은 그대로 성립 + 짝 조건만 거짓.
        # NOT(A and B) 를 (not A and not B) 로 접으면 드모르간 위반이라 정반대 분기가
        # 나온다(합성 검수에서 실제로 잡힌 결함).
        if reference in visiting:
            return []          # 순환 — 사실을 만들 수 없다(지어내기 금지)
        cache_key = ("satisfy", reference)
        cached = memo.get(cache_key)
        if cached is not None:
            return list(cached)
        tree = _resolved_tree(reference, cond_by_anchor)
        if tree is None:
            return []
        seen = visiting | {reference}
        inherited, own = _split_inherited(tree)
        terms: list[str] = []
        for value in inherited:
            terms.extend(_satisfy_terms(value, cond_by_anchor, seen, memo))
        if own:
            first = own[0] if len(own) == 1 else ast.BoolOp(op=ast.And(), values=own)
            terms.extend(_falsify_terms(first, cond_by_anchor, seen, memo))
        result = tuple(_ordered_unique(terms))
        memo[cache_key] = result
        return list(result)

    return [ast.unparse(node)]


def _falsify_terms(
    node: ast.AST,
    cond_by_anchor: dict[int, str],
    visiting: set[int],
    memo: dict[tuple[str, int], tuple[str, ...]],
) -> list[str]:
    """이 조건을 **거짓**으로 만드는 항 목록 (항들의 AND) — 드모르간 정확 적용."""
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.Or):
            # NOT(A or B) = (NOT A) and (NOT B) — 전부 부정해야 한다.
            terms: list[str] = []
            for value in node.values:
                terms.extend(_falsify_terms(value, cond_by_anchor, visiting, memo))
            return _ordered_unique(terms)
        # NOT(A and B) = (NOT A) or (NOT B) — 하나만 깨면 거짓이다.
        return (
            _falsify_terms(node.values[0], cond_by_anchor, visiting, memo)
            if node.values else []
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _satisfy_terms(node.operand, cond_by_anchor, visiting, memo)

    reference = _row_reference(node)
    if reference is not None:
        # 여집합이 거짓 = 그 짝 분기가 실제로 성립한 경우다.
        if reference in visiting:
            return []
        cache_key = ("falsify", reference)
        cached = memo.get(cache_key)
        if cached is not None:
            return list(cached)
        tree = _resolved_tree(reference, cond_by_anchor)
        if tree is None:
            return []
        result = tuple(_ordered_unique(
            _satisfy_terms(tree, cond_by_anchor, visiting | {reference}, memo)
        ))
        memo[cache_key] = result
        return list(result)

    return [_negate(ast.unparse(node))]


def _fits(term: str) -> bool:
    # Example은 검증된 Rule truth의 결정론 projection이다. 길이로 버리면 source의 긴
    # opaque predicate가 조건+outcome을 가졌는데도 Example만 조용히 사라진다.
    return bool(term) and "\n" not in term


def synthesize_examples(rules) -> dict[int, list[dict]]:
    """anchor → 예시 dict 목록. 규칙 목록 하나(한 함수·한 배치)를 그대로 받는다."""
    condition_by_anchor = {
        rule.anchor_line: rule.business_condition
        for rule in rules or ()
        if getattr(rule, "anchor_line", None) is not None
    }
    synthesized: dict[int, list[dict]] = {}
    # One routine is one acyclic row-reference graph. Reusing resolved row facts avoids
    # re-walking every inherited else-if prefix for every later rule.
    memo: dict[tuple[str, int], tuple[str, ...]] = {}
    for rule in rules or ():
        anchor = rule.anchor_line
        cond = (rule.business_condition or "").strip()
        effects = [str(effect) for effect in (rule.business_effects or ())]
        # An Example must demonstrate an observable rule effect. A condition-only row is still
        # a valid RULE/control fact, but an empty outcome creates a misleading scenario node.
        if not cond or not effects:
            synthesized[anchor] = []
            continue
        try:
            tree = ast.parse(cond, mode="eval").body
        except SyntaxError:
            synthesized[anchor] = []      # 문법 위반은 채점 축이 다룬다
            continue
        terms = [
            term
            for term in _ordered_unique(
                _satisfy_terms(tree, condition_by_anchor, {anchor}, memo)
            )
            if _fits(term)
        ]
        if not terms:
            synthesized[anchor] = []
            continue
        synthesized[anchor] = [
            {
                "given_conditions": terms,
                "expected_effects": effects,
            },
        ]
    return synthesized
