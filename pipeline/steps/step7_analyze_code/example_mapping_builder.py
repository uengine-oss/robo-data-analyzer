"""Example Mapping 단일 빌더 — analyze_code 3분할 결과 → EM 그래프.

Framework / DBMS **공통 단일 빌더**. anchor 는 ``FUNCTION`` 하나.

흐름: 분석된 CodeNode 들의 ``extra["rules" / "questions"]``
→ Rule·Example·Question 노드 + HAS_RULE / HAS_EXAMPLE / HAS_QUESTION /
  AFFECTS_TABLE 엣지. 결정론 — LLM 0회.

단일 transport 형식(spec 116): {rule_id, anchor_line, cond, then[], nl, when,
examples[{kind, given[], then[]}], affected_tables[]} — Rule id = ``<fid>::R<anchor>``
(파일 절대 줄이라 함수 내 유일). AFFECTS_TABLE 은 규칙 단위(then 결정론 귀속).
형식이 아니면 **즉시 실패** — 조용히 RULE 0 개가 되는 상태를 금지한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from graph.schema.graph_edges import (
    AffectsTable,
    HasExample,
    HasQuestion,
    HasRule,
)
from graph.schema.graph_nodes import Example, CodeNode, Question, Rule


@dataclass
class ExampleMappingResult:
    """EM 빌더 산출물 — 노드 3종 + 엣지 4종."""
    rules: list[Rule] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    has_rule: list[HasRule] = field(default_factory=list)
    has_example: list[HasExample] = field(default_factory=list)
    has_question: list[HasQuestion] = field(default_factory=list)
    affects_table: list[AffectsTable] = field(default_factory=list)


def build_example_mapping(code_nodes: list[CodeNode]) -> ExampleMappingResult:
    """분석된 CodeNode 들 → Example Mapping 노드·엣지 (결정론, LLM 0)."""
    result = ExampleMappingResult()
    for fn in code_nodes:
        extra = fn.extra or {}
        _collect_rules(fn.id, extra.get("rules") or [], result)
        _collect_questions(fn.id, extra.get("questions") or [], result)
    return result


def _collect_rules(fid: str, rules: list, result: ExampleMappingResult) -> None:
    """rules → Rule 노드 + HAS_RULE + 하위 examples. 계약 형식이 아니면 즉시 실패."""
    ordinal = 0
    for r in rules:
        if not isinstance(r, dict):
            raise TypeError(
                f"rule transport entry must be an object for {fid}: {type(r).__name__}"
            )
        if not r.get("rule_id"):
            raise ValueError(
                f"unknown rule transport shape for {fid}: keys={sorted(r)} — "
                "structured rule_id 형식이 아님 (silent RULE 0 금지)"
            )
        ordinal += 1
        _collect_rule(fid, ordinal, r, result)


def _collect_rule(
    fid: str, ordinal: int, r: dict, result: ExampleMappingResult,
) -> None:
    """구조화 규칙 1행 → Rule 노드(함수별 고유) + HAS_RULE + Example + 규칙 단위 AFFECTS."""
    if str(r.get("nl") or ""):
        raise ValueError(
            f"structured rule prose is retired for {fid} {r.get('rule_id')!r}; "
            "Rule.nl must be the empty compatibility field"
        )
    anchor_line = r.get("anchor_line")
    if (
        isinstance(anchor_line, bool)
        or not isinstance(anchor_line, int)
        or anchor_line <= 0
    ):
        raise ValueError(
            f"structured rule requires positive anchor_line: {fid} {r.get('rule_id')!r}"
        )
    rid = f"{fid}::{r['rule_id']}"
    result.rules.append(Rule(
        id=rid,
        rule_id=str(r["rule_id"]),
        anchor_line=anchor_line,
        cond=str(r.get("cond") or ""),
        then=tuple(str(s) for s in (r.get("then") or [])),
        nl="",
        when=str(r.get("when") or ""),
    ))
    result.has_rule.append(HasRule(
        source_id=fid,
        target_id=rid,
        occurrence_id=rid,
        ordinal=ordinal,
        anchor_line=anchor_line,
    ))
    for idx, e in enumerate(r.get("examples") or [], start=1):
        if not isinstance(e, dict):
            raise TypeError(
                f"example transport entry must be an object for {rid}: {type(e).__name__}"
            )
        given = tuple(str(x) for x in (e.get("given") or []))
        then = tuple(str(x) for x in (e.get("then") or []))
        if not (given or then):
            raise ValueError(f"empty example transport for {rid} at ordinal {idx}")
        eid = f"{rid}::E{idx}"
        result.examples.append(Example(
            id=eid, given=given, then=then, kind=str(e.get("kind") or ""),
        ))
        result.has_example.append(HasExample(
            source_id=rid, target_id=eid, occurrence_id=rid, ordinal=idx,
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
                "READ", "INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "UNKNOWN",
            }
            or op_source not in {"SCANNER", "LLM_INFERRED", "UNRESOLVED"}
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


def _collect_questions(fid: str, questions: list, result: ExampleMappingResult) -> None:
    """questions → Question 노드 + HAS_QUESTION 엣지. dict({text,reason}) / str 둘 다 허용."""
    for idx, q in enumerate(questions, start=1):
        if isinstance(q, dict):
            text = (q.get("text") or "").strip()
            reason = (q.get("reason") or "").strip()
        elif isinstance(q, str):
            text, reason = q.strip(), ""
        else:
            raise TypeError(
                f"question transport entry must be an object or string for {fid}: "
                f"{type(q).__name__}"
            )
        if not text:
            raise ValueError(f"empty question transport for {fid} at ordinal {idx}")
        qid = f"{fid}::Q{idx}"
        result.questions.append(Question(id=qid, text=text, reason=reason))
        result.has_question.append(HasQuestion(source_id=fid, target_id=qid))


