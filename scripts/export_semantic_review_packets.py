"""Export deterministic semantic review packets from the current analyzer graph.

The script performs no semantic judgment and calls no LLM.  It keeps the
source/gold side of existing canonical packets and replaces only the current
graph output side, making repeated direct reviews comparable.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from graph.connection_context import Neo4jOverride, set_override
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from shared.config.settings import SETTINGS


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _canonical_hash(value: Any) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _text_item(output_id: str, text: str) -> dict[str, str]:
    return {
        "output_id": output_id,
        "text": text,
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


async def _export(args: argparse.Namespace) -> None:
    function_rows = _read_jsonl(args.function_packets.resolve())
    schema_rows = _read_jsonl(args.schema_packets.resolve())
    function_ids = [str(row["func_id"]) for row in function_rows]
    schema_ids = [str(row["node_id"]) for row in schema_rows]

    set_override(
        Neo4jOverride(
            uri=SETTINGS.graph_database.uri,
            user=SETTINGS.graph_database.user,
            password=SETTINGS.graph_database.password,
            database=args.database,
        )
    )
    client = Neo4jClient()
    try:
        from graph.generation import bind_active_completed_generation
        await bind_active_completed_generation(client)
        code_records = await client.run_read_query(
            f"""
            MATCH (n:ANALYZER_OWNED)
            WHERE n.graph_owner = '{ANALYZER_GRAPH_OWNER}'
              AND n.generation_id = $generation_id AND n.id IN $ids
            RETURN n.id AS id, n.summary AS summary,
                   n.stereotype AS stereotype
            """,
            {"ids": function_ids},
        )
        rule_records = await client.run_read_query(
            f"""
            MATCH (n:ANALYZER_OWNED)-[hr:HAS_RULE]->(r:RULE)
            WHERE n.graph_owner = '{ANALYZER_GRAPH_OWNER}'
              AND n.generation_id = $generation_id AND n.id IN $ids
              AND r.generation_id = $generation_id
            OPTIONAL MATCH (r)-[he:HAS_EXAMPLE]->(e:EXAMPLE {{generation_id: $generation_id}})
            RETURN n.id AS id, r.id AS occurrence_id,
                   coalesce(hr.ordinal, 0) AS rule_ordinal,
                   r.anchor_line AS anchor_line, r.cond AS cond, r.then AS effects,
                   r.nl AS nl, e.id AS example_id,
                   coalesce(he.ordinal, 0) AS example_ordinal,
                   e.kind AS kind, e.given AS given, e.then AS example_effects
            ORDER BY id, rule_ordinal, occurrence_id, example_ordinal, example_id
            """,
            {"ids": function_ids},
        )
        question_records = await client.run_read_query(
            f"""
            MATCH (n:ANALYZER_OWNED)-[:HAS_QUESTION]->(q:QUESTION)
            WHERE n.graph_owner = '{ANALYZER_GRAPH_OWNER}'
              AND n.generation_id = $generation_id AND n.id IN $ids
              AND q.generation_id = $generation_id
            RETURN n.id AS id, q.id AS question_id, q.text AS text,
                   q.reason AS reason
            ORDER BY id, question_id
            """,
            {"ids": function_ids},
        )
        schema_records = await client.run_read_query(
            f"""
            MATCH (n:ANALYZER_OWNED)
            WHERE n.graph_owner = '{ANALYZER_GRAPH_OWNER}'
              AND n.generation_id = $generation_id AND n.id IN $ids
            RETURN n.id AS id, properties(n) AS props
            """,
            {"ids": schema_ids},
        )
        glossary_records = await client.run_read_query(
            f"""
            MATCH (g:GLOSSARY {{id: "glossary", graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
            RETURN g.terms_json AS terms_json, g.n_terms AS n_terms,
                   g.source_kind AS source_kind, g.source_sha256 AS source_sha256,
                   g.schema_version AS schema_version
            """
        )
    finally:
        await client.close()

    code_by_id = {str(row["id"]): row for row in code_records}
    rules_by_id: dict[str, list[dict[str, Any]]] = {}
    questions_by_id: dict[str, list[dict[str, Any]]] = {}
    for record in rule_records:
        rules_by_id.setdefault(str(record["id"]), []).append(record)
    for record in question_records:
        questions_by_id.setdefault(str(record["id"]), []).append(record)
    schema_by_id = {
        str(row["id"]): dict(row.get("props") or {}) for row in schema_records
    }

    exported_functions: list[dict[str, Any]] = []
    for old in function_rows:
        func_id = str(old["func_id"])
        current = code_by_id.get(func_id)
        if current is None:
            raise RuntimeError(f"function missing from current graph: {func_id}")

        fields: dict[str, list[dict[str, str]]] = {
            "summary(함수)": [],
            "RULE": [],
            "EXAMPLE": [],
            "QUESTION": [],
        }
        for key, prop in (
            ("summary(함수)", "summary"),
        ):
            text = str(current.get(prop) or "").strip()
            if text:
                fields[key].append(_text_item(f"{key}:0", text))

        seen_rules: set[str] = set()
        for record in rules_by_id.get(func_id, []):
            occurrence_id = str(record["occurrence_id"])
            if occurrence_id not in seen_rules:
                # 파이썬 leaf 계약: cond → then 이 규칙의 기계형 본문이다 (nl 은 표시 보조).
                effects = ", ".join(str(s) for s in (record.get("effects") or []))
                cond = str(record.get("cond") or "").strip() or "항상"
                text = f"{cond} → {effects}"
                nl = str(record.get("nl") or "").strip()
                if nl:
                    text += f" ({nl})"
                fields["RULE"].append(_text_item(f"RULE:{occurrence_id}", text))
                seen_rules.add(occurrence_id)
            example_id = record.get("example_id")
            if example_id:
                given = ", ".join(str(s) for s in (record.get("given") or []))
                example_effects = ", ".join(
                    str(s) for s in (record.get("example_effects") or [])
                )
                text = (
                    f"{str(record.get('kind') or '').strip()}: "
                    f"given [{given}] then [{example_effects}]"
                )
                fields["EXAMPLE"].append(
                    _text_item(f"EXAMPLE:{example_id}", text)
                )

        for record in questions_by_id.get(func_id, []):
            text = (
                f"{str(record.get('text') or '').strip()} / "
                f"{str(record.get('reason') or '').strip()}"
            )
            fields["QUESTION"].append(
                _text_item(f"QUESTION:{record['question_id']}", text)
            )

        actual_stereotype = str(current.get("stereotype") or "")
        output_hash = _canonical_hash(
            {
                "actual_stereotype": actual_stereotype,
                "output_fields": fields,
            }
        )
        exported_functions.append(
            {
                **old,
                "actual_stereotype": actual_stereotype,
                "output_fields": fields,
                "output_hash": output_hash,
            }
        )

    exported_schemas: list[dict[str, Any]] = []
    for old in schema_rows:
        node_id = str(old["node_id"])
        props = schema_by_id.get(node_id)
        if props is None:
            raise RuntimeError(f"schema node missing from current graph: {node_id}")
        actual = {
            key: props.get(key)
            for key in old.get("actual", {})
        }
        exported_schemas.append({**old, "actual": actual})

    output_dir = args.output_dir.resolve()
    function_path = output_dir / "review-packets.jsonl"
    schema_path = output_dir / "schema-review-packets.jsonl"
    _write_jsonl(function_path, exported_functions)
    _write_jsonl(schema_path, exported_schemas)
    if len(glossary_records) > 1:
        raise RuntimeError("current graph contains multiple owned glossary nodes")
    if glossary_records:
        record = glossary_records[0]
        glossary_payload = json.loads(str(record.get("terms_json") or ""))
        if not isinstance(glossary_payload, dict) or not isinstance(
            glossary_payload.get("terms"), list
        ):
            raise RuntimeError("invalid human glossary graph payload")
        glossary = {
            "supplied": True,
            "source_kind": record.get("source_kind"),
            "source_sha256": record.get("source_sha256"),
            "schema_version": record.get("schema_version"),
            "n_terms": record.get("n_terms"),
            "payload": glossary_payload,
        }
    else:
        glossary = {"supplied": False, "payload": {"terms": [], "actions": []}}
    glossary_path = output_dir / "human-glossary.json"
    glossary_path.write_text(
        json.dumps(glossary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "functions": len(exported_functions),
                "schemas": len(exported_schemas),
                "function_packets": str(function_path),
                "schema_packets": str(schema_path),
                "human_glossary": str(glossary_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function-packets", type=Path, required=True)
    parser.add_argument("--schema-packets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--database",
        default=SETTINGS.graph_database.database,
    )
    args = parser.parse_args()
    asyncio.run(_export(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
