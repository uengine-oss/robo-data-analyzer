"""Step4 unknown-format learning and deterministic custom chunk application (spec 078).

The Agent only proposes span-producing skills; a deterministic verifier gate
decides.  A freshly proposed skill is promoted only when it also holds on a
held-out file it never saw, and every skill must reject negative decoys.
Files that cannot be verified stay honestly ``unanalyzed`` — no partial chunks.
"""
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
from graph.schema.graph_nodes import ExternalChunk, ProjectFile
from shared.files.paths import resolve_within
from agents.external import ExternalAgentRejected, run_external_agent
from agents.skills.catalog import EXTERNAL_SKILLS_DIR
from pipeline.execution.graph_delta_stream import emit_edges, emit_message, emit_nodes
from pipeline.execution.step_config import step_config
from pipeline.steps.step3_load_external.chunk_builder import build_source_chunks
from pipeline.steps.step3_load_external.encoding_reader import read_text
from pipeline.steps.step3_load_external.parsers.span_common import SemanticSpan
from pipeline.steps.step3_load_external.span_verifier import (
    SpanVerificationError,
    verify_spans,
)
from pipeline.steps.step4_scan_project.external_skills import (
    ExternalSkillValidationError,
    ValidatedExternalSkill,
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

# 다른 형식으로 위장한 decoy — 어떤 skill 도 여기서 매치를 내면 승격 금지 (공용, 고객 데이터 아님).
_BUILTIN_NEGATIVE_SAMPLES = (
    "<root>\n  <item id=\"decoy\">value</item>\n</root>\n",
    "{\n  \"decoy\": {\"nested\": true}\n}\n",
    "# comment only\n\n; another comment\n",
)


def _skill_chunks(
    validated: ValidatedExternalSkill, file: ProjectFile, source: str,
) -> tuple[list[ExternalChunk], list[HasChunk]]:
    """Re-slice proposed spans from the source and verify the invariants."""
    spans = [
        SemanticSpan(
            char_start=match.start,
            char_end=match.end,
            keys=(match.key,),
            semantic_kind="agent_segment",
            name=match.key,
        )
        for match in apply_validated_external_skill(validated, source)
    ]
    verified = verify_spans(source, spans)
    return build_source_chunks(
        file_id=file.id, relpath=file.relpath, source=source, verified=verified)


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
    new_chunks: list[ExternalChunk] = []
    new_edges: list[HasChunk] = []

    def reject(files: list[ProjectFile], reason: str, detail: str) -> None:
        log_warning(_STEP, reason, detail, files=[file.relpath for file in files])
        for file in files:
            updated[file.id] = replace(file, status="unanalyzed", reason=reason)

    with step_processing_scope(_STEP, stream):
        for extension, files in sorted(grouped.items()):
            await gate()
            samples = [decoded[file.id] for file in files]
            negative_samples = [
                *_BUILTIN_NEGATIVE_SAMPLES,
                *(decoded[other.id][:20000]
                  for other_ext, group in sorted(grouped.items()) if other_ext != extension
                  for other in group[:2]),
            ]
            matches = [
                skill for skill in loaded_skills
                if any(skill_matches_file(skill, extension, decoded[file.id]) for file in files)
            ]
            if len(matches) > 1:
                reject(files, "pattern_rejected",
                       f"multiple external skills match {extension}: {[s.name for s in matches]}")
                continue

            validated = None
            if matches:
                # 저장된 skill 은 이 파일들을 본 적이 없다 — 현재 파일 전체가 held-out 증거.
                try:
                    validated = validate_external_skill(
                        matches[0], samples, negative_samples=negative_samples)
                except ExternalSkillValidationError as error:
                    log_warning(_STEP, "stored_skill_rejected", str(error), skill=matches[0].name)

            if validated is None:
                if len(samples) < 2:
                    # held-out 검증 없이는 새 skill 을 승격할 수 없다 (agent-verifier-contract).
                    reject(files, "no_held_out_evidence",
                           f"single {extension} file cannot provide held-out validation")
                    continue
                run = cfg.get("run")
                cost_budget = cfg.get("cost_budget")
                llm_semaphore = cfg.get("llm_semaphore")
                if run is None or cost_budget is None or llm_semaphore is None or pause is None:
                    raise RuntimeError(
                        "unknown external format requires run/cost_budget/llm_semaphore/pause configurable"
                    )
                train_samples, held_out_samples = samples[:-1], samples[-1:]
                try:
                    proposed = await run_external_agent(
                        extension=extension,
                        samples=train_samples,
                        negative_samples=negative_samples,
                        run_dir=run.run_dir,
                        cost_budget=cost_budget,
                        llm_semaphore=llm_semaphore,
                        pause=pause,
                        stream_emit=stream.emit if stream is not None else None,
                    )
                    # 결정론 held-out 게이트: 제안에 쓰지 않은 파일에서도 성립해야 승격.
                    validated = validate_external_skill(
                        proposed.skill, held_out_samples, negative_samples=negative_samples)
                    persist_external_skill(validated.skill, skills_root)
                    loaded_skills.append(validated.skill)
                except (ExternalAgentRejected, ExternalSkillValidationError) as error:
                    reject(files, "pattern_rejected", str(error))
                    continue

            for file in files:
                async with track_processing("ProjectFile", file.relpath, sub_action="custom_format"):
                    try:
                        chunks, edges = _skill_chunks(validated, file, decoded[file.id])
                    except (ExternalSkillValidationError, SpanVerificationError) as error:
                        updated[file.id] = replace(file, reason="pattern_rejected")
                        log_warning(_STEP, "pattern_rejected", str(error), file_path=file.relpath)
                        continue
                    if not chunks:
                        updated[file.id] = replace(file, reason="pattern_rejected")
                        continue
                    new_chunks.extend(chunks)
                    new_edges.extend(edges)
                    updated[file.id] = replace(file, category="custom", status="analyzed", reason="")

    await gate()
    project_files = [updated[file.id] for file in state.get("project_files") or []]
    if neo is not None:
        await upsert_external_nodes(neo, [*project_files, *new_chunks])
        await upsert_external_edges(neo, new_edges)
    await emit_nodes(stream, project_files)
    await emit_nodes(stream, new_chunks)
    await emit_edges(stream, new_edges)
    await gate()
    emit_message(stream, f"✅ 외부 포맷 스캔 완료 — custom 청크 {len(new_chunks)}")
    log_info(
        _STEP,
        "done",
        unknown_files=len(unknown_files),
        custom_chunks=len(new_chunks),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "project_files": project_files,
        "external_chunks": [*(state.get("external_chunks") or []), *new_chunks],
        "external_edges": new_edges,
    }
