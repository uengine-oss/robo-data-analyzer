"""Publish one already-closed product graph in one Neo4j transaction."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from graph.neo4j_client import Neo4jClient
from graph.product_graph import GRAPH_OWNER, ProductGraph


ROUTINE_LABELS = ("FUNCTION", "PROCEDURE", "METHOD", "TRIGGER")


class ProductWriteError(RuntimeError):
    """Neo4j did not persist exactly the validated product graph."""


@dataclass(frozen=True)
class ProductWriteReceipt:
    graph_sha256: str
    node_count: int
    relationship_count: int


async def ensure_product_schema(client: Neo4jClient, graph: ProductGraph) -> None:
    """Install only constraints/indexes used by the direct product graph."""
    labels = sorted({node.label for node in graph.nodes})
    for label in labels:
        await client.run_query(
            f"CREATE CONSTRAINT uniq_{label.lower()}_id IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n._id IS UNIQUE"
        )
    present = set(labels)
    for label in ROUTINE_LABELS:
        if label not in present:
            continue
        await client.run_query(
            f"CREATE VECTOR INDEX {label.lower()}_search_embedding IF NOT EXISTS "
            f"FOR (n:{label}) ON n._search_embedding "
            "OPTIONS {indexConfig: {`vector.similarity_function`: 'cosine'}}"
        )


async def replace_product_graph(
    client: Neo4jClient,
    graph: ProductGraph,
) -> ProductWriteReceipt:
    """Replace Analyzer-owned products atomically after in-memory validation."""
    await ensure_product_schema(client, graph)

    nodes_by_label: dict[str, list[dict[str, object]]] = defaultdict(list)
    for node in graph.nodes:
        nodes_by_label[node.label].append(node.write_properties())
    relationships_by_type: dict[str, list[dict[str, object]]] = defaultdict(list)
    for relationship in graph.relationships:
        relationships_by_type[relationship.relationship_type].append({
            "source_id": relationship.source_id,
            "target_id": relationship.target_id,
            "properties": relationship.write_properties(),
        })

    async def _replace(transaction) -> ProductWriteReceipt:
        await transaction.run(
            """
MATCH ()-[relationship]-()
WHERE relationship._owner = $owner
DELETE relationship
""",
            owner=GRAPH_OWNER,
        )
        await transaction.run(
            """
MATCH (node)
WHERE node._owner = $owner
  AND NOT node:TABLE AND NOT node:COLUMN
DETACH DELETE node
""",
            owner=GRAPH_OWNER,
        )
        for label, items in sorted(nodes_by_label.items()):
            if label in {"TABLE", "COLUMN"}:
                result = await transaction.run(
                    f"""
UNWIND $items AS item
MERGE (node:{label} {{_id: item._id}})
WITH node, item, node.description AS saved_description,
     node.logical_name AS saved_logical_name
SET node = item
SET node.description = coalesce(saved_description, item.description),
    node.logical_name = coalesce(saved_logical_name, item.logical_name)
RETURN count(node) AS written
""",
                    items=items,
                )
            else:
                result = await transaction.run(
                    f"""
UNWIND $items AS item
MERGE (node:{label} {{_id: item._id}})
SET node = item
RETURN count(node) AS written
""",
                    items=items,
                )
            row = await result.single(strict=True)
            if int(row["written"]) != len(items):
                raise ProductWriteError(
                    f"{label} write count differs: {row['written']} != {len(items)}"
                )

        for relationship_type, items in sorted(relationships_by_type.items()):
            merge = (
                "MERGE (source)-[relationship:FK {_owner: $owner, "
                "from_column: item.properties.from_column, "
                "to_column: item.properties.to_column}]->(target)"
                if relationship_type == "FK"
                else f"MERGE (source)-[relationship:{relationship_type} "
                     "{_owner: $owner}]->(target)"
            )
            result = await transaction.run(
                f"""
UNWIND $items AS item
MATCH (source {{_id: item.source_id}})
MATCH (target {{_id: item.target_id}})
{merge}
SET relationship = item.properties
RETURN count(relationship) AS written
""",
                items=items,
                owner=GRAPH_OWNER,
            )
            row = await result.single(strict=True)
            if int(row["written"]) != len(items):
                raise ProductWriteError(
                    f"{relationship_type} write count differs: "
                    f"{row['written']} != {len(items)}"
                )

        node_result = await transaction.run(
            """
UNWIND $ids AS id
MATCH (node {_id: id, _owner: $owner})
RETURN count(node) AS matched
""",
            ids=[node.node_id for node in graph.nodes],
            owner=GRAPH_OWNER,
        )
        node_row = await node_result.single(strict=True)
        if int(node_row["matched"]) != len(graph.nodes):
            raise ProductWriteError("product node readback is incomplete")

        relationship_result = await transaction.run(
            """
UNWIND $items AS item
MATCH (source {_id: item.source_id})
      -[relationship]->
      (target {_id: item.target_id})
WHERE type(relationship) = item.relationship_type
  AND relationship._owner = $owner
  AND (item.relationship_type <> 'FK' OR (
    relationship[item.from_column_key] = item.from_column
    AND relationship[item.to_column_key] = item.to_column
  ))
RETURN count(relationship) AS matched
""",
            items=[{
                "relationship_type": relationship.relationship_type,
                "source_id": relationship.source_id,
                "target_id": relationship.target_id,
                "from_column": relationship.properties.get("from_column"),
                "to_column": relationship.properties.get("to_column"),
                "from_column_key": "from_column",
                "to_column_key": "to_column",
            } for relationship in graph.relationships],
            owner=GRAPH_OWNER,
        )
        relationship_row = await relationship_result.single(strict=True)
        if int(relationship_row["matched"]) != len(graph.relationships):
            raise ProductWriteError("product relationship readback is incomplete")

        return ProductWriteReceipt(
            graph_sha256=graph.sha256,
            node_count=len(graph.nodes),
            relationship_count=len(graph.relationships),
        )

    return await client.execute_write(_replace)
