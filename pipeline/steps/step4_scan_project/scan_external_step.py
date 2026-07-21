"""Step4 unknown-format learning and deterministic custom chunk application."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from graph.edges.external_links import upsert_external_edges
from graph.nodes.external import upsert_external_nodes
from graph.schema.graph_edges import HasChunk
from graph.schema.graph_nodes import ProjectFile, UnresolvedRef
from graph.schema.node_ids import unresolved_ref_id
from shared.files.paths import resolve_within
from agents.external import ExternalAgentRejected, run_external_agent
from agents.skills.catalog import EXTERNAL_SKILLS_DIR
from pipeline.execution.graph_delta_stream import emit_edges, emit_message, emit_nodes
from pipeline.execution.step_config import step_config
from pipeline.steps.step3_load_external.chunking import make_external_chunks
from pipeline.steps.step3_load_external.encoding_reader import read_text
from pipeline.steps.step4_scan_project.external_skills import (
    ExternalSkillValidationError,
    apply_validated_external_skill,
    load_external_skills,
    persist_external_skill,
    skill_matches_file,
    validate_external_skill,
)
from shared.events.processing_events import step_processing_scope, track_processing
from shared.events.runtime_logger import log_info, log_warning
from shared.files.cache import SourceFileCache


_STEP = "step4.scan_external"


def _failure(file: ProjectFile, detail: str) -> UnresolvedRef:
    return UnresolvedRef(
        id=unresolved_ref_id("external-pattern", file.id),
        name=file.name,
        target_type=file.ext or "unknown_text",
        key="",
        reason="pattern_rejected",
        file_path=file.relpath,
        detail=" ".join(detail.split())[:300],
    )


async def scan_external_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    unknown_files = [
        file for file in (state.get("project_files") or [])
        if file.category == "unknown" and file.status == "unanalyzed"
    ]
    if not unknown_files:
        return {}
    cfg = step_config(config)
    codebase = cfg.get("codebase")
    source_cache = cfg.get("source_cache")
    if codebase is None or not isinstance(source_cache, SourceFileCache):
        raise RuntimeError("step4.scan_external requires codebase and run-scoped SourceFileCache")
    root = Path(codebase.root).resolve()
    stream = cfg.get("stream")
    pause = cfg.get("pause")
    neo = cfg.get("neo_client")
    skills_root: Path = EXTERNAL_SKILLS_DIR
    started = time.monotonic()

    async def gate() -> None:
        if pause is not None:
            await pause.gate()

    decoded: dict[str, str] = {}
    grouped: dict[str, list[ProjectFile]] = defaultdict(list)
    for file in unknown_files:
        path = resolve_within(root, file.relpath)
        decoded[file.id] = read_text(path, source_cache).text
        grouped[file.ext or Path(file.relpath).suffix.lower()].append(file)

    loaded_skills = load_external_skills(skills_root)
    updated = {file.id: file for file in state.get("project_files") or []}
    new_chunks = []
    new_edges = []
    unresolved: list[UnresolvedRef] = []

    with step_processing_scope(_STEP, stream):
        for extension, files in sorted(grouped.items()):
            await gate()
            samples = [decoded[file.id] for file in files]
            matches = [
                skill for skill in loaded_skills
                if any(skill_matches_file(skill, extension, decoded[file.id]) for file in files)
            ]
            validated = None
            if len(matches) == 1:
                try:
                    validated = validate_external_skill(matches[0], samples)
                except ExternalSkillValidationError as error:
                    log_warning(_STEP, "stored_skill_rejected", str(error), skill=matches[0].name)
            elif len(matches) > 1:
                detail = f"multiple external skills match {extension}: {[skill.name for skill in matches]}"
                for file in files:
                    unresolved.append(_failure(file, detail))
                continue

            if validated is None:
                run = cfg.get("run")
                cost_budget = cfg.get("cost_budget")
                llm_semaphore = cfg.get("llm_semaphore")
                if run is None or cost_budget is None or llm_semaphore is None or pause is None:
                    raise RuntimeError(
                        "unknown external format requires run/cost_budget/llm_semaphore/pause configurable"
                    )
                try:
                    validated = await run_external_agent(
                        extension=extension,
                        samples=samples,
                        negative_samples=[],
                        run_dir=run.run_dir,
                        cost_budget=cost_budget,
                        llm_semaphore=llm_semaphore,
                        pause=pause,
                        stream_emit=stream.emit if stream is not None else None,
                    )
                    persist_external_skill(validated.skill, skills_root)
                    loaded_skills.append(validated.skill)
                except ExternalAgentRejected as error:
                    for file in files:
                        unresolved.append(_failure(file, str(error)))
                    continue

            for file in files:
                async with track_processing("ProjectFile", file.relpath, sub_action="custom_format"):
                    file_matches = apply_validated_external_skill(validated, decoded[file.id])
                    by_key: dict[str, list] = defaultdict(list)
                    for match in file_matches:
                        by_key[match.key].append(match)
                    if not by_key:
                        updated[file.id] = replace(file, reason="pattern_rejected")
                        unresolved.append(_failure(file, "validated skill produced no key for this file"))
                        continue
                    for key, observations in sorted(by_key.items()):
                        values = list(dict.fromkeys(observation.value for observation in observations))
                        text = "\n".join(values)
                        chunks, parts = make_external_chunks(
                            file_id=file.id,
                            key=key,
                            kind="custom",
                            text=text,
                            file_path=file.relpath,
                            start_line=min(observation.line for observation in observations),
                            end_line=max(observation.line for observation in observations),
                        )
                        new_chunks.extend(chunks)
                        new_edges.append(HasChunk(file.id, chunks[0].id))
                        new_edges.extend(parts)
                    updated[file.id] = replace(file, category="custom", status="analyzed", reason="")

    await gate()
    project_files = [updated[file.id] for file in state.get("project_files") or []]
    if neo is not None:
        await upsert_external_nodes(neo, [*project_files, *new_chunks, *unresolved])
        await upsert_external_edges(neo, new_edges)
    await emit_nodes(stream, project_files)
    await emit_nodes(stream, new_chunks)
    await emit_nodes(stream, unresolved)
    await emit_edges(stream, new_edges)
    await gate()
    emit_message(stream, f"✅ 외부 포맷 스캔 완료 — custom 청크 {len(new_chunks)}, 미해결 {len(unresolved)}")
    log_info(
        _STEP,
        "done",
        unknown_files=len(unknown_files),
        custom_chunks=len(new_chunks),
        unresolved=len(unresolved),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "project_files": project_files,
        "external_chunks": [*(state.get("external_chunks") or []), *new_chunks],
        "external_edges": new_edges,
        "unresolved_refs": unresolved,
    }
