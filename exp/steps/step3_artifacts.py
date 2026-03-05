"""Step 3: 비자바 아티팩트 파싱. MyBatis XML(lxml), YAML(ruamel), properties(직접)."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from exp.models import ArtifactChunk, ConfigSectionChunk, SqlStatementChunk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 3-1 MyBatis XML (lxml)
# ---------------------------------------------------------------------------

def _read_file_text(path: str, encoding: str = "utf-8") -> str:
    for enc in (encoding, "utf-8", "utf-8-sig", "cp949"):
        try:
            return Path(path).read_text(encoding=enc)
        except Exception:
            continue
    return Path(path).read_bytes().decode("utf-8", errors="replace")


def parse_mybatis_xml(path: str) -> List[SqlStatementChunk]:
    """lxml으로 mapper namespace + select|insert|update|delete id 파싱."""
    try:
        from lxml import etree
    except ImportError:
        logger.warning("lxml 미설치: MyBatis XML 스킵")
        logger.info("[EXP:STEP3] 스킵 | parser=mybatis file=%s reason=lxml_not_available", Path(path).name)
        return []

    text = _read_file_text(path)
    try:
        root = etree.fromstring(text.encode("utf-8"))
    except Exception as e:
        logger.debug("XML 파싱 실패 %s: %s", path, e)
        logger.info("[EXP:STEP3] 스킵 | parser=mybatis file=%s reason=xml_parse_error error=%s", Path(path).name, e)
        return []

    # namespace: {http://www.mybatis.org/schema/mapper} 등
    ns = root.nsmap.get(None) or ""
    ns_map = {"m": ns} if ns else None
    namespace = root.get("namespace", "").strip()
    if not namespace and ns_map:
        ns_tag = "{%s}mapper" % ns if ns else "mapper"
        if root.tag == ns_tag or root.tag == "mapper":
            namespace = root.get("namespace", "").strip()

    if not namespace:
        for elem in root.iter():
            if "namespace" in elem.attrib:
                namespace = elem.get("namespace", "").strip()
                break

    def local_tag(e):
        return e.tag.split("}")[-1] if "}" in str(e.tag) else e.tag

    chunks: List[SqlStatementChunk] = []
    for tag in ("select", "insert", "update", "delete"):
        for elem in root.iter():
            if local_tag(elem) != tag:
                continue
            st_id = elem.get("id", "").strip()
            if not st_id:
                continue
            sql_key = f"{namespace}.{st_id}" if namespace else st_id
            start_line = getattr(elem, "sourceline", None) or 0
            sql_text = (elem.text or "") + "".join(
                etree.tostring(c, encoding="unicode") for c in elem
            )
            end_line = start_line + max(0, sql_text.count("\n"))
            chunks.append(
                SqlStatementChunk(
                    sql_key=sql_key,
                    sql_type=tag.lower(),
                    start_line=start_line,
                    end_line=end_line,
                    sql_text=sql_text.strip(),
                    file_path=path,
                )
            )
    logger.info("[EXP:STEP3] mybatis 완료 | file=%s chunks=%d namespace=%s", Path(path).name, len(chunks), namespace or "")
    return chunks


def parse_all_mybatis_xml(xml_sqlmap_jobs: List[str]) -> List[SqlStatementChunk]:
    """여러 XML 파일에서 SqlStatementChunk 수집."""
    out: List[SqlStatementChunk] = []
    for path in xml_sqlmap_jobs:
        out.extend(parse_mybatis_xml(path))
    return out


# ---------------------------------------------------------------------------
# 3-3 YAML (ruamel.yaml)
# ---------------------------------------------------------------------------

def parse_yaml_config(path: str) -> List[ConfigSectionChunk]:
    """ruamel.yaml로 key-path 추출 (라인 유지)."""
    try:
        from ruamel.yaml import YAML
    except ImportError:
        try:
            import yaml
        except ImportError:
            logger.warning("ruamel.yaml / PyYAML 미설치: YAML 스킵")
            logger.info("[EXP:STEP3] 스킵 | parser=yaml file=%s reason=no_yaml_lib", Path(path).name)
            return []
        # PyYAML fallback: 라인 정보 없음
        text = _read_file_text(path)
        data = yaml.safe_load(text)
        chunks = []
        def flatten(d: dict, prefix: str = "") -> None:
            for k, v in d.items():
                key_path = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict) and v:
                    flatten(v, key_path)
                else:
                    chunks.append(
                        ConfigSectionChunk(
                            config_key_path=key_path,
                            start_line=0,
                            end_line=0,
                            raw_snippet=str(v)[:200],
                            file_path=path,
                        )
                    )
        if isinstance(data, dict):
            flatten(data)
        return chunks

    chunks: List[ConfigSectionChunk] = []
    yaml_loader = YAML()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml_loader.load(f)
    except Exception as e:
        logger.info("[EXP:STEP3] 스킵 | parser=yaml file=%s reason=load_error error=%s", Path(path).name, e)
        return []

    def walk(node, prefix: str = "", lc_getter=None):
        if hasattr(node, "lc") and hasattr(node.lc, "line"):
            line = node.lc.line
        else:
            line = 0
        if isinstance(node, dict):
            for k, v in node.items():
                key_path = f"{prefix}.{k}" if prefix else str(k)
                walk(v, key_path, getattr(v, "lc", None))
                if not isinstance(v, (dict, list)) or (isinstance(v, (dict, list)) and not v):
                    chunks.append(
                        ConfigSectionChunk(
                            config_key_path=key_path,
                            start_line=line,
                            end_line=line,
                            raw_snippet=str(v)[:200] if v is not None else "",
                            file_path=path,
                        )
                    )
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{prefix}[{i}]", getattr(v, "lc", None))

    if isinstance(data, dict):
        walk(data)
    return chunks


def parse_properties(path: str) -> List[ConfigSectionChunk]:
    """properties 직접 파싱 (라인 유지)."""
    text = _read_file_text(path)
    chunks: List[ConfigSectionChunk] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            chunks.append(
                ConfigSectionChunk(
                    config_key_path=key,
                    start_line=line_no,
                    end_line=line_no,
                    raw_snippet=value[:200],
                    file_path=path,
                )
            )
    return chunks



def _local_tag(elem):
    return elem.tag.split("}")[-1] if "}" in str(elem.tag) else elem.tag

def parse_ejb_jar(path: str) -> List[ArtifactChunk]:
    """ejb-jar.xml에서 session/entity bean 등 청크 추출 (선택)."""
    try:
        from lxml import etree
    except ImportError:
        logger.info("[EXP:STEP3] 스킵 | parser=ejb_jar file=%s reason=lxml_not_available", Path(path).name)
        return []
    text = _read_file_text(path)
    try:
        root = etree.fromstring(text.encode("utf-8"))
    except Exception as e:
        logger.info("[EXP:STEP3] 스킵 | parser=ejb_jar file=%s reason=xml_parse_error error=%s", Path(path).name, e)
        return []
    chunks: List[ArtifactChunk] = []
    for elem in root.iter():
        tag = _local_tag(elem)
        if tag not in ("session", "entity", "message-driven"):
            continue
        ejb_name = elem.find(".//{*}ejb-name")
        hint = (ejb_name.text or "").strip() if ejb_name is not None else (elem.get("id") or "")
        chunk_id = f"{path}:{hint}:{id(elem)}" if hint else f"{path}:{id(elem)}"
        snippet = etree.tostring(elem, encoding="unicode")
        sl = getattr(elem, "sourceline", None) or 0
        chunk_type = "ejb_entity" if tag == "entity" else "ejb_bean"
        chunks.append(ArtifactChunk(chunk_id=chunk_id, chunk_type=chunk_type, hint_key=hint or None, text=snippet.strip(), source_path=path, start_line=sl, end_line=sl + max(0, snippet.count("\n"))))
    logger.info("[EXP:STEP3] ejb_jar 완료 | file=%s chunks=%d", Path(path).name, len(chunks))
    return chunks


def extract_ejb2_beans_from_ejb_jar(path: str) -> List[Dict[str, Any]]:
    """ejb-jar.xml에서 EJB2 Bean 메타데이터(session/entity/message-driven)를 추출해 BEAN2 DTO 목록으로 반환.

    DTO 스키마:
      - bean_name: ejb-name 또는 ejb-class의 짧은 이름
      - bean_kind: 'SESSION' | 'ENTITY' | 'MESSAGE_DRIVEN'
      - style: 'EJB2_XML'
      - impl_class_fqn: ejb-class (FQN)
      - persistence_type: ENTITY일 때 CMP/BMP 등
      - source_path: ejb-jar.xml 경로
    """
    beans: List[Dict[str, Any]] = []
    try:
        from lxml import etree
    except ImportError:
        logger.info("[EXP:STEP3] Bean 추출 스킵 | parser=ejb_jar file=%s reason=lxml_not_available", Path(path).name)
        return beans
    text = _read_file_text(path)
    try:
        root = etree.fromstring(text.encode("utf-8"))
    except Exception as e:
        logger.info("[EXP:STEP3] Bean 추출 스킵 | parser=ejb_jar file=%s reason=xml_parse_error error=%s", Path(path).name, e)
        return beans
    kind_map = {
        "session": "SESSION",
        "entity": "ENTITY",
        "message-driven": "MESSAGE_DRIVEN",
    }
    for elem in root.iter():
        tag = _local_tag(elem)
        bean_kind = kind_map.get(tag)
        if not bean_kind:
            continue
        ejb_name_el = elem.find(".//{*}ejb-name")
        ejb_class_el = elem.find(".//{*}ejb-class")
        bean_name = (ejb_name_el.text or "").strip() if ejb_name_el is not None and ejb_name_el.text else ""
        impl_class = (ejb_class_el.text or "").strip() if ejb_class_el is not None and ejb_class_el.text else ""
        if not bean_name and impl_class:
            bean_name = impl_class.rsplit(".", 1)[-1]
        persistence_type = ""
        if bean_kind == "ENTITY":
            pt_el = elem.find(".//{*}persistence-type")
            if pt_el is not None and pt_el.text:
                persistence_type = pt_el.text.strip()
        if not bean_name and not impl_class:
            continue
        beans.append(
            {
                "bean_name": bean_name,
                "bean_kind": bean_kind,
                "style": "EJB2_XML",
                "impl_class_fqn": impl_class,
                "persistence_type": persistence_type,
                "source_path": path,
            }
        )
    logger.info("[EXP:STEP3] ejb_jar Bean 메타 추출 완료 | file=%s beans=%d", Path(path).name, len(beans))
    return beans

def parse_entitymodel_xml(path: str) -> List[ArtifactChunk]:
    """entitymodel.xml (OFBiz) entity 정의 청크 추출 (선택)."""
    try:
        from lxml import etree
    except ImportError:
        logger.info("[EXP:STEP3] 스킵 | parser=entitymodel file=%s reason=lxml_not_available", Path(path).name)
        return []
    text = _read_file_text(path)
    try:
        root = etree.fromstring(text.encode("utf-8"))
    except Exception as e:
        logger.info("[EXP:STEP3] 스킵 | parser=entitymodel file=%s reason=xml_parse_error error=%s", Path(path).name, e)
        return []
    chunks: List[ArtifactChunk] = []
    for elem in root.iter():
        if _local_tag(elem) != "entity":
            continue
        name = elem.get("entity-name") or elem.get("name") or ""
        chunk_id = f"{path}:{name}:{id(elem)}" if name else f"{path}:{id(elem)}"
        snippet = (elem.text or "") + "".join(etree.tostring(c, encoding="unicode") for c in elem)
        sl = getattr(elem, "sourceline", None) or 0
        chunks.append(ArtifactChunk(chunk_id=chunk_id, chunk_type="entitymodel_entity", hint_key=name or None, text=snippet.strip()[:2000], source_path=path, start_line=sl, end_line=sl + max(0, snippet.count("\n"))))
    logger.info("[EXP:STEP3] entitymodel 완료 | file=%s chunks=%d", Path(path).name, len(chunks))
    return chunks

def _flush_neo4j_buffer(writer: Any, query_buffer: List[Any], batch_size: int) -> None:
    """query_buffer를 batch_size씩 Neo4j에 보내고 버퍼 비움."""
    if not query_buffer or batch_size <= 0:
        return
    try:
        asyncio.run(writer.run_queries_batch_sync(query_buffer, batch_size=batch_size))
    except Exception as e:
        logger.warning("[EXP:STEP3] Neo4j 배치 flush 실패: %s", e)
    query_buffer.clear()


def run_step3(
    xml_sqlmap_jobs: List[str],
    yaml_jobs: List[str],
    properties_jobs: List[str],
    xml_ejb_jobs: Optional[List[str]] = None,
    xml_entitymodel_jobs: Optional[List[str]] = None,
    xml_cmp_mapping_jobs: Optional[List[str]] = None,
    neo4j_writer: Optional[Any] = None,
    neo4j_batch_size: int = 30,
) -> tuple[List[SqlStatementChunk], List[ConfigSectionChunk], List[ArtifactChunk]]:
    """Step 3 전체: SqlStatement + ConfigSection 청크 생성. neo4j_writer 있으면 파일/청크 단위로 배치 flush."""
    logger.info(
        "[EXP:STEP3] 아티팩트 진입 | xml_sqlmap=%d yaml=%d properties=%d xml_ejb=%d xml_entitymodel=%d xml_cmp_mapping=%d",
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
    bean2_records: List[Dict[str, Any]] = []
    query_buffer: List[Any] = []
    batch_size = neo4j_batch_size if neo4j_writer else 0

    def flush_if_needed() -> None:
        if neo4j_writer and batch_size and len(query_buffer) >= batch_size:
            _flush_neo4j_buffer(neo4j_writer, query_buffer, batch_size)

    logger.info("[EXP:STEP3] [섹션] MyBatis XML 파싱 → SqlStatementChunk | 파일 %d개", len(xml_sqlmap_jobs))
    for path in xml_sqlmap_jobs:
        chunks = parse_mybatis_xml(path)
        sql_chunks.extend(chunks)
        if neo4j_writer and batch_size and chunks:
            try:
                from exp.neo4j import build_sql_statement_queries
                query_buffer.extend(build_sql_statement_queries(chunks))
                flush_if_needed()
            except Exception as e:
                logger.debug("STEP3 SqlStatement 쿼리 빌드 스킵: %s", e)
    if yaml_jobs:
        logger.info("[EXP:STEP3] [섹션] YAML 설정 파싱 → ConfigSectionChunk | 파일 %d개", len(yaml_jobs))
    for p in yaml_jobs:
        chunks = parse_yaml_config(p)
        config_chunks.extend(chunks)
        if neo4j_writer and batch_size and chunks:
            try:
                from exp.neo4j import build_config_section_queries
                query_buffer.extend(build_config_section_queries(chunks))
                flush_if_needed()
            except Exception as e:
                logger.debug("STEP3 ConfigSection 쿼리 빌드 스킵: %s", e)
    if properties_jobs:
        logger.info("[EXP:STEP3] [섹션] properties 파싱 → ConfigSectionChunk | 파일 %d개", len(properties_jobs))
    for p in properties_jobs:
        chunks = parse_properties(p)
        config_chunks.extend(chunks)
        if neo4j_writer and batch_size and chunks:
            try:
                from exp.neo4j import build_config_section_queries
                query_buffer.extend(build_config_section_queries(chunks))
                flush_if_needed()
            except Exception as e:
                logger.debug("STEP3 ConfigSection 쿼리 빌드 스킵: %s", e)
    if xml_ejb_jobs:
        logger.info("[EXP:STEP3] [섹션] EJB XML 파싱 → ArtifactChunk | 파일 %d개", len(xml_ejb_jobs))
    for p in xml_ejb_jobs or []:
        chunks = parse_ejb_jar(p)
        artifact_chunks.extend(chunks)
        # EJB2 Bean 메타데이터 추출 (BEAN2 노드용)
        bean2_records.extend(extract_ejb2_beans_from_ejb_jar(p))
        if neo4j_writer and batch_size and chunks:
            try:
                from exp.neo4j import build_artifact_chunk_queries
                query_buffer.extend(build_artifact_chunk_queries(chunks))
                flush_if_needed()
            except Exception as e:
                logger.debug("STEP3 ArtifactChunk 쿼리 빌드 스킵: %s", e)
    if xml_entitymodel_jobs:
        logger.info("[EXP:STEP3] [섹션] entitymodel XML 파싱 → ArtifactChunk | 파일 %d개", len(xml_entitymodel_jobs))
    for p in xml_entitymodel_jobs or []:
        chunks = parse_entitymodel_xml(p)
        artifact_chunks.extend(chunks)
        if neo4j_writer and batch_size and chunks:
            try:
                from exp.neo4j import build_artifact_chunk_queries
                query_buffer.extend(build_artifact_chunk_queries(chunks))
                flush_if_needed()
            except Exception as e:
                logger.debug("STEP3 ArtifactChunk 쿼리 빌드 스킵: %s", e)

    # CMP 매핑 XML은 구조화 파싱 없이 전체를 하나의 ArtifactChunk(cmp_mapping)로 저장
    if xml_cmp_mapping_jobs:
        logger.info("[EXP:STEP3] [섹션] CMP 매핑 XML 파싱 → ArtifactChunk(cmp_mapping) | 파일 %d개", len(xml_cmp_mapping_jobs))
    for p in xml_cmp_mapping_jobs or []:
        try:
            text = _read_file_text(p)
        except Exception as e:
            logger.info("[EXP:STEP3] 스킵 | parser=cmp_mapping file=%s reason=read_error error=%s", Path(p).name, e)
            continue
        snippet = text.strip()
        snippet = snippet[:2000]
        sl = 1
        el = max(1, snippet.count("\n") + 1)
        chunk_id = f"{p}:cmp_mapping"
        chunk = ArtifactChunk(
            chunk_id=chunk_id,
            chunk_type="cmp_mapping",
            hint_key=None,
            text=snippet,
            source_path=p,
            start_line=sl,
            end_line=el,
        )
        artifact_chunks.append(chunk)
        if neo4j_writer and batch_size:
            try:
                from exp.neo4j import build_artifact_chunk_queries
                query_buffer.extend(build_artifact_chunk_queries([chunk]))
                flush_if_needed()
            except Exception as e:
                logger.debug("STEP3 CMP ArtifactChunk 쿼리 빌드 스킵: %s", e)

    # EJB2 Bean 메타를 기반으로 BEAN2 노드 + IMPLEMENTED_BY 관계 생성
    if neo4j_writer and batch_size and bean2_records:
        try:
            from exp.neo4j import build_bean2_queries
            query_buffer.extend(build_bean2_queries(bean2_records))
            flush_if_needed()
        except Exception as e:
            logger.debug("STEP3 Bean2 쿼리 빌드 스킵: %s", e)

    if neo4j_writer and query_buffer:
        _flush_neo4j_buffer(neo4j_writer, query_buffer, batch_size)
        logger.info("[EXP:STEP3] Neo4j 잔여 배치 flush 완료")

    logger.info(
        "[EXP:STEP3] 아티팩트 완료 | sql_chunks=%d config_chunks=%d artifact_chunks=%d",
        len(sql_chunks),
        len(config_chunks),
        len(artifact_chunks),
    )
    return sql_chunks, config_chunks, artifact_chunks
