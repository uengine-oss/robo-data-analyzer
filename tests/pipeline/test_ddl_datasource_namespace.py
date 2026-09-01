import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pipeline.stages.ddl.ddl_parser import DDLParseResult
from pipeline.stages.ddl import parse_ddl_step as subject


class DdlDatasourceNamespaceTest(unittest.IsolatedAsyncioTestCase):
    async def test_request_datasource_is_forwarded_to_ddl_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schema.sql").write_text("CREATE TABLE sample(id int);", encoding="utf-8")
            codebase = SimpleNamespace(root=root, ddl_dir=root, datasource="shopmall")
            config = {"codebase": codebase, "neo_client": object(), "stream": None}
            with ExitStack() as stack:
                stack.enter_context(patch.object(subject, "step_config", return_value=config))
                parser = stack.enter_context(
                    patch.object(subject, "parse_ddl_file", return_value=DDLParseResult())
                )
                for name in ("emit_nodes", "emit_edges"):
                    stack.enter_context(patch.object(subject, name, new=AsyncMock()))
                await subject.parse_ddl_step({})
            self.assertEqual(parser.call_args.kwargs["db"], "shopmall")
            self.assertEqual(
                b"CREATE TABLE sample(id int);",
                parser.call_args.kwargs["sealed_source_bytes"],
            )


if __name__ == "__main__":
    unittest.main()
