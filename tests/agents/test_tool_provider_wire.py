"""Provider-facing Agent tool wire의 최소·무손실 계약."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.utils.function_calling import convert_to_openai_tool

from agents.tools.file_tools import make_filesystem_tools
from agents.tools.inspect_tools import list_skills, make_list_code_nodes, read_skill
from agents.tools.pattern_mining import make_structure_candidate_tool
from agents.tools.sample_tools import make_sample_exploration_tools
from agents.tools.verify_tools import (
    make_verify_external_rule_tool,
    make_verify_table_regex_tool,
)


def _compact(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _provider_wire(tool: object) -> dict:
    return convert_to_openai_tool(tool, strict=True)


class ToolProviderWireContractTest(unittest.TestCase):
    def _toolsets(self) -> tuple[list, list]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        scan_tools = [
            *make_filesystem_tools(root).values(),
            make_list_code_nodes([], project_root=root),
            list_skills,
            read_skill,
            make_verify_table_regex_tool(root, [], []),
        ]
        external_tools = [
            make_structure_candidate_tool(["REC A payload\n"]),
            *make_sample_exploration_tools(["REC A payload\n"]),
            make_verify_external_rule_tool(
                ["REC A payload\n"],
                ["unrelated\n"],
            ),
        ]
        return scan_tools, external_tools

    def test_provider_wire_stays_compact_without_verbose_prompt_sections(self) -> None:
        scan_tools, external_tools = self._toolsets()

        scan_size = sum(len(_compact(_provider_wire(tool))) for tool in scan_tools)
        external_size = sum(
            len(_compact(_provider_wire(tool))) for tool in external_tools
        )

        self.assertLessEqual(scan_size, 4_000)
        self.assertLessEqual(external_size, 2_100)
        for tool in [*scan_tools, *external_tools]:
            wire = _provider_wire(tool)["function"]
            description = wire["description"]
            for repeated_heading in (
                "**언제 사용**",
                "**인자**",
                "**결과 형식**",
                "**실패 응답**",
                "**운영 팁**",
            ):
                self.assertNotIn(repeated_heading, description, tool.name)
            parameters = wire["parameters"]
            self.assertNotIn("title", _compact(parameters), tool.name)
            self.assertNotIn('"description"', _compact(parameters), tool.name)

    def test_descriptions_keep_closure_security_and_verification_meaning(self) -> None:
        scan_tools, external_tools = self._toolsets()
        descriptions = {
            tool.name: _provider_wire(tool)["function"]["description"]
            for tool in [*scan_tools, *external_tools]
        }

        required_markers = {
            "ls": ("프로젝트 상대", "선택 source", "next_offset"),
            "glob_search": ("프로젝트 상대", "선택 source", "next_offset"),
            "grep": ("path:line:content", "선택-source", "next_offset"),
            "read_file": ("번호 붙은 원문", "선택-source", "next_start_line"),
            "read_file_range": ("inclusive", "선택-source", "next_start_line"),
            "list_code_nodes": ("Parser", "start_line", "next_offset"),
            "read_skill": ("내부 rule ID", "경로 이탈", "verify_table_regex"),
            "verify_table_regex": ("production table linker", "entity", "offset"),
            "list_structure_candidates": ("모든 sample", "verify_external_rule", "next_offset"),
            "sample_overview": ("모든 sample", "최초·최후 줄", "next_offset"),
            "read_sample_range": ("번호 붙은 원문", "next_start_line"),
            "grep_samples": ("대표 매치 원문", "파일·줄", "next_offset"),
            "verify_external_rule": ("production extractor", "대표 key·길이·원문", "negative"),
        }
        for tool_name, markers in required_markers.items():
            for marker in markers:
                self.assertIn(marker, descriptions[tool_name], tool_name)

    def test_source_and_match_excerpts_remain_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.c"
            source.write_text("first\nCALL_TABLE(orders)\nlast\n", encoding="utf-8")
            filesystem_tools = make_filesystem_tools(root)

            grep_result = filesystem_tools["grep"].invoke(
                {"pattern": "CALL_TABLE", "path_glob": "**/*.c"}
            )
            range_result = filesystem_tools["read_file_range"].invoke(
                {"path": "sample.c", "start_line": 2, "end_line": 2}
            )

        self.assertIn("sample.c:2:CALL_TABLE(orders)", grep_result)
        self.assertIn("2: CALL_TABLE(orders)", range_result)

        sample_tools = {
            tool.name: tool
            for tool in make_sample_exploration_tools(["REC A payload\nREC B payload\n"])
        }
        sample_grep = sample_tools["grep_samples"].invoke({"pattern": "REC [AB]"})
        self.assertIn("sample[0] 1줄: 'REC A'", sample_grep)

        verify = make_verify_external_rule_tool(
            ["REC A payload\nREC B payload\n"],
            ["unrelated\n"],
        )
        verified = verify.invoke({"pattern": r"^REC (?P<key>[A-Z]) .+$"})
        self.assertIn("key='A' chars=13 text='REC A payload'", verified)
        self.assertIn("key='B' chars=13 text='REC B payload'", verified)


if __name__ == "__main__":
    unittest.main()
