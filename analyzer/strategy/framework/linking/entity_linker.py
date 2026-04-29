"""엔티티 식별 도메인 — Startpoint 수집 + linking 에이전트 드라이버.

본 모듈은 entity 도메인 전체를 담는다 (단일 처리 흐름):
  1. 엔티티 시작점 수집 (Startpoint) — 어노테이션/상속/XML/CLASS 후보 수집
  2. linking 에이전트 호출 — EntityStartpoints → Function→Table 엣지·FK·컬럼 변환
  3. 4가지 시나리오 (DDL / 엔티티 FQN / 후보 / profile-only) 를 배타적 우선순위로 선택

시스템 프롬프트는 모든 시나리오 공통. scenario 는 user message 의 `category:` 힌트로
전달되고, LLM 이 `list_skills()` / `read_skill(name)` 을 호출해 해당 카테고리에 맞는
스킬 본문을 로드해 처리한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from config.settings import settings
from util.llm_audit_logger import log_llm_interaction
from analyzer.strategy.framework.common.models.node_models import ColumnRef, FkEdge, FunctionTableEdge
from analyzer.strategy.framework.linking.agent.linking_agent import init_linking_env
from analyzer.strategy.framework.linking.agent.linking_tools import ToolResultCollector

logger = logging.getLogger(__name__)

_BATCH_SIZE = settings.deep_agent.entity_batch_size


# ═══════════════════════════════════════════════════════════════════════
# Section 1: 엔티티 시작점 수집 (어노테이션/XML/CLASS 후보)
# ═══════════════════════════════════════════════════════════════════════


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

# 엔티티 후보로 취급할 AST 노드 타입
_ENTITY_CANDIDATE_KINDS = frozenset({"CLASS", "STRUCT", "UNION"})

# 경로 기반 사전 분류 힌트
_ENTITY_DIR_HINTS = frozenset({"entity", "entities", "model", "models", "domain", "schema"})
_DTO_DIR_HINTS = frozenset({"dto", "vo", "request", "response", "view"})


@dataclass
class EntityStartpoints:
    """엔티티 식별을 위한 모든 시작점."""
    ddl_tables: List[Tuple[str, Dict]]
    xml_entity_fqns: set
    annotated_fqns: set
    class_candidates: List[Dict[str, str]]
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
    candidates = _extract_entity_candidates(module_index or {})

    return EntityStartpoints(
        ddl_tables=ddl_tables,
        xml_entity_fqns=xml_fqns,
        annotated_fqns=annotated,
        class_candidates=candidates,
        codebase_pattern=codebase_pattern,
    )


def _extract_entity_candidates(module_index: Dict[str, Any]) -> List[Dict[str, str]]:
    """module_index에서 엔티티 후보(CLASS/STRUCT/UNION)를 추출한다.

    DTO 디렉토리 제외, entity 디렉토리 우선.
    """
    candidates: List[Dict[str, str]] = []
    for fqn, info in module_index.items():
        if not isinstance(info, dict):
            continue
        if info.get("kind") not in _ENTITY_CANDIDATE_KINDS:
            continue
        fp = info.get("file_path", "")
        path_class = _classify_by_path(fp)
        if path_class == "dto":
            continue
        candidates.append({
            "fqn": fqn,
            "name": info.get("name", ""),
            "file_path": fp,
            "annotations": (info.get("annotations") or "")[:100],
            "comment": (info.get("comment") or "")[:100],
            "path_hint": path_class,
        })
    candidates.sort(key=lambda c: (0 if c.get("path_hint") == "entity" else 1))
    return candidates


def _classify_by_path(file_path: str) -> str:
    """파일 경로의 디렉토리명으로 entity/dto/unknown 사전 분류."""
    parts_lower = {p.lower() for p in Path(file_path).parts} if file_path else set()
    if parts_lower & _ENTITY_DIR_HINTS:
        return "entity"
    if parts_lower & _DTO_DIR_HINTS:
        return "dto"
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════
# Section 2: 엔티티 식별 드라이버 — linking 에이전트 호출
# ═══════════════════════════════════════════════════════════════════════


def run_entity_identification(
    startpoints: EntityStartpoints,
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[List[FunctionTableEdge], List[FkEdge], List[ColumnRef]]:
    """Startpoint 기반 엔티티 식별 + Function→Table 연결."""
    scenario = _determine_scenario(startpoints)
    if scenario is None:
        logger.info("[ENTITY] startpoints 전부 비어있음 → 종료")
        return [], [], []

    build_agent, _system_prompt, user_template = init_linking_env(
        state, api_key=api_key, run_dir=run_dir,
    )

    batches = _build_batches(startpoints, scenario)
    logger.info("[ENTITY] scenario=%s batches=%d", scenario, len(batches))

    collector = ToolResultCollector()
    all_table: List[FunctionTableEdge] = []
    all_fk: List[FkEdge] = []
    all_columns: List[ColumnRef] = []
    failed_batches = 0

    function_count = int(state.get("function_count") or 0)
    for batch_idx, batch in enumerate(batches):
        agent = build_agent(collector, batch_startpoint_tables=batch.table_names)
        user_msg = _render_user_message(batch, startpoints, user_template, function_count)

        logger.info("[ENTITY] 배치 %d/%d 시작 | scenario=%s items=%d",
                    batch_idx + 1, len(batches), scenario, len(batch.items))
        invoke_ok = False
        try:
            agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
            log_llm_interaction(
                prompt_name=f"framework/entity/{scenario}",
                input_payload=user_msg,
                output_payload={"total": collector.total_count},
                metadata={"scenario": scenario, "batch": batch_idx, "items": len(batch.items)},
            )
            invoke_ok = True
        except Exception as ex:  # noqa: SILENT_FAIL — 한 배치 invoke 실패가 다른 배치를 막지 않도록 errlog 후 다음 배치 진행
            logger.error("[ENTITY] 배치 %d invoke 실패: %s", batch_idx, ex)

        result = collector.snapshot_and_clear()
        if not invoke_ok:
            failed_batches += 1
            dropped = len(result["table_access"]) + len(result["fk"]) + len(result.get("columns", []))
            if dropped:
                logger.warning(
                    "[ENTITY] 실패 배치 부분결과 폐기 | batch=%d dropped=%d",
                    batch_idx, dropped,
                )
            continue
        all_table.extend(result["table_access"])
        all_fk.extend(result["fk"])
        all_columns.extend(result.get("columns", []))

    logger.info(
        "[ENTITY] 완료 | tables=%d READS/WRITES=%d FK=%d columns=%d failed_batches=%d",
        len({e.table_name for e in all_table}), len(all_table), len(all_fk), len(all_columns),
        failed_batches,
    )
    return all_table, all_fk, all_columns


# ═══════════════════════════════════════════════════════════════════════
# 시나리오 정의 (디스패치 테이블 — 우선순위 · 데이터 소스 · 테이블 추출)
# ═══════════════════════════════════════════════════════════════════════


# 각 시나리오의 데이터 소스와 테이블명 추출 로직을 한 곳에 모음.
# 우선순위: 리스트 순서대로. has(sp) 가 True 인 첫 항목이 선택됨.
_SCENARIOS: List[Tuple[str, Callable[[EntityStartpoints], bool],
                       Callable[[EntityStartpoints], list],
                       Callable[[list], Set[str]]]] = [
    (
        "ddl",
        lambda sp: bool(sp.ddl_tables),
        lambda sp: list(sp.ddl_tables),
        lambda chunk: {name for name, _ in chunk},
    ),
    (
        "entity_fqn",
        lambda sp: bool(sp.xml_entity_fqns or sp.annotated_fqns),
        lambda sp: sorted(sp.xml_entity_fqns | sp.annotated_fqns),
        lambda _chunk: set(),
    ),
    (
        "candidates",
        lambda sp: bool(sp.class_candidates),
        lambda sp: list(sp.class_candidates),
        lambda _chunk: set(),
    ),
    (
        "profile_only",
        lambda sp: bool(sp.codebase_pattern),
        lambda _sp: [None],   # 빈 아이템 하나 — codebase_pattern 만으로 수색
        lambda _chunk: set(),
    ),
]


def _determine_scenario(sp: EntityStartpoints) -> Optional[str]:
    """우선순위 리스트에서 has(sp) 가 True 인 첫 시나리오 선택."""
    return next((name for name, has, _src, _tbl in _SCENARIOS if has(sp)), None)


# ═══════════════════════════════════════════════════════════════════════
# 배치 생성
# ═══════════════════════════════════════════════════════════════════════


class _Batch:
    __slots__ = ("scenario", "items", "table_names")

    def __init__(self, scenario: str, items: list, table_names: Set[str]):
        self.scenario = scenario
        self.items = items
        self.table_names = table_names


def _build_batches(sp: EntityStartpoints, scenario: str) -> List[_Batch]:
    """디스패치 테이블에서 데이터 소스·테이블 추출 함수를 찾아 일괄 배치."""
    _name, _has, source_fn, table_fn = next(
        (entry for entry in _SCENARIOS if entry[0] == scenario)
    )
    source = source_fn(sp)
    if not source:
        return [_Batch(scenario, [], set())]
    return [
        _Batch(scenario, source[i:i + _BATCH_SIZE], table_fn(source[i:i + _BATCH_SIZE]))
        for i in range(0, len(source), _BATCH_SIZE)
    ]


# ═══════════════════════════════════════════════════════════════════════
# User Message 렌더링 — 시나리오 분기 없이 통일 포맷
# ═══════════════════════════════════════════════════════════════════════


def _extract_existing_fks(ddl_tables: list) -> list:
    """DDL 메타에서 기존 FK 목록 추출 (ddl 시나리오 외에서는 빈 리스트)."""
    return [
        {"from": f"{name}.{fk.get('column', '')}", "to": fk.get("ref", "")}
        for name, meta in ddl_tables
        for fk in (meta.get("foreign_keys") or meta.get("foreignKeys") or [])
        if fk.get("column") and fk.get("ref")
    ][:50]


def _render_user_message(batch: _Batch, sp: EntityStartpoints, user_template: str, function_count: int = 0) -> str:
    """시나리오 힌트(category) + 원본 데이터(data) + 프로파일 지침(profile_hint) 을 채운다.

    - `data`: JSON — 아이템 + 시나리오별 보조 맥락(existing_fks, xml_entity_hints).
    - `profile_hint`: 평문 — codebase_pattern 가 있으면 prose 섹션으로 렌더.
      LLM 이 JSON 파싱 없이 바로 읽을 수 있도록 평문으로 분리 (profile_only 시나리오 특히 중요).
    """
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
# Section 3: 엔티티 매칭 헬퍼 — DeepAgent 결과 → module_index 클래스 매칭
# ═══════════════════════════════════════════════════════════════════════


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
