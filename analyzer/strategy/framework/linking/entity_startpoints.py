"""엔티티 시작점 수집 — 엔티티 식별을 위한 결정론적 준비 단계.

어노테이션/상속 패턴 판별, XML 엔티티 추출, CLASS/STRUCT 후보 수집 등
엔티티 식별 파이프라인에 필요한 모든 시작점을 모은다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


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
    """XML 엔티티 청크(EJB `<entity>`, hibernate-mapping 등)의 related_module_fqns 에서 FQN 추출.

    artifact 의 related_module_fqns 중 module_index 에 실제 존재하는 FQN 만 반환.
    """
    if not artifact_index or not module_index:
        return set()
    entity_fqns: set = set()
    for ac in artifact_index.values():
        if getattr(ac, "chunk_type", "") not in ("entity", "cmp_mapping"):
            continue
        related = getattr(ac, "related_module_fqns", None) or []
        for fqn in related:
            if fqn in module_index:
                entity_fqns.add(fqn)
    return entity_fqns


# ---------------------------------------------------------------------------
# Startpoint 수집 (엔티티 식별용)
# ---------------------------------------------------------------------------

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
    profile_summary: str


def collect_entity_startpoints(
    module_index: Dict[str, Any],
    artifact_index: Optional[Dict[str, Any]],
    ddl_table_metadata: Optional[Dict],
    profile_summary: str,
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
        profile_summary=profile_summary,
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
    from pathlib import Path as _Path
    parts_lower = {p.lower() for p in _Path(file_path).parts} if file_path else set()
    if parts_lower & _ENTITY_DIR_HINTS:
        return "entity"
    if parts_lower & _DTO_DIR_HINTS:
        return "dto"
    return "unknown"
