import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pipeline.steps.step1_parse_ddl.ddl_parser import DDLParseResult
from pipeline.steps.step1_parse_ddl import parse_ddl_step as subject


class DdlDatasourceNamespaceTest(unittest.IsolatedAsyncioTestCase):
    async def test_request_datasource_is_forwarded_to_ddl_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schema.sql").write_text("CREATE TABLE sample(id int);", encoding="utf-8")
            codebase = SimpleNamespace(root=root, ddl_dir=root, datasource="shopmall")
            config = {"codebase": codebase, "neo_client": object(), "stream": None}
            async_functions = (
                "upsert_schemas", "upsert_tables", "upsert_columns", "upsert_has_column",
                "upsert_belongs_to", "upsert_fk",
            )
            with ExitStack() as stack:
                stack.enter_context(patch.object(subject, "step_config", return_value=config))
                parser = stack.enter_context(
                    patch.object(subject, "parse_ddl_file", return_value=DDLParseResult())
                )
                for name in async_functions:
                    stack.enter_context(patch.object(subject, name, new=AsyncMock()))
                await subject.parse_ddl_step({})
            self.assertEqual(parser.call_args.kwargs["db"], "shopmall")


if __name__ == "__main__":
    unittest.main()
