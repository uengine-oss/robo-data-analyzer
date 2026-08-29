"""Example Mapping 단일 빌더 — analyze_code Rule 결과 → EM 그래프.

Framework / DBMS **공통 단일 빌더**. anchor 는 ``FUNCTION`` 하나.

흐름: 분석된 CodeNode 들의 ``extra["rules"]``
→ Rule·Example 노드 + HAS_RULE / HAS_EXAMPLE / AFFECTS_TABLE 엣지.
결정론 — LLM 0회.

Transport carries ``anchor_line`` plus structural/business conditions and effects,
derived examples, and confirmed table effects. Rule id and owner are reconstructed as
``<code id>::R-<anchor>`` and HAS_RULE rather than duplicated in the payload.
형식이 아니면 **즉시 실패** — 조용히 RULE 0 개가 되는 상태를 금지한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from graph.schema.graph_edges import (
    AffectsTable,
    HasExample,
    HasRule,
)
from graph.schema.graph_nodes import Example, CodeNode, Rule


@dataclass
class ExampleMappingResult:
    """Rule graph projection — Rule/Example nodes and their three edge types."""
    rules: list[Rule] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    has_rule: list[HasRule] = field(default_factory=list)
    has_example: list[HasExample] = field(default_factory=list)
    affects_table: list[AffectsTable] = field(default_factory=list)


def build_example_mapping(code_nodes: list[CodeNode]) -> ExampleMappingResult:
    """분석된 CodeNode 들 → Example Mapping 노드·엣지 (결정론, LLM 0)."""
    result = ExampleMappingResult()
    for fn in code_nodes:
        extra = fn.extra or {}
        _collect_rules(fn.id, extra.get("rules") or [], result)
    return result


def _collect_rules(fid: str, rules: list, result: ExampleMappingResult) -> None:
    """rules → Rule 노드 + HAS_RULE + 하위 examples. 계약 형식이 아니면 즉시 실패."""
    ordinal = 0
    allowed_fields = {
        "anchor_line", "structural_condition", "structural_effects",
        "business_effects",
        "examples", "affected_tables",
    }
    for r in rules:
        if not isinstance(r, dict):
            raise TypeError(
                f"rule transport entry must be an object for {fid}: {type(r).__name__}"
            )
        unknown_fields = set(r) - allowed_fields
        if unknown_fields:
            raise ValueError(
                f"unknown rule transport fields for {fid}: {sorted(unknown_fields)}"
            )
        ordinal += 1
        _collect_rule(fid, ordinal, r, result)


def _collect_rule(
    fid: str, ordinal: int, r: dict, result: ExampleMappingResult,
) -> None:
    """구조화 규칙 1행 → Rule 노드(함수별 고유) + HAS_RULE + Example + 규칙 단위 AFFECTS."""
    anchor_line = r.get("anchor_line")
    if (
        isinstance(anchor_line, bool)
        or not isinstance(anchor_line, int)
        or anchor_line <= 0
    ):
        raise ValueError(
            f"structured rule requires positive anchor_line: {fid}"
        )
    rid = f"{fid}::R-{anchor_line}"
    business_effects = tuple(
        str(s) for s in (r.get("business_effects") or [])
    )
    if not business_effects:
        raise ValueError(
            f"product Rule requires at least one validated business effect: {rid}"
        )
    result.rules.append(Rule(
        id=rid,
        anchor_line=anchor_line,
        structural_condition=str(r.get("structural_condition") or ""),
        structural_effects=tuple(
            str(s) for s in (r.get("structural_effects") or [])
        ),
        business_effects=business_effects,
    ))
    result.has_rule.append(HasRule(
        source_id=fid,
        target_id=rid,
        ordinal=ordinal,
    ))
    for idx, e in enumerate(r.get("examples") or [], start=1):
        if not isinstance(e, dict):
            raise TypeError(
                f"example transport entry must be an object for {rid}: {type(e).__name__}"
            )
        given_conditions = tuple(
            str(x) for x in (e.get("given_conditions") or [])
        )
        expected_effects = tuple(
            str(x) for x in (e.get("expected_effects") or [])
        )
        if not (given_conditions or expected_effects):
            raise ValueError(f"empty example transport for {rid} at ordinal {idx}")
        eid = f"{rid}::E{idx}"
        result.examples.append(Example(
            id=eid,
            given_conditions=given_conditions,
            expected_effects=expected_effects,
        ))
        result.has_example.append(HasExample(
            source_id=rid, target_id=eid, ordinal=idx,
        ))
    # 규칙 단위 결정론 귀속 — then 효과에서 코드가 만든 transport 를 그대로 검증 적재.
    _collect_affects_table(rid, r.get("affected_tables") or [], result)


def _collect_affects_table(
    eid: str, affected: list, result: ExampleMappingResult,
) -> None:
    """affected_tables[] → 규칙 단위 AFFECTS_TABLE 엣지. 불완전 transport는 즉시 실패한다."""
    for entry in affected:
        if not isinstance(entry, dict):
            raise ValueError("AFFECTS_TABLE entry must be an object")
        table = str(entry.get("table") or "").strip()
        access = str(entry.get("access") or "").strip().upper()
        op = str(entry.get("op") or "").strip().upper()
        op_source = str(entry.get("op_source") or "").strip().upper()
        if (
            not table
            or access not in {"READ", "WRITE", "READ_WRITE"}
            or op not in {
                "READ", "INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE",
            }
            or op_source not in {"SCANNER", "ANALYZER_DERIVED"}
        ):
            raise ValueError(
                f"incomplete AFFECTS_TABLE transport for {eid}: "
                f"table={table!r}, access={access!r}, op={op!r}, op_source={op_source!r}"
            )
        result.affects_table.append(
            AffectsTable(
                source_id=eid,
                target_id=table,
                access=access,
                op=op,
                op_source=op_source,
            )
        )


