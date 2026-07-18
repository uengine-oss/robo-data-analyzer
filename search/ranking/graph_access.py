"""Scoped graph reads used by semantic retrieval."""
from __future__ import annotations

from graph.ownership import ANALYZER_GRAPH_OWNER
from search.corpus.evidence_reader import read_rules

TABLE_RELATIONS = ("READS", "WRITES", "AFFECTS_TABLE")


def callees(session, code_id: str) -> list[dict]:
    return session.run(
        "MATCH (x {id:$id, graph_owner:$graph_owner})-[:CALLS]->"
        "(b {graph_owner:$graph_owner}) WHERE b.id IS NOT NULL "
        "RETURN b.id AS id, coalesce(b.name,'') AS name",
        id=code_id,
        graph_owner=ANALYZER_GRAPH_OWNER,
    ).data()


def call_neighbors(session, code_id: str) -> list[dict]:
    outgoing = session.run(
        "MATCH (a {id:$id, graph_owner:$graph_owner})-[:CALLS]->"
        "(b {graph_owner:$graph_owner}) WHERE b.id IS NOT NULL "
        "RETURN b.id AS id, coalesce(b.name,'') AS name, 'CALLS' AS relation, 'out' AS direction",
        id=code_id,
        graph_owner=ANALYZER_GRAPH_OWNER,
    ).data()
    incoming = session.run(
        "MATCH (a {graph_owner:$graph_owner})-[:CALLS]->"
        "(b {id:$id, graph_owner:$graph_owner}) WHERE a.id IS NOT NULL "
        "RETURN a.id AS id, coalesce(a.name,'') AS name, 'CALLS' AS relation, 'in' AS direction",
        id=code_id,
        graph_owner=ANALYZER_GRAPH_OWNER,
    ).data()
    return outgoing + incoming


def table_neighbors(session, code_id: str) -> list[dict]:
    return session.run(
        "MATCH (a {id:$id, graph_owner:$graph_owner})-[r]->"
        "(t:TABLE {graph_owner:$graph_owner}) "
        "WHERE type(r) IN $rels AND t.id IS NOT NULL "
        "RETURN t.id AS id, elementId(t) AS eid, coalesce(t.name,'') AS name, "
        "coalesce(t.logical_name,'') AS logical_name, "
        "coalesce(t.analyzed_description, t.description, '') AS summary, type(r) AS relation",
        id=code_id,
        rels=list(TABLE_RELATIONS),
        graph_owner=ANALYZER_GRAPH_OWNER,
    ).data()


def rules_for(session, code_id: str) -> list[dict]:
    return read_rules(session, code_id)
