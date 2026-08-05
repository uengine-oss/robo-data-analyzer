"""Shopmall skill의 raw-source SQL 패턴 회귀 테스트."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.steps.step5_linking.link_tables.regex_table_linker import (
    _entity_group_complete,
    _resolve_table,
)
from agents.contracts import DdlTransform
import unittest
from tests.unittest_function_cases import load_function_cases


_SKILL = _ROOT / "agents" / "skills" / "framework" / "shopmall-c-facade" / "SKILL.md"


def _patterns() -> dict[str, re.Pattern]:
    # spec 108 — 스킬 fence 는 이제 DSL 규칙. 컴파일 경유로 같은 행동 코퍼스를 검증한다
    # (원본 raw 정규식 시절의 회귀 케이스가 그대로 DSL 표현력 증명이 된다 — SC-001).
    from agents.rule_compiler import compile_rules, parse_rule
    text = _SKILL.read_text(encoding="utf-8")
    block = text.split("```yaml", 1)[1].split("```", 1)[0]
    data = yaml.safe_load(block)
    rules = [parse_rule(r) for r in data["table_access_rules"]]
    return {p.id: re.compile(p.call_regex) for p in compile_rules(rules)}


def test_adjacent_literals_are_matched_without_source_rewriting() -> None:
    source = '''sprintf(g_sqlbuf,
        "UPDATE orders "
        "SET order_status_cd = '%s' "
        "WHERE order_id = %ld", status, order_id);'''
    match = _patterns()["shopmall_sql_update"].search(source)
    assert match is not None
    assert match.groupdict() == {"op_token": "UPDATE", "entity": "orders"}


def test_multiple_patterns_preserve_multiple_tables() -> None:
    source = '''
        sprintf(g_sqlbuf, "SELECT order_id FROM orders WHERE order_id=%ld", order_id);
        sprintf(g_sqlbuf, "UPDATE shipping " "SET ship_status_cd='20'", order_id);
    '''
    patterns = _patterns()
    read = patterns["shopmall_sql_select"].search(source)
    write = patterns["shopmall_sql_update"].search(source)
    assert read is not None and read.group("entity") == "orders"
    assert write is not None and write.group("entity") == "shipping"


def test_runtime_table_placeholder_is_not_captured_as_entity() -> None:
    source = 'sprintf(g_sqlbuf, "UPDATE %s SET status_cd=\'20\'", table_name);'
    assert _patterns()["shopmall_sql_update"].search(source) is None


def test_prefix_alternation_is_rejected_as_partial_entity() -> None:
    source = "UPDATE product_stock SET avail_qty = 1"
    bad = re.compile(r"UPDATE\s+(?P<entity>product|product_stock)\s*")
    match = bad.search(source)
    assert match is not None
    assert match.group("entity") == "product"
    assert not _entity_group_complete(source, match)


def test_identifier_punctuation_is_preserved_and_as_is_is_exact() -> None:
    source = 'FROM "order-item" WHERE id = 1'
    quoted = re.compile(r'FROM\s+"(?P<entity>[A-Za-z][A-Za-z0-9-]*)"')
    match = quoted.search(source)
    assert match is not None
    assert match.group("entity") == "order-item"
    assert _entity_group_complete(source, match)

    transform = DdlTransform(ddl_naming_rule="as_is")
    lookup = {"product_stock": "db:product_stock", "order-item": "db:order-item"}
    assert _resolve_table("product_stock", lookup, transform) == "db:product_stock"
    assert _resolve_table("PRODUCT_STOCK", lookup, transform) is None
    assert _resolve_table("order_item", lookup, transform) is None


def test_entity_completeness_respects_pattern_consumed_tail():
    """spec 079 회귀 — 2026-07-22 실측: ProFrame SQL_KEY(테이블명+``_<op><nnnn>`` 꼬리)
    314/314 이 부분추출로 오탈락. 가드는 entity 그룹 끝이 아니라 **매치 전체의 끝**
    기준으로 "이름이 이어지는가"를 판정해야 한다 (패턴이 소비한 꼬리는 계약의 일부).
    """
    tail_pattern = re.compile(
        r'mpfmdbio\s*\(\s*"(?P<entity>[A-Za-z0-9_]+?)_(?P<op_token>[siud])\d{4}"'
    )
    ok = tail_pattern.search('rc = mpfmdbio("acct_ledger_s0004", &in);')
    assert ok is not None and ok.group("entity") == "acct_ledger"
    assert _entity_group_complete(ok.string, ok) is True    # 꼬리는 계약 — 통과

    open_pattern = re.compile(
        r'mpfmdbio\s*\(\s*"(?P<entity>[A-Za-z0-9_]+?)_(?P<op_token>[siud])\d{4}'
    )
    cut = open_pattern.search('rc = mpfmdbio("acct_ledger_s0004abc", &in);')
    assert cut is not None
    assert _entity_group_complete(cut.string, cut) is False  # 매치 뒤 이름 이어짐 — 진짜 잘림

    bare = re.compile(r'get\("(?P<entity>[a-z]+)')
    part = bare.search('get("stock_item")')
    assert part is not None and part.group("entity") == "stock"
    assert _entity_group_complete(part.string, part) is False  # 기존 반토막 보호 유지


if __name__ == "__main__":
    test_adjacent_literals_are_matched_without_source_rewriting()
    test_multiple_patterns_preserve_multiple_tables()
    test_runtime_table_placeholder_is_not_captured_as_entity()
    test_prefix_alternation_is_rejected_as_partial_entity()
    test_identifier_punctuation_is_preserved_and_as_is_is_exact()
    test_entity_completeness_respects_pattern_consumed_tail()
    print("[OK] shopmall skill SQL patterns: 6/6 passed")


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
