"""Shopmall skill의 raw-source SQL 패턴 회귀 테스트."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis_flow.step4_linking.stage1_regex_grep.regex_table_linker import (
    _entity_group_complete,
    _resolve_table,
)
from data_models.agent_output import DdlTransform


_SKILL = _ROOT / "skills" / "shopmall-c-facade" / "SKILL.md"


def _patterns() -> dict[str, re.Pattern]:
    text = _SKILL.read_text(encoding="utf-8")
    block = text.split("```yaml", 1)[1].split("```", 1)[0]
    data = yaml.safe_load(block)
    return {
        item["id"]: re.compile(item["call_regex"])
        for item in data["table_access_patterns"]
    }


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


if __name__ == "__main__":
    test_adjacent_literals_are_matched_without_source_rewriting()
    test_multiple_patterns_preserve_multiple_tables()
    test_runtime_table_placeholder_is_not_captured_as_entity()
    test_prefix_alternation_is_rejected_as_partial_entity()
    test_identifier_punctuation_is_preserved_and_as_is_is_exact()
    print("[OK] shopmall skill SQL patterns: 5/5 passed")
