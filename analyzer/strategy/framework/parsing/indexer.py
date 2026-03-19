"""Step 4: 인덱스 구축 (기존 step4_index 통합)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from analyzer.strategy.framework.models import (
    FunctionFacts,
    SqlStatementChunk,
    ConfigSectionChunk,
    ArtifactChunk,
)

logger = logging.getLogger(__name__)


def build_sql_index(sql_chunks: List[SqlStatementChunk]) -> Dict[str, SqlStatementChunk]:
    """sql_key -> SqlStatementChunk."""
    return {c.sql_key: c for c in sql_chunks}


def build_module_index(
    function_facts_list: List[FunctionFacts],
    module_index_from_ast: Dict[str, Any],
) -> Dict[str, Any]:
    """module_index: fqn/simpleName -> ClassNode 정보. AST에서 만든 것과 병합."""
    index = dict(module_index_from_ast)
    for m in function_facts_list:
        if not m.fqn or not m.module_key:
            continue
        if m.module_key not in index:
            index[m.module_key] = {
                "module_key": m.module_key,
                "fqn": m.fqn.rsplit(".", 1)[0] if "." in m.fqn else m.fqn,
                "package_name": m.package_name,
                "name": m.module_key.split(".")[-1] if m.module_key else "",
                "kind": "CLASS",
            }
    return index


def build_config_index(
    config_chunks: List[ConfigSectionChunk],
) -> Dict[str, ConfigSectionChunk]:
    """config_key_path -> ConfigSectionChunk."""
    return {c.config_key_path: c for c in config_chunks}


def build_artifact_index(artifact_chunks: List[ArtifactChunk]) -> Dict[str, ArtifactChunk]:
    """chunk_id -> ArtifactChunk. hint_key 기준 중복 제거 (같은 XML의 빌드 복사본 등)."""
    seen_hints: Dict[str, str] = {}  # hint_key -> first chunk_id
    result: Dict[str, ArtifactChunk] = {}
    for c in artifact_chunks:
        hint = (getattr(c, "hint_key", None) or "").strip()
        if hint and hint in seen_hints:
            continue  # 같은 hint_key의 청크가 이미 등록됨
        if hint:
            seen_hints[hint] = c.chunk_id
        result[c.chunk_id] = c
    if len(result) < len(artifact_chunks):
        logger.info(
            "[FW:STEP4] artifact_index 중복 제거 | 원본=%d → 고유=%d",
            len(artifact_chunks), len(result),
        )
    return result


def run_step4(
    sql_chunks: List[SqlStatementChunk],
    function_facts_list: List[FunctionFacts],
    module_index_from_ast: Dict[str, Any],
    config_chunks: List[ConfigSectionChunk],
    artifact_chunks: Optional[List[ArtifactChunk]] = None,
) -> Tuple[
    Dict[str, SqlStatementChunk],
    Dict[str, Any],
    Dict[str, ConfigSectionChunk],
    Dict[str, ArtifactChunk],
]:
    """인덱스 4종 생성 (sql, class, config, artifact)."""
    logger.info(
        "[FW:STEP4] 인덱스 빌드 진입 | sql_chunks=%d function_facts=%d config_chunks=%d artifact_chunks=%d",
        len(sql_chunks),
        len(function_facts_list),
        len(config_chunks),
        len(artifact_chunks or []),
    )
    logger.info("[FW:STEP4] [섹션] sql_index / module_index / config_index / artifact_index 빌드")
    sql_index = build_sql_index(sql_chunks)
    module_index = build_module_index(function_facts_list, module_index_from_ast)
    config_index = build_config_index(config_chunks)
    artifact_index = build_artifact_index(artifact_chunks or [])
    logger.info(
        "[FW:STEP4] 인덱스 완료 | sql_index=%d module_index=%d config_index=%d artifact_index=%d",
        len(sql_index),
        len(module_index),
        len(config_index),
        len(artifact_index),
    )
    return sql_index, module_index, config_index, artifact_index
