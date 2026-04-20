"""Example Mapping 의 examples/questions 를 Cypher UNWIND 용 row 리스트로 평탄화.

Framework(함수) 와 DBMS(프로시저) 양쪽에서 공유. Example 의 given/then 이
dict 면 JSON 직렬화(하이브리드 구조), 문자열이면 그대로. then.writes[] 는
(Example)-[:AFFECTS_TABLE {op}]->(Table) 엣지로 승격.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Tuple


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

        if isinstance(then_raw, dict):
            for w in (then_raw.get("writes") or []):
                if not isinstance(w, dict):
                    continue
                tname = str(w.get("table") or "").strip()
                op = str(w.get("op") or "").strip().upper()
                if tname and op:
                    write_edges.append({
                        "example_id": example_id,
                        "table_name": tname,
                        "op": op,
                    })

    return example_rows, write_edges
