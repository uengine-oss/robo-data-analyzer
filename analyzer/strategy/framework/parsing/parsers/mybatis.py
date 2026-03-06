"""MyBatis XML 파서."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from analyzer.strategy.framework.models import SqlStatementChunk
from .base import _local_tag, _parse_xml_safe

logger = logging.getLogger(__name__)


def parse_mybatis_xml(path: str) -> List[SqlStatementChunk]:
    """lxml으로 mapper namespace + select|insert|update|delete id 파싱."""

    def _parse(root, file_path):
        from lxml import etree  # type: ignore[import]

        ns = root.nsmap.get(None) or ""
        namespace = root.get("namespace", "").strip()
        if not namespace and ns:
            ns_tag = "{%s}mapper" % ns
            if root.tag == ns_tag or root.tag == "mapper":
                namespace = root.get("namespace", "").strip()
        if not namespace:
            for elem in root.iter():
                if "namespace" in elem.attrib:
                    namespace = elem.get("namespace", "").strip()
                    break
        chunks: List[SqlStatementChunk] = []
        for tag in ("select", "insert", "update", "delete"):
            for elem in root.iter():
                if _local_tag(elem) != tag:
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
                        file_path=file_path,
                    )
                )
        logger.info(
            "[FW:STEP3] mybatis 완료 | file=%s chunks=%d namespace=%s",
            Path(file_path).name,
            len(chunks),
            namespace or "",
        )
        return chunks

    return _parse_xml_safe(path, _parse)


def parse_all_mybatis_xml(xml_sqlmap_jobs: List[str]) -> List[SqlStatementChunk]:
    """여러 XML 파일에서 SqlStatementChunk 수집."""
    out: List[SqlStatementChunk] = []
    for path in xml_sqlmap_jobs:
        out.extend(parse_mybatis_xml(path))
    return out
