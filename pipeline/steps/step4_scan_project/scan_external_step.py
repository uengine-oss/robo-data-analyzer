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
from pipeline.steps.step3_load_external.encoding_reader import TextDecodeError, read_text
from pipeline.steps.step3_load_external.parsers.span_common import SemanticSpan, is_reference_key
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
from pipeline.steps.step4_scan_project.structure_miner import line_shape_profile
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


# 파일 내 held-out 분할 기준: 검증 증거의 단위는 파일이 아니라 반복 레코드다.
# 최소 반복 증거(비공백 20줄)와 유의미한 미관측 구간(비공백 5줄)이 있어야 하며,
# 라인 경계로만 자른다. 기준 미달이면 분할하지 않는다(정직한 미분석 유지).
_INTRA_MIN_LINES = 20
_INTRA_MIN_HELD_OUT_LINES = 5
_INTRA_TRAIN_RATIO = 0.7


def _shape_groups(
    files: list[ProjectFile], profiles: dict[str, frozenset],
) -> list[tuple[set, list[ProjectFile]]]:
    """같은 확장자 안에서 내용 모양이 겹치는 파일끼리 묶는다 (감사 반영).

    "같은 확장자 = 같은 형식" 은 이름 기반 짐작이라 혼합 확장자에서 오분할을
    낳는다 — 지배적 줄 모양이 하나라도 겹칠 때만 같은 형식으로 본다. 겹침이
    전무한 파일(자유서식 포함)은 단독 그룹이 되어 파일 내 held-out 경로를 탄다.
    """
    groups: list[tuple[set, list[ProjectFile]]] = []
    for file in files:
        profile = set(profiles[file.id])
        merged: tuple[set, list[ProjectFile]] | None = None
        for group in groups:
            if profile & group[0]:
                group[0].update(profile)
                group[1].append(file)
                merged = group
                break
        if merged is None:
            groups.append((profile, [file]))
            continue
        # 새 파일이 다리가 되어 기존 그룹끼리 연결될 수 있다 — 고정점까지 병합.
        changed = True
        while changed:
            changed = False
            for a in range(len(groups)):
                for b in range(a + 1, len(groups)):
                    if groups[a][0] & groups[b][0]:
                        groups[a][0].update(groups[b][0])
                        groups[a][1].extend(groups[b][1])
                        del groups[b]
                        changed = True
                        break
                if changed:
                    break
    return groups


def _intra_file_held_out(text: str) -> tuple[str, str] | None:
    lines = text.splitlines(keepends=True)
    if sum(1 for line in lines if line.strip()) < _INTRA_MIN_LINES:
        return None
    cut = int(len(lines) * _INTRA_TRAIN_RATIO)
    train, held_out = "".join(lines[:cut]), "".join(lines[cut:])
    if sum(1 for line in held_out.splitlines() if line.strip()) < _INTRA_MIN_HELD_OUT_LINES:
        return None
    return train, held_out


