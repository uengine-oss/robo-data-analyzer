"""spec 054 US3 — 결정론 품질검사기 회귀 테스트.

load_target_orders의 반복 패턴을 안전 상한 안에서 재현해 이상 감지되는지, 정상 고유
Example은 통과하는지 검증한다.
"""
from pipeline.models.code_analysis_output import (
    AffectedTable, ExampleItem, RuleItem, RulesExamplesOutput,
)
from pipeline.steps.step7_analyze_code.rule_quality import (
    check_rule_quality,
)
import unittest


def _ex(given, when="대상 판정", then="제외", tables=None):
    ats = [AffectedTable(table=t, op="READ") for t in (tables or [])]
    return ExampleItem(given=given, when=when, then=then, affected_tables=ats)


def _rule(stmt, examples):
    return RuleItem(statement=stmt, anchor_line=1, examples=examples)


def test_clean_result_not_anomaly():
    # 고유 Example 5개 — dup 0, max_group 1
    exs = [_ex(f"상태={s}", then=f"결과{s}") for s in ("A", "B", "C", "D", "E")]
    out = RulesExamplesOutput(rules=[_rule("상태별 처리", exs)])
    r = check_rule_quality(out)
    assert not r.is_anomaly, r.reasons
    assert r.dup_ratio == 0.0 and r.max_group == 1


def test_load_target_orders_repetition_anomaly():
    # 동일 의미("결제내역 없음→제외") 22회 + 성공/취소 대표 각 1 = 상한 24개
    dup = [_ex("결제내역 없음") for _ in range(22)]
    uniq = [_ex("결제 성공", then="대상 추가"), _ex("주문 취소", then="제외")]
    out = RulesExamplesOutput(rules=[_rule("결제 상태별 대상 선별", dup + uniq)])
    r = check_rule_quality(out)
    assert r.is_anomaly
    assert r.max_group == 22
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
    exs = [_ex(f"입력{i}", then=f"결과{i}") for i in range(24)]  # 캡 도달, 전부 고유
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


# ── 스위트 편입 브리지 (2026-07-22 테스트 정리) ──────────────────────────
# 함수식 케이스는 unittest discover 에 수집되지 않아 스위트에서 실행되지 않는
# 일회성이었다. 아래 브리지가 전 케이스를 스위트 회귀로 편입한다 (재사용 보장).
class ModuleCasesSuiteBridge(unittest.TestCase):
    def test_module_cases(self):
        import asyncio
        import inspect
        cases = [(n, f) for n, f in sorted(globals().items())
                 if n.startswith("test_") and inspect.isfunction(f)]
        self.assertTrue(cases, "함수식 케이스가 없다")
        for name, fn in cases:
            with self.subTest(case=name):
                if inspect.iscoroutinefunction(fn):
                    asyncio.run(fn())
                else:
                    fn()
