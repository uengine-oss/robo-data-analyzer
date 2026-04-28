"""Example Mapping 도메인 — Rule / Example / Question 처리.

원본 위치 (이전):
  - `analyzer/common/cypher/example_mapping.py` (Cypher 빌더)
  - `analyzer/common/llm/example_collector.py` (LLM 결과 평탄화)

Framework·DBMS 양쪽 strategy 가 공유. EM 데이터 흐름:
  1. LLM 응답 dict → ``collect_example_rows`` 가 평탄화 (Cypher UNWIND row 형태)
  2. ``EmBatch`` 로 anchor 단위 묶음
  3. ``build_em_queries`` 로 Cypher 쿼리 리스트 생성
  4. ``rule_hash`` 로 같은 의미 statement 의 동일 해시 부여 (Rule 노드 공유)

Cypher 의 anchor 노드 키만 strategy 별로 다르다 (Framework: FUNCTION.function_id,
DBMS: PROCEDURE composite key). 본 모듈은 그 공통 로직을 하나의 빌더로 통합한다.

정책 정렬:
  - 잘못된 LLM 응답(dict 가 아닌 항목 등)은 errlog + skip — silent 가 아니라 명시적 errlog.
  - rule_id / statement / text 가 비면 해당 row drop (errlog 없이 skip 정상 — LLM 의
    optional 항목).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

# then.writes 의 op 가 누락됐거나 single-string 형식일 때의 generic op.
# 정확한 op (INSERT/UPDATE/DELETE) 가 필요하면 LLM 출력의 then.writes[].op 또는 부모 노드 라벨로 추적.
DEFAULT_WRITE_OP = "WRITE"


def _parse_write_entry(w: Any) -> Tuple[str, str]:
    """LLM 출력의 then.writes[] 한 항목 → (table_name, op).

    허용 형식:
      - dict (정식): ``{"table": "X", "op": "INSERT", "columns": {...}}`` — op 누락 시 generic
      - string (간이): ``"X"`` — op generic
      - 그 외: 무시 (("", "") 반환 → 호출자가 skip)
    """
    if isinstance(w, dict):
        return (
            str(w.get("table") or "").strip(),
            str(w.get("op") or "").strip().upper() or DEFAULT_WRITE_OP,
        )
    if isinstance(w, str):
        return w.strip(), DEFAULT_WRITE_OP
    return "", ""


# ═══════════════════════════════════════════════════════════════════════════
# LLM 응답 평탄화 — examples / questions → Cypher UNWIND row
# ═══════════════════════════════════════════════════════════════════════════


def _serialize(raw: Any) -> str:
    """dict/list 는 JSON 문자열, 그 외는 strip 된 문자열. 빈 dict/list 는 빈 문자열."""
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, ensure_ascii=False) if raw else ""
    return str(raw or "").strip()


def collect_example_rows(
    examples: Iterable[Any],
    valid_rule_ids: Iterable[str],
    example_id_factory,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """examples 를 example_rows + example_write_edges 로 평탄화.

    Args:
        examples: LLM 응답의 examples 리스트.
        valid_rule_ids: 수집된 Rule 의 rule_id 집합 — 참조 없는 Example 은 드롭.
        example_id_factory: (rule_id, idx) → example_id 문자열. 네이밍은 호출자가 결정.

    Returns:
        (example_rows, example_write_edges)
        - example_rows: {rule_id, example_id, description, given, when_, then_, is_boundary}
        - example_write_edges: {example_id, table_name, op}  (then.writes[] 유래)

    given/then 이 dict 면 JSON 직렬화 (하이브리드 구조), 문자열이면 strip.
    then.writes[] 는 (Example)-[:AFFECTS_TABLE {op}]->(Table) 엣지로 승격.
    """
    valid = set(valid_rule_ids)
    example_rows: List[Dict[str, Any]] = []
    write_edges: List[Dict[str, Any]] = []

    for idx, e in enumerate(examples or [], start=1):
        if not isinstance(e, dict):
            continue
        rid = str(e.get("rule_id", "")).strip()
        if rid not in valid:
            continue

        given_raw = e.get("given")
        then_raw = e.get("then")
        given = _serialize(given_raw)
        when_ = str(e.get("when") or "").strip()
        then_ = _serialize(then_raw)
        if not (given or when_ or then_):
            continue

        example_id = example_id_factory(rid, idx)
        example_rows.append({
            "rule_id": rid,
            "example_id": example_id,
            "description": (e.get("description") or "").strip(),
            "given": given, "when_": when_, "then_": then_,
            "is_boundary": bool(e.get("is_boundary")),
        })

        # then.writes[] → AFFECTS_TABLE 엣지. LLM 형식 다양성 흡수 (dict / string / op 누락 dict 모두).
        if isinstance(then_raw, dict):
            for w in (then_raw.get("writes") or []):
                tname, op = _parse_write_entry(w)
                if tname:
                    write_edges.append({"example_id": example_id, "table_name": tname, "op": op})

    return example_rows, write_edges


# ═══════════════════════════════════════════════════════════════════════════
# Rule 해시 — 같은 의미 statement = 같은 해시 (함수 간 Rule 공유용)
# ═══════════════════════════════════════════════════════════════════════════


def rule_hash(statement: str) -> str:
    """Rule 정규화 해시 — 같은 의미 문장은 같은 해시.

    공백 정규화 + 소문자화 후 MD5 12자리. 의미 보존이 아닌 "동일 statement 식별" 용.
    """
    normalized = re.sub(r'\s+', ' ', (statement or "").lower().strip())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════════════
# EmBatch — anchor 단위 입력
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class EmBatch:
    """Example Mapping 의 anchor 단위 입력.

    여러 EmBatch 를 한 번에 build_em_queries 에 넘기면 anchor MATCH 가 UNWIND 로
    병렬 매칭된다.

    Attributes:
        em_node_id:   Example/Question id 의 namespace prefix.
                      Framework: function_id (예: ``com.foo.Bar.baz``)
                      DBMS:      ``{full_directory}/{file_name}:{procedure_name}``
        anchor_keys:  anchor MATCH 절의 ``row.xxx`` 참조 키 → 값.
                      Framework: ``{"function_id": fid}``
                      DBMS:      ``{"schema_name": ..., "procedure_name": ...,
                                    "directory": ..., "file_name": ...}``
        rules:        LLM 응답의 raw rules 리스트 (각 항목은 dict, 형식 검증은 본 모듈에서)
        examples:     동상
        questions:    동상
    """

    em_node_id: str
    anchor_keys: Dict[str, Any]
    rules: List[Dict[str, Any]] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)
    questions: List[Dict[str, Any]] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Cypher 쿼리 빌더 — anchor 별 EM 그래프 생성
# ═══════════════════════════════════════════════════════════════════════════


def build_em_queries(
    anchor_match: str,
    batches: List[EmBatch],
    *,
    rule_rel_extra_props: Iterable[str] = (
        "flow_id", "guard_rule_id", "branch_from", "coupled_domains",
    ),
    flow_rel_id_field: str = "function_id",
) -> List[Dict[str, Any]]:
    """anchor 별 EM 그래프(Rule/Example/Question + 관계) Cypher 쿼리 리스트 반환.

    Args:
        anchor_match: anchor MATCH 절. 변수명은 반드시 ``a`` 로 통일.
                      예) ``MATCH (a:FUNCTION {function_id: row.function_id})``
                      예) ``MATCH (a:PROCEDURE {schema_name: row.schema_name,
                          procedure_name: row.procedure_name, directory: row.directory,
                          file_name: row.file_name})``
        batches:      EmBatch 리스트 (anchor 별 EM 데이터 묶음)
        rule_rel_extra_props: HAS_RULE 관계에 SET 할 추가 속성. 기본은 framework/dbms
                      공통(flow_id/guard_rule_id/branch_from/coupled_domains).
        flow_rel_id_field:    Rule 간 NEXT/BRANCH 관계 식별용 키 이름.
                      Framework: ``function_id``, DBMS: ``procedure_id``.

    Returns:
        Cypher 쿼리 dict 리스트. 호출자는 graph_writer.run_graph_queries 등으로 실행.
    """
    if not batches:
        return []

    # ─── (a) 평탄화: anchor 별 raw → row ───────────────────────────────
    rule_rows: List[Dict[str, Any]] = []
    example_rows: List[Dict[str, Any]] = []
    example_write_edges: List[Dict[str, Any]] = []
    question_rows: List[Dict[str, Any]] = []
    next_edges: List[Dict[str, Any]] = []
    branch_edges: List[Dict[str, Any]] = []

    for batch in batches:
        flow_by_rid: Dict[str, str] = {}
        branch_by_rid: Dict[str, Any] = {}

        for r in batch.rules:
            if not isinstance(r, dict):
                logger.error(
                    "[EM_CYPHER] rule 항목이 dict 아님 — skip | em_node_id=%s type=%s",
                    batch.em_node_id, type(r).__name__,
                )
                continue
            rid = str(r.get("rule_id", "")).strip()
            statement = (r.get("statement") or "").strip()
            if not rid or not statement:
                continue
            flow_id = str(r.get("flow_id", "")).strip() or rid
            branch_from = r.get("branch_from") or None
            flow_by_rid[rid] = flow_id
            branch_by_rid[rid] = branch_from
            row: Dict[str, Any] = {
                **batch.anchor_keys,
                "rule_id": rid,
                "rule_hash": rule_hash(statement),
                "statement": statement,
                "flow_id": flow_id,
                "guard_rule_id": r.get("guard_rule_id") or None,
                "branch_from": branch_from,
                "coupled_domains": [
                    str(d).strip()
                    for d in (r.get("coupled_domains") or [])
                    if str(d).strip()
                ],
            }
            rule_rows.append(row)

        # Example: 평탄화 — given/then dict→JSON 직렬화, then.writes[] → AFFECTS_TABLE 엣지
        rows, writes = collect_example_rows(
            batch.examples,
            valid_rule_ids=flow_by_rid.keys(),
            example_id_factory=lambda rid, idx: f"{batch.em_node_id}::{rid}::{idx}",
        )
        for r in rows:
            r.update(batch.anchor_keys)
            example_rows.append(r)
        example_write_edges.extend(writes)

        for idx, q in enumerate(batch.questions, start=1):
            if not isinstance(q, dict):
                logger.error(
                    "[EM_CYPHER] question 항목이 dict 아님 — skip | em_node_id=%s type=%s",
                    batch.em_node_id, type(q).__name__,
                )
                continue
            text = (q.get("text") or "").strip()
            if not text:
                continue
            question_rows.append({
                **batch.anchor_keys,
                "question_id": f"{batch.em_node_id}::Q{idx}",
                "text": text,
                "reason": (q.get("reason") or "").strip(),
            })

        # Rule 간 흐름 관계: 메인 NEXT (flow 가 ``-`` 없는 것 = 같은 flow chain)
        # 분기 BRANCH (branch_from 명시 또는 flow="X-Y" 형식)
        main_rids = sorted(
            (rid for rid, flow in flow_by_rid.items() if "-" not in flow),
            key=lambda rid: flow_by_rid[rid],
        )
        for i in range(len(main_rids) - 1):
            next_edges.append({
                **batch.anchor_keys,
                flow_rel_id_field: batch.em_node_id,
                "from_rule_id": main_rids[i],
                "to_rule_id": main_rids[i + 1],
            })
        flow_to_rid = {flow: rid for rid, flow in flow_by_rid.items()}
        for rid, flow in flow_by_rid.items():
            if "-" not in flow:
                continue
            parent_rid = (
                branch_by_rid.get(rid)
                or flow_to_rid.get(flow.rsplit("-", 1)[0])
            )
            if parent_rid and parent_rid in flow_by_rid:
                branch_edges.append({
                    **batch.anchor_keys,
                    flow_rel_id_field: batch.em_node_id,
                    "from_rule_id": parent_rid,
                    "to_rule_id": rid,
                })

    # ─── (b) Cypher 조립 ──────────────────────────────────────────────
    queries: List[Dict[str, Any]] = []

    # Rule 노드 (rule_hash 공유) + HAS_RULE 관계 + 추가 속성
    if rule_rows:
        rel_set_lines = ",\n".join(
            f"    rel.{prop} = row.{prop}" for prop in rule_rel_extra_props
        )
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                f"{anchor_match}\n"
                "MERGE (r:Rule {rule_hash: row.rule_hash})\n"
                "  ON CREATE SET r.statement = row.statement, r.created_at = timestamp()\n"
                "MERGE (a)-[rel:HAS_RULE {local_id: row.rule_id}]->(r)\n"
                f"SET {rel_set_lines},\n"
                "    rel.updated_at = timestamp()\n"
                "RETURN a, rel, r"
            ),
            "params": {"rows": rule_rows},
        })

    # Example 노드 + HAS_EXAMPLE 관계
    # given/then 은 하이브리드 JSON 직렬화 문자열 (apoc.convert.fromJsonMap 으로 역직렬화).
    if example_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                f"{anchor_match}\n"
                "MATCH (a)-[:HAS_RULE {local_id: row.rule_id}]->(r:Rule)\n"
                "MERGE (e:Example {example_id: row.example_id})\n"
                "SET e.description = row.description,\n"
                "    e.given = row.given, e.when_ = row.when_, e.then_ = row.then_,\n"
                "    e.is_boundary = row.is_boundary, e.updated_at = timestamp()\n"
                "MERGE (r)-[:HAS_EXAMPLE]->(e)\n"
                "RETURN r, e"
            ),
            "params": {"rows": example_rows},
        })

    # Example → Table AFFECTS_TABLE 엣지 (then.writes[] 로부터)
    if example_write_edges:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (e:Example {example_id: row.example_id})\n"
                "MATCH (t:Table) WHERE toLower(t.name) = toLower(row.table_name)\n"
                "MERGE (e)-[rel:AFFECTS_TABLE {op: row.op}]->(t)\n"
                "SET rel.updated_at = timestamp()\n"
                "RETURN e, rel, t"
            ),
            "params": {"rows": example_write_edges},
        })

    # Question 노드 + HAS_QUESTION 관계
    if question_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                f"{anchor_match}\n"
                "MERGE (q:Question {question_id: row.question_id})\n"
                "  ON CREATE SET q.text = row.text, q.reason = row.reason, q.created_at = timestamp()\n"
                "  ON MATCH  SET q.text = row.text, q.reason = row.reason, q.updated_at = timestamp()\n"
                "MERGE (a)-[:HAS_QUESTION]->(q)\n"
                "RETURN a, q"
            ),
            "params": {"rows": question_rows},
        })

    # Rule 간 NEXT(메인 흐름) / BRANCH(분기) 관계
    for rel_type, edges in (("NEXT", next_edges), ("BRANCH", branch_edges)):
        if not edges:
            continue
        queries.append({
            "query": (
                f"UNWIND $rows AS row\n"
                f"{anchor_match}\n"
                f"MATCH (a)-[:HAS_RULE {{local_id: row.from_rule_id}}]->(r1:Rule)\n"
                f"MATCH (a)-[:HAS_RULE {{local_id: row.to_rule_id}}]->(r2:Rule)\n"
                f"MERGE (r1)-[:{rel_type} {{{flow_rel_id_field}: row.{flow_rel_id_field}}}]->(r2)"
            ),
            "params": {"rows": edges},
        })

    return queries


__all__ = [
    "EmBatch",
    "build_em_queries",
    "collect_example_rows",
    "rule_hash",
]
