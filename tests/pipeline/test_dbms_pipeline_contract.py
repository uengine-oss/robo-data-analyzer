import unittest

from pipeline.strategies import DBMS_PIPELINE


class DbmsPipelineContractTest(unittest.TestCase):
    @staticmethod
    def _all_step_names() -> list[str]:
        names: list[str] = []
        for step in DBMS_PIPELINE.steps:
            members = step if isinstance(step, tuple) else (step,)
            names.extend(member.__name__ for member in members)
        return names

    def test_dbms_pipeline_uses_only_its_table_linker(self) -> None:
        step_names = self._all_step_names()

        self.assertIn("link_dbms_tables_step", step_names)
        self.assertIn("resolve_data_objects_step", step_names)
        self.assertNotIn("discover_datasource_step", step_names)
        self.assertNotIn("link_deterministic_step", step_names)
        self.assertLess(
            step_names.index("resolve_data_objects_step"),
            step_names.index("link_dbms_tables_step"),
        )

    def test_dbms_does_not_reingest_catalogs_derived_lineage_view(self) -> None:
        self.assertNotIn("enrich_lineage_from_catalog_step", self._all_step_names())


if __name__ == "__main__":
    unittest.main()
