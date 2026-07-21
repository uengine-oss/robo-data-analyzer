import unittest

from pipeline.steps.step8_post_processing.enrich_lineage_from_catalog_step import (
    _catalog_lineage_url,
)


class CatalogLineageRouteTest(unittest.TestCase):
    def test_uses_documented_non_redirecting_route(self) -> None:
        self.assertEqual(
            _catalog_lineage_url("http://catalog.example/robo"),
            "http://catalog.example/robo/lineage/",
        )


if __name__ == "__main__":
    unittest.main()
