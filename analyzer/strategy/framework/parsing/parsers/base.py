"""비타겟 파일 파서 공통 + run_step3 오케스트레이터."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from analyzer.strategy.framework.models import SqlStatementChunk, ConfigSectionChunk, ArtifactChunk
from util.text_utils import read_text_with_fallback

logger = logging.getLogger(__name__)


def _local_tag(elem) -> str:
    """XML 요소의 네임스페이스를 제거한 로컬 태그명.

    Comment·ProcessingInstruction 등 tag가 문자열이 아닌 노드는 빈 문자열을 반환한다.
    """
    tag = elem.tag
    if not isinstance(tag, str):
        return ""
    return tag.split("}")[-1] if "}" in tag else tag


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
    sql_jobs: List[str],
    config_jobs: List[str],
    _unused: Optional[List[str]] = None,
    *,
    xml_artifact_jobs: Optional[List[str]] = None,
    entity_jobs: Optional[List[str]] = None,
    xml_cmp_mapping_jobs: Optional[List[str]] = None,
) -> Tuple[List[SqlStatementChunk], List[ConfigSectionChunk], List[ArtifactChunk]]:
    """Step 3 전체: SqlStatement + ConfigSection + Artifact 청크 생성.

    범용 — sql/entity/config 3종 분류. 프레임워크 구분 없음.
    모든 XML은 parse_xml_universal()로 통합 처리.
    """
    from .yaml_config import parse_yaml_config
    from .properties import parse_properties
    from .xml_universal import parse_xml_universal

    # entity_jobs와 xml_artifact_jobs 통합 (backward compat)
    all_entity_jobs = list(entity_jobs or []) + list(xml_artifact_jobs or [])
    # 모든 XML을 한 곳에서 처리
    all_xml_jobs = list(sql_jobs) + list(all_entity_jobs)

    logger.info(
        "[FW:STEP3] 아티팩트 진입 | xml=%d config=%d (sql=%d entity=%d)",
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
        logger.info("[FW:STEP3] [섹션] XML 범용 파싱 | 파일 %d개", len(all_xml_jobs))
    for path in all_xml_jobs:
        xml_sql, xml_entities = parse_xml_universal(path)
        sql_chunks.extend(xml_sql)
        artifact_chunks.extend(xml_entities)

    # 설정 파일 파싱 (yaml, properties)
    if config_jobs:
        logger.info("[FW:STEP3] [섹션] Config 파싱 | 파일 %d개", len(config_jobs))
    for p in config_jobs:
        ext = Path(p).suffix.lower()
        if ext in (".yaml", ".yml"):
            config_chunks.extend(parse_yaml_config(p))
        elif ext == ".properties":
            config_chunks.extend(parse_properties(p))

    # CMP 매핑 (하위호환)
    if xml_cmp_mapping_jobs:
        logger.info("[FW:STEP3] [섹션] CMP 매핑 XML | 파일 %d개", len(xml_cmp_mapping_jobs))
    for p in xml_cmp_mapping_jobs or []:
        try:
            text = read_text_with_fallback(p)
        except Exception as e:
            logger.info("[FW:STEP3] 스킵 | parser=cmp_mapping file=%s error=%s", Path(p).name, e)
            continue
        snippet = text.strip()[:2000]
        sl = 1
        el = max(1, snippet.count("\n") + 1)
        artifact_chunks.append(ArtifactChunk(
            chunk_id=f"{p}:cmp_mapping",
            chunk_type="cmp_mapping",
            hint_key=None,
            text=snippet,
            source_path=p,
            start_line=sl,
            end_line=el,
        ))

    logger.info(
        "[FW:STEP3] 아티팩트 완료 | sql_chunks=%d config_chunks=%d artifact_chunks=%d",
        len(sql_chunks),
        len(config_chunks),
        len(artifact_chunks),
    )
    return sql_chunks, config_chunks, artifact_chunks
