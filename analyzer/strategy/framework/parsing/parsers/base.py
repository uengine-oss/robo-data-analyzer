"""비타겟 파일 파서 공통 + run_step3 오케스트레이터."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from analyzer.strategy.framework.models import SqlStatementChunk, ConfigSectionChunk, ArtifactChunk
from util.text_utils import read_text_with_fallback

logger = logging.getLogger(__name__)


def _local_tag(elem) -> str:
    """XML 요소의 네임스페이스를 제거한 로컬 태그명."""
    return elem.tag.split("}")[-1] if "}" in str(elem.tag) else elem.tag


def _parse_xml_safe(path: str, parser_fn):
    """공통 XML 로드 + lxml import + 에러 핸들링."""
    try:
        from lxml import etree  # type: ignore[import]
    except ImportError:
        logger.info("[FW:STEP3] 스킵 | file=%s reason=lxml_not_available", Path(path).name)
        return []
    text = read_text_with_fallback(path)
    try:
        root = etree.fromstring(text.encode("utf-8"))
    except Exception as e:
        logger.info("[FW:STEP3] 스킵 | file=%s reason=xml_parse_error error=%s", Path(path).name, e)
        return []
    return parser_fn(root, path)


def run_step3(
    xml_sqlmap_jobs: List[str],
    yaml_jobs: List[str],
    properties_jobs: List[str],
    xml_ejb_jobs: Optional[List[str]] = None,
    xml_entitymodel_jobs: Optional[List[str]] = None,
    xml_cmp_mapping_jobs: Optional[List[str]] = None,
) -> Tuple[List[SqlStatementChunk], List[ConfigSectionChunk], List[ArtifactChunk]]:
    """Step 3 전체: SqlStatement + ConfigSection + Artifact 청크 생성."""
    from .mybatis import parse_mybatis_xml
    from .yaml_config import parse_yaml_config
    from .properties import parse_properties
    from .ejb import parse_ejb_jar
    from .entitymodel import parse_entitymodel_xml

    logger.info(
        "[FW:STEP3] 아티팩트 진입 | xml_sqlmap=%d yaml=%d properties=%d xml_ejb=%d xml_entitymodel=%d xml_cmp_mapping=%d",
        len(xml_sqlmap_jobs),
        len(yaml_jobs),
        len(properties_jobs),
        len(xml_ejb_jobs or []),
        len(xml_entitymodel_jobs or []),
        len(xml_cmp_mapping_jobs or []),
    )
    sql_chunks: List[SqlStatementChunk] = []
    config_chunks: List[ConfigSectionChunk] = []
    artifact_chunks: List[ArtifactChunk] = []

    logger.info(
        "[FW:STEP3] [섹션] MyBatis XML 파싱 → SqlStatementChunk | 파일 %d개",
        len(xml_sqlmap_jobs),
    )
    for path in xml_sqlmap_jobs:
        chunks_sql = parse_mybatis_xml(path)
        sql_chunks.extend(chunks_sql)

    if yaml_jobs:
        logger.info(
            "[FW:STEP3] [섹션] YAML 설정 파싱 → ConfigSectionChunk | 파일 %d개",
            len(yaml_jobs),
        )
    for p in yaml_jobs:
        chunks_yaml = parse_yaml_config(p)
        config_chunks.extend(chunks_yaml)

    if properties_jobs:
        logger.info(
            "[FW:STEP3] [섹션] properties 파싱 → ConfigSectionChunk | 파일 %d개",
            len(properties_jobs),
        )
    for p in properties_jobs:
        chunks_props = parse_properties(p)
        config_chunks.extend(chunks_props)

    if xml_ejb_jobs:
        logger.info(
            "[FW:STEP3] [섹션] EJB XML 파싱 → ArtifactChunk | 파일 %d개",
            len(xml_ejb_jobs),
        )
    for p in xml_ejb_jobs or []:
        chunks_ejb = parse_ejb_jar(p)
        artifact_chunks.extend(chunks_ejb)

    if xml_entitymodel_jobs:
        logger.info(
            "[FW:STEP3] [섹션] entitymodel XML 파싱 → ArtifactChunk | 파일 %d개",
            len(xml_entitymodel_jobs),
        )
    for p in xml_entitymodel_jobs or []:
        chunks_entity = parse_entitymodel_xml(p)
        artifact_chunks.extend(chunks_entity)

    if xml_cmp_mapping_jobs:
        logger.info(
            "[FW:STEP3] [섹션] CMP 매핑 XML 파싱 → ArtifactChunk(cmp_mapping) | 파일 %d개",
            len(xml_cmp_mapping_jobs),
        )
    for p in xml_cmp_mapping_jobs or []:
        try:
            text = read_text_with_fallback(p)
        except Exception as e:
            logger.info(
                "[FW:STEP3] 스킵 | parser=cmp_mapping file=%s reason=read_error error=%s",
                Path(p).name,
                e,
            )
            continue
        snippet = text.strip()[:2000]
        sl = 1
        el = max(1, snippet.count("\n") + 1)
        chunk = ArtifactChunk(
            chunk_id=f"{p}:cmp_mapping",
            chunk_type="cmp_mapping",
            hint_key=None,
            text=snippet,
            source_path=p,
            start_line=sl,
            end_line=el,
        )
        artifact_chunks.append(chunk)

    logger.info(
        "[FW:STEP3] 아티팩트 완료 | sql_chunks=%d config_chunks=%d artifact_chunks=%d",
        len(sql_chunks),
        len(config_chunks),
        len(artifact_chunks),
    )
    return sql_chunks, config_chunks, artifact_chunks
