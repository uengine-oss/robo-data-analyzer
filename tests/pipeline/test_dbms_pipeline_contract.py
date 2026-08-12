import unittest

from pipeline.strategies import DBMS_PIPELINE


class DbmsPipelineContractTest(unittest.TestCase):
    def test_dbms_pipeline_uses_only_its_table_linker(self) -> None:
        step_names = [step.__name__ for step in DBMS_PIPELINE.steps if callable(step)]

        self.assertIn("link_dbms_tables_step", step_names)
        self.assertIn("resolve_data_objects_step", step_names)
        self.assertNotIn("discover_datasource_step", step_names)
        self.assertNotIn("link_deterministic_step", step_names)
        self.assertLess(
            step_names.index("resolve_data_objects_step"),
            step_names.index("link_dbms_tables_step"),
        )


if __name__ == "__main__":
    unittest.main()
