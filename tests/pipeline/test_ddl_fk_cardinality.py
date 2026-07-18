import tempfile
import unittest
from pathlib import Path

from pipeline.steps.step1_parse_ddl.ddl_parser import parse_ddl_file


class DdlFkCardinalityTest(unittest.TestCase):
    def _parse_edges(self, child_definition: str):
        ddl = f"""
        CREATE TABLE parent (
            tenant_id INT,
            id INT,
            PRIMARY KEY (tenant_id, id)
        );
        CREATE TABLE child (
            {child_definition}
        );
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schema.sql"
            path.write_text(ddl, encoding="utf-8")
            return parse_ddl_file(path, db="fixture", default_schema="public").fk_edges

    def test_ordinary_foreign_key_is_many_to_one_and_declared(self):
        edges = self._parse_edges(
            "id INT PRIMARY KEY, parent_id INT, "
            "FOREIGN KEY (parent_id) REFERENCES parent(id)"
        )
        self.assertEqual([(e.fk_type, e.is_candidate) for e in edges], [("many_to_one", False)])

    def test_unique_foreign_key_is_one_to_one(self):
        edges = self._parse_edges(
            "id INT PRIMARY KEY, parent_id INT UNIQUE, "
            "FOREIGN KEY (parent_id) REFERENCES parent(id)"
        )
        self.assertEqual([e.fk_type for e in edges], ["one_to_one"])

    def test_composite_unique_foreign_key_is_one_to_one_for_every_member(self):
        edges = self._parse_edges(
            "id INT PRIMARY KEY, tenant_id INT, parent_id INT, "
            "UNIQUE (tenant_id, parent_id), "
            "FOREIGN KEY (tenant_id, parent_id) REFERENCES parent(tenant_id, id)"
        )
        self.assertEqual(len(edges), 2)
        self.assertEqual({e.fk_type for e in edges}, {"one_to_one"})

    def test_subset_of_composite_primary_key_is_many_to_one(self):
        edges = self._parse_edges(
            "tenant_id INT, parent_id INT, PRIMARY KEY (tenant_id, parent_id), "
            "FOREIGN KEY (parent_id) REFERENCES parent(id)"
        )
        self.assertEqual([e.fk_type for e in edges], ["many_to_one"])


if __name__ == "__main__":
    unittest.main()
