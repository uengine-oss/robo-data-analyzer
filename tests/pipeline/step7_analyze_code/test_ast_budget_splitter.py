"""spec 054 US3 — AST 예산 분할기 회귀 테스트.

검증: 작은 함수 미분할 / 예산 안 서브트리 통째 1조각 / 예산 초과 시 한 단계 재귀 /
if 조건+몸통(then+else) 연속 보존 / 조상 경로 전달.
"""
from pipeline.steps.step7_analyze_code.ast_budget_splitter import (
    SubtreeNode, approx_tokens, split,
)


def _lines(n: int) -> list[str]:
    # 각 줄 40자 고정 → 라인 수로 예산 계산이 예측 가능.
    return [f"line{i:03d}".ljust(40) for i in range(1, n + 1)]


def test_small_function_not_split():
    lines = _lines(40)
    top = [SubtreeNode("if1", "IF", 2, 8)]
    # 큰 예산 → 전체가 예산 안 → 분할 안 함
    assert split(1, 40, top, lines, budget_tokens=10_000) == []


def test_top_level_subtrees_each_one_fragment():
    lines = _lines(40)
    top = [
        SubtreeNode("if1", "IF", 2, 8, [SubtreeNode("else1", "ELSE", 5, 7)]),
        SubtreeNode("loop1", "LOOP", 10, 16),
        SubtreeNode("if2", "IF", 20, 28),
    ]
    # 각 top control ~7~9줄(≈80~104토큰). 예산 120 → 각각 단독 조각(묶으면 초과).
    frags = split(1, 40, top, lines, budget_tokens=120)
    assert len(frags) == 3, [(f.start_line, f.end_line) for f in frags]
    # if 조건(2)+몸통+else(7)가 한 조각에 연속 = [2,8]
    if_frag = frags[0]
    assert if_frag.start_line == 2 and if_frag.end_line == 8, "if-else 연속 범위 보존"
    # 조각들이 각 control 범위를 덮음
    ranges = {(f.start_line, f.end_line) for f in frags}
    assert (2, 8) in ranges and (10, 16) in ranges and (20, 28) in ranges


def test_oversized_subtree_recurses_one_level():
    lines = _lines(60)
    # LOOP(10-40)은 크고 그 안에 IF(12-20), IF(25-35) 자식. LOOP 단독이 예산 초과.
    loop = SubtreeNode("loop1", "LOOP", 10, 40, [
        SubtreeNode("if_a", "IF", 12, 20),
        SubtreeNode("if_b", "IF", 25, 35),
    ])
    top = [SubtreeNode("if0", "IF", 2, 8), loop]
    # LOOP(31줄≈354토큰) > 예산 150 → 자식 IF 로 재귀. 자식 IF(9~11줄≈100~126) < 150.
    frags = split(1, 60, top, lines, budget_tokens=150)
    # if0 는 통째, LOOP 는 자식 2개로 재귀
    ranges = [(f.start_line, f.end_line) for f in frags]
    assert (2, 8) in ranges, ranges
    assert (12, 20) in ranges and (25, 35) in ranges, "LOOP 자식으로 재귀"
    # LOOP 자식 조각들은 조상 경로에 LOOP 헤더를 가진다
    child_frags = [f for f in frags if (f.start_line, f.end_line) in ((12, 20), (25, 35))]
    assert all(any("LOOP" in a for a in f.ancestor_path) for f in child_frags), \
        "재귀 조각은 조상 LOOP 경로 보유"


def test_if_condition_and_body_not_split_apart():
    lines = _lines(30)
    # 단일 IF(2-25)가 예산 초과지만 자식이 없으면 통째로(조건+몸통 불가분, 못 쪼갬).
    top = [SubtreeNode("bigif", "IF", 2, 25)]
    frags = split(1, 30, top, lines, budget_tokens=50)
    assert len(frags) == 1
    assert frags[0].start_line == 2 and frags[0].end_line == 25


def test_approx_tokens_monotonic():
    assert approx_tokens("") == 0
    assert approx_tokens("x" * 35) == 10
