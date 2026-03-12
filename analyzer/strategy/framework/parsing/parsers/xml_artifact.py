"""EJB XML 파서."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from analyzer.strategy.framework.models import ArtifactChunk
from .base import _local_tag, _parse_xml_safe

logger = logging.getLogger(__name__)


def parse_ejb_jar(path: str) -> List[ArtifactChunk]:
    """ejb-jar.xml에서 session/entity bean 등 청크 추출."""

    def _parse(root, file_path):
        from lxml import etree  # type: ignore[import]

        chunks: List[ArtifactChunk] = []
        for elem in root.iter():
            tag = _local_tag(elem)
            if tag not in ("session", "entity", "message-driven"):
                continue
            ejb_name = elem.find(".//{*}ejb-name")
            hint = (ejb_name.text or "").strip() if ejb_name is not None else (elem.get("id") or "")
            chunk_id = f"{file_path}:{hint}:{id(elem)}" if hint else f"{file_path}:{id(elem)}"
            snippet = etree.tostring(elem, encoding="unicode")
            sl = getattr(elem, "sourceline", None) or 0
            chunk_type = "entity"
            # EJB XML에서 관련 클래스 FQN 추출 (ejb-class, home, remote, local, local-home)
            related_fqns: List[str] = []
            for fqn_tag in ("ejb-class", "home", "remote", "local", "local-home"):
                el = elem.find(f".//{{*}}{fqn_tag}")
                if el is not None and el.text and el.text.strip():
                    related_fqns.append(el.text.strip())
            chunks.append(
                ArtifactChunk(
                    chunk_id=chunk_id,
                    chunk_type=chunk_type,
                    hint_key=hint or None,
                    text=snippet.strip(),
                    source_path=file_path,
                    start_line=sl,
                    end_line=sl + max(0, snippet.count("\n")),
                    related_class_fqns=related_fqns or None,
                )
            )
        logger.info(
            "[FW:STEP3] ejb_jar 완료 | file=%s chunks=%d",
            Path(file_path).name,
            len(chunks),
        )
        return chunks

    return _parse_xml_safe(path, _parse)


def extract_ejb2_beans_from_ejb_jar(path: str) -> List[Dict[str, Any]]:
    """ejb-jar.xml에서 EJB2 Bean 메타데이터를 추출해 BEAN2 DTO 목록으로 반환."""
    kind_map = {"session": "SESSION", "entity": "ENTITY", "message-driven": "MESSAGE_DRIVEN"}

    def _parse(root, file_path):
        beans: List[Dict[str, Any]] = []
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
                    "source_path": file_path,
                }
            )
        logger.info(
            "[FW:STEP3] ejb_jar Bean 메타 추출 완료 | file=%s beans=%d",
            Path(file_path).name,
            len(beans),
        )
        return beans

    return _parse_xml_safe(path, _parse)
