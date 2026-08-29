from pathlib import Path
from types import SimpleNamespace

import json

import scripts.evidence_preflight as evidence_preflight
from scripts.evidence_preflight import _Corpus, _resolved_rows, _stored_rows
from pipeline.steps.step2_load_ast.load_ast_step import ast_json_paths


def test_preflight_imports_only_current_production_contracts() -> None:
    assert _Corpus.__name__ == "_Corpus"


def test_dbms_preflight_forwards_all_structural_call_inputs(
    tmp_path: Path, monkeypatch,
) -> None:
    source = tmp_path / "source"
    analysis = tmp_path / "analysis"
    source.mkdir()
    analysis.mkdir()
    artifact = analysis / "unit.json"
    artifact.write_text("{}", encoding="utf-8")
    values = {
        "call_sites": [{"call_id": "call:1"}],
        "callable_facts": [{"fact_id": "callable:1"}],
        "parameter_facts": [{"fact_id": "parameter:1"}],
        "binding_target_facts": [{"fact_id": "binding:1"}],
        "scope_directive_facts": [{"fact_id": "scope:1"}],
        "macro_facts": [{"fact_id": "macro:1"}],
        "import_refs": [{"fact_id": "import:1"}],
        "evidence_sources": [{"source_id": "source:1"}],
        "evidence_completeness": [{"source_id": "source:1"}],
        "preprocessing_contexts": [{"source_id": "source:1"}],
    }
    parsed = SimpleNamespace(
        code_nodes=[], modules=[], variables=[], constants=[], parent_of_edges=[],
        **values,
    )
    monkeypatch.setattr(evidence_preflight, "detect_strategy", lambda _source: "dbms")
    monkeypatch.setattr(evidence_preflight, "ast_json_paths", lambda _analysis: [artifact])
    monkeypatch.setattr(
        evidence_preflight, "_read_dbms",
        lambda _raw, _source, _artifact: parsed,
    )
    captured = {}

    def classify(call_sites, code_nodes, **kwargs):
        captured["call_sites"] = call_sites
        captured["code_nodes"] = code_nodes
        captured.update(kwargs)
        return SimpleNamespace(edges=[])

    monkeypatch.setattr(evidence_preflight, "classify_call_sites", classify)

    corpus = _Corpus(tmp_path)
    summary = corpus.dbms_summary_dependencies()

    assert summary["calls"] == 0
    assert captured["call_sites"] == values["call_sites"]
    assert captured["code_nodes"] == []
    for field, expected in values.items():
        if field != "call_sites":
            assert captured[field] == expected


def test_resolved_rows_compare_effect_payload_not_local_reference_names() -> None:
    valued = _resolved_rows({
        "conditions": {},
        "effects": {"E1": {
            "op": "ret", "target": None, "value": "RET_OK", "from_source": True,
        }},
        "rows": {"R7": {"role": "exit", "when": [], "direct": ["E1"]}},
    })
    duplicated = _resolved_rows({
        "conditions": {},
        "effects": {
            "E8": {"op": "ret", "target": None, "value": "RET_OK", "from_source": True},
            "E9": {"op": "ret", "target": None, "value": None, "from_source": None},
        },
        "rows": {"R7": {"role": "exit", "when": [], "direct": ["E8", "E9"]}},
    })
    assert valued != duplicated


def test_ast_json_paths_includes_nested_target_artifacts(tmp_path: Path) -> None:
    root_artifact = tmp_path / "root.json"
    nested_artifact = tmp_path / "shop_mall" / "order" / "order.json"
    ignored_file = tmp_path / "shop_mall" / "order" / "notes.txt"
    nested_artifact.parent.mkdir(parents=True)
    root_artifact.write_text("{}", encoding="utf-8")
    nested_artifact.write_text("{}", encoding="utf-8")
    ignored_file.write_text("not an artifact", encoding="utf-8")

    assert ast_json_paths(tmp_path) == [
        root_artifact,
        nested_artifact,
    ]


def test_stored_batch_rule_rows_are_grouped_by_explicit_owner(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "llm-prompts"
    prompt_dir.mkdir()
    packets = []
    for key, node, owner, row in (
        ("10-10", "code:p.sql:r@1:CALL@10", "code:p.sql:r@1", 10),
        ("20-20", "code:p.sql:s@1:CALL@20", "code:p.sql:s@1", 20),
    ):
        evidence = {
            "conditions": {},
            "effects": {"E1": {"op": "call", "target": "x"}},
            "rows": {f"R{row}": {"role": "statement", "when": [], "direct": ["E1"]}},
        }
        packets.append(
            f"### [RULE_TARGET {key}]\n"
            f"target_id: {node}\nowner_id: {owner}\n"
            f"rule_evidence_json: {json.dumps(evidence, separators=(',', ':'))}"
        )
    record = {"target_id": "batch:multiple-rule-owners", "user_content": "\n\n".join(packets)}
    (prompt_dir / "code_rules_examples_batch.jsonl").write_text(
        json.dumps(record) + "\n", encoding="utf-8",
    )

    stored = _stored_rows(tmp_path)

    assert set(stored) == {"code:p.sql:r@1", "code:p.sql:s@1"}
    assert set(stored["code:p.sql:r@1"]) == {"R10"}
    assert set(stored["code:p.sql:s@1"]) == {"R20"}


def test_stored_rows_include_gate_and_non_gate_prompt_families(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "llm-prompts"
    prompt_dir.mkdir()
    for filename, row in (
        ("code_gate_rules_batch.jsonl", 11),
        ("code_rules_examples_batch.jsonl", 22),
    ):
        evidence = {
            "conditions": {},
            "effects": {},
            "rows": {f"R{row}": {"role": "decision", "when": [], "direct": []}},
        }
        record = {
            "target_id": "code:p.sql:r@1",
            "user_content": "rule_evidence_json: "
            + json.dumps(evidence, separators=(",", ":")),
        }
        (prompt_dir / filename).write_text(
            json.dumps(record) + "\n", encoding="utf-8",
        )

    stored = _stored_rows(tmp_path)

    assert set(stored["code:p.sql:r@1"]) == {"R11", "R22"}


def test_current_stored_rows_come_from_deterministic_rule_artifact(tmp_path: Path) -> None:
    directory = tmp_path / "rule-structures"
    directory.mkdir()
    record = {
        "schema": "deterministic-rule/v2",
        "target_id": "code:order.c:validate",
        "evidence": {
            "conditions": {"C1": "ready"},
            "effects": {},
            "rows": {"R1": {"role": "decision", "when": ["C1"]}},
        },
        "rules": [{
            "anchor_line": 1,
            "structural_condition": 'F("ready")',
            "structural_effects": [],
            "business_condition": None,
            "business_effects": [],
        }],
    }
    (directory / "validate.json").write_text(
        json.dumps(record), encoding="utf-8",
    )

    stored = _stored_rows(tmp_path)

    assert stored == {
        "code:order.c:validate": {
            "R1": {
                "role": "decision",
                "when_evidence": [{"holds": True, "source": "ready"}],
                "direct_evidence": [],
            },
        },
    }
