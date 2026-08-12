from __future__ import annotations

import json

import pytest

from pipeline.models.data_object_usage import dump_data_object_usages
from pipeline.models.data_object_usage import usages_from_parser_ast
from pipeline.steps.step2_load_ast.node_builders import _make_code_node


def test_parser_evidence_is_loaded_without_rewriting_alias_or_coordinates() -> None:
    node = _make_code_node(
        {
            "type": "SELECT",
            "startLine": 239,
            "endLine": 245,
            "dataObjectEvidenceVersion": 1,
            "dataObjectReferences": [
                {"rawReference": "RDIB2EN_TB", "name": "RDIB2EN_TB", "alias": "A", "access": "READ", "startLine": 241},
                {"rawReference": "RDD01DD_TB", "name": "RDD01DD_TB", "alias": "B", "access": "READ", "startLine": 242},
            ],
            "qualifiedColumnReferences": [
                {"rawReference": "A.BNB_CODE", "qualifier": "A", "name": "BNB_CODE", "startLine": 239},
                {"rawReference": "B.VAL", "qualifier": "B", "name": "VAL", "startLine": 239},
                {"rawReference": "OUTER.ID", "qualifier": "OUTER", "name": "ID", "startLine": 244},
            ],
        },
        "code:p:SELECT@239", "code:p", "", "p.sql", "code:p:PROC@1",
    )

    usages = node.extra["data_object_usages"]
    assert [(usage.alias, [column.name for column in usage.qualified_columns]) for usage in usages] == [
        ("A", ["BNB_CODE"]),
        ("B", ["VAL"]),
    ]
    assert [column.raw_reference for column in node.extra["unresolved_qualified_columns"]] == ["OUTER.ID"]
    payload = json.loads(dump_data_object_usages(usages))
    assert payload[0]["evidence_line"] == 241
    assert payload[1]["qualified_columns"][0]["raw_reference"] == "B.VAL"


def test_legacy_ast_is_explicitly_distinguished_from_complete_empty_evidence() -> None:
    legacy = _make_code_node(
        {"type": "SELECT", "startLine": 1, "endLine": 1},
        "legacy", "module", "", "p.sql", None,
    )
    complete_empty = _make_code_node(
        {"type": "SELECT", "startLine": 1, "endLine": 1, "dataObjectEvidenceVersion": 1},
        "new", "module", "", "p.sql", None,
    )

    assert "data_object_evidence_version" not in legacy.extra
    assert complete_empty.extra["data_object_evidence_version"] == 1
    assert complete_empty.extra["data_object_usages"] == ()


def test_unknown_parser_evidence_version_fails_closed_at_its_coordinate() -> None:
    with pytest.raises(ValueError, match=r"dataObjectEvidenceVersion=2 at p\.sql:9"):
        _make_code_node(
            {
                "type": "SELECT", "startLine": 9, "endLine": 9,
                "dataObjectEvidenceVersion": 2,
            },
            "newer", "module", "", "p.sql", None,
        )


def test_quoted_aliases_with_different_case_do_not_cross_bind_columns() -> None:
    usages, unresolved = usages_from_parser_ast(
        source_id="select:1", evidence_file="p.sql",
        references=(
            {"rawReference": "TREE_T", "name": "TREE_T", "alias": '"A"', "access": "READ", "startLine": 1},
            {"rawReference": "TREE_T", "name": "TREE_T", "alias": '"a"', "access": "READ", "startLine": 1},
        ),
        qualified_columns=(
            {"rawReference": '"A".ID', "qualifier": '"A"', "name": "ID", "startLine": 1},
            {"rawReference": '"a".ID', "qualifier": '"a"', "name": "ID", "startLine": 1},
        ),
    )

    assert unresolved == ()
    assert [
        [column.qualifier for column in usage.qualified_columns]
        for usage in usages
    ] == [['"A"'], ['"a"']]


def test_quoted_physical_identifiers_are_preserved_as_case_sensitive_evidence() -> None:
    usages, unresolved = usages_from_parser_ast(
        source_id="select:quoted", evidence_file="p.sql",
        references=({
            "rawReference": '"App"."Orders"', "schema": '"App"',
            "name": '"Orders"', "schemaQuoted": True, "nameQuoted": True,
            "alias": "o", "access": "READ", "startLine": 3,
        },),
        qualified_columns=({
            "rawReference": 'o."OrderId"', "qualifier": "o", "name": '"OrderId"',
            "nameQuoted": True, "startLine": 3,
        },),
    )

    assert unresolved == ()
    assert usages[0].schema_quoted is True
    assert usages[0].name_quoted is True
    assert usages[0].qualified_columns[0].name_quoted is True
