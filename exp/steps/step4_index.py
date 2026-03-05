"""Step 4: sql_index, class_index, config_index 생성."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from exp.models import ArtifactChunk, ConfigSectionChunk, MethodFacts, SqlStatementChunk

logger = logging.getLogger(__name__)


def build_sql_index(sql_chunks: List[SqlStatementChunk]) -> Dict[str, SqlStatementChunk]:
    """sql_key -> SqlStatementChunk."""
    return {c.sql_key: c for c in sql_chunks}


def build_class_index(
    method_facts_list: List[MethodFacts],
    class_index_from_ast: Dict[str, Any],
) -> Dict[str, Any]:
    """class_index: fqn/simpleName -> ClassNode 정보. AST에서 만든 것과 병합."""
    index = dict(class_index_from_ast)
    for m in method_facts_list:
        if m.class_key and m.class_key not in index:
            index[m.class_key] = {
                "class_key": m.class_key,
                "fqn": m.fqn.rsplit(".", 1)[0] if "." in m.fqn else m.fqn,
                "package_name": m.package_name,
                "name": m.class_key.split(".")[-1] if m.class_key else "",
                "kind": "CLASS",
            }
    return index


def build_config_index(
    config_chunks: List[ConfigSectionChunk],
) -> Dict[str, ConfigSectionChunk]:
    """config_key_path -> ConfigSectionChunk (또는 id)."""
    return {c.config_key_path: c for c in config_chunks}


def build_artifact_index(artifact_chunks: List[ArtifactChunk]) -> Dict[str, ArtifactChunk]:
    """chunk_id -> ArtifactChunk."""
    return {c.chunk_id: c for c in artifact_chunks}


def run_step4(
    sql_chunks: List[SqlStatementChunk],
    method_facts_list: List[MethodFacts],
    class_index_from_ast: Dict[str, Any],
    config_chunks: List[ConfigSectionChunk],
    artifact_chunks: List[ArtifactChunk] | None = None,
) -> tuple[
    Dict[str, SqlStatementChunk],
    Dict[str, Any],
    Dict[str, ConfigSectionChunk],
    Dict[str, ArtifactChunk],
]:
    """인덱스 4종 생성 (sql, class, config, artifact)."""
    logger.info(
        "[EXP:STEP4] 인덱스 빌드 진입 | sql_chunks=%d method_facts=%d config_chunks=%d artifact_chunks=%d",
        len(sql_chunks),
        len(method_facts_list),
        len(config_chunks),
        len(artifact_chunks or []),
    )
    logger.info("[EXP:STEP4] [섹션] sql_index / class_index / config_index / artifact_index 빌드")
    sql_index = build_sql_index(sql_chunks)
    class_index = build_class_index(method_facts_list, class_index_from_ast)
    config_index = build_config_index(config_chunks)
    artifact_index = build_artifact_index(artifact_chunks or [])
    logger.info(
        "[EXP:STEP4] 인덱스 완료 | sql_index=%d class_index=%d config_index=%d artifact_index=%d",
        len(sql_index),
        len(class_index),
        len(config_index),
        len(artifact_index),
    )
    return sql_index, class_index, config_index, artifact_index
