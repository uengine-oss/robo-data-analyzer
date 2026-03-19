"""Neo4j 쿼리 생성 공통 헬퍼."""

from __future__ import annotations

from typing import Any, Callable, Dict, List


def _merge_node_query(label: str, key_field: str, key_val, props: dict, sub_label: str | None = None) -> dict:
    """Generic MERGE (n:Label:SubLabel {key: $key}) ON CREATE SET ... ON MATCH SET ... builder.

    label: primary 역할 라벨 (MODULE, FUNCTION, VARIABLE, CONSTANT 등)
    sub_label: 실제 AST 타입 서브레이블 (CLASS, METHOD, DEFINE 등). None이면 생략.
    """
    # kind 속성은 더 이상 저장하지 않음 — 서브레이블로 대체
    filtered_props = {k: v for k, v in props.items() if k != "kind"}

    set_parts = [f"n.{key_field} = ${key_field}"]
    params = {key_field: key_val}
    for prop, val in filtered_props.items():
        set_parts.append(
            f"n.{prop} = CASE WHEN ${prop} IS NOT NULL AND trim(toString(${prop})) <> '' "
            f"THEN ${prop} ELSE n.{prop} END"
        )
        params[prop] = val
    set_clause = ", ".join(set_parts)

    # primary와 sub_label이 동일하면 서브레이블 생략 (Neo4j는 동일 라벨 중복 불가)
    # 예: C FUNCTION → primary=FUNCTION, sub=FUNCTION → 그냥 :FUNCTION
    if sub_label and sub_label != label:
        label_str = f"{label}:{sub_label}"
    else:
        label_str = label
    q = (
        f"MERGE (n:{label_str} {{{key_field}: ${key_field}}})\n"
        f"ON CREATE SET {set_clause}, n.created_at = timestamp()\n"
        f"ON MATCH SET {set_clause}, n.updated_at = timestamp()\n"
        "RETURN n"
    )
    return {"query": q, "params": params}


def _merge_rel_query(
    src_label: str, src_key: str, src_val,
    dst_label: str, dst_key: str, dst_val,
    rel_type: str, props: dict | None = None,
) -> dict:
    """Generic MERGE (a)-[r:REL]->(b) builder."""
    src_param = f"src_{src_key}"
    dst_param = f"dst_{dst_key}"
    q = (
        f"MATCH (a:{src_label} {{{src_key}: ${src_param}}})\n"
        f"MATCH (b:{dst_label} {{{dst_key}: ${dst_param}}})\n"
        f"MERGE (a)-[r:{rel_type}]->(b)\n"
        "SET r.updated_at = timestamp()\n"
        "RETURN r"
    )
    params: dict = {src_param: src_val, dst_param: dst_val}
    if props:
        params.update(props)
    return {"query": q, "params": params}


def _build_unwind_query(label: str, merge_key: str, set_clause: str) -> Callable:
    """Returns a function that takes rows and returns {query, params}."""
    q = (
        "UNWIND $rows AS row\n"
        f"MERGE (n:{label} {{{merge_key}: row.{merge_key}}})\n"
        "ON CREATE SET n.created_at = timestamp()\n"
        f"SET {set_clause}, n.updated_at = timestamp()"
    )

    def _fn(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not rows:
            return {"query": "", "params": {"rows": []}}
        return {"query": q, "params": {"rows": rows}}

    return _fn
