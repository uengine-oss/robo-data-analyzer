"""spec 052 — referenced symbol values remain structured end to end."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.steps.step6_build_context.referenced_symbols import _build_reference_context
from pipeline.models.analysis_context import ReferencedSymbolContext, dump_referenced_symbols
import unittest


def _state():
    constants = [
        NS(id="c.max", name="MAX_DAILY_AUTH", var_type="int", value="3"),
        NS(id="c.status", name="DEFAULT_STATUS", var_type="char*", value='"PAID"'),
        NS(id="c.van", name="DEFAULT_VAN", var_type="char*", value='"SK-VAN"'),
    ]
    variables = [
        NS(id="v.status", name="status", var_type="char*", init_value="DEFAULT_STATUS"),
    ]
    return {
        "constants": constants,
        "variables": variables,
        "references_edges": [NS(source_id="fn", target_id="c.max"), NS(source_id="fn", target_id="v.status")],
        "init_by_edges": [
            NS(from_id="v.status", source_id="c.status"),
            NS(from_id="v.status", source_id="c.van"),
        ],
    }


def test_exact_values_and_all_init_sources_survive_json():
    symbols = tuple(_build_reference_context(_state())["fn"])
    payload = json.loads(dump_referenced_symbols(symbols))
    constant = next(item for item in payload if item["name"] == "MAX_DAILY_AUTH")
    variable = next(item for item in payload if item["name"] == "status")
    default_status = next(item for item in payload if item["name"] == "DEFAULT_STATUS")
    default_van = next(item for item in payload if item["name"] == "DEFAULT_VAN")
    assert constant["declared_value"] == "3"
    assert constant["value_semantics"] == "immutable_constant"
    assert variable["declared_value"] == "DEFAULT_STATUS"
    assert variable["value_semantics"] == "declaration_initializer"
    assert variable["initialization_sources"] == ["DEFAULT_STATUS", "DEFAULT_VAN"]
    assert default_status["declared_value"] == '"PAID"'
    assert default_status["value_semantics"] == "immutable_constant"
    assert default_van["declared_value"] == '"SK-VAN"'


def test_unicode_quotes_and_commas_are_valid_json():
    symbol = ReferencedSymbolContext(
        id="u", name="한글,값", kind="constant", data_type="str",
        declared_value='"가,나"', value_semantics="immutable_constant",
    )
    assert json.loads(dump_referenced_symbols((symbol,)))[0]["declared_value"] == '"가,나"'


def test_framework_and_dbms_consumers_use_same_json_contract():
    framework = (_ROOT / "pipeline/steps/step7_analyze_code/three_split_llm_fanout.py").read_text(encoding="utf-8")
    # DBMS 프롬프트 조립은 batch_prompt_values 로 이사 (2026-07 리팩토링) — 계약 대상 갱신.
    dbms = (_ROOT / "pipeline/steps/step7_analyze_code/batch_prompt_values.py").read_text(encoding="utf-8")
    canonical_call = "dump_referenced_symbols(ctx.referenced_symbols)"
    legacy_join = "join(" + "ctx.referenced_symbols)"
    assert canonical_call in framework
    assert canonical_call in dbms
    assert legacy_join not in framework
    assert legacy_join not in dbms


if __name__ == "__main__":
    test_exact_values_and_all_init_sources_survive_json()
    test_unicode_quotes_and_commas_are_valid_json()
    test_framework_and_dbms_consumers_use_same_json_contract()
    print("[OK] structured_referenced_symbols: 3/3 passed")


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
