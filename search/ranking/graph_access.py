"""Generation-scoped graph reads for current published product nodes."""
from __future__ import annotations

from search.corpus.evidence_reader import read_rules


TABLE_RELATIONS = ("READS", "WRITES")


def callees(session, code_stable_id: str) -> list[dict]:
    return session.run(
        "MATCH (caller:CodeNode {stable_id:$stable_id, "
        "analysis_scope_id:$analysis_scope_id, generation_id:$generation_id})"
        "-[:CALLS]->(callee:CodeNode {analysis_scope_id:$analysis_scope_id, "
        "generation_id:$generation_id}) "
        "RETURN callee.stable_id AS id, coalesce(callee.name,'') AS name",
        stable_id=code_stable_id,
    ).data()


def call_neighbors(session, code_stable_id: str) -> list[dict]:
    outgoing = session.run(
        "MATCH (caller:CodeNode {stable_id:$stable_id, "
        "analysis_scope_id:$analysis_scope_id, generation_id:$generation_id})"
        "-[:CALLS]->(callee:CodeNode {analysis_scope_id:$analysis_scope_id, "
        "generation_id:$generation_id}) "
        "RETURN callee.stable_id AS id, coalesce(callee.name,'') AS name, "
        "'CALLS' AS relation, 'out' AS direction",
        stable_id=code_stable_id,
    ).data()
    incoming = session.run(
        "MATCH (caller:CodeNode {analysis_scope_id:$analysis_scope_id, "
        "generation_id:$generation_id})-[:CALLS]->"
        "(callee:CodeNode {stable_id:$stable_id, "
        "analysis_scope_id:$analysis_scope_id, generation_id:$generation_id}) "
        "RETURN caller.stable_id AS id, coalesce(caller.name,'') AS name, "
        "'CALLS' AS relation, 'in' AS direction",
        stable_id=code_stable_id,
    ).data()
    return outgoing + incoming


def table_neighbors(session, code_stable_id: str) -> list[dict]:
    return session.run(
        "MATCH (routine:CodeNode {stable_id:$stable_id, "
        "analysis_scope_id:$analysis_scope_id, generation_id:$generation_id})-[access]->"
        "(table:Table {analysis_scope_id:$analysis_scope_id, "
        "generation_id:$generation_id}) "
        "WHERE type(access) IN $relations "
        "RETURN table.stable_id AS id, elementId(table) AS eid, "
        "coalesce(table.physical_table_name,'') AS name, "
        "coalesce(table.logical_name,'') AS logical_name, "
        "coalesce(table.analyzed_description,'') AS summary, "
        "type(access) AS relation",
        stable_id=code_stable_id,
        relations=list(TABLE_RELATIONS),
    ).data()


def rules_for(session, code_stable_id: str) -> list[dict]:
    return read_rules(session, code_stable_id)
