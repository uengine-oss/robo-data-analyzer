"""범용 XML 파서 — 모든 식별자 속성값 + SQL 패턴 기반 추출.

하드코딩된 태그명(SQL_TAGS, ENTITY_TAGS) 없이:
1. 텍스트에 SQL 패턴이 있으면 SqlStatementChunk
2. id/name 등 식별자 속성이 있으면 ArtifactChunk
→ 링킹 단계에서 key_in_code로 소스코드 참조 여부 자동 판별
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Tuple

from lxml import etree  # type: ignore[import]

from analyzer.strategy.framework.common.models.node_models import SqlStatementChunk, ArtifactChunk
from util.encoding_reader import read_text_with_fallback_sync as read_text_with_fallback

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
    """공통 XML 로드 + 파서 호출. 파싱 실패는 fail-fast.

    lxml 은 requirements.txt 의 필수 의존성이므로 ImportError 폴백 없음.
    """
    text = read_text_with_fallback(path)
    try:
        root = etree.fromstring(text.encode("utf-8"))
    except Exception as e:
        logger.error(
            "[PHASE:1:ARTIFACT] XML 파싱 실패 | file=%s | %s: %s",
            Path(path).name, type(e).__name__, e, exc_info=True,
        )
        raise RuntimeError(
            f"XML 파싱 실패: {path} ({type(e).__name__}: {e})"
        ) from e
    return parser_fn(root, path)

# SQL 패턴 감지 (텍스트 내용이 SQL 구문인지)
_SQL_PATTERN = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|MERGE\s+INTO)\b",
    re.IGNORECASE,
)

# 식별자 속성 — 소스코드에서 참조될 수 있는 속성명
_IDENTIFIER_ATTRS = ("id", "name", "class", "ref", "bean", "entity-name", "ejb-name", "resource", "type", "resultType", "parameterType", "resultMap")

# 구조적 속성 — 소스코드에서 참조되지 않는 속성 (제외)
_STRUCTURAL_ATTRS = frozenset({"xmlns", "version", "encoding", "schemaLocation", "xsi", "dtd", "targetNamespace"})

# FQN 패턴: com.example.ModuleName 형태
_FQN_PATTERN = re.compile(r"\b([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\.[A-Z]\w*)\b")

# 식별자 패턴: 최소 2자 이상, 알파벳/숫자/점/언더스코어
_IDENTIFIER_VALUE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")


def _extract_fqns_from_text(text: str) -> List[str]:
    """텍스트에서 패키지 FQN 패턴을 추출."""
    if not text:
        return []
    return [m.group(1) for m in _FQN_PATTERN.finditer(text) if m.group(1).count(".") >= 2]


def _is_identifier_value(value: str) -> bool:
    """속성값이 식별자 패턴인지 확인. URL, 순수 숫자, 1자 값 등 제외."""
    if not value or len(value) < 2:
        return False
    if value.startswith("http") or value.startswith("/"):
        return False
    return bool(_IDENTIFIER_VALUE_PATTERN.match(value))


def _get_element_text(elem) -> str:
    """요소의 전체 텍스트 (자식 포함)."""
    return (elem.text or "") + "".join(etree.tostring(c, encoding="unicode") for c in elem)


def parse_xml_universal(path: str) -> Tuple[List[SqlStatementChunk], List[ArtifactChunk]]:
    """XML 파일에서 SQL 청크와 Artifact 청크를 범용 추출.

    1. 텍스트에 SQL 패턴 + id 속성 → SqlStatementChunk
    2. 식별자 속성값이 있는 요소 → ArtifactChunk
    """

    def _parse(root, file_path):
        sql_chunks: List[SqlStatementChunk] = []
        artifact_chunks: List[ArtifactChunk] = []
        seen_keys: set = set()  # 중복 방지

        # namespace 추출 (SQL 매핑 XML용)
        namespace = root.get("namespace", "").strip()
        if not namespace:
            for elem in root.iter():
                if not isinstance(elem.tag, str):
                    continue
                if "namespace" in elem.attrib:
                    namespace = elem.get("namespace", "").strip()
                    break

        for elem in root.iter():
            if not isinstance(elem.tag, str):
                continue

            start_line = getattr(elem, "sourceline", None) or 0
            elem_text = _get_element_text(elem)

            # ── SQL 패턴 감지 (텍스트 내용 기반) ──
            st_id = elem.get("id", "").strip()
            if st_id and elem_text.strip() and _SQL_PATTERN.search(elem_text):
                sql_key = f"{namespace}.{st_id}" if namespace else st_id
                if sql_key not in seen_keys:
                    seen_keys.add(sql_key)
                    tag = _local_tag(elem).lower()
                    end_line = start_line + max(0, elem_text.count("\n"))
                    sql_chunks.append(
                        SqlStatementChunk(
                            sql_key=sql_key,
                            sql_type=tag,
                            start_line=start_line,
                            end_line=end_line,
                            sql_text=elem_text.strip(),
                            file_path=file_path,
                        )
                    )
                    continue  # SQL로 처리된 요소는 Artifact로 중복 등록하지 않음

            # ── 식별자 속성값 기반 Artifact 추출 ──
            hint = ""
            for attr_name in _IDENTIFIER_ATTRS:
                val = elem.get(attr_name, "").strip()
                if val and _is_identifier_value(val):
                    hint = val
                    break

            # 하위 태그에서 name 관련 요소 찾기 (hint 없을 때)
            if not hint:
                for child in elem:
                    lt = _local_tag(child).lower()
                    if "name" in lt and child.text and _is_identifier_value(child.text.strip()):
                        hint = child.text.strip()
                        break

            if not hint:
                continue

            chunk_id = f"{file_path}:{hint}:{start_line}"
            if chunk_id in seen_keys:
                continue
            seen_keys.add(chunk_id)

            # entity 여부 판단 (태그명 기반 — 참고 정보로만 사용)
            tag_lower = _local_tag(elem).lower()
            chunk_type = "entity" if tag_lower in ("entity", "class", "entity-mapping", "hibernate-mapping") else "config"

            block_text = etree.tostring(elem, encoding="unicode")
            related_fqns = _extract_fqns_from_text(block_text)

            artifact_chunks.append(
                ArtifactChunk(
                    chunk_id=chunk_id,
                    chunk_type=chunk_type,
                    hint_key=hint,
                    text=block_text.strip()[:3000],
                    source_path=file_path,
                    start_line=start_line,
                    end_line=start_line + max(0, block_text.count("\n")),
                    related_module_fqns=related_fqns or None,
                )
            )

        logger.info(
            "[PHASE:1:ARTIFACT] XML 파싱 완료 | file=%s sql=%d artifact=%d",
            Path(file_path).name,
            len(sql_chunks),
            len(artifact_chunks),
        )
        return sql_chunks, artifact_chunks

    result = _parse_xml_safe(path, _parse)
    if not result:
        return [], []
    return result
