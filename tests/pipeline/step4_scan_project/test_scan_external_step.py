from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from graph.schema.node_ids import project_file_id
from agents.external import ExternalAgentRejected
from agents.skills.catalog import EXTERNAL_SKILLS_DIR, FRAMEWORK_SKILLS_DIR, build_skill_catalog
from pipeline.models.external_source import ExternalSourceFile
from pipeline.steps.step4_scan_project.external_skills import (
    ExternalExtractionRule,
    ExternalSkill,
    persist_external_skill,
    validate_external_skill,
)
from pipeline.steps.step4_scan_project.scan_external_step import (
    _shape_safe_negative_samples,
    scan_external_step,
)
from pipeline.strategies import DBMS_PIPELINE, FRAMEWORK_PIPELINE
from shared.files.cache import SourceFileCache


class _Pause:
    async def gate(self) -> None:
        await asyncio.sleep(0)


def _skill() -> ExternalSkill:
    return ExternalSkill(
        name="uppercase-properties",
        extensions=(".cfgx",),
        rules=(ExternalExtractionRule(
            name="assignment",
            pattern=r"^(?P<key>[A-Z][A-Z0-9_.-]+)=(?P<value>[^\r\n]*)\r?$",
            key_group="key",
        ),),
    )


def _pipe_skill() -> ExternalSkill:
    return ExternalSkill(
        name="pipe-table",
        extensions=(".cfgx",),
        rules=(ExternalExtractionRule(
            name="row",
            pattern=r"(?m)^(?P<key>[a-z]+\d+)\|\d+\|\w+$",
            key_group="key",
        ),),
    )