def _skill_chunks(
    validated: ValidatedExternalSkill, file: ProjectFile, source: str,
) -> tuple[list[ExternalChunk], list[HasChunk]]:
    """Re-slice proposed spans from the source and verify the invariants."""
    spans = [
        SemanticSpan(
            char_start=match.start,
            char_end=match.end,
            # 어댑터 경로와 같은 식별자 형태 게이트 — 형태 미달 key 는 링킹 대상에서
            # 제외한다 (청크는 유지). custom key 가 REFER_TO 오연결을 만들지 않게.
            keys=(match.key,) if is_reference_key(match.key) else (),
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
    undecodable: list[tuple[ProjectFile, str]] = []
    for file in unknown_files:
        path = resolve_within(root, file.relpath)
        try:
            decoded[file.id] = read_text(path, source_cache).text
        except TextDecodeError as error:
            # 해독 불가 파일 하나가 run 전체를 죽이면 안 된다 — 그 파일만 정직 미분석.
            undecodable.append((file, str(error)))
            continue
        grouped[file.ext or Path(file.relpath).suffix.lower()].append(file)

    loaded_skills = load_external_skills(skills_root)
    updated = {file.id: file for file in state.get("project_files") or []}
    new_chunks: list[ExternalChunk] = []
    new_edges: list[HasChunk] = []

    def reject(files: list[ProjectFile], reason: str, detail: str) -> None:
        log_warning(_STEP, reason, detail, files=[file.relpath for file in files])
        for file in files:
            updated[file.id] = replace(file, status="unanalyzed", reason=reason)

    profiles = {file_id: line_shape_profile(text) for file_id, text in decoded.items()}
    format_groups = [
        (extension, group_profile, group_files)
        for extension, files in sorted(grouped.items())
        for group_profile, group_files in _shape_groups(files, profiles)
    ]

    with step_processing_scope(_STEP, stream):
        for file, detail in undecodable:
            reject([file], "undecodable_text", detail)
        for extension, group_profile, files in format_groups:
            await gate()
            samples = [decoded[file.id] for file in files]
            member_ids = {file.id for file in files}
            # 미끼는 "다른 그룹" 파일 중 모양이 겹치지 않는 것만 — 같은 형식이 두
            # 확장자로 존재할 때 서로를 실격시키던 가정("다른 확장자=다른 형식")을
            # 내용 증거로 대체한다 (감사 반영). 같은 확장자의 다른 모양 그룹은
            # 이제 정당한 미끼가 된다.
            shape_safe_decoys = [
                decoded[other.id][:20000]
                for other in unknown_files
                if other.id in decoded and other.id not in member_ids
                and not (set(profiles[other.id]) & group_profile)
            ][:6]
            negative_samples = [*_BUILTIN_NEGATIVE_SAMPLES, *shape_safe_decoys]
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
                run = cfg.get("run")
                cost_budget = cfg.get("cost_budget")
                llm_semaphore = cfg.get("llm_semaphore")
                if run is None or cost_budget is None or llm_semaphore is None or pause is None:
                    raise RuntimeError(
                        "unknown external format requires run/cost_budget/llm_semaphore/pause configurable"
                    )
                if len(samples) >= 2:
                    # train 은 큰 파일부터 — 구조 증거가 많은 쪽을 관찰시키고 작은 쪽을 held-out 으로.
                    ordered = sorted(samples, key=len, reverse=True)
                    train_samples, held_out_samples = ordered[:-1], ordered[-1:]
                else:
                    # 파일이 1개뿐이면 파일 내부 반복으로 미관측 증거를 만든다:
                    # 앞부분만 관찰시키고 뒷부분(held-out)은 Agent 가 보지 못한다.
                    # 반복이 빈약해 분할이 성립하지 않으면 종전대로 정직하게 미분석.
                    split = _intra_file_held_out(samples[0])
                    if split is None:
                        reject(files, "no_held_out_evidence",
                               f"single {extension} file lacks enough repetition for intra-file held-out")
                        continue
                    train_samples, held_out_samples = [split[0]], [split[1]]
                heldout_feedback = ""
                validated = None
                try:
                    # held-out 실패는 사유를 피드백으로 1회 재제안한다 — 승인 권한은
                    # 여전히 결정론 게이트에 있고, Agent 는 교정 기회만 얻는다.
                    for _held_out_attempt in range(2):
                        proposed = await run_external_agent(
                            extension=extension,
                            samples=train_samples,
                            negative_samples=negative_samples,
                            run_dir=run.run_dir,
                            cost_budget=cost_budget,
                            llm_semaphore=llm_semaphore,
                            pause=pause,
                            stream_emit=stream.emit if stream is not None else None,
                            initial_feedback=heldout_feedback,
                        )
                        try:
                            validated = validate_external_skill(
                                proposed.skill, held_out_samples,
                                negative_samples=negative_samples)
                            break
                        except ExternalSkillValidationError as error:
                            heldout_feedback = (
                                f"held-out validation failed: {error} — 규칙이 관찰 파일에만 "
                                "있는 접두/토큰을 리터럴로 고정하지 않았는지 점검하고, "
                                "구조(문자 부류)로 일반화한 변형을 채택하라."
                            )
                            log_warning(_STEP, "held_out_retry", str(error), extension=extension)
                    if validated is None:
                        reject(files, "pattern_rejected", heldout_feedback)
                        continue
                    if extension:
                        persist_external_skill(validated.skill, skills_root)
                        loaded_skills.append(validated.skill)
                    else:
                        # 확장자 없는 파일용 skill 은 재사용 매칭 근거(확장자)가 없어
                        # 저장하지 않는다 — 와일드카드 재사용은 형식 납치 위험이 더 크다.
                        # 이번 run 안에서는 그대로 사용한다 (다음 run 은 재학습).
                        log_info(_STEP, "extensionless_skill_in_run_only", skill=validated.skill.name)
                except ExternalAgentRejected as error:
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
    analyzed_count = sum(
        1 for file in unknown_files if updated[file.id].status == "analyzed")
    unanalyzed_count = len(unknown_files) - analyzed_count
    # 전건 거부여도 성공처럼 읽히지 않게 분석/미분석을 그대로 표기한다 (감사 반영).
    emit_message(
        stream,
        f"외부 포맷 스캔 — 분석 {analyzed_count} · 미분석 {unanalyzed_count} · "
        f"custom 청크 {len(new_chunks)}",
    )
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
