"""Publish one already-closed product graph in one Neo4j transaction."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from graph.neo4j_client import Neo4jClient
from shared.config.app_settings import SETTINGS
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
            f"CREATE CONSTRAINT uniq_{label.lower()}_product_id IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.product_id IS UNIQUE"
        )
    present = set(labels)
    if not SETTINGS.analysis_pipeline.embed_enabled:
        # **임베딩을 끄면 벡터 색인은 채울 것이 없다.** 그런데도 만들면,
        # 저장소가 벡터 색인을 다르게 받는 경우 run 이 발행 직전에 통째로 죽는다.
        # Ontological 은 `vector.dimensions` 를 필수로 요구한다:
        #   Neo.ClientError.Statement.ArgumentError
        #   CREATE VECTOR INDEX needs OPTIONS {indexConfig: {`vector.dimensions`: N}}
        # 차원은 임베딩 모델이 정하는 값이라, 임베딩이 꺼진 run 에는 정답이 없다.
        return
    for label in ROUTINE_LABELS:
        if label not in present:
            continue
        dimensions = {
            len(embedding)
            for node in graph.nodes
            if node.label == label
            for embedding in (node.properties.get("_search_embedding"),)
            if isinstance(embedding, (list, tuple)) and embedding
        }
        if len(dimensions) != 1:
            raise ProductWriteError(
                f"{label} search embeddings have no single dimension: "
                f"{sorted(dimensions)}"
            )
        dimension = dimensions.pop()
        await client.run_query(
            f"CREATE VECTOR INDEX {label.lower()}_search_embedding IF NOT EXISTS "
            f"FOR (n:{label}) ON n._search_embedding "
            "OPTIONS {indexConfig: {"
            f"`vector.dimensions`: {dimension}, "
            "`vector.similarity_function`: 'cosine'}}"
        )


async def replace_product_graph(
    client: Neo4jClient,
    graph: ProductGraph,
) -> ProductWriteReceipt:
    """Replace Analyzer-owned products atomically after in-memory validation."""
    await ensure_product_schema(client, graph)

    nodes_by_label: dict[str, list[dict[str, object]]] = defaultdict(list)
    for node in graph.nodes:
        write_properties = node.write_properties()
        node_id = str(write_properties.pop("product_id"))
        nodes_by_label[node.label].append({
            "node_id": node_id,
            "properties": write_properties,
        })
    node_label_by_id = {node.node_id: node.label for node in graph.nodes}
    relationships_by_shape: dict[
        tuple[str, str, str], list[dict[str, object]]
    ] = defaultdict(list)
    for relationship in graph.relationships:
        try:
            source_label = node_label_by_id[relationship.source_id]
            target_label = node_label_by_id[relationship.target_id]
        except KeyError as error:
            raise ProductWriteError(
                "product relationship endpoint is absent from the product graph: "
                f"{relationship.source_id!r} -> {relationship.target_id!r}"
            ) from error
        relationships_by_shape[(
            relationship.relationship_type,
            source_label,
            target_label,
        )].append({
            "source_id": relationship.source_id,
            "target_id": relationship.target_id,
            "properties": relationship.write_properties(),
        })

    async def _replace(transaction) -> ProductWriteReceipt:
        # The gateway materializes streamed graph deltas before the validated
        # product graph is published.  Relationship endpoints can therefore
        # leave graph-bolt placeholder nodes whose only properties are the
        # streamed identity (`_id`) and concrete type (`_type`).
        # They have neither product identity nor ownership and used to survive
        # every replacement, eventually overwhelming the real graph in reads.
        # Restrict cleanup to that exact placeholder shape so data owned by
        # Catalog/Fabric (or user-authored design nodes) is never selected.
        await transaction.run(
            """
MATCH (node)
WHERE node.product_id IS NULL
  AND node._owner IS NULL
  AND node._id IS NOT NULL
  AND node._type IS NOT NULL
  AND size(keys(node)) = 2
DETACH DELETE node
"""
        )
        await transaction.run(
            """
MATCH (source)-[relationship]-(target)
WHERE relationship._owner = $owner
   OR (source._owner = $owner AND target._owner = $owner)
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
MERGE (node:{label} {{product_id: item.node_id}})
WITH node, item, node.description AS saved_description,
     node.logical_name AS saved_logical_name
SET node = item.properties
SET node.product_id = item.node_id
SET node.description = coalesce(saved_description, item.properties.description),
    node.logical_name = coalesce(saved_logical_name, item.properties.logical_name)
RETURN count(node) AS written
""",
                    items=items,
                )
            else:
                result = await transaction.run(
                    f"""
UNWIND $items AS item
MERGE (node:{label} {{product_id: item.node_id}})
SET node = item.properties
SET node.product_id = item.node_id
RETURN count(node) AS written
""",
                    items=items,
                )
            row = await result.single(strict=True)
            if int(row["written"]) != len(items):
                raise ProductWriteError(
                    f"{label} write count differs: {row['written']} != {len(items)}"
                )

        for (
            relationship_type,
            source_label,
            target_label,
        ), items in sorted(relationships_by_shape.items()):
            for item in items:
                properties = item["properties"]
                create = (
                    f"CREATE (source:{source_label})-[relationship:FK {{"
                    f"from_column: $from_column, to_column: $to_column}}]->(target:{target_label})"
                    if relationship_type == "FK"
                    else f"CREATE (source:{source_label})-"
                         f"[relationship:{relationship_type}]->(target:{target_label})"
                )
                try:
                    result = await transaction.run(
                        f"""
