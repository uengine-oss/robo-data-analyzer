"""Step 4: 인덱스 구축 (기존 step4_index 통합)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from analyzer.strategy.framework.common.models import (
    SqlStatementChunk,
    ConfigSectionChunk,
    ArtifactChunk,
)

logger = logging.getLogger(__name__)


def build_sql_index(sql_chunks: List[SqlStatementChunk]) -> Dict[str, SqlStatementChunk]:
    """sql_key -> SqlStatementChunk."""
    return {c.sql_key: c for c in sql_chunks}


def build_module_index(
    module_index_from_ast: Dict[str, Any],
) -> Dict[str, Any]:
    """module_index: fqn/simpleName -> ClassNode 정보. AST 기반만 사용."""
    return dict(module_index_from_ast)


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
            "[PHASE:1:INDEX] artifact_index 중복 제거 | 원본=%d → 고유=%d",
            len(artifact_chunks), len(result),
        )
    return result


def build_search_indexes(
    sql_chunks: List[SqlStatementChunk],
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
        "[PHASE:1:INDEX] 인덱스 빌드 진입 | sql_chunks=%d config_chunks=%d artifact_chunks=%d",
        len(sql_chunks),
        len(config_chunks),
        len(artifact_chunks or []),
    )
    logger.info("[PHASE:1:INDEX] [섹션] sql_index / module_index / config_index / artifact_index 빌드")
    sql_index = build_sql_index(sql_chunks)
    module_index = build_module_index(module_index_from_ast)
    config_index = build_config_index(config_chunks)
    artifact_index = build_artifact_index(artifact_chunks or [])
    logger.info(
        "[PHASE:1:INDEX] 인덱스 완료 | sql_index=%d module_index=%d config_index=%d artifact_index=%d",
        len(sql_index),
        len(module_index),
        len(config_index),
        len(artifact_index),
    )
    return sql_index, module_index, config_index, artifact_index
