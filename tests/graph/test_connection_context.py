import unittest

from graph.connection_context import Neo4jOverride
from analyzer_settings import AnalysisGraphDatabaseSettings


class AnalyzerConnectionContextTest(unittest.TestCase):
    def test_override_rejects_invalid_uri_and_system_database(self):
        invalid_headers = (
            {"x-neo4j-uri": "http://127.0.0.1:7687"},
            {"x-neo4j-uri": "bolt://user:pass@127.0.0.1:7687"},
            {"x-neo4j-uri": "bolt:///missing"},
            {
                "x-neo4j-uri": "bolt://127.0.0.1:7687",
                "x-neo4j-database": "system",
            },
        )
        for headers in invalid_headers:
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                Neo4jOverride.from_headers(headers)

    def test_override_accepts_user_database(self):
        override = Neo4jOverride.from_headers(
            {
                "x-neo4j-uri": "neo4j://127.0.0.1:7687",
                "x-neo4j-database": "analysis",
            }
        )
        self.assertEqual(override.database, "analysis")

    def test_static_configuration_rejects_system_database(self):
        with self.assertRaises(ValueError):
            AnalysisGraphDatabaseSettings(database="system")


if __name__ == "__main__":
    unittest.main()
