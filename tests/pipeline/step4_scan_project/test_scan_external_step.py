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

    async def test_single_small_file_without_repetition_stays_unanalyzed(self) -> None:
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

    async def test_single_file_with_repetition_promotes_via_intra_file_held_out(self) -> None:
        source = "".join(f"KEY{i:02d}.PATH=/v{i}\n" for i in range(30))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            (root / "settings.cfgx").write_text(source, encoding="utf-8", newline="\n")
            invoke = AsyncMock(return_value=validate_external_skill(_skill(), [source]))

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {"project_files": [_project_file()], "external_chunks": []},
                    _config(root),
                )

            invoke.assert_awaited_once()
            # Agent 는 앞 70%(21줄)만 본다 — 뒷부분은 미관측 held-out 증거.
            observed = invoke.await_args.kwargs["samples"]
            self.assertEqual(["".join(source.splitlines(keepends=True)[:21])], observed)
            self.assertEqual("analyzed", result["project_files"][0].status)
            # 청크는 분할과 무관하게 파일 전문에서 생성된다.
            self.assertEqual(30, len(result["external_chunks"]))
            self.assertTrue((skills / _skill().name / "SKILL.md").is_file())

    async def test_single_file_whose_tail_breaks_the_rule_is_rejected(self) -> None:
        source = "".join(f"KEY{i:02d}.PATH=/v{i}\n" for i in range(21)) + "opaque tail\n" * 9
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            (root / "settings.cfgx").write_text(source, encoding="utf-8", newline="\n")
            invoke = AsyncMock(return_value=validate_external_skill(
                _skill(), ["".join(source.splitlines(keepends=True)[:21])]))

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {"project_files": [_project_file()], "external_chunks": []},
                    _config(root),
                )

            # held-out(뒷부분)에서 규칙이 죽으면 재제안 없이 정직 거부.
            self.assertEqual(1, invoke.await_count)
            self.assertEqual([], result["external_chunks"])
            self.assertEqual("unanalyzed", result["project_files"][0].status)
            self.assertEqual("pattern_rejected", result["project_files"][0].reason)

    async def test_undecodable_file_is_rejected_without_killing_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            (root / "broken.cfgx").write_bytes(b"\x00\x01\xff\xfe" * 40)
            (root / "settings.cfgx").write_text("ORDER.API=/orders\n", encoding="utf-8", newline="\n")
            persist_external_skill(_skill(), skills)
            invoke = AsyncMock()

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {
                        "project_files": [
                            _project_file("broken.cfgx"), _project_file("settings.cfgx"),
                        ],
                        "external_chunks": [],
                    },
                    _config(root),
                )

            # 해독 불가 파일은 run 을 죽이지 않고 혼자 미분석으로 남는다.
            by_path = {f.relpath: (f.status, f.reason) for f in result["project_files"]}
            self.assertEqual(("unanalyzed", "undecodable_text"), by_path["broken.cfgx"])
            self.assertEqual(("analyzed", ""), by_path["settings.cfgx"][:1] + (by_path["settings.cfgx"][1],))

    async def test_non_identifier_keys_never_become_link_targets(self) -> None:
        # custom key 는 어댑터와 같은 식별자 형태 게이트를 거친다 — 형태 미달이면
        # 청크는 남되 링킹 대상 key 가 되지 않는다 (REFER_TO 오염 차단).
        loose = ExternalSkill(
            name="loose-sentence",
            extensions=(".cfgx",),
            content_signatures=("=",),
            rules=(ExternalExtractionRule(
                name="entry",
                pattern=r"(?m)^(?P<key>[A-Z]+ [A-Z]+)=(?P<value>.+)$",
                key_group="key",
                value_group="value",
            ),),
            explanation="space-delimited key records",
            provenance="test sample structure",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            source = "ORDER API=/orders\n"
            (root / "settings.cfgx").write_text(source, encoding="utf-8", newline="\n")
            persist_external_skill(loose, skills)

            with patch(
                "pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills,
            ):
                result = await scan_external_step(
                    {"project_files": [_project_file()], "external_chunks": []},
                    _config(root),
                )

            chunk = result["external_chunks"][0]
            self.assertEqual((), chunk.keys)
            self.assertEqual(source[chunk.char_start:chunk.char_end], chunk.text)

    async def test_mixed_shapes_within_extension_learn_separately(self) -> None:
        # "같은 확장자 = 같은 형식" 가정 제거 — 모양이 다르면 딴 형식으로 묶는다.
        upper = "".join(f"KEY{i:02d}.PATH=/v{i}\n" for i in range(30))
        table = "".join(f"row{i}|{i}|on\n" for i in range(30))
        table_skill = ExternalSkill(
            name="pipe-table",
            extensions=(".cfgx",),
            content_signatures=("|",),
            rules=(ExternalExtractionRule(
                name="row",
                pattern=r"(?m)^(?P<key>[a-z]+\d+)\|\d+\|\w+$",
                key_group="key",
            ),),
            explanation="pipe-delimited table rows",
            provenance="test sample structure",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            (root / "a.cfgx").write_text(upper, encoding="utf-8", newline="\n")
            (root / "b.cfgx").write_text(table, encoding="utf-8", newline="\n")
            invoke = AsyncMock(side_effect=[
                validate_external_skill(_skill(), [upper]),
                validate_external_skill(table_skill, [table]),
            ])

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {
                        "project_files": [_project_file("a.cfgx"), _project_file("b.cfgx")],
                        "external_chunks": [],
                    },
                    _config(root),
                )

            self.assertEqual(2, invoke.await_count)     # 형식 그룹 2개로 따로 학습
            statuses = {f.relpath: f.status for f in result["project_files"]}
            self.assertEqual({"a.cfgx": "analyzed", "b.cfgx": "analyzed"}, statuses)
            self.assertEqual(60, len(result["external_chunks"]))

    async def test_same_shape_across_extensions_is_not_used_as_decoy(self) -> None:
        # "다른 확장자 = 다른 형식" 가정 제거 — 같은 형식이 두 확장자로 존재해도
        # 서로를 미끼로 실격시키지 않는다.
        cfgx = "".join(f"AAA{i:02d}.PATH=/x{i}\n" for i in range(30))
        cfgy = "".join(f"BBB{i:02d}.PATH=/y{i}\n" for i in range(30))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            (root / "one.cfgx").write_text(cfgx, encoding="utf-8", newline="\n")
            (root / "two.cfgy").write_text(cfgy, encoding="utf-8", newline="\n")
            twin = _project_file("two.cfgy")
            twin = type(twin)(**{**twin.__dict__, "ext": ".cfgy"})
            invoke = AsyncMock(side_effect=[
                validate_external_skill(_skill(), [cfgx]),
                validate_external_skill(
                    _skill().model_copy(update={"name": "twin-format", "extensions": (".cfgy",)}),
                    [cfgy],
                ),
            ])

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {
                        "project_files": [_project_file("one.cfgx"), twin],
                        "external_chunks": [],
                    },
                    _config(root),
                )

            statuses = {f.relpath: (f.status, f.reason) for f in result["project_files"]}
            self.assertEqual(("analyzed", ""), statuses["one.cfgx"])
            self.assertEqual(("analyzed", ""), statuses["two.cfgy"])

    async def test_extensionless_files_analyze_in_run_but_do_not_persist(self) -> None:
        source = "".join(f"KEY{i:02d}.PATH=/v{i}\n" for i in range(30))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            (root / "DATAFILE").write_text(source, encoding="utf-8", newline="\n")
            base = _project_file("DATAFILE")
            file = type(base)(**{**base.__dict__, "ext": ""})
            invoke = AsyncMock(return_value=validate_external_skill(_skill(), [source]))

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {"project_files": [file], "external_chunks": []},
                    _config(root),
                )

            self.assertEqual("analyzed", result["project_files"][0].status)
            # 확장자 없는 skill 은 재사용 근거가 없어 저장하지 않는다 (in-run 전용).
            self.assertEqual([], list(skills.glob("*/SKILL.md")))

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
