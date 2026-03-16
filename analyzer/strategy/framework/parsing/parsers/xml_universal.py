"""범용 XML 파서 — SQL 태그와 Entity 태그를 프레임워크 무관하게 추출."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from analyzer.strategy.framework.models import SqlStatementChunk, ArtifactChunk
from .base import _local_tag, _parse_xml_safe

logger = logging.getLogger(__name__)

# 용도 기반 태그 분류 — 프레임워크 이름 없이 태그명만
SQL_TAGS = frozenset({"select", "insert", "update", "delete"})
ENTITY_TAGS = frozenset({"entity", "class", "entity-mapping", "hibernate-mapping"})

# FQN 패턴: com.example.ClassName 형태 (entity 블록에서 관련 클래스 추출용)
_FQN_PATTERN = re.compile(r"\b([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\.[A-Z]\w*)\b")


def _extract_fqns_from_text(text: str) -> List[str]:
    """텍스트에서 Java/패키지 FQN 패턴을 추출. 예: com.banking.loan.entity.LoanBean"""
    if not text:
        return []
    fqns = []
    for m in _FQN_PATTERN.finditer(text):
        fqn = m.group(1)
        # 최소 2단계 패키지 (com.xxx.Yyy)
        if fqn.count(".") >= 2:
            fqns.append(fqn)
    return fqns


def parse_xml_universal(path: str) -> Tuple[List[SqlStatementChunk], List[ArtifactChunk]]:
    """XML 파일에서 SQL 청크와 Entity 청크를 범용 추출.

    - SQL 태그 (select, insert, update, delete) → SqlStatementChunk
    - Entity 태그 (entity, class 등) → ArtifactChunk (블록 텍스트 통째로)
    - 나머지 → 무시

    Returns:
        (sql_chunks, entity_chunks)
    """

    def _parse(root, file_path):
        from lxml import etree  # type: ignore[import]

        sql_chunks: List[SqlStatementChunk] = []
        entity_chunks: List[ArtifactChunk] = []

        # namespace 추출 (SQL 매핑 XML용)
        namespace = root.get("namespace", "").strip()
        if not namespace:
            for elem in root.iter():
                if "namespace" in elem.attrib:
                    namespace = elem.get("namespace", "").strip()
                    break

        for elem in root.iter():
            tag = _local_tag(elem).lower()

            # --- SQL 태그 ---
            if tag in SQL_TAGS:
                st_id = elem.get("id", "").strip()
                if not st_id:
                    continue
                sql_key = f"{namespace}.{st_id}" if namespace else st_id
                start_line = getattr(elem, "sourceline", None) or 0
                sql_text = (elem.text or "") + "".join(
                    etree.tostring(c, encoding="unicode") for c in elem
                )
                end_line = start_line + max(0, sql_text.count("\n"))
                sql_chunks.append(
                    SqlStatementChunk(
                        sql_key=sql_key,
                        sql_type=tag,
                        start_line=start_line,
                        end_line=end_line,
                        sql_text=sql_text.strip(),
                        file_path=file_path,
                    )
                )

            # --- Entity 태그 ---
            elif tag in ENTITY_TAGS:
                # 블록 텍스트 통째로 저장 (속성명 하드코딩 없음)
                block_text = etree.tostring(elem, encoding="unicode")
                start_line = getattr(elem, "sourceline", None) or 0

                # hint_key: 아무 name/id 속성이든 첫 번째로 발견된 것
                hint = ""
                for attr in ("name", "entity-name", "ejb-name", "id"):
                    val = elem.get(attr, "").strip()
                    if val:
                        hint = val
                        break
                if not hint:
                    # 하위 태그에서 name 관련 요소 찾기
                    for name_tag in elem:
                        lt = _local_tag(name_tag).lower()
                        if "name" in lt and name_tag.text:
                            hint = name_tag.text.strip()
                            break

                chunk_id = f"{file_path}:{hint}:{start_line}" if hint else f"{file_path}:entity:{start_line}"

                # entity 블록 텍스트(query 포함)는 통째로 ArtifactChunk에 저장.
                # TABLE/COLUMN은 sql_table_extract LLM이 처리.
                # query↔메서드 매칭은 hint_key + 원문을 deepagent가 처리.

                # FQN 추출 — 블록 텍스트에서 패키지.클래스명 패턴 추출
                related_fqns = _extract_fqns_from_text(block_text)

                entity_chunks.append(
                    ArtifactChunk(
                        chunk_id=chunk_id,
                        chunk_type="entity",
                        hint_key=hint or None,
                        text=block_text.strip()[:3000],
                        source_path=file_path,
                        start_line=start_line,
                        end_line=start_line + max(0, block_text.count("\n")),
                        related_class_fqns=related_fqns or None,
                    )
                )

        logger.info(
            "[FW:STEP3] XML 파싱 완료 | file=%s sql=%d entity=%d",
            Path(file_path).name,
            len(sql_chunks),
            len(entity_chunks),
        )
        return sql_chunks, entity_chunks

    result = _parse_xml_safe(path, _parse)
    if not result:
        return [], []
    return result
