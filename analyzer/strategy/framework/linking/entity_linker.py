"""엔티티 식별 도메인 — Startpoint 수집 + 시나리오별 linking 에이전트 드라이버.

entity 도메인 전체를 담는다 (분기는 2종 — 명시 시그널 vs 맨땅):

  A. 명시 시그널 분기 — 테이블/엔티티가 이미 식별돼 있는 경우
     - `ddl`        : DDL 메타가 있을 때 (또는 LLM 처리로 추출된 테이블이 있을 때)
     - `entity_fqn` : @Entity / XML 매핑 등 어노테이션·선언 기반 시그널이 있을 때
     → 시나리오별 단일 batch — 옛 흐름 그대로

  B. 맨땅 분기 (`candidates` + `profile_only` 통합) — 명시 시그널 없는 경우
     - 분석 대상 파일 (ontology 의 module 역할 — kind in module_types(lang)) 별로 병렬 invoke
     - 각 파일 agent 가 자기 파일 chunks 만 검색 (file_path_filter)
     - ProFrame `mpfmdbio("table_<crud>####", ...)` 같은 결정론 패턴은 미리 추출해 user_msg 에 직접 주입
     - codebase_pattern (profile) 가 검색 동력
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from config.settings import settings
from util.llm_audit_logger import log_llm_interaction
from analyzer.strategy.framework.common.domain.ontology import get_ontology
from analyzer.strategy.framework.common.models.node_models import ColumnRef, FkEdge, FunctionTableEdge
from analyzer.strategy.framework.linking.agent.linking_agent import init_linking_env
from analyzer.strategy.framework.linking.agent.linking_tools import ToolResultCollector

logger = logging.getLogger(__name__)

# 임시 조치: DDL/entity_fqn 배치당 테이블 개수 (추후 settings.deep_agent.entity_batch_size / env 로 복원)
_BATCH_SIZE = 3


# 엔티티 시그널 — 언어/프레임워크 무관 (어노테이션, 상속 패턴)
ENTITY_SIGNALS_ANNOTATION = [
    # JVM (Java, Kotlin, etc.)
    "@Entity", "@Table", "@MappedSuperclass", "@Document", "@RedisHash",
    # Python (Django, SQLAlchemy)
    "db.Model", "models.Model",
    # .NET (C#, etc.)
    "[Table", "[Key",
    # General
    "EntityBean",
]


# ProFrame `mpfmdbio("table_<crud>####", ...)` — 첫 인자 리터럴
_MPFMDBIO_RE = re.compile(r'mpfmdbio\s*\(\s*"([^"]+)"', re.IGNORECASE)
# 리터럴 → table 정규화: `<table>_<s|i|u|d>####` suffix strip
_DBIO_SUFFIX_RE = re.compile(r'^(.+)_([siud])\d{4}$', re.IGNORECASE)


@dataclass
class EntityStartpoints:
    """엔티티 식별 시작점 — 시나리오 dispatch 의 입력."""
    ddl_tables: List[Tuple[str, Dict]]
    xml_entity_fqns: set
    annotated_fqns: set
    codebase_pattern: str


def identify_entity_classes(module_index: Dict[str, Any]) -> set:
    """module_index에서 엔티티 후보 FQN 집합을 반환. 어노테이션/상속 패턴으로 판별 (범용)."""
    entity_fqns: set = set()
    for fqn, info in module_index.items():
        if not isinstance(info, dict):
            continue
        ann = str(info.get("annotations") or "")
        if any(s in ann for s in ENTITY_SIGNALS_ANNOTATION):
            entity_fqns.add(fqn)
    return entity_fqns


def extract_entity_fqns_from_artifacts(
    artifact_index: Optional[Dict[str, Any]],
    module_index: Optional[Dict[str, Any]],
) -> set:
    """XML 엔티티 청크(EJB `<entity>`, hibernate-mapping 등)의 related_module_fqns 에서 FQN 추출."""
    if not artifact_index or not module_index:
        return set()
    entity_fqns: set = set()
    for ac in artifact_index.values():
        if ac.chunk_type not in ("entity", "cmp_mapping"):
            continue
        related = getattr(ac, "related_module_fqns", None) or []
        for fqn in related:
            if fqn in module_index:
                entity_fqns.add(fqn)
    return entity_fqns


def collect_entity_startpoints(
    module_index: Dict[str, Any],
    artifact_index: Optional[Dict[str, Any]],
    ddl_table_metadata: Optional[Dict],
    codebase_pattern: str,
) -> EntityStartpoints:
    """결정론적으로 수집 가능한 모든 엔티티 시작점을 모은다."""
    ddl_tables = [
        (meta.get("original_name", name), meta)
        for (schema, name), meta in (ddl_table_metadata or {}).items()
    ]
    xml_fqns = extract_entity_fqns_from_artifacts(artifact_index, module_index)
    annotated = identify_entity_classes(module_index or {})
    return EntityStartpoints(
        ddl_tables=ddl_tables,
        xml_entity_fqns=xml_fqns,
        annotated_fqns=annotated,
        codebase_pattern=codebase_pattern,
    )


# ═══════════════════════════════════════════════════════════════════════
# Section 2: 결정론 mpfmdbio 추출 (맨땅 분기 전용)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class DeterministicDbioCall:
    """결정론적으로 추출된 ProFrame mpfmdbio 호출."""
    function_id: str
    table_name: str
    access: str         # "READS" | "WRITES"
    raw_literal: str    # 원본 리터럴 (suffix 포함)
    line: int


def extract_deterministic_dbio_calls(
    chunk_index: Dict[str, Dict[str, Any]],
) -> Dict[str, List[DeterministicDbioCall]]:
    """함수 청크 텍스트에서 `mpfmdbio("...")` 패턴 → file_path 별 결정론 호출 리스트."""
    by_file: Dict[str, List[DeterministicDbioCall]] = {}
    for cid, info in chunk_index.items():
        if "#" in cid or cid.startswith("C:"):
            continue
        text = info.get("text") or ""
        if "mpfmdbio" not in text.lower():
            continue
        file_path = info.get("filePath") or ""
        span = info.get("span") or [0, 0]
        base_line = int(span[0]) if span else 0
        for m in _MPFMDBIO_RE.finditer(text):
            raw = m.group(1)
            sm = _DBIO_SUFFIX_RE.match(raw)
            if sm:
                table = sm.group(1).lower()
                crud = sm.group(2).lower()
                access = "READS" if crud == "s" else "WRITES"
            else:
                table = raw.lower()
                access = "READS"
            line_offset = text[:m.start()].count("\n")
            by_file.setdefault(file_path, []).append(
                DeterministicDbioCall(
                    function_id=cid,
                    table_name=table,
                    access=access,
                    raw_literal=raw,
                    line=base_line + line_offset,
                )
            )
    return by_file


def _determine_target_files(
    module_index: Dict[str, Any],
    chunk_index: Dict[str, Dict[str, Any]],
    lang: str,
) -> List[Tuple[str, str]]:
    """맨땅 분기의 분석 대상 = MODULE 역할 항목 (ontology 가 언어별 정의).

    각 항목의 file_path 는 chunk_index 에서 — 그 항목의 함수 chunk 들이 가진 filePath 다수결로 결정.
    chunk_index 에 흔적이 없는 항목은 분석 대상이 아님.
    """
    module_kinds = get_ontology().module_types(lang)
    module_entries = [
        (fqn, info)
        for fqn, info in (module_index or {}).items()
        if isinstance(info, dict) and info.get("kind") in module_kinds
    ]
    fqn_to_filepath: Dict[str, str] = {}
    for cid, c_info in (chunk_index or {}).items():
        if "#" in cid or cid.startswith("C:"):
            continue
        for fqn, _ in module_entries:
            if cid.startswith(fqn + "."):
                fp = c_info.get("filePath") or ""
                if fp and fqn not in fqn_to_filepath:
                    fqn_to_filepath[fqn] = fp
                break

    targets: List[Tuple[str, str]] = []
    for fqn, _info in module_entries:
        fp = fqn_to_filepath.get(fqn, "")
        if fp:
            targets.append((fqn, fp))
    targets.sort()
    return targets


# ═══════════════════════════════════════════════════════════════════════
# Section 3: Top-level 드라이버 — 시나리오 dispatch
# ═══════════════════════════════════════════════════════════════════════


def run_entity_identification(
    startpoints: EntityStartpoints,
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[List[FunctionTableEdge], List[FkEdge], List[ColumnRef]]:
    """Startpoint 기반 엔티티 식별 + Function→Table 연결.

    분기 결정:
      - `ddl` / `entity_fqn` 시나리오 → 옛 단일 batch 흐름 (`_run_explicit_scenario`)
      - 명시 시그널 없으면 → 맨땅 흐름 (`_run_blank_slate`)
    """
    build_agent, _system_prompt, user_template = init_linking_env(
        state, api_key=api_key, run_dir=run_dir,
    )
    function_count = int(state.get("function_count") or 0)

    scenario = _determine_scenario(startpoints)
    if scenario in ("ddl", "entity_fqn"):
        return _run_explicit_scenario(
            scenario, startpoints, build_agent, user_template, function_count,
        )

    # 명시 시그널 없음 — 맨땅: 파일별 병렬 + 결정론 mpfmdbio + profile
    if not startpoints.codebase_pattern and not state.get("module_index"):
        logger.info("[ENTITY] startpoints / module_index 모두 비어있음 → 종료")
        return [], [], []
    return _run_blank_slate(
        startpoints, state, build_agent, user_template, function_count,
    )


# ═══════════════════════════════════════════════════════════════════════
# Section 3-A: 명시 시그널 분기 — 옛 단일 batch 흐름 (ddl / entity_fqn)
# ═══════════════════════════════════════════════════════════════════════


def _run_explicit_scenario(
    scenario: str,
    startpoints: EntityStartpoints,
    build_agent,
    user_template: str,
    function_count: int,
) -> Tuple[List[FunctionTableEdge], List[FkEdge], List[ColumnRef]]:
    """ddl / entity_fqn 시나리오 — batch 단위 ThreadPoolExecutor 병렬 invoke."""
    batches = _build_batches(startpoints, scenario)
    logger.info("[ENTITY] scenario=%s batches=%d", scenario, len(batches))

    collector = ToolResultCollector()
    failed_batches = 0
    max_workers = max(1, min(len(batches), settings.concurrency.max_concurrency))

    def _invoke_batch(batch_idx: int, batch: _Batch) -> bool:
        agent = build_agent(collector)
        user_msg = _render_explicit_user_message(batch, startpoints, user_template, function_count)
        logger.info(
            "[ENTITY] 배치 %d/%d 시작 | scenario=%s items=%d",
            batch_idx + 1, len(batches), scenario, len(batch.items),
        )
        try:
            agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
            log_llm_interaction(
                prompt_name=f"framework/entity/{scenario}",
                input_payload=user_msg,
                output_payload={"batch": batch_idx, "items": len(batch.items)},
                metadata={"scenario": scenario, "batch": batch_idx, "items": len(batch.items)},
            )
            return True
        except Exception as ex:  # noqa: SILENT_FAIL — 한 batch 실패가 다른 batch 를 막지 않도록
            logger.error("[ENTITY] 배치 %d invoke 실패: %s", batch_idx, ex)
            return False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_invoke_batch, i, b) for i, b in enumerate(batches)]
        for fut in as_completed(futures):
            if not fut.result():
                failed_batches += 1

    snapshot = collector.snapshot_and_clear()
    all_table = snapshot["table_access"]
    all_fk = snapshot["fk"]
    all_columns = snapshot.get("columns", [])

    logger.info(
        "[ENTITY] 완료 | scenario=%s tables=%d READS/WRITES=%d FK=%d columns=%d failed_batches=%d",
        scenario,
        len({e.table_name for e in all_table}),
        len(all_table), len(all_fk), len(all_columns),
        failed_batches,
    )
    return all_table, all_fk, all_columns


# 시나리오 dispatch — 명시 시그널만 (맨땅 분기는 별도)
_SCENARIOS: List[Tuple[str, Callable[[EntityStartpoints], bool],
                       Callable[[EntityStartpoints], list]]] = [
    (
        "ddl",
        lambda sp: bool(sp.ddl_tables),
        lambda sp: list(sp.ddl_tables),
    ),
    (
        "entity_fqn",
        lambda sp: bool(sp.xml_entity_fqns or sp.annotated_fqns),
        lambda sp: sorted(sp.xml_entity_fqns | sp.annotated_fqns),
    ),
]


def _determine_scenario(sp: EntityStartpoints) -> Optional[str]:
    """명시 시그널 우선순위 — ddl > entity_fqn. 둘 다 없으면 None (= 맨땅)."""
    return next((name for name, has, _src in _SCENARIOS if has(sp)), None)


class _Batch:
    __slots__ = ("scenario", "items")

    def __init__(self, scenario: str, items: list):
        self.scenario = scenario
        self.items = items


def _build_batches(sp: EntityStartpoints, scenario: str) -> List[_Batch]:
    """시나리오별 데이터 소스를 _BATCH_SIZE 단위로 분할."""
    _name, _has, source_fn = next(entry for entry in _SCENARIOS if entry[0] == scenario)
    source = source_fn(sp)
    if not source:
        return [_Batch(scenario, [])]
    return [
        _Batch(scenario, source[i:i + _BATCH_SIZE])
        for i in range(0, len(source), _BATCH_SIZE)
    ]


def _render_explicit_user_message(
    batch: _Batch, sp: EntityStartpoints, user_template: str, function_count: int = 0,
) -> str:
    """ddl / entity_fqn 시나리오 user message — 시나리오 힌트(category) + 데이터 + profile_hint."""
    data = {
        "scenario": batch.scenario,
        "items": batch.items,
        "context": {
            "existing_fks": _extract_existing_fks(list(sp.ddl_tables)),
            "xml_entity_hints": sorted(sp.xml_entity_fqns | sp.annotated_fqns)[:30],
        },
    }
    profile_hint = ""
    if sp.codebase_pattern:
        profile_hint = (
            "\n---\n"
            "[Project Framework · DB Access Pattern Analysis — Search Map]\n\n"
            f"{sp.codebase_pattern}\n"
        )

    return user_template.format(
        category=batch.scenario,
        data=json.dumps(data, ensure_ascii=False, default=str),
        profile_hint=profile_hint,
    )


# ═══════════════════════════════════════════════════════════════════════
# Section 3-B: 맨땅 분기 — 파일별 병렬 + 결정론 mpfmdbio + profile
# ═══════════════════════════════════════════════════════════════════════


def _run_blank_slate(
    startpoints: EntityStartpoints,
    state: Dict[str, Any],
    build_agent,
    user_template: str,
    function_count: int,
) -> Tuple[List[FunctionTableEdge], List[FkEdge], List[ColumnRef]]:
    """명시 시그널이 없을 때 — 분석 대상 파일별로 agent 1개씩 동시 invoke.

    각 파일 agent:
      - bundle_search 가 자기 파일 chunks 만 보도록 file_path_filter 주입
      - 결정론 mpfmdbio 페어를 user_msg 에 직접 주입 → agent 검색 부담 경감
      - codebase_pattern (profile) 이 검색 동력
    """
    module_index = state.get("module_index") or {}
    chunk_index = state.get("chunk_index") or {}
    lang = state.get("lang") or ""

    target_files = _determine_target_files(module_index, chunk_index, lang)
    if not target_files:
        logger.info("[ENTITY] 맨땅 — 분석 대상 파일 없음 (module 역할이 chunk_index 에 없음) → 종료")
        return [], [], []

    deterministic_by_file = extract_deterministic_dbio_calls(chunk_index)
    total_det = sum(len(v) for v in deterministic_by_file.values())
    logger.info(
        "[ENTITY] 맨땅 시작 | files=%d deterministic_dbio=%d",
        len(target_files), total_det,
    )

    all_table: List[FunctionTableEdge] = []
    all_fk: List[FkEdge] = []
    all_columns: List[ColumnRef] = []
    failed_files = 0

    max_workers = max(1, len(target_files))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_fqn = {
            ex.submit(
                _run_one_file,
                fqn, file_path,
                deterministic_by_file.get(file_path, []),
                startpoints, build_agent, user_template, function_count,
            ): fqn
            for fqn, file_path in target_files
        }
        for fut in as_completed(future_to_fqn):
            fqn = future_to_fqn[fut]
            try:
                result = fut.result()
            except Exception as ex_err:  # noqa: SILENT_FAIL — 한 파일 실패가 다른 파일을 막지 않도록
                logger.error("[ENTITY] 파일 invoke 실패 | file_fqn=%s | %s", fqn, ex_err)
                failed_files += 1
                continue
            all_table.extend(result["table_access"])
            all_fk.extend(result["fk"])
            all_columns.extend(result.get("columns", []))

    logger.info(
        "[ENTITY] 맨땅 완료 | files=%d tables=%d READS/WRITES=%d FK=%d columns=%d failed=%d",
        len(target_files),
        len({e.table_name for e in all_table}),
        len(all_table), len(all_fk), len(all_columns),
        failed_files,
    )
    return all_table, all_fk, all_columns


def _run_one_file(
    file_fqn: str,
    file_path: str,
    deterministic_calls: List[DeterministicDbioCall],
    startpoints: EntityStartpoints,
    build_agent,
    user_template: str,
    function_count: int,
) -> Dict[str, list]:
    """한 파일 invoke — 격리된 collector / agent / file_path_filter."""
    collector = ToolResultCollector()
    agent = build_agent(collector, file_path_filter=file_path)
    user_msg = _render_blank_slate_user_message(
        file_fqn, file_path, deterministic_calls, startpoints, user_template, function_count,
    )
    invoke_ok = False
    try:
        agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
        log_llm_interaction(
            prompt_name=f"framework/entity/blank_slate/{file_fqn}",
            input_payload=user_msg,
            output_payload={"total": collector.total_count},
            metadata={"file_fqn": file_fqn, "deterministic_calls": len(deterministic_calls)},
        )
        invoke_ok = True
    except Exception as ex:
        logger.error("[ENTITY] 파일 %s invoke 실패: %s", file_fqn, ex)

    result = collector.snapshot_and_clear()
    if not invoke_ok:
        dropped = len(result["table_access"]) + len(result["fk"]) + len(result.get("columns", []))
        if dropped:
            logger.warning(
                "[ENTITY] 실패 파일 부분결과 폐기 | file_fqn=%s dropped=%d",
                file_fqn, dropped,
            )
        return {"refer_to": [], "table_access": [], "fk": [], "columns": []}
    return result


def _render_blank_slate_user_message(
    file_fqn: str,
    file_path: str,
    deterministic_calls: List[DeterministicDbioCall],
    sp: EntityStartpoints,
    user_template: str,
    function_count: int,
) -> str:
    """맨땅 — 파일별 user message."""
    items: List[Any] = [
        {
            "function_id": d.function_id,
            "table_name": d.table_name,
            "access": d.access,
            "raw_literal": d.raw_literal,
            "line": d.line,
        }
        for d in deterministic_calls
    ] or [None]
    data = {
        "scenario": "profile_only",
        "file_fqn": file_fqn,
        "file_path": file_path,
        "items": items,
        "context": {
            "existing_fks": _extract_existing_fks(list(sp.ddl_tables)),
            "ddl_table_names": sorted({name for name, _ in sp.ddl_tables}),
            "xml_entity_hints": sorted(sp.xml_entity_fqns | sp.annotated_fqns),
            "function_count_total": function_count,
        },
    }
    profile_hint = ""
    if sp.codebase_pattern:
        profile_hint = (
            "\n---\n"
            "[Project Framework · DB Access Pattern Analysis — Search Map]\n\n"
            f"{sp.codebase_pattern}\n"
        )

    return user_template.format(
        category="profile_only",
        data=json.dumps(data, ensure_ascii=False, default=str),
        profile_hint=profile_hint,
    )


# ═══════════════════════════════════════════════════════════════════════
# Section 4: 공통 헬퍼
# ═══════════════════════════════════════════════════════════════════════


def _extract_existing_fks(ddl_tables: list) -> list:
    """DDL 메타에서 기존 FK 목록 추출."""
    return [
        {"from": f"{name}.{fk.get('column', '')}", "to": fk.get("ref", "")}
        for name, meta in ddl_tables
        for fk in (meta.get("foreign_keys") or meta.get("foreignKeys") or [])
        if fk.get("column") and fk.get("ref")
    ]


def collect_entity_fqns_from_edges(
    edges: list,
    module_index: Dict[str, Any],
) -> Dict[str, str]:
    """DeepAgent FunctionTableEdge 의 table_name 으로 module_index 의 동일명 클래스 매칭."""
    table_names_normalized = {
        e.table_name.upper().replace("_", ""): e.table_name
        for e in edges if e.table_name
    }
    entity_map: Dict[str, str] = {}
    for fqn, info in module_index.items():
        if not isinstance(info, dict):
            continue
        module_name = info.get("name", "")
        normalized = module_name.upper().replace("_", "") if module_name else ""
        if normalized in table_names_normalized:
            entity_map[fqn] = table_names_normalized[normalized]
    return entity_map
