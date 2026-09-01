"""Small graph reads over the directly published product nodes."""
from __future__ import annotations

from search.corpus.evidence_reader import read_rules


TABLE_RELATIONS = ("READS", "WRITES")


def callees(session, node_id: str) -> list[dict]:
    return session.run(
        "MATCH (caller {_id:$node_id, _owner:'analyzer'})-[:CALLS]->"
        "(callee {_owner:'analyzer'}) "
        "RETURN callee._id AS id, coalesce(callee.name,'') AS name",
        node_id=node_id,
    ).data()


def call_neighbors(session, node_id: str) -> list[dict]:
    outgoing = session.run(
        "MATCH (caller {_id:$node_id, _owner:'analyzer'})-[:CALLS]->"
        "(callee {_owner:'analyzer'}) "
        "RETURN callee._id AS id, coalesce(callee.name,'') AS name, "
        "'CALLS' AS relation, 'out' AS direction",
        node_id=node_id,
    ).data()
    incoming = session.run(
        "MATCH (caller {_owner:'analyzer'})-[:CALLS]->"
        "(callee {_id:$node_id, _owner:'analyzer'}) "
        "RETURN caller._id AS id, coalesce(caller.name,'') AS name, "
        "'CALLS' AS relation, 'in' AS direction",
        node_id=node_id,
    ).data()
    return outgoing + incoming


def table_neighbors(session, node_id: str) -> list[dict]:
    return session.run(
        "MATCH (routine {_id:$node_id, _owner:'analyzer'})-[access]->"
        "(table:TABLE {_owner:'analyzer'}) "
        "WHERE type(access) IN $relations "
        "RETURN table._id AS id, elementId(table) AS eid, "
        "coalesce(table.name,'') AS name, "
        "coalesce(table.logical_name,'') AS logical_name, "
        "coalesce(table.summary,'') AS summary, type(access) AS relation",
        node_id=node_id,
        relations=list(TABLE_RELATIONS),
    ).data()


def rules_for(session, node_id: str) -> list[dict]:
    return read_rules(session, node_id)
