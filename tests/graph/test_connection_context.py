import unittest

from graph.connection_context import Neo4jOverride
from shared.config.app_settings import AnalysisGraphDatabaseSettings


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

    def test_database_only_uses_container_connection(self):
        from shared.config.app_settings import SETTINGS

        override = Neo4jOverride.from_headers({"x-neo4j-database": "project_analysis"})
        self.assertIsNotNone(override)
        self.assertEqual(override.database, "project_analysis")
        self.assertEqual(override.uri, SETTINGS.graph_database.uri)

    def test_database_only_rejects_system(self):
        with self.assertRaises(ValueError):
            Neo4jOverride.from_headers({"x-neo4j-database": "system"})

    def test_static_configuration_rejects_system_database(self):
        with self.assertRaises(ValueError):
            AnalysisGraphDatabaseSettings(database="system")


if __name__ == "__main__":
    unittest.main()