MATCH (source:{source_label} {{product_id: $source_id}})
MATCH (target:{target_label} {{product_id: $target_id}})
{create}
SET relationship = $properties
RETURN count(relationship) AS written
""",
                        source_id=item["source_id"],
                        target_id=item["target_id"],
                        owner=GRAPH_OWNER,
                        properties=properties,
                        from_column=properties.get("from_column"),
                        to_column=properties.get("to_column"),
                    )
                except Exception as error:
                    raise ProductWriteError(
                        "relationship write failed: "
                        f"{relationship_type} {source_label}:{item['source_id']} -> "
                        f"{target_label}:{item['target_id']}"
                    ) from error
                row = await result.single(strict=True)
                if int(row["written"]) != 1:
                    raise ProductWriteError(
                        f"{relationship_type} write count differs: "
                        f"{row['written']} != 1"
                    )

        matched_nodes = 0
        for label, items in sorted(nodes_by_label.items()):
            node_result = await transaction.run(
                f"""
UNWIND $ids AS id
MATCH (node:{label} {{product_id: id, _owner: $owner}})
RETURN count(node) AS matched
""",
                ids=[str(item["node_id"]) for item in items],
                owner=GRAPH_OWNER,
            )
            node_row = await node_result.single(strict=True)
            matched_nodes += int(node_row["matched"])
        if matched_nodes != len(graph.nodes):
            raise ProductWriteError("product node readback is incomplete")

        return ProductWriteReceipt(
            graph_sha256=graph.sha256,
            node_count=len(graph.nodes),
            relationship_count=len(graph.relationships),
        )

    await _prepare_storage(client, nodes_by_label, relationships_by_shape)
    return await client.execute_write(_replace)


_PROBE_ID = "__robo_storage_probe__"


def _sample_properties(items: list[dict[str, object]]) -> dict[str, object]:
    """같은 모양의 항목들이 쓰는 속성 키를 모두 담은 견본 — 키마다 처음 나온 값."""
    sample: dict[str, object] = {}
    for item in items:
        for key, value in (item.get("properties") or {}).items():
            if value is not None and key not in sample:
                sample[key] = value
    return sample


async def _prepare_storage(
    client: Neo4jClient,
    nodes_by_label: dict[str, list[dict[str, object]]],
    relationships_by_shape: dict[tuple[str, str, str], list[dict[str, object]]],
) -> None:
    """바꿔 쓰기 전에 **저장 자리(라벨 테이블 · 속성 컬럼)를 짧은 트랜잭션으로 먼저 만든다.**

    Ontological DB 는 처음 보는 라벨·속성을 만나면 그 자리에서 테이블을 만들거나
    `ALTER TABLE … ADD COLUMN` 을 한다(배타 잠금). 그게 `_replace` 의 큰 트랜잭션 안에서
    일어나면, 같은 그래프를 훑는 읽기(catalog `check-data` 등 — 모든 노드 테이블에 공유 잠금)와
    잠금 순서가 엇갈려 교착이 나고 분석 전체가 실패했다(lite 0.1.7, 2026-11-19 · 새 그래프의 첫 분석:
    `ALTER TABLE og_data.n_41 ADD COLUMN IF NOT EXISTS p__owner` vs `og_node_json` 집계).

    여기서는 라벨마다 · 관계 모양마다 견본 속성으로 탐침을 만들었다가 같은 트랜잭션에서 지운다.
    트랜잭션이 짧아 교착이 나도 드라이버가 곧바로 다시 시도해 끝난다. 이미 자리가 있으면 바뀌는
    것이 없다(지운 탐침은 남지 않는다).
    """
    for label, items in sorted(nodes_by_label.items()):
        properties = _sample_properties(items)

        async def probe_node(tx, label=label, properties=properties):
            result = await tx.run(
                f"""
CREATE (probe:{label})
SET probe = $properties
SET probe.product_id = $probe_id
WITH probe
DETACH DELETE probe
RETURN 1 AS probed
""",
                properties=properties,
                probe_id=_PROBE_ID,
            )
            await result.single(strict=True)

        await client.execute_write(probe_node)

    for (relationship_type, source_label, target_label), items in sorted(relationships_by_shape.items()):
        properties = _sample_properties(items)

        async def probe_relationship(
            tx,
            relationship_type=relationship_type,
            source_label=source_label,
            target_label=target_label,
            properties=properties,
        ):
            result = await tx.run(
                f"""
CREATE (source:{source_label} {{product_id: $source_id}})
CREATE (target:{target_label} {{product_id: $target_id}})
CREATE (source)-[relationship:{relationship_type}]->(target)
SET relationship = $properties
WITH source, target
DETACH DELETE source, target
RETURN 1 AS probed
""",
                properties=properties,
                source_id=_PROBE_ID + ":source",
                target_id=_PROBE_ID + ":target",
            )
            await result.single(strict=True)

        await client.execute_write(probe_relationship)
