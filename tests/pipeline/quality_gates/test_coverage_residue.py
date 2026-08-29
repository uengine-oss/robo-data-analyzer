"""커버리지 잔차 감사 계약 (골키퍼 그물2).

함수마다 "결정론 사실(분기 범위·행·statement·호출)이 딛지 않는 live 소스 줄"을
잔차로 표면화한다 — 파서도 분석기도 모르는 구문이 무리(shape)로 드러나는 유일한
범용 장치(실측 근거: `PFM_CATCH:` 라벨 67행이 어떤 사실에도 안 덮인 채 소실됐다).
위험 구문(ontology unsupported_constructs — 값 추적 금지 대상)도 같은 감사가 센다.
"""
import unittest

from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.coverage_residue import coverage_residue
from tests.pipeline.parser_call_fixtures import parser_call_occurrences


def _inventory(source, tree, statements, calls=(), handler_markers=()):
    return build_inventory(
        source, tree, "c", handler_markers=handler_markers,
        statements=statements,
        call_occurrences=parser_call_occurrences(calls),
    )


class CoverageResidueTest(unittest.TestCase):
    def test_unmodeled_label_line_is_residue(self):
        # handler 마커로 선언되지 않은 라벨 — 어떤 사실도 안 덮는 줄이 잔차로 잡힌다.
        source = "\n".join([
            "long f(void) {",   # 1
            "  init();",        # 2  호출 사실 ✓
            "RETRY_POINT:",     # 3  ← 잔차 (라벨, 미선언)
            "  return 0;",      # 4  exit 행 ✓
            "}",                # 5  구두점 — 잔차 아님
        ])
        report = coverage_residue(_inventory(
            source, [],
            statements=(StatementFact("r4", "RETURN", 4, 4, expression="0"),),
            calls=((2, "init"),),
        ))
        self.assertEqual([3], [entry["line"] for entry in report["residue"]])
        self.assertTrue(report["residue"][0]["shape"])

    def test_fully_covered_function_has_no_residue(self):
        source = "\n".join([
            "long f(long x) {",     # 1
            "  if (x > 0) {",       # 2  분기 범위 ✓
            "    return 1;",        # 3  분기 범위 ✓
            "  }",                  # 4  분기 범위 ✓
            "  return 0;",          # 5  exit 행 ✓
            "}",                    # 6
        ])
        report = coverage_residue(_inventory(
            source,
            [ControlSubtreeNode(node_id="if@2", ntype="IF", start=2, end=4,
                                expression="x > 0")],
            statements=(
                StatementFact("r3", "RETURN", 3, 3, expression="1"),
                StatementFact("r5", "RETURN", 5, 5, expression="0"),
            ),
        ))
        self.assertEqual([], report["residue"])
        self.assertEqual(0, report["unsupported_total"])

    def test_unsupported_constructs_are_counted(self):
        # setjmp/함수 포인터 호출 — 값 추적을 흉내 내지 않고 장부로 센다.
        source = "\n".join([
            "long f(void) {",           # 1
            "  if (setjmp(env)) {",     # 2
            "    return 1;",            # 3
            "  }",                      # 4
            "  (*handler_fn)(0);",      # 5
            "  return 0;",              # 6
            "}",                        # 7
        ])
        report = coverage_residue(_inventory(
            source,
            [ControlSubtreeNode(node_id="if@2", ntype="IF", start=2, end=4,
                                expression="setjmp(env)")],
            statements=(
                StatementFact("r3", "RETURN", 3, 3, expression="1"),
                StatementFact("r6", "RETURN", 6, 6, expression="0"),
            ),
            calls=((2, "setjmp"),),
        ))
        kinds = {u["kind"] for u in report["unsupported"]}
        self.assertEqual({"nonlocal_jump", "dynamic_call"}, kinds)
        self.assertEqual(2, report["unsupported_total"])


if __name__ == "__main__":
    unittest.main()
