"""Step4 unknown-format learning and deterministic custom chunk application.

The Agent only proposes span-producing skills; a deterministic verifier gate
decides.  A freshly proposed skill is persisted for later runs only when it
also holds on a whole file the Agent never observed.  A lone file is still
analyzed from its complete source for the current run, but its rule is not
persisted without independent evidence.  Every skill must reject every known
negative decoy.  Format groups are built from dominant line shapes, not
filename extensions.  Files that cannot be verified stay honestly
``unanalyzed`` — no partial records.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.files.safe_paths import resolve_within
from agents.external_agent import ExternalAgentRejected, run_external_agent
from agents.agent_runtime import AgentModelOutputError
from agents.skills.skill_loader import EXTERNAL_SKILLS_DIR
from pipeline.execution.graph_delta_stream import emit_message
from pipeline.execution.quality_finding_log import record_analysis_findings
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.external_source import ExternalSourceFile, ExternalSourceRecord
from pipeline.execution.step_config import step_config
from pipeline.stages.external.chunk_builder import build_source_records
from pipeline.stages.external.encoding_reader import TextDecodeError, read_text
from pipeline.stages.external.parsers.span_common import SemanticSpan, is_reference_key
from pipeline.stages.external.span_verifier import (
    SpanVerificationError,
    verify_spans,
)
from pipeline.stages.scan.external_skills import (
    ExternalSkill,
    ExternalSkillValidationError,
    ValidatedExternalSkill,
    apply_validated_external_skill,
    load_external_skills,
    persist_external_skill,
    validate_external_skill,
)
from pipeline.stages.scan.structure_miner import line_shape_profile
from shared.events.processing_events import step_processing_scope, track_processing
from shared.events.runtime_logger import log_info, log_warning
from shared.files.source_cache import SourceFileCache


_STEP = "step4.scan_external"

# 다른 형식으로 위장한 decoy — 어떤 skill 도 여기서 매치를 내면 승격 금지 (공용, 고객 데이터 아님).
_BUILTIN_NEGATIVE_SAMPLES = (
    "<root>\n  <item id=\"decoy\">value</item>\n</root>\n",
    "{\n  \"decoy\": {\"nested\": true}\n}\n",
    "# comment only\n\n; another comment\n",
)


def _shape_groups(
    files: list[ExternalSourceFile], profiles: dict[str, frozenset],
) -> list[tuple[set, list[ExternalSourceFile]]]:
    """같은 확장자 안에서 내용 모양이 겹치는 파일끼리 묶는다.

    "같은 확장자 = 같은 형식" 은 이름 기반 짐작이라 혼합 확장자에서 오분할을
    낳는다 — 지배적 줄 모양이 하나라도 겹칠 때만 같은 형식으로 본다. 겹침이
    전무한 파일(자유서식 포함)은 단독 그룹이 되어 현재 실행 전용 검증 경로를 탄다.
    """
    groups: list[tuple[set, list[ExternalSourceFile]]] = []
    for file in files:
        profile = set(profiles[file.source_file_id])
        merged: tuple[set, list[ExternalSourceFile]] | None = None
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


def _shape_safe_negative_samples(
    *,
    unknown_files: list[ExternalSourceFile],
    decoded: dict[str, str],
    profiles: dict[str, frozenset],
    member_ids: set[str],
    group_profile: set,
) -> list[str]:
    """Return every complete, structurally disjoint file as a negative example."""
    return [
        decoded[other.source_file_id]
        for other in unknown_files
        if other.source_file_id in decoded
        and other.source_file_id not in member_ids
        and not (set(profiles[other.source_file_id]) & group_profile)
    ]


def _skill_records(
    validated: ValidatedExternalSkill,
    source_file: ExternalSourceFile,
    source: str,
) -> list[ExternalSourceRecord]:
    """Re-slice proposed spans from the source and verify the invariants."""
    spans: list[SemanticSpan] = []
    for match in apply_validated_external_skill(validated, source):
        # 검증된 custom key 는 이 포맷에서 코드가 레코드를 찾는 실제 주소다.
        # 식별자 형태 게이트를 통과하지 못하면 원문 레코드는 보존하되 연결 후보로는
        # 내보내지 않는다.
        addressable_keys = (match.key,) if is_reference_key(match.key) else ()
        spans.append(SemanticSpan(
            char_start=match.start,
            char_end=match.end,
            keys=addressable_keys,
            semantic_kind="agent_segment",
            name=match.key,
            addressable_keys=addressable_keys,
        ))
    verified = verify_spans(source, spans)
    return build_source_records(
        file_id=source_file.source_file_id,
        relpath=source_file.project_relative_path,
        source=source,
        verified=verified,
        external_adapter_id=f"analyzer.external.skill.{validated.skill.name}",
        external_adapter_version=validated.fingerprint,
    )


async def _learn_group_skill(
    *,
    extension: str,
    samples: list[str],
    negative_samples: list[str],
    cfg: dict,
    pause,
    stream,
    skills_root: Path,
    loaded_skills: list[ExternalSkill],
    skill_ordinal: int,
) -> tuple[ValidatedExternalSkill | None, tuple[str, str] | None]:
    """한 형식 그룹의 새 skill 학습 — Agent 제안 → 검증 → 조건부 저장.

    성공 시 (validated, None), 실패 시 (None, (reason, detail)) 로 호출부가 reject 한다.
    승인 권한은 끝까지 결정론 게이트에 있다. 독립 파일이 있을 때만 재사용 skill로
    저장하고, 단일 파일 제안은 그 파일 전문을 다시 검증한 현재 run에서만 사용한다.
    """
    run = cfg.get("run")
    cost_budget = cfg.get("cost_budget")
    llm_semaphore = cfg.get("llm_semaphore")
    if run is None or cost_budget is None or llm_semaphore is None or pause is None:
        raise RuntimeError(
            "unknown external format requires run/cost_budget/llm_semaphore/pause configurable"
        )
    has_independent_held_out = len(samples) >= 2
    if has_independent_held_out:
        # train 은 큰 파일부터 — 구조 증거가 많은 쪽을 관찰시키고 작은 쪽을 held-out 으로.
        ordered = sorted(samples, key=len, reverse=True)
        train_samples, held_out_samples = ordered[:-1], ordered[-1:]
    else:
        # 한 파일을 임의 줄 수나 비율로 자르면 레코드 경계를 깨고 짧은 유효 파일을
        # 건너뛴다. 전문을 제안·검증에 사용하되 독립 증거가 없으므로 저장하지 않는다.
        train_samples = list(samples)
        held_out_samples = list(samples)

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
            skill_ordinal=skill_ordinal,
        )
        validated = validate_external_skill(
            proposed.skill, held_out_samples, negative_samples=negative_samples
        )
        if extension and has_independent_held_out:
            persist_external_skill(validated.skill, skills_root)
            loaded_skills.append(validated.skill)
        else:
            reason = (
                "extensionless"
                if not extension
                else "no_independent_held_out_file"
            )
            log_info(
                _STEP,
                "external_skill_in_run_only",
                skill=validated.skill.name,
                reason=reason,
            )
        return validated, None
    except (ExternalAgentRejected, ExternalSkillValidationError, AgentModelOutputError) as error:
        status = "unavailable" if isinstance(error, AgentModelOutputError) else "rejected"
        record_analysis_findings([AnalysisFinding(
            concern="external_format",
            status=status,
            target_id=extension or "<extensionless>",
            reasons=(f"{type(error).__name__}: {error}",),
            evidence_rule="external_agent",
        )])
        return None, ("pattern_rejected", str(error))


async def scan_external_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    unknown_files = [
        file for file in (state.get("external_source_files") or [])
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
    skills_root: Path = EXTERNAL_SKILLS_DIR
    started = time.monotonic()

    async def gate() -> None:
        if pause is not None:
            await pause.gate()

    decoded: dict[str, str] = {}
    grouped: dict[str, list[ExternalSourceFile]] = defaultdict(list)
    undecodable: list[tuple[ExternalSourceFile, str]] = []
    for file in unknown_files:
        path = resolve_within(root, file.project_relative_path)
        try:
            decoded[file.source_file_id] = read_text(path, source_cache).text
        except TextDecodeError as error:
            # 해독 불가 파일 하나가 run 전체를 죽이면 안 된다 — 그 파일만 정직 미분석.
            undecodable.append((file, str(error)))
            continue
        grouped[
            file.extension or Path(file.project_relative_path).suffix.lower()
        ].append(file)

    loaded_skills = load_external_skills(skills_root)
    updated = {
        file.source_file_id: file
        for file in state.get("external_source_files") or []
    }
    new_records: list[ExternalSourceRecord] = []

    def reject(files: list[ExternalSourceFile], reason: str, detail: str) -> None:
        log_warning(
            _STEP,
            reason,
            detail,
            files=[file.project_relative_path for file in files],
        )
        for file in files:
            updated[file.source_file_id] = replace(
                file, status="unanalyzed", disposition_reason=reason,
            )

    profiles = {file_id: line_shape_profile(text) for file_id, text in decoded.items()}
    format_groups = [
        (extension, group_profile, group_files)
        for extension, files in sorted(grouped.items())
        for group_profile, group_files in _shape_groups(files, profiles)
    ]

    with step_processing_scope(_STEP, stream):
        for file, detail in undecodable:
            reject([file], "undecodable_text", detail)
        extension_ordinals: dict[str, int] = defaultdict(int)
        for extension, group_profile, files in format_groups:
            extension_ordinals[extension] += 1
            await gate()
            samples = [decoded[file.source_file_id] for file in files]
            member_ids = {file.source_file_id for file in files}
            # 미끼는 "다른 그룹" 파일 중 모양이 겹치지 않는 것만 — 같은 형식이 두
            # 확장자로 존재할 때 서로를 실격시키던 가정("다른 확장자=다른 형식")을
            # 내용 증거로 대체한다. 같은 확장자의 다른 모양 그룹은
            # 이제 정당한 미끼가 된다.
            shape_safe_decoys = _shape_safe_negative_samples(
                unknown_files=unknown_files,
                decoded=decoded,
                profiles=profiles,
                member_ids=member_ids,
                group_profile=group_profile,
            )
            negative_samples = [*_BUILTIN_NEGATIVE_SAMPLES, *shape_safe_decoys]
            stored_candidates = [
                skill for skill in loaded_skills
                if extension.lower() in skill.extensions
            ]
            validated_candidates: list[ValidatedExternalSkill] = []
            for skill in stored_candidates:
                try:
                    validated_candidates.append(validate_external_skill(
                        skill, samples, negative_samples=negative_samples,
                    ))
                except ExternalSkillValidationError as error:
                    log_warning(
                        _STEP, "stored_skill_rejected", str(error), skill=skill.name,
                    )
            if len(validated_candidates) > 1:
                reject(files, "pattern_rejected",
                       "multiple external skills validate for "
                       f"{extension}: {[item.skill.name for item in validated_candidates]}")
                continue

            validated = (
                validated_candidates[0] if validated_candidates else None
            )

            if validated is None:
                validated, failure = await _learn_group_skill(
                    extension=extension,
                    samples=samples,
                    negative_samples=negative_samples,
                    cfg=cfg,
                    pause=pause,
                    stream=stream,
                    skills_root=skills_root,
                    loaded_skills=loaded_skills,
                    skill_ordinal=extension_ordinals[extension],
                )
                if validated is None:
                    reject(files, *failure)
                    continue

            for file in files:
                async with track_processing(
                    "ExternalSourceFile",
                    file.project_relative_path,
                    sub_action="custom_format",
                ):
                    try:
                        records = _skill_records(
                            validated, file, decoded[file.source_file_id],
                        )
                    except (ExternalSkillValidationError, SpanVerificationError) as error:
                        updated[file.source_file_id] = replace(
                            file, disposition_reason="pattern_rejected",
                        )
                        log_warning(
                            _STEP,
                            "pattern_rejected",
                            str(error),
                            file_path=file.project_relative_path,
                        )
                        continue
                    if not records:
                        updated[file.source_file_id] = replace(
                            file, disposition_reason="pattern_rejected",
                        )
                        continue
                    new_records.extend(records)
                    updated[file.source_file_id] = replace(
                        file,
                        category="custom",
                        status="analyzed",
                        disposition_reason="",
                    )

    await gate()
    source_files = [
        updated[file.source_file_id]
        for file in state.get("external_source_files") or []
    ]
    await gate()
    analyzed_count = sum(
        1
        for file in unknown_files
        if updated[file.source_file_id].status == "analyzed"
    )
    unanalyzed_count = len(unknown_files) - analyzed_count
    # 전건 거부여도 성공처럼 읽히지 않게 분석/미분석을 그대로 표기한다.
    emit_message(
        stream,
        f"외부 포맷 스캔 — 분석 {analyzed_count} · 미분석 {unanalyzed_count} · "
        f"검증 레코드 {len(new_records)}",
    )
    log_info(
        _STEP,
        "done",
        unknown_files=len(unknown_files),
        custom_source_records=len(new_records),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "external_source_files": source_files,
        "external_source_records": [
            *(state.get("external_source_records") or []),
            *new_records,
        ],
    }
