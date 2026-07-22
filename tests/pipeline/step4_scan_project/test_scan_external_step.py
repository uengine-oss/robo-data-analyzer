from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from graph.schema.graph_edges import HasChunk
from graph.schema.graph_nodes import ProjectFile
from graph.schema.node_ids import project_file_id
from agents.external import ExternalAgentRejected
from agents.skills.catalog import EXTERNAL_SKILLS_DIR, FRAMEWORK_SKILLS_DIR, build_skill_catalog
from pipeline.steps.step4_scan_project.external_skills import (
    ExternalExtractionRule,
    ExternalSkill,
    persist_external_skill,
    validate_external_skill,
)
from pipeline.steps.step4_scan_project.scan_external_step import scan_external_step
from pipeline.strategies import DBMS_PIPELINE, FRAMEWORK_PIPELINE
from shared.files.cache import SourceFileCache


class _Pause:
    async def gate(self) -> None:
        await asyncio.sleep(0)


def _skill() -> ExternalSkill:
    return ExternalSkill(
        name="uppercase-properties",
        extensions=(".cfgx",),
        content_signatures=("=",),
        rules=(ExternalExtractionRule(
            name="assignment",
            pattern=r"^(?P<key>[A-Z][A-Z0-9_.-]+)=(?P<value>[^\r\n]*)\r?$",
            key_group="key",
            value_group="value",
        ),),
        explanation="Extract uppercase assignment keys.",
        provenance="test consensus",
    )


def _project_file(relpath: str = "settings.cfgx") -> ProjectFile:
    return ProjectFile(
        id=project_file_id("shop", relpath),
        name=Path(relpath).name,
        project="shop",
        relpath=relpath,
        ext=".cfgx",
        category="unknown",
        status="unanalyzed",
        reason="unsupported_text_format",
    )


def _config(root: Path) -> dict:
    return {"configurable": {
        "codebase": SimpleNamespace(root=root),
        "source_cache": SourceFileCache(),
        "pause": _Pause(),
        "run": SimpleNamespace(run_dir=root / "run"),
        "cost_budget": object(),
        "llm_semaphore": asyncio.Semaphore(7),
    }}


class ScanExternalStepTest(unittest.IsolatedAsyncioTestCase):
    async def test_valid_persisted_skill_reloads_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            source = "ORDER.API=/orders\n"
            (root / "settings.cfgx").write_text(source, encoding="utf-8", newline="\n")
            persist_external_skill(_skill(), skills)
            invoke = AsyncMock()

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {"project_files": [_project_file()], "external_chunks": []},
                    _config(root),
                )

            invoke.assert_not_awaited()
            self.assertEqual("analyzed", result["project_files"][0].status)
            self.assertEqual("custom", result["project_files"][0].category)
            chunk = result["external_chunks"][0]
            self.assertEqual(("ORDER.API",), chunk.keys)
            # 청크는 원문 슬라이스여야 한다 (재직렬화 금지).
            self.assertEqual(source[chunk.char_start:chunk.char_end], chunk.text)
            self.assertEqual("agent_segment", chunk.semantic_kind)
            self.assertIsInstance(result["external_edges"][0], HasChunk)

    async def test_new_skill_requires_held_out_pass_then_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            (root / "settings.cfgx").write_text("ORDER.API=/orders\n", encoding="utf-8")
            (root / "other.cfgx").write_text("STOCK.API=/stocks\n", encoding="utf-8")
            accepted = validate_external_skill(_skill(), ["ORDER.API=/orders\n"])
            invoke = AsyncMock(return_value=accepted)

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {
                        "project_files": [
                            _project_file("other.cfgx"), _project_file("settings.cfgx"),
                        ],
                        "external_chunks": [],
                    },
                    _config(root),
                )

            invoke.assert_awaited_once()
            # 제안에는 held-out 1개를 제외한 train 샘플만 전달된다.
            self.assertEqual(1, len(invoke.await_args.kwargs["samples"]))
            self.assertTrue((skills / _skill().name / "SKILL.md").is_file())
            all_keys = {key for chunk in result["external_chunks"] for key in chunk.keys}
            self.assertEqual({"ORDER.API", "STOCK.API"}, all_keys)

    async def test_single_file_without_held_out_stays_unanalyzed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            (root / "settings.cfgx").write_text("ORDER.API=/orders\n", encoding="utf-8")
            invoke = AsyncMock()

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {"project_files": [_project_file()], "external_chunks": []},
                    _config(root),
                )

            invoke.assert_not_awaited()
            self.assertEqual([], result["external_chunks"])
            self.assertEqual("unanalyzed", result["project_files"][0].status)
            self.assertEqual("no_held_out_evidence", result["project_files"][0].reason)

    async def test_agent_rejection_keeps_files_honestly_unanalyzed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            (root / "settings.cfgx").write_text("not understood\n", encoding="utf-8")
            (root / "other.cfgx").write_text("also opaque\n", encoding="utf-8")
            invoke = AsyncMock(side_effect=ExternalAgentRejected("no reusable consensus"))

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {
                        "project_files": [
                            _project_file("settings.cfgx"), _project_file("other.cfgx"),
                        ],
                        "external_chunks": [],
                    },
                    _config(root),
                )

            self.assertEqual([], result["external_chunks"])
            for file in result["project_files"]:
                self.assertEqual(("unanalyzed", "pattern_rejected"), (file.status, file.reason))

    def test_catalogs_and_strategy_positions_are_isolated(self) -> None:
        framework = build_skill_catalog(FRAMEWORK_SKILLS_DIR)
        external = build_skill_catalog(EXTERNAL_SKILLS_DIR)
        for name in (
            "ejb-cmp", "jpa-standard", "mybatis-ibatis", "ofbiz",
            "proframe-c-pfm-dbio", "shopmall-c-facade",
        ):
            self.assertIn(f"{name}:", framework)
            self.assertNotIn(f"{name}:", external)
        for strategy in (FRAMEWORK_PIPELINE, DBMS_PIPELINE):
            names = [step.__name__ if not isinstance(step, tuple) else "parallel_load" for step in strategy.steps]
            self.assertLess(names.index("parallel_load"), names.index("scan_external_step"))
            self.assertLess(names.index("scan_external_step"), names.index("link_external_step"))


if __name__ == "__main__":
    unittest.main()
