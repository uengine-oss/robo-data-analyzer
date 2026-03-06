"""entitymodel.xml (OFBiz) 파서."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from analyzer.strategy.framework.models import ArtifactChunk
from .base import _local_tag, _parse_xml_safe

logger = logging.getLogger(__name__)


def parse_entitymodel_xml(path: str) -> List[ArtifactChunk]:
    """entitymodel.xml (OFBiz) entity 정의 청크 추출."""

    def _parse(root, file_path):
        from lxml import etree  # type: ignore[import]

        chunks: List[ArtifactChunk] = []
        for elem in root.iter():
            if _local_tag(elem) != "entity":
                continue
            name = elem.get("entity-name") or elem.get("name") or ""
            chunk_id = f"{file_path}:{name}:{id(elem)}" if name else f"{file_path}:{id(elem)}"
            snippet = (elem.text or "") + "".join(
                etree.tostring(c, encoding="unicode") for c in elem
            )
            sl = getattr(elem, "sourceline", None) or 0
            chunks.append(
                ArtifactChunk(
                    chunk_id=chunk_id,
                    chunk_type="entitymodel_entity",
                    hint_key=name or None,
                    text=snippet.strip()[:2000],
                    source_path=file_path,
                    start_line=sl,
                    end_line=sl + max(0, snippet.count("\n")),
                )
            )
        logger.info(
            "[FW:STEP3] entitymodel 완료 | file=%s chunks=%d",
            Path(file_path).name,
            len(chunks),
        )
        return chunks

    return _parse_xml_safe(path, _parse)
