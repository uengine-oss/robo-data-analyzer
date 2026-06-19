"""Example Mapping 단일 빌더 — analyze_code 3분할 결과 → EM 그래프.

Framework / DBMS **공통 단일 빌더**. 원본은 strategy 별 anchor(Framework=FUNCTION /
DBMS=PROCEDURE)가 달라 분기가 있었으나, refact 는 M5 에서 DBMS range 도 FUNCTION 으로
통일 → anchor 가 ``FUNCTION`` 하나 → 분기조차 불필요.

흐름: 분석된 CodeNode 들의 ``extra["rules" / "examples" / "questions"]``
→ Rule(공유)·Example·Question 노드 + HAS_RULE / HAS_EXAMPLE / HAS_QUESTION /
  NEXT / BRANCH / AFFECTS_TABLE 엣지. 결정론 — LLM 0회.

Rule 공유: 같은 statement 는 ``rule_hash`` 가 같아 1 노드 — 코드노드 간 규칙을 공유한다.
per-function 정보(local rule_id / flow_id / coupled_domains)는 ``HAS_RULE`` 엣지 속성에.

응답 형식 단일 진실 ([[code_analysis_output]] RulesExamplesOutput):
- rules:    {rule_id, statement, flow_id, coupled_domains}
- examples: {rule_id, description, given, when, then, affected_tables}
- questions:{text, reason}
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from data_models.graph_edges import (
    AffectsTable,
    Branch,
    HasExample,
    HasQuestion,
    HasRule,
    Next,
)
from data_models.graph_nodes import Example, CodeNode, Question, Rule


@dataclass
class ExampleMappingResult:
    """EM 빌더 산출물 — 노드 3종 + 엣지 6종."""
    rules: list[Rule] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    has_rule: list[HasRule] = field(default_factory=list)
    has_example: list[HasExample] = field(default_factory=list)
    has_question: list[HasQuestion] = field(default_factory=list)
    next_edges: list[Next] = field(default_factory=list)
    branch_edges: list[Branch] = field(default_factory=list)
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
    """코드노드 1개의 rules/examples/questions → 노드·엣지 누적."""
    extra = fn.extra or {}
    flow_by_rid, hash_by_rid = _collect_rules(
        fn.id, extra.get("rules") or [], result, rule_by_hash,
    )
    _collect_examples(fn.id, extra.get("examples") or [], hash_by_rid, result)
    _collect_questions(fn.id, extra.get("questions") or [], result)
    _collect_flow_edges(fn.id, flow_by_rid, result)


def _collect_rules(
    fid: str, rules: list, result: ExampleMappingResult, rule_by_hash: dict[str, Rule],
) -> tuple[dict[str, str], dict[str, str]]:
    """rules → Rule 노드(공유) + HAS_RULE 엣지. (flow_by_rid, hash_by_rid) 반환."""
    flow_by_rid: dict[str, str] = {}
    hash_by_rid: dict[str, str] = {}
    for r in rules:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("rule_id") or "").strip()
        statement = (r.get("statement") or "").strip()
        if not rid or not statement:
            continue
        rh = rule_hash(statement)
        rule_by_hash.setdefault(rh, Rule(id=rh, statement=statement))
        flow_id = str(r.get("flow_id") or "").strip() or rid
        flow_by_rid[rid] = flow_id
        hash_by_rid[rid] = rh
        result.has_rule.append(HasRule(
            source_id=fid,
            target_id=rh,
            local_rule_id=rid,
            flow_id=flow_id,
            coupled_domains=tuple(
                str(d).strip() for d in (r.get("coupled_domains") or []) if str(d).strip()
            ),
        ))
    return flow_by_rid, hash_by_rid


def _collect_examples(
    fid: str, examples: list, hash_by_rid: dict[str, str], result: ExampleMappingResult,
) -> None:
    """examples → Example 노드 + HAS_EXAMPLE + AFFECTS_TABLE."""
    for idx, e in enumerate(examples, start=1):
        if not isinstance(e, dict):
            continue
        rid = str(e.get("rule_id") or "").strip()
        rh = hash_by_rid.get(rid)
        if rh is None:
            continue   # 존재하지 않는 Rule 참조 — 드롭
        given = (e.get("given") or "").strip()
        when = (e.get("when") or "").strip()
        then = (e.get("then") or "").strip()
        if not (given or when or then):
            continue
        eid = f"{fid}::{rid}::{idx}"
        result.examples.append(Example(
            id=eid,
            description=(e.get("description") or "").strip(),
            given=given, when=when, then=then,
        ))
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
            AffectsTable(source_id=eid, table_name=table, op=op)
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


def _collect_flow_edges(
    fid: str,
    flow_by_rid: dict[str, str],
    result: ExampleMappingResult,
) -> None:
    """flow_id 로 Rule 간 NEXT(메인 흐름) / BRANCH(분기) 도출.

    flow_id 에 ``-`` 없음 = 메인 chain → flow_id 순서대로 NEXT.
    ``-`` 있음 = 분기 → 상위 flow_id (``rsplit("-", 1)[0]``) 의 Rule 에서 BRANCH.
    """
    main_rids = sorted(
        (rid for rid, flow in flow_by_rid.items() if "-" not in flow),
        key=lambda rid: flow_by_rid[rid],
    )
    for from_rid, to_rid in zip(main_rids, main_rids[1:]):
        result.next_edges.append(Next(owner_id=fid, from_rule_id=from_rid, to_rule_id=to_rid))

    flow_to_rid = {flow: rid for rid, flow in flow_by_rid.items()}
    for rid, flow in flow_by_rid.items():
        if "-" not in flow:
            continue
        parent = flow_to_rid.get(flow.rsplit("-", 1)[0])
        if parent and parent in flow_by_rid:
            result.branch_edges.append(
                Branch(owner_id=fid, from_rule_id=parent, to_rule_id=rid)
            )


