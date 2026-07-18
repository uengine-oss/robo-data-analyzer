"""spec 054 US3 — 결정론 품질검사기 회귀 테스트.

load_target_orders(보존 run: 60개 중 53 반복, 동일 의미 24회, Rule 2개 동일)를 재현해
이상 감지되는지, 정상(고유 21~22)은 통과하는지 검증한다.
"""
from pipeline.models.code_analysis_output import (
    AffectedTable, ExampleItem, RuleItem, RulesExamplesOutput,
)
from pipeline.steps.step6_analyze_code.rule_quality import (
    check_rule_quality, deterministic_dedupe,
)


def _ex(given, when="대상 판정", then="제외", tables=None):
    ats = [AffectedTable(table=t, op="READ") for t in (tables or [])]
    return ExampleItem(given=given, when=when, then=then, affected_tables=ats)


def _rule(stmt, examples):
    return RuleItem(statement=stmt, examples=examples)


def test_clean_result_not_anomaly():
    # 고유 Example 5개 — dup 0, max_group 1
    exs = [_ex(f"상태={s}", then=f"결과{s}") for s in ("A", "B", "C", "D", "E")]
    out = RulesExamplesOutput(rules=[_rule("상태별 처리", exs)])
    r = check_rule_quality(out)
    assert not r.is_anomaly, r.reasons
    assert r.dup_ratio == 0.0 and r.max_group == 1


def test_load_target_orders_repetition_anomaly():
    # 동일 의미("결제내역 없음→제외") 24회 + 성공/취소 대표 각 1 = 26개, 24 중복
    dup = [_ex("결제내역 없음") for _ in range(24)]
    uniq = [_ex("결제 성공", then="대상 추가"), _ex("주문 취소", then="제외")]
    out = RulesExamplesOutput(rules=[_rule("결제 상태별 대상 선별", dup + uniq)])
    r = check_rule_quality(out)
    assert r.is_anomaly
    assert r.max_group == 24
    assert r.dup_ratio > 0.5
    assert any("반복" in x for x in r.reasons)


def test_duplicate_rule_statement_anomaly():
    # Rule 2개가 사실상 동일 statement
    out = RulesExamplesOutput(rules=[
        _rule("결제 누락이면 대상에서 제외한다", [_ex("결제 없음")]),
        _rule("결제 누락이면 대상에서 제외한다", [_ex("결제 없음2", then="보류")]),
    ])
    r = check_rule_quality(out)
    assert r.is_anomaly
    assert r.duplicate_rule_statements >= 1


def test_cap_reached_anomaly():
    exs = [_ex(f"입력{i}", then=f"결과{i}") for i in range(80)]  # 캡 도달, 전부 고유
    out = RulesExamplesOutput(rules=[_rule("대량 매핑", exs)])
    r = check_rule_quality(out)
    assert r.is_anomaly
    assert r.cap_reached
    assert any("캡" in x for x in r.reasons)


def test_empty_not_anomaly():
    out = RulesExamplesOutput(rules=[])
    r = check_rule_quality(out)
    assert not r.is_anomaly
    assert r.total_examples == 0


def test_deterministic_dedupe_removes_exact_dups():
    dup = [_ex("결제내역 없음") for _ in range(24)]
    uniq = [_ex("결제 성공", then="대상 추가")]
    out = RulesExamplesOutput(rules=[_rule("선별", dup + uniq)])
    deterministic_dedupe(out)
    # 24개 완전중복 → 1개로, 고유 1개 유지 = 2개
    assert len(out.rules[0].examples) == 2
    # dedupe 후 재검사 = 정상
    assert not check_rule_quality(out).is_anomaly