def _source_file(relpath: str = "settings.cfgx") -> ExternalSourceFile:
    return ExternalSourceFile(
        source_file_id=project_file_id("shop", relpath),
        project_relative_path=relpath,
        extension=".cfgx",
        category="unknown",
        status="unanalyzed",
        disposition_reason="unsupported_text_format",
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
    def test_negative_examples_keep_every_file_and_complete_text(self) -> None:
        files = [_source_file(f"other-{index}.cfgx") for index in range(8)]
        decoded = {
            file.source_file_id: ("x" * 20_000) + f"tail-{index}"
            for index, file in enumerate(files)
        }
        profiles = {
            file.source_file_id: frozenset({f"shape-{index}"})
            for index, file in enumerate(files)
        }

        negatives = _shape_safe_negative_samples(
            unknown_files=files,
            decoded=decoded,
            profiles=profiles,
            member_ids=set(),
            group_profile={"member-shape"},
        )

        self.assertEqual(8, len(negatives))
        self.assertEqual(20_006, len(negatives[-1]))
        self.assertTrue(negatives[-1].endswith("tail-7"))

    async def test_valid_persisted_skill_reloads_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            source = "ORDER.API=/orders\n"
            (root / "settings.cfgx").write_text(source, encoding="utf-8", newline="\n")
            persist_external_skill(_skill(), skills)
            # 같은 확장자를 선언해도 실제 원문 전체 검증에 실패하는 skill은
            # 정확한 저장 skill의 선택을 막지 않는다.
            persist_external_skill(_pipe_skill(), skills)
            invoke = AsyncMock()

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {"external_source_files": [_source_file()], "external_source_records": []},
                    _config(root),
                )

            invoke.assert_not_awaited()
            self.assertEqual("analyzed", result["external_source_files"][0].status)
            self.assertEqual("custom", result["external_source_files"][0].category)
            record = result["external_source_records"][0]
            self.assertEqual(("ORDER.API",), record.addressable_keys)
            # 청크는 원문 슬라이스여야 한다 (재직렬화 금지).
            self.assertEqual(
                source[record.source_character_start:record.source_character_end],
                record.exact_text,
            )
            self.assertEqual("agent_segment", record.semantic_kind)

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
                        "external_source_files": [
                            _source_file("other.cfgx"), _source_file("settings.cfgx"),
                        ],
                        "external_source_records": [],
                    },
                    _config(root),
                )

            invoke.assert_awaited_once()
            # 제안에는 held-out 1개를 제외한 train 샘플만 전달된다.
            self.assertEqual(1, len(invoke.await_args.kwargs["samples"]))
            self.assertTrue((skills / _skill().name / "SKILL.md").is_file())
            all_keys = {
                key
                for record in result["external_source_records"]
                for key in record.addressable_keys
            }
            self.assertEqual({"ORDER.API", "STOCK.API"}, all_keys)

    async def test_single_small_file_is_analyzed_for_current_run_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            source = "ORDER.API=/orders\n"
            (root / "settings.cfgx").write_text(
                source, encoding="utf-8", newline="\n",
            )
            invoke = AsyncMock(return_value=validate_external_skill(_skill(), [source]))

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {"external_source_files": [_source_file()], "external_source_records": []},
                    _config(root),
                )

            invoke.assert_awaited_once()
            self.assertEqual([source], invoke.await_args.kwargs["samples"])
            self.assertEqual(1, len(result["external_source_records"]))
            self.assertEqual("analyzed", result["external_source_files"][0].status)
            self.assertFalse((skills / _skill().name / "SKILL.md").exists())

    async def test_single_file_uses_full_source_but_does_not_persist_rule(self) -> None:
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
                    {"external_source_files": [_source_file()], "external_source_records": []},
                    _config(root),
                )

            invoke.assert_awaited_once()
            observed = invoke.await_args.kwargs["samples"]
            self.assertEqual([source], observed)
            self.assertEqual("analyzed", result["external_source_files"][0].status)
            self.assertEqual(30, len(result["external_source_records"]))
            self.assertFalse((skills / _skill().name / "SKILL.md").exists())

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
                    {"external_source_files": [_source_file()], "external_source_records": []},
                    _config(root),
                )

            # held-out(뒷부분)에서 규칙이 죽으면 재제안 없이 정직 거부.
            self.assertEqual(1, invoke.await_count)
            self.assertEqual([], result["external_source_records"])
            self.assertEqual("unanalyzed", result["external_source_files"][0].status)
            self.assertEqual(
                "pattern_rejected",
                result["external_source_files"][0].disposition_reason,
            )

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
                        "external_source_files": [
                            _source_file("broken.cfgx"), _source_file("settings.cfgx"),
                        ],
                        "external_source_records": [],
                    },
                    _config(root),
                )

            # 해독 불가 파일은 run 을 죽이지 않고 혼자 미분석으로 남는다.
            by_path = {
                file.project_relative_path: (
                    file.status, file.disposition_reason,
                )
                for file in result["external_source_files"]
            }
            self.assertEqual(("unanalyzed", "undecodable_text"), by_path["broken.cfgx"])
            self.assertEqual(("analyzed", ""), by_path["settings.cfgx"][:1] + (by_path["settings.cfgx"][1],))

    async def test_non_identifier_keys_never_become_link_targets(self) -> None:
        # custom key 는 어댑터와 같은 식별자 형태 게이트를 거친다 — 형태 미달이면
        # 청크는 남되 링킹 대상 key 가 되지 않는다 (REFER_TO 오염 차단).
        loose = ExternalSkill(
            name="loose-sentence",
            extensions=(".cfgx",),
            rules=(ExternalExtractionRule(
                name="entry",
                pattern=r"(?m)^(?P<key>[A-Z]+ [A-Z]+)=(?P<value>.+)$",
                key_group="key",
            ),),
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
                    {"external_source_files": [_source_file()], "external_source_records": []},
                    _config(root),
                )

            record = result["external_source_records"][0]
            self.assertEqual((), record.addressable_keys)
            self.assertEqual(
                source[record.source_character_start:record.source_character_end],
                record.exact_text,
            )

    async def test_mixed_shapes_within_extension_learn_separately(self) -> None:
        # "같은 확장자 = 같은 형식" 가정 제거 — 모양이 다르면 딴 형식으로 묶는다.
        upper = "".join(f"KEY{i:02d}.PATH=/v{i}\n" for i in range(30))
        table = "".join(f"row{i}|{i}|on\n" for i in range(30))
        table_skill = _pipe_skill()
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
                        "external_source_files": [_source_file("a.cfgx"), _source_file("b.cfgx")],
                        "external_source_records": [],
                    },
                    _config(root),
                )

            self.assertEqual(2, invoke.await_count)     # 형식 그룹 2개로 따로 학습
            statuses = {
                file.project_relative_path: file.status
                for file in result["external_source_files"]
            }
            self.assertEqual({"a.cfgx": "analyzed", "b.cfgx": "analyzed"}, statuses)
            self.assertEqual(60, len(result["external_source_records"]))

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
            twin = _source_file("two.cfgy")
            twin = type(twin)(**{**twin.__dict__, "extension": ".cfgy"})
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
                        "external_source_files": [_source_file("one.cfgx"), twin],
                        "external_source_records": [],
                    },
                    _config(root),
                )

            statuses = {
                file.project_relative_path: (
                    file.status, file.disposition_reason,
                )
                for file in result["external_source_files"]
            }
            self.assertEqual(("analyzed", ""), statuses["one.cfgx"])
            self.assertEqual(("analyzed", ""), statuses["two.cfgy"])

    async def test_extensionless_files_analyze_in_run_but_do_not_persist(self) -> None:
        source = "".join(f"KEY{i:02d}.PATH=/v{i}\n" for i in range(30))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            skills = Path(temp) / "skills"
            root.mkdir()
            (root / "DATAFILE").write_text(source, encoding="utf-8", newline="\n")
            base = _source_file("DATAFILE")
            file = type(base)(**{**base.__dict__, "extension": ""})
            invoke = AsyncMock(return_value=validate_external_skill(_skill(), [source]))

            with (
                patch("pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR", skills),
                patch("pipeline.steps.step4_scan_project.scan_external_step.run_external_agent", invoke),
            ):
                result = await scan_external_step(
                    {"external_source_files": [file], "external_source_records": []},
                    _config(root),
                )

            self.assertEqual("analyzed", result["external_source_files"][0].status)
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
                        "external_source_files": [
                            _source_file("settings.cfgx"), _source_file("other.cfgx"),
                        ],
                        "external_source_records": [],
                    },
                    _config(root),
                )

            self.assertEqual([], result["external_source_records"])
            for file in result["external_source_files"]:
                self.assertEqual(
                    ("unanalyzed", "pattern_rejected"),
                    (file.status, file.disposition_reason),
                )

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
