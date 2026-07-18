"""Every Analyzer relationship writer must stay inside the Analyzer graph owner."""

import re
import unittest
from pathlib import Path


EDGE_DIRECTORY = Path(__file__).resolve().parents[2] / "graph" / "edges"
NODE_MATCH_PATTERN = re.compile(r"^\s*MATCH\s+\(", re.MULTILINE)
NODE_MERGE_PATTERN = re.compile(r"^\s*MERGE\s+\([^\n]*\{id:", re.MULTILINE)


class EdgeOwnerScopeContractTest(unittest.TestCase):
    def test_every_relationship_endpoint_match_is_owner_scoped(self) -> None:
        violations: list[str] = []
        for edge_file in sorted(EDGE_DIRECTORY.glob("*.py")):
            source = edge_file.read_text(encoding="utf-8")
            for line_number, line in enumerate(source.splitlines(), start=1):
                if NODE_MATCH_PATTERN.match(line) and "graph_owner" not in line:
                    violations.append(f"{edge_file.name}:{line_number}: {line.strip()}")
                if NODE_MERGE_PATTERN.match(line) and "graph_owner" not in line:
                    violations.append(f"{edge_file.name}:{line_number}: {line.strip()}")

        self.assertEqual(violations, [], "unscoped Analyzer relationship endpoints")


if __name__ == "__main__":
    unittest.main()
