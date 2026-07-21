"""Example Mapping 단일 빌더 — analyze_code 3분할 결과 → EM 그래프.

Framework / DBMS **공통 단일 빌더**. anchor 는 ``FUNCTION`` 하나.

흐름: 분석된 CodeNode 들의 ``extra["rules" / "questions"]``
→ Rule(공유)·Example·Question 노드 + HAS_RULE / HAS_EXAMPLE / HAS_QUESTION /
  AFFECTS_TABLE 엣지. 결정론 — LLM 0회.

Rule 공유: 같은 statement 는 ``rule_hash`` 가 같아 1 노드 — 코드노드 간 규칙을 공유한다.
examples 는 소속 rule 하위 중첩.

응답 형식 단일 진실 ([[code_analysis_output]] RulesExamplesOutput):
- rules:    {statement, examples:[{given, when, then, affected_tables}]}
- questions:{text, reason}
"""
from __future__ import annotations

import hashlib
import re
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


def rule_hash(statement: str) -> str:
    """정규화 statement → MD5[:12]. 같은 의미 문장 = 같은 해시 (Rule 노드 공유)."""
    normalized = re.sub(r"\s+", " ", (statement or "").lower().strip())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]


def build_example_mapping(code_nodes: list[CodeNode]) -> ExampleMappingResult:
    """분석된 CodeNode 들 → Example Mapping 노드·엣지 (결정론, LLM 0)."""
    result = ExampleMappingResult()
    rule_by_hash: dict[str, Rule] = {}
    for fn in code_nodes:
        _build_one(fn, result, rule_by_hash)
    result.rules = list(rule_by_hash.values())
    return result


def _build_one(
    fn: CodeNode, result: ExampleMappingResult, rule_by_hash: dict[str, Rule],
) -> None:
    """코드노드 1개의 rules(하위 examples)/questions → 노드·엣지 누적."""
    extra = fn.extra or {}
    _collect_rules(fn.id, extra.get("rules") or [], result, rule_by_hash)
    _collect_questions(fn.id, extra.get("questions") or [], result)


def _collect_rules(
    fid: str, rules: list, result: ExampleMappingResult, rule_by_hash: dict[str, Rule],
) -> None:
    """rules → Rule 노드(공유) + HAS_RULE + 하위 examples(HAS_EXAMPLE·AFFECTS_TABLE)."""
    for r in rules:
        if not isinstance(r, dict):
            continue
        statement = (r.get("statement") or "").strip()
        if not statement:
            continue
        rh = rule_hash(statement)
        rule_by_hash.setdefault(rh, Rule(id=rh, statement=statement))
        result.has_rule.append(HasRule(source_id=fid, target_id=rh))
        _collect_examples(fid, rh, r.get("examples") or [], result)


def _collect_examples(
    fid: str, rh: str, examples: list, result: ExampleMappingResult,
) -> None:
    """rule 하위 examples → Example 노드 + HAS_EXAMPLE + AFFECTS_TABLE."""
    for idx, e in enumerate(examples, start=1):
        if not isinstance(e, dict):
            continue
        given = (e.get("given") or "").strip()
        when = (e.get("when") or "").strip()
        then = (e.get("then") or "").strip()
        if not (given or when or then):
            continue
        eid = f"{fid}::{rh}::{idx}"
        result.examples.append(Example(id=eid, given=given, when=when, then=then))
        result.has_example.append(HasExample(source_id=rh, target_id=eid))
        _collect_affects_table(eid, e.get("affected_tables") or [], result)


def _collect_affects_table(
    eid: str, affected: list, result: ExampleMappingResult,
) -> None:
    """affected_tables[] → AFFECTS_TABLE 엣지 (op enum 별)."""
    for entry in affected:
        if not isinstance(entry, dict):
            continue
        table = str(entry.get("table") or "").strip()
        op = str(entry.get("op") or "").strip().upper()
        if not table or not op:
            continue
        result.affects_table.append(
            AffectsTable(source_id=eid, target_id=table, op=op)
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
            continue
        if not text:
            continue
        qid = f"{fid}::Q{idx}"
        result.questions.append(Question(id=qid, text=text, reason=reason))
        result.has_question.append(HasQuestion(source_id=fid, target_id=qid))


