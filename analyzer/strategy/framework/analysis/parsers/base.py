"""파서 오케스트레이터 — Step 3 (Sql/Config/Artifact 청크 통합 생성).

각 파일 형식별 파서 (xml_universal / yaml_config / properties) 호출만 담당.
XML 헬퍼 (_local_tag, _parse_xml_safe) 는 xml_universal 자체에 포함 (단일 도메인).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from analyzer.strategy.framework.common.models.node_models import (
    ArtifactChunk,
    ConfigSectionChunk,
    SqlStatementChunk,
)

from .properties import parse_properties
from .xml_universal import parse_xml_universal
from .yaml_config import parse_yaml_config

logger = logging.getLogger(__name__)


def parse_config_and_artifacts(
    sql_jobs: List[str],
    config_jobs: List[str],
    *,
    entity_jobs: Optional[List[str]] = None,
) -> Tuple[List[SqlStatementChunk], List[ConfigSectionChunk], List[ArtifactChunk]]:
    """Step 3 전체: SqlStatement + ConfigSection + Artifact 청크 생성.

    범용 — sql/entity/config 3종 분류. 프레임워크 구분 없음.
    모든 XML은 parse_xml_universal()로 통합 처리.
    """
    all_entity_jobs = list(entity_jobs or [])
    all_xml_jobs = list(sql_jobs) + all_entity_jobs

    logger.info(
        "[PHASE:1:ARTIFACT] 아티팩트 진입 | xml=%d config=%d (sql=%d entity=%d)",
        len(all_xml_jobs),
        len(config_jobs),
        len(sql_jobs),
        len(all_entity_jobs),
    )
    sql_chunks: List[SqlStatementChunk] = []
    config_chunks: List[ConfigSectionChunk] = []
    artifact_chunks: List[ArtifactChunk] = []

    # XML 파싱 — 범용 파서로 통합 (sql/entity 구분은 파서가 태그로 판단)
    if all_xml_jobs:
        logger.info("[PHASE:1:ARTIFACT] [섹션] XML 범용 파싱 | 파일 %d개", len(all_xml_jobs))
    for path in all_xml_jobs:
        xml_sql, xml_entities = parse_xml_universal(path)
        sql_chunks.extend(xml_sql)
        artifact_chunks.extend(xml_entities)

    # 설정 파일 파싱 (yaml, properties)
    if config_jobs:
        logger.info("[PHASE:1:ARTIFACT] [섹션] Config 파싱 | 파일 %d개", len(config_jobs))
    for p in config_jobs:
        ext = Path(p).suffix.lower()
        if ext in (".yaml", ".yml"):
            config_chunks.extend(parse_yaml_config(p))
        elif ext == ".properties":
            config_chunks.extend(parse_properties(p))

    logger.info(
        "[PHASE:1:ARTIFACT] 아티팩트 완료 | sql_chunks=%d config_chunks=%d artifact_chunks=%d",
        len(sql_chunks),
        len(config_chunks),
        len(artifact_chunks),
    )
    return sql_chunks, config_chunks, artifact_chunks
