from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
import unittest

from graph.schema.graph_edges import CallsSql, UsesConfig
from graph.schema.graph_nodes import CodeNode, Table
from graph.schema.node_ids import project_file_id
from pipeline.steps.step3_load_external.chunking import make_external_chunks
from pipeline.steps.step5_linking.link_external.linker import link_external


FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "external_linking"


def _rows(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if not rows or any(row.get("verdict") != "OK" for row in rows):
        raise AssertionError(f"invalid answer-key fixture: {name}")
    return rows


def _sql_for(rows: list[dict[str, str]]) -> str:
    read_tables = [row["table"] for row in rows if row["rel"] == "READS"]
    write_tables = [row["table"] for row in rows if row["rel"] == "WRITES"]
    if read_tables and write_tables:
        return "; ".join(
            f"INSERT INTO {table} (id) SELECT id FROM {read_tables[0]}"
            for table in write_tables
        )
    operation = rows[0].get("dml") or ("SELECT" if read_tables else "UPDATE")
    tables = read_tables or write_tables
    if operation == "SELECT":
        return "SELECT * FROM " + tables[0] + " " + " ".join(
            f"JOIN {table} ON 1=1" for table in tables[1:]
        )
    if operation == "INSERT":
        return f"INSERT INTO {tables[0]} (id) VALUES (1)"
    if operation == "DELETE":
        return f"DELETE FROM {tables[0]} WHERE id=1"
    return "; ".join(f"UPDATE {table} SET id=1" for table in tables)


class ExternalAnswerKeyParityTest(unittest.TestCase):
    def test_answer_key_a_all_file_key_pairs_match_without_extras(self) -> None:
        expected_rows = _rows("answer_key_a.tsv")
        keys_by_file: dict[str, list[str]] = defaultdict(list)
        for row in expected_rows:
            keys_by_file[row["java_file"]].append(row["sql_key"])
        code_nodes = [
            CodeNode(
                id=f"code:fixture:{index}",
                signature="fixture()",
                file_path=file_path,
                start_line=1,
                end_line=len(keys),
                label="METHOD",
                name="fixture",
                code_text="\n".join(f'call("{key}");' for key in keys),
            )
            for index, (file_path, keys) in enumerate(sorted(keys_by_file.items()), 1)
        ]
        chunks = []
        for index, key in enumerate(sorted({row["sql_key"] for row in expected_rows}), 1):
            created, _ = make_external_chunks(
                file_id=project_file_id("answer-a", f"mapper-{index}.xml"),
                key=key,
                kind="config",
                text=key,
                file_path=f"mapper-{index}.xml",
                start_line=1,
                end_line=1,
            )
            chunks.extend(created)
        outcome = link_external(
            code_nodes=code_nodes,
            modules=[],
            variables=[],
            constants=[],
            chunks=chunks,
            tables=[],
            routine_ids={node.id for node in code_nodes},
        )
        code_by_id = {node.id: node for node in code_nodes}
        chunk_by_id = {chunk.id: chunk for chunk in chunks}
        actual = {
            (code_by_id[edge.source_id].file_path, chunk_by_id[edge.target_id].key)
            for edge in outcome.external_edges
            if isinstance(edge, UsesConfig)
        }
        expected = {(row["java_file"], row["sql_key"]) for row in expected_rows}
        self.assertEqual(expected, actual)

    def test_answer_key_b_and_shopmall_table_triples_match_without_extras(self) -> None:
        for fixture in ("answer_key_b.tsv", "shopmall_answer_key.tsv"):
            with self.subTest(fixture=fixture):
                rows = _rows(fixture)
                by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
                keys_by_method: dict[str, list[str]] = defaultdict(list)
                for row in rows:
                    key = row.get("sql_key") or row["key"]
                    by_key[key].append(row)
                    keys_by_method[row["method"]].append(key)
                code_nodes = [
                    CodeNode(
                        id=f"code:{method}", signature=f"{method}()", file_path="Fixture.java",
                        start_line=1, end_line=len(keys), label="METHOD", name=method,
                        code_text="\n".join(f'call("{key}");' for key in keys),
                    )
                    for method, keys in sorted(keys_by_method.items())
                ]
                chunks = []
                for index, (key, key_rows) in enumerate(sorted(by_key.items()), 1):
                    created, _ = make_external_chunks(
                        file_id=project_file_id(fixture, f"mapper-{index}.xml"),
                        key=key,
                        kind="sql",
                        text=_sql_for(key_rows),
                        file_path=f"mapper-{index}.xml",
                        start_line=1,
                        end_line=1,
                    )
                    chunks.extend(created)
                tables = [Table("", "", name) for name in sorted({row["table"] for row in rows})]
                outcome = link_external(
                    code_nodes=code_nodes,
                    modules=[],
                    variables=[],
                    constants=[],
                    chunks=chunks,
                    tables=tables,
                    routine_ids={node.id for node in code_nodes},
                )
                calls = {
                    (edge.source_id, edge.target_id)
                    for edge in outcome.external_edges
                    if isinstance(edge, CallsSql)
                }
                code_by_id = {node.id: node for node in code_nodes}
                chunk_by_id = {chunk.id: chunk for chunk in chunks if chunk.part_index == 0}
                table_by_id = {table.id: table for table in tables}
                anchor_relations = defaultdict(list)
                for edge in outcome.reads_writes_edges:
                    if edge.source_id in chunk_by_id:
                        anchor_relations[edge.source_id].append(edge)
                actual_with_dml = {
                    (
                        code_by_id[code_id].name,
                        chunk_by_id[chunk_id].key,
                        table_by_id[edge.target_id].name,
                        edge.relation,
                        edge.dml_type,
                    )
                    for code_id, chunk_id in calls
                    for edge in anchor_relations[chunk_id]
                }
                expected_with_dml = {
                    (
                        row["method"],
                        row.get("sql_key") or row["key"],
                        row["table"],
                        row["rel"],
                        row.get("dml") or ("SELECT" if row["rel"] == "READS" else "UPDATE"),
                    )
                    for row in rows
                }
                if fixture == "answer_key_b.tsv":
                    self.assertEqual(expected_with_dml, actual_with_dml)
                else:
                    expected = {item[:4] for item in expected_with_dml}
                    actual = {item[:4] for item in actual_with_dml}
                    self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
