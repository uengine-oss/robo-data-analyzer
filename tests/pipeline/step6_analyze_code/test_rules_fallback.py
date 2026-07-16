"""rules_fallback — 폴백 오케스트레이션 단위 테스트 (spec 054 US3).

LLM 은 콜러블 주입으로 스텁. 스트림(emit_current/track_processing)은 scope 미설정 시
no-op 이라 그대로 통과. 검증: 분할→조각→병합→재검사 통과, 수렴→dedupe+플래그,
조각 부분 실패 계속, BudgetExceededError 전파.
"""
from __future__ import annotations

import unittest

from pipeline.steps.step6_analyze_code.ast_budget_splitter import SubtreeNode
from pipeline.steps.step6_analyze_code.rule_quality import check_rule_quality
from pipeline.steps.step6_analyze_code.rules_fallback import (
    ANOMALY_FLAG,
    STATUS_FALLBACK_FLAGGED,
    STATUS_FALLBACK_OK,
    build_fragments,
    run_rules_fallback,
)
from pipeline.data_models.code_analysis_output import (
    AffectedTable,
    ExampleItem,
    RuleItem,
    RulesExamplesOutput,
)
from llm.client.usage.cost_budget import BudgetExceededError


def _ex(given="g", when="w", then="t", tables=()):
    return ExampleItem(
        given=given, when=when, then=then,
        affected_tables=[AffectedTable(table=t, op=op) for t, op in tables],
    )


def _anomalous() -> RulesExamplesOutput:
    """같은 (given,when,then) 4회 반복 → max_group=4, dup_ratio=0.75 → 이상."""
    return RulesExamplesOutput(rules=[
        RuleItem(statement="주문 금액이 임계 이상이면 할인",
                 examples=[_ex(), _ex(), _ex(), _ex()])])


def _clean() -> RulesExamplesOutput:
    return RulesExamplesOutput(rules=[
        RuleItem(statement="주문 금액이 임계 이상이면 할인",
                 examples=[_ex("g1", "w1", "t1", (("orders", "READ"),)),
                           _ex("g2", "w2", "t2")])])


_BIG_BODY = "\n".join(f"line{i} = compute({i})" for i in range(1, 101))


class BuildFragmentsTest(unittest.TestCase):
    def test_no_subtree_no_fragments(self):
        self.assertEqual(build_fragments(_BIG_BODY, None), [])
        self.assertEqual(build_fragments(_BIG_BODY, []), [])

    def test_small_body_within_budget_no_split(self):
        subtree = [SubtreeNode("if1", "IF", 1, 3)]
        self.assertEqual(build_fragments("a\nb\nc", subtree, budget_tokens=10000), [])

    def test_over_budget_with_subtree_splits(self):
        subtree = [SubtreeNode("if1", "IF", 1, 50), SubtreeNode("if2", "IF", 51, 100)]
        frags = build_fragments(_BIG_BODY, subtree, budget_tokens=20)
        self.assertGreaterEqual(len(frags), 1)
        self.assertTrue(all(f.text for f in frags))


class RunFallbackTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 사전조건: _anomalous 는 실제로 이상 판정돼야 폴백 의미가 있다.
        self.assertTrue(check_rule_quality(_anomalous(), _BIG_BODY).is_anomaly)

    async def test_split_merge_recheck_pass(self):
        subtree = [SubtreeNode("if1", "IF", 1, 50), SubtreeNode("if2", "IF", 51, 100)]
        calls = {"frag": 0, "merge": 0}

        async def af(frag):
            calls["frag"] += 1
            return _clean()

        async def mf(outs):
            calls["merge"] += 1
            return _clean()

        outcome = await run_rules_fallback(
            code_id="fn::calc_discount",
            rules_out=_anomalous(),
            report=check_rule_quality(_anomalous(), _BIG_BODY),
            code_body=_BIG_BODY,
            control_subtree=subtree,
            analyze_fragment=af,
            merge_fragments=mf,
            budget_tokens=20,
        )
        self.assertEqual(outcome.status, STATUS_FALLBACK_OK)
        self.assertIsNone(outcome.anomaly_flag)
        self.assertGreaterEqual(outcome.fragment_count, 1)
        self.assertEqual(calls["merge"], 1)         # 병합은 정확히 1회
        self.assertGreaterEqual(calls["frag"], 1)
        self.assertFalse(outcome.recheck.is_anomaly)

    async def test_no_subtree_converges_to_dedupe_flag(self):
        async def af(frag):  # 호출되면 안 됨
            raise AssertionError("분할 없음이면 조각 분석 호출 금지")

        async def mf(outs):
            raise AssertionError("분할 없음이면 병합 호출 금지")

        outcome = await run_rules_fallback(
            code_id="fn::x",
            rules_out=_anomalous(),
            report=check_rule_quality(_anomalous(), _BIG_BODY),
            code_body=_BIG_BODY,
            control_subtree=None,
            analyze_fragment=af,
            merge_fragments=mf,
        )
        self.assertEqual(outcome.status, STATUS_FALLBACK_FLAGGED)
        self.assertEqual(outcome.anomaly_flag, ANOMALY_FLAG)
        self.assertEqual(outcome.fragment_count, 0)
        # dedupe 로 완전중복 4→1 정리 → 재검사(post) 이상 소멸
        self.assertEqual(len(outcome.rules_out.rules[0].examples), 1)
        self.assertFalse(outcome.recheck.is_anomaly)

    async def test_partial_fragment_failure_continues(self):
        subtree = [SubtreeNode("if1", "IF", 1, 50), SubtreeNode("if2", "IF", 51, 100)]
        seen = {"n": 0}

        async def af(frag):
            seen["n"] += 1
            if seen["n"] == 1:
                raise RuntimeError("첫 조각 LLM 실패")
            return _clean()

        async def mf(outs):
            # 실패 조각 제외한 나머지만 병합 입력으로 온다.
            self.assertTrue(len(outs) >= 1)
            return _clean()

        outcome = await run_rules_fallback(
            code_id="fn::x",
            rules_out=_anomalous(),
            report=check_rule_quality(_anomalous(), _BIG_BODY),
            code_body=_BIG_BODY,
            control_subtree=subtree,
            analyze_fragment=af,
            merge_fragments=mf,
            budget_tokens=20,
        )
        self.assertEqual(outcome.status, STATUS_FALLBACK_OK)
        self.assertGreaterEqual(seen["n"], 2)

    def test_flag_persists_to_graph_item(self):
        """FR-023: 이상 플래그는 그래프 적재 item 에 포함돼야 함수 노드에 저장된다.
        정상경로(플래그 없음)는 키 자체가 빠져 속성이 생기지 않는다(nonempty 규약)."""
        from graph.schema.graph_nodes import CodeNode
        from graph.nodes.code_node import to_summary_item

        flagged = CodeNode(id="f1", label="FUNCTION", name="f1", signature="f1()",
                           file_path="x.c", start_line=1, end_line=2, owner_id="m")
        flagged.summary = "s"
        flagged.extra["rules_quality_flag"] = "rules_quality_anomaly"
        self.assertEqual(to_summary_item(flagged).get("rules_quality_flag"), "rules_quality_anomaly")

        clean = CodeNode(id="f2", label="FUNCTION", name="f2", signature="f2()",
                         file_path="x.c", start_line=1, end_line=2, owner_id="m")
        clean.summary = "s"
        self.assertNotIn("rules_quality_flag", to_summary_item(clean))

    async def test_budget_exceeded_propagates(self):
        subtree = [SubtreeNode("if1", "IF", 1, 100)]

        async def af(frag):
            raise BudgetExceededError(1.0, 0.5)

        async def mf(outs):
            return _clean()

        with self.assertRaises(BudgetExceededError):
            await run_rules_fallback(
                code_id="fn::x",
                rules_out=_anomalous(),
                report=check_rule_quality(_anomalous(), _BIG_BODY),
                code_body=_BIG_BODY,
                control_subtree=subtree,
                analyze_fragment=af,
                merge_fragments=mf,
                budget_tokens=20,
            )


if __name__ == "__main__":
    unittest.main()
