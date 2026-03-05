"""Step 5: 관계 연결. Method→SQL (인덱스), SQL→Table (LLM 기반 테이블/컬럼/FK 추출)."""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from exp.models import (
    REF_CANDIDATE_CALL_REF,
    REF_CANDIDATE_CONFIG_KEY_REF,
    REF_CANDIDATE_DATA_PATH_REF,
    REF_CANDIDATE_NEW_INSTANCE_REF,
    REF_CANDIDATE_SQL_KEY_LITERAL,
    REF_CANDIDATE_SQL_LITERAL,
    ReferenceCandidate,
    ReferToEdge,
    UnresolvedLink,
    ColumnRef,
    FkEdge,
    MethodFacts,
    MethodTableEdge,
    ReadsWritesEdge,
    SqlStatementChunk,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 엔티티 후보 식별 (프레임워크 무관)
# ---------------------------------------------------------------------------

ENTITY_SIGNALS_ANNOTATION = ["@Entity", "@Table", "@MappedSuperclass", "@Document", "@RedisHash"]
ENTITY_SIGNALS_IMPLEMENTS = ["EntityBean"]


def identify_entity_classes(class_index: Dict[str, Any]) -> set:
    """class_index에서 엔티티 후보 FQN 집합을 반환. 어노테이션/인터페이스 키워드 기반."""
    entity_fqns: set = set()
    for fqn, info in class_index.items():
        if not isinstance(info, dict):
            continue
        ann = str(info.get("annotations") or "")
        impl = str(info.get("implements_types") or "")
        if any(s in ann for s in ENTITY_SIGNALS_ANNOTATION):
            entity_fqns.add(fqn)
        if any(s in impl for s in ENTITY_SIGNALS_IMPLEMENTS):
            entity_fqns.add(fqn)
    return entity_fqns


# LLM에 넣을 SQL 최대 길이(한 번에). 초과 시 적당히 분할.
SQL_LLM_CHUNK_MAX = 7000
# 이 길이 초과 시에만 분할 (적당히, 잘게 쪼개지 않음).
SQL_SPLIT_THRESHOLD = 8000
# 한 문장당 최대 분할 개수 (너무 잘게 쪼개지 않음). 조각당 최대 SQL_LLM_CHUNK_MAX로 잘라서 LLM에 전부 전달.
SQL_MAX_CHUNKS = 20

# 따옴표로 감싼 문자열 추출 (단순 매칭용). "..." 또는 '...'
QUOTED_STRING_PATTERN = re.compile(r'["\']([^"\']+)["\']')
SQL_LITERAL_START = re.compile(r'["\']\s*(SELECT|INSERT|UPDATE|DELETE)\s', re.IGNORECASE)
CONFIG_KEY_PATTERN = re.compile(r'\.(?:getProperty|get)\s*\(\s*["\']([^"\']+)["\']', re.IGNORECASE)
DATA_PATH_PATTERN = re.compile(r'\.(?:getString|get)\s*\(\s*["\']@([^"\']+)["\']', re.IGNORECASE)


def extract_quoted_strings(code: str) -> List[str]:
    """코드에서 따옴표로 감싼 모든 문자열 추출 (중복 제거, 순서 유지)."""
    seen: set = set()
    out: List[str] = []
    for m in QUOTED_STRING_PATTERN.finditer(code):
        s = m.group(1).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def extract_sql_keys_from_code(code: str, sql_index_keys: Optional[Dict[str, Any]] = None) -> List[str]:
    """메서드 코드에서 SQL 키 추출. sql_index_keys 있으면 단순 문자열 매칭(따옴표 문자열 중 인덱스에 있는 것만)."""
    if sql_index_keys is not None:
        keys_set = set(sql_index_keys) if not hasattr(sql_index_keys, "keys") else set(sql_index_keys.keys())
        return [s for s in extract_quoted_strings(code) if s in keys_set]
    return [s for s in extract_quoted_strings(code)]


def extract_reference_candidates_from_code(
    code: str,
    method_id: str,
    code_ref: str,
    start_line: int,
    end_line: int,
    sql_index_keys: Optional[Dict[str, Any]] = None,
) -> List[ReferenceCandidate]:
    """코드 문자열에서 참조 후보 추출. sql_index_keys 있으면 따옴표 문자열 중 인덱스에 있는 것을 SQL_KEY_LITERAL로 추가."""
    candidates: List[ReferenceCandidate] = []
    if sql_index_keys is not None:
        keys_set = set(sql_index_keys.keys()) if hasattr(sql_index_keys, "keys") else set(sql_index_keys)
        for s in extract_quoted_strings(code):
            if s in keys_set:
                candidates.append(ReferenceCandidate(kind=REF_CANDIDATE_SQL_KEY_LITERAL, value=s, line_range=(start_line, end_line)))
    else:
        for s in extract_quoted_strings(code):
            if "." in s:  # namespace.id 형태일 가능성
                candidates.append(ReferenceCandidate(kind=REF_CANDIDATE_SQL_KEY_LITERAL, value=s, line_range=(start_line, end_line)))
    if SQL_LITERAL_START.search(code):
        candidates.append(ReferenceCandidate(kind=REF_CANDIDATE_SQL_LITERAL, value=code[:500], line_range=(start_line, end_line)))
    for m in CONFIG_KEY_PATTERN.finditer(code):
        candidates.append(ReferenceCandidate(kind=REF_CANDIDATE_CONFIG_KEY_REF, value=m.group(1), line_range=(start_line, end_line)))
    for m in DATA_PATH_PATTERN.finditer(code):
        candidates.append(ReferenceCandidate(kind=REF_CANDIDATE_DATA_PATH_REF, path="@" + m.group(1), line_range=(start_line, end_line)))
    return candidates


def resolve_candidates(
    method_id: str,
    caller_fqn: str,
    code_ref: Optional[str],
    candidates: List[Any],
    sql_index: Dict[str, SqlStatementChunk],
    config_index: Any,
    class_index: Dict[str, Any],
    symbol_type_index: Dict[str, Dict[str, str]],
) -> Tuple[List[ReferToEdge], List[UnresolvedLink]]:
    """참조 후보 결정론 해석 → ReferToEdge 또는 UnresolvedLink."""
    edges: List[ReferToEdge] = []
    unresolved: List[UnresolvedLink] = []
    for c in candidates:
        kind = c.get("kind", "") if isinstance(c, dict) else getattr(c, "kind", "")
        value = c.get("value") if isinstance(c, dict) else getattr(c, "value", None)
        target_type = None
        target_id = None
        if kind == REF_CANDIDATE_SQL_KEY_LITERAL and value:
            if value in sql_index:
                target_type, target_id = "SqlStatement", value
                logger.debug("[EXP:STEP5:resolve] candidate | kind=SQL_KEY_LITERAL value=%s method_id=%s result=ReferToEdge target_type=SqlStatement", value, method_id)
            else:
                unresolved.append(UnresolvedLink(method_id=method_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range") if isinstance(c, dict) else getattr(c, "line_range", None)))
                logger.debug("[EXP:STEP5:resolve] candidate | kind=SQL_KEY_LITERAL value=%s method_id=%s result=unresolved reason=sql_key_not_in_index", value, method_id)
                continue
        elif kind == REF_CANDIDATE_SQL_LITERAL:
            unresolved.append(UnresolvedLink(method_id=method_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range") if isinstance(c, dict) else getattr(c, "line_range", None)))
            logger.debug("[EXP:STEP5:resolve] candidate | kind=SQL_LITERAL method_id=%s result=unresolved reason=sql_literal", method_id)
            continue
        elif kind == REF_CANDIDATE_CONFIG_KEY_REF and value and config_index and getattr(config_index, "get", None):
            if value in config_index:
                target_type, target_id = "ConfigSection", value
                logger.debug("[EXP:STEP5:resolve] candidate | kind=CONFIG_KEY_REF value=%s method_id=%s result=ReferToEdge target_type=ConfigSection", value, method_id)
            else:
                unresolved.append(UnresolvedLink(method_id=method_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range") if isinstance(c, dict) else getattr(c, "line_range", None)))
                logger.debug("[EXP:STEP5:resolve] candidate | kind=CONFIG_KEY_REF value=%s method_id=%s result=unresolved reason=config_key_not_in_index", value, method_id)
                continue
        elif kind == REF_CANDIDATE_CALL_REF:
            receiver = c.get("receiver") if isinstance(c, dict) else getattr(c, "receiver", None)
            receiver_type = (symbol_type_index.get(method_id) or {}).get(receiver) if receiver else None
            if receiver_type:
                info = class_index.get(receiver_type)
                if isinstance(info, dict):
                    target_type, target_id = "Class", info.get("fqn") or receiver_type
                elif info:
                    target_type, target_id = "Class", str(info)
            if not target_type:
                unresolved.append(UnresolvedLink(method_id=method_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range") if isinstance(c, dict) else getattr(c, "line_range", None)))
                logger.debug("[EXP:STEP5:resolve] candidate | kind=CALL_REF receiver=%s method_id=%s result=unresolved reason=receiver_type_unknown", receiver, method_id)
                continue
            else:
                logger.debug("[EXP:STEP5:resolve] candidate | kind=CALL_REF receiver=%s method_id=%s result=ReferToEdge reason=receiver_type_resolved", receiver, method_id)
        elif kind == REF_CANDIDATE_NEW_INSTANCE_REF:
            className = c.get("className") if isinstance(c, dict) else getattr(c, "className", None)
            if className:
                info = class_index.get(className)
                if isinstance(info, dict):
                    target_type, target_id = "Class", info.get("fqn", className)
                elif className in class_index:
                    target_type, target_id = "Class", className
            if not target_type:
                unresolved.append(UnresolvedLink(method_id=method_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range") if isinstance(c, dict) else getattr(c, "line_range", None)))
                logger.debug("[EXP:STEP5:resolve] candidate | kind=NEW_INSTANCE_REF className=%s method_id=%s result=unresolved reason=class_not_found", className, method_id)
                continue
            else:
                logger.debug("[EXP:STEP5:resolve] candidate | kind=NEW_INSTANCE_REF className=%s method_id=%s result=ReferToEdge reason=class_found", className, method_id)
        else:
            unresolved.append(UnresolvedLink(method_id=method_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range") if isinstance(c, dict) else getattr(c, "line_range", None)))
            logger.debug("[EXP:STEP5:resolve] candidate | kind=%s method_id=%s result=unresolved reason=unknown_kind", kind, method_id)
            continue
        if target_type and target_id:
            edges.append(ReferToEdge(method_id=method_id, target_type=target_type, target_id=target_id, evidence={"file": code_ref, "candidate_kind": kind}, confidence=1.0))
    method_short = ".".join(method_id.rsplit(".", 2)[-2:]) if method_id and "." in method_id else (method_id or "")
    logger.debug("[EXP:STEP5:resolve] 메서드 완료 | method=%s edges=%d unresolved=%d", method_short, len(edges), len(unresolved))
    return edges, unresolved


def _key_in_code(code: str, key: str) -> bool:
    """인덱스 키가 코드에 등장하는지 검사 (추출 없음). key 또는 'key' / \"key\" 형태."""
    if not code or not key:
        return False
    if key in code:
        return True
    if ('"' + key + '"') in code or ("'" + key + "'") in code:
        return True
    return False


def link_chunks_to_indexes(
    method_facts_list: List[MethodFacts],
    field_chunks: Optional[List[Dict[str, Any]]] = None,
    sql_index: Optional[Dict[str, SqlStatementChunk]] = None,
    config_index: Optional[Any] = None,
    class_index: Optional[Dict[str, Any]] = None,
) -> Tuple[List[ReferToEdge], List[UnresolvedLink]]:
    """Method/Field 청크 코드에 인덱스 키가 있으면 REFER_TO 연결. 문자열 추출/패턴 없이 key in code 만 검사."""
    edges: List[ReferToEdge] = []
    seen: set = set()  # (source_id, source_kind, target_type, target_id)

    def add_edge(method_id: str, field_id: Optional[str], target_type: str, target_id: str, evidence: Dict[str, Any]) -> None:
        sid = field_id if field_id else method_id
        kind = "Field" if field_id else "Method"
        t = (sid, kind, target_type, target_id)
        if t in seen:
            return
        seen.add(t)
        edges.append(
            ReferToEdge(
                method_id=method_id,
                target_type=target_type,
                target_id=target_id,
                evidence=evidence,
                confidence=1.0,
                field_id=field_id,
            )
        )

    for m in method_facts_list:
        code = (m.code or "").strip()
        if not code:
            continue
        evidence = {"file": m.code_ref, "line_range": [m.start_line, m.end_line]}
        if sql_index:
            for key in sql_index:
                if _key_in_code(code, key):
                    add_edge(m.method_id, None, "SqlStatement", key, evidence)
        if config_index and hasattr(config_index, "keys"):
            for key in config_index.keys():
                if _key_in_code(code, key):
                    add_edge(m.method_id, None, "ConfigSection", key, evidence)
        if class_index and hasattr(class_index, "keys"):
            for key in class_index.keys():
                if not _key_in_code(code, key):
                    continue
                info = class_index.get(key)
                target_id = key
                if isinstance(info, dict):
                    target_id = info.get("fqn", key) or key
                elif info is not None:
                    target_id = str(info)
                add_edge(m.method_id, None, "Class", target_id, evidence)

    if field_chunks:
        for fc in field_chunks:
            field_id = fc.get("field_id") or ""
            code = (fc.get("code") or "").strip()
            if not field_id or not code:
                continue
            evidence = {"file": fc.get("code_ref"), "line_range": fc.get("line_range")}
            if sql_index:
                for key in sql_index:
                    if _key_in_code(code, key):
                        add_edge("", field_id, "SqlStatement", key, evidence)
            if config_index and hasattr(config_index, "keys"):
                for key in config_index.keys():
                    if _key_in_code(code, key):
                        add_edge("", field_id, "ConfigSection", key, evidence)
            if class_index and hasattr(class_index, "keys"):
                for key in class_index.keys():
                    if not _key_in_code(code, key):
                        continue
                    info = class_index.get(key)
                    target_id = key
                    if isinstance(info, dict):
                        target_id = info.get("fqn", key) or key
                    elif info is not None:
                        target_id = str(info)
                    add_edge("", field_id, "Class", target_id, evidence)

    return edges, []


def _paren_depth_at(text: str, pos: int) -> int:
    """text[0:pos] 구간에서 괄호 깊이. '(' +1, ')' -1. 따옴표 안의 괄호는 무시."""
    depth = 0
    i = 0
    while i < pos and i < len(text):
        c = text[i]
        if c in ("'", '"'):
            quote = c
            i += 1
            while i < pos and i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    return depth


# 키워드 경계 분할: depth 0에서만 매칭 (단어 경계). 대소문자 무시.
_SQL_TABLE_KEYWORDS = re.compile(
    r"\b(FROM|JOIN|INTO|UPDATE|DELETE)\b",
    re.IGNORECASE,
)


def _find_safe_break(text: str, start: int, ideal_end: int) -> int:
    """ideal_end 이전에서 끊기: 괄호·따옴표 밖(depth 0)인 쉼표·줄바꿈 뒤에서만 끊음.
    FROM/JOIN/WHERE/AND/OR에서는 끊지 않음 → 테이블·컬럼이 한 쌍으로 같은 조각에 있고, 참조관계(JOIN ON 등)도 잘리지 않음."""
    if ideal_end >= len(text):
        return len(text)
    last_safe = ideal_end
    depth = 0
    i = start
    in_single = False
    in_double = False
    while i < ideal_end and i < len(text):
        c = text[i]
        if in_single:
            if c == "\\":
                i += 2
                continue
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "'":
            in_single = True
            i += 1
            continue
        if c == '"':
            in_double = True
            i += 1
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            if c == ",":
                # 컬럼/테이블 목록 구분 쉼표 직후 → 테이블·컬럼 쌍·참조관계가 조각 안에서 유지됨
                last_safe = i + 1
            elif c == "\n":
                # 줄 단위(컬럼 한 줄 끝) 직후
                last_safe = i + 1
        i += 1
    return last_safe if last_safe > start else ideal_end


def _find_safe_break_at_keywords(text: str, start: int, ideal_end: int) -> int:
    """ideal_end 이전에서 끊기: depth 0에서 FROM/JOIN/INTO/UPDATE/DELETE가 한 번이라도 나온 뒤에만
    last_safe를 갱신. 끊기는 depth 0인 쉼표·줄바꿈 또는 다음 키워드 직전."""
    if ideal_end >= len(text):
        return len(text)
    last_safe = start
    depth = 0
    i = start
    in_single = False
    in_double = False
    seen_table_keyword = False
    while i < ideal_end and i < len(text):
        c = text[i]
        if in_single:
            if c == "\\":
                i += 2
                continue
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "'":
            in_single = True
            i += 1
            continue
        if c == '"':
            in_double = True
            i += 1
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            if not seen_table_keyword and _SQL_TABLE_KEYWORDS.match(text[i:]):
                seen_table_keyword = True
            if seen_table_keyword:
                if c == "," or c == "\n":
                    last_safe = i + 1
                # 다음 키워드 시작 직전에서 끊기 (다음 조각이 테이블 문맥으로 시작하도록)
                next_kw = _SQL_TABLE_KEYWORDS.search(text[i + 1:ideal_end])
                if next_kw and next_kw.start() > 0:
                    candidate = i + 1 + next_kw.start()
                    if start < candidate < ideal_end:
                        last_safe = candidate
        i += 1
    if last_safe <= start:
        return ideal_end
    return min(last_safe, ideal_end)


def _split_sql_at_keyword_boundaries(sql_text: str) -> List[str]:
    """긴 SQL을 키워드 경계(FROM/JOIN/INTO/UPDATE/DELETE)에서만 분할. 각 조각은 최소 하나의 테이블 문맥 포함."""
    text = (sql_text or "").strip()
    if not text or len(text) <= SQL_SPLIT_THRESHOLD:
        return [text] if text else []
    out: List[str] = []
    start = 0
    while start < len(text):
        ideal_end = min(start + SQL_LLM_CHUNK_MAX, len(text))
        if ideal_end >= len(text):
            out.append(text[start:].strip())
            break
        break_at = _find_safe_break_at_keywords(text, start, ideal_end)
        if break_at <= start:
            break_at = ideal_end
        piece = text[start:break_at].strip()
        if piece:
            out.append(piece)
        start = break_at
        if len(out) >= SQL_MAX_CHUNKS:
            if start < len(text):
                out.append(text[start:].strip())
            break
    return out if out else [text]


def _split_sql_for_llm(sql_text: str) -> List[str]:
    """긴 SQL을 키워드 경계에서 분할. 괄호 depth 0인 FROM/JOIN/INTO/UPDATE/DELETE 근처에서만 자름."""
    text = (sql_text or "").strip()
    if not text or len(text) <= SQL_SPLIT_THRESHOLD:
        return [text] if text else []

    # 1) 세미콜론 분리는 "진짜 문장 끝"만: 조각이 너무 많거나 너무 작으면 무시
    parts_by_semicolon = [p.strip() for p in text.split(";") if p.strip()]
    use_semicolon = (
        len(parts_by_semicolon) <= 5
        and all(len(p) >= 200 for p in parts_by_semicolon)
    )
    if use_semicolon and parts_by_semicolon:
        parts = parts_by_semicolon
    else:
        parts = [text]

    out: List[str] = []
    for part in parts:
        if len(part) <= SQL_LLM_CHUNK_MAX:
            out.append(part)
            continue
        # 2) 키워드 경계 분할 사용 (테이블·컬럼 쌍이 같은 조각에 유지되도록)
        pieces = _split_sql_at_keyword_boundaries(part)
        out.extend(pieces)
    return out if out else [text]


def _expand_chunks_with_sql_split(chunks: List[SqlStatementChunk]) -> List[SqlStatementChunk]:
    """긴 SQL 청크를 키워드 경계에서 분할해 여러 청크로 확장. 같은 sql_key 유지, 조각 순서 유지."""
    out: List[SqlStatementChunk] = []
    for c in chunks:
        if getattr(c, "sql_type", "") == "entity_definition":
            out.append(c)
            continue
        sql_text = (getattr(c, "sql_text", "") or "").strip()
        if not sql_text or len(sql_text) <= SQL_SPLIT_THRESHOLD:
            out.append(c)
            continue
        pieces = _split_sql_at_keyword_boundaries(sql_text)
        if len(pieces) <= 1:
            out.append(c)
            continue
        sql_key = getattr(c, "sql_key", "") or ""
        sql_type = getattr(c, "sql_type", "") or ""
        start_line = getattr(c, "start_line", 0) or 0
        end_line = getattr(c, "end_line", 0) or 0
        file_path = getattr(c, "file_path", "") or ""
        for piece in pieces:
            if not piece.strip():
                continue
            out.append(
                SqlStatementChunk(
                    sql_key=sql_key,
                    sql_type=sql_type,
                    start_line=start_line,
                    end_line=end_line,
                    sql_text=piece,
                    file_path=file_path,
                )
            )
    return out


def _extract_tables_columns_fk_via_llm(
    sql_text: str,
    sql_key: str,
    api_key: Optional[str] = None,
) -> Tuple[List[Tuple[str, str, str]], List[ColumnRef], List[FkEdge]]:
    """LLM으로 한 조각에서 테이블·컬럼·FK 추출. (분할된 조각용, 최대 SQL_LLM_CHUNK_MAX 권장)."""
    api_key = api_key or os.environ.get("LLM_API_KEY", "")
    if not (sql_text or "").strip():
        return [], [], []
    if not api_key:
        logger.warning("[EXP:STEP5] LLM_API_KEY 없음, sql_table_extract 스킵 | sql_key=%s", sql_key)
        return [], [], []
    text = (sql_text.strip() or "")[:SQL_LLM_CHUNK_MAX]
    try:
        from util.rule_loader import RuleLoader
        loader = RuleLoader(target_lang="exp")
        result = loader.execute(
            "sql_table_extract",
            {"sql_items": [{"sql_key": sql_key or "", "sql_text": text, "prev_summary": ""}]},
            api_key=api_key,
        )
        res_list = result.get("results") or []
        result = res_list[0] if res_list and isinstance(res_list[0], dict) else {}
    except Exception as e:
        logger.warning("[EXP:STEP5] LLM sql_table_extract 오류 | sql_key=%s error=%s", sql_key, e)
        return [], [], []
    tables: List[Tuple[str, str, str]] = []
    for row in (result.get("tables") or []):
        if isinstance(row, dict) and row.get("name"):
            schema = str(row.get("schema") or "").strip()
            name = str(row.get("name") or "").strip()
            rel = str(row.get("relation") or "SELECT").strip().upper()
            if rel not in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                rel = "SELECT"
            tables.append((schema, name, rel))
    columns: List[ColumnRef] = []
    for row in (result.get("columns") or []):
        if isinstance(row, dict) and row.get("column_name"):
            columns.append(
                ColumnRef(
                    table_schema=str(row.get("table_schema") or "").strip(),
                    table_name=str(row.get("table_name") or "").strip(),
                    column_name=str(row.get("column_name") or "").strip(),
                )
            )
    fks: List[FkEdge] = []
    for row in (result.get("fks") or []):
        if isinstance(row, dict) and row.get("from_table") and row.get("to_table"):
            fks.append(
                FkEdge(
                    from_schema=str(row.get("from_schema") or "").strip(),
                    from_table=str(row.get("from_table") or "").strip(),
                    from_column=str(row.get("from_column") or "").strip(),
                    to_schema=str(row.get("to_schema") or "").strip(),
                    to_table=str(row.get("to_table") or "").strip(),
                    to_column=str(row.get("to_column") or "").strip(),
                    candidate=True,
                )
            )
    return tables, columns, fks


def _extract_tables_columns_fk_from_sql(
    sql_text: str,
    sql_key: str,
    api_key: Optional[str] = None,
) -> Tuple[List[Tuple[str, str, str]], List[ColumnRef], List[FkEdge]]:
    """긴 SQL은 적당히 분할 후 LLM 호출·결과 merge. 짧으면 한 번만 호출."""
    if not (sql_text or "").strip():
        return [], [], []
    pieces = _split_sql_for_llm(sql_text)
    if len(pieces) > 1:
        logger.debug("[EXP:STEP5] 긴 SQL 분할 | sql_key=%s pieces=%d", sql_key, len(pieces))
    all_tables: List[Tuple[str, str, str]] = []
    all_columns: List[ColumnRef] = []
    all_fks: List[FkEdge] = []
    seen_table: set = set()
    seen_col: set = set()
    seen_fk: set = set()
    for i, piece in enumerate(pieces):
        if not piece.strip():
            continue
        t, c, f = _extract_tables_columns_fk_via_llm(piece, f"{sql_key}" + (f"@part{i}" if len(pieces) > 1 else ""), api_key)
        for x in t:
            key = (x[0], x[1], x[2])
            if key not in seen_table:
                seen_table.add(key)
                all_tables.append(x)
        for x in c:
            key = (x.table_schema, x.table_name, x.column_name)
            if key not in seen_col:
                seen_col.add(key)
                all_columns.append(x)
        for x in f:
            key = (x.from_schema, x.from_table, x.from_column, x.to_schema, x.to_table, x.to_column)
            if key not in seen_fk:
                seen_fk.add(key)
                all_fks.append(x)
    return all_tables, all_columns, all_fks


def sql_parse(sql_text: str) -> Dict[str, Any]:
    """SQL 텍스트 → operation, tables, confidence. DeepAgent 툴용. LLM 전용, 실패 시 빈 결과."""
    api_key = os.environ.get("LLM_API_KEY", "")
    tables_tuples: List[Tuple[str, str, str]] = []
    columns_list: List[ColumnRef] = []
    if api_key and (sql_text or "").strip():
        tables_tuples, columns_list, _ = _extract_tables_columns_fk_from_sql(sql_text, "", api_key)
    by_table: Dict[Tuple[str, str], List[str]] = {}
    for c in columns_list:
        key = (c.table_schema or "", c.table_name or "")
        by_table.setdefault(key, [])
        if c.column_name and c.column_name not in by_table[key]:
            by_table[key].append(c.column_name)
    rels = set(r for (_, _, r) in tables_tuples)
    write_ops = {"WRITES", "INSERT", "UPDATE", "DELETE"}
    operation = "MIXED" if len(rels) > 1 else ("WRITE" if rels & write_ops else "READ")
    tables = []
    for schema, table, rel in tables_tuples:
        cols = by_table.get((schema or "", table or ""), [])
        tables.append({"schema": schema or "", "name": table, "columns": cols})
    confidence = 0.9 if tables_tuples else 0.0
    return {"operation": operation, "tables": tables, "confidence": confidence}


def _process_one_sql_chunk(
    chunk: SqlStatementChunk,
    api_key: str,
) -> Tuple[List[ReadsWritesEdge], List[FkEdge], List[ColumnRef]]:
    """단일 SQL 청크에 대해 LLM 테이블/컬럼/FK 추출. 병렬 호출용."""
    sql_key = getattr(chunk, "sql_key", "") or ""
    sql_text = (getattr(chunk, "sql_text", "") or "").strip()
    edges: List[ReadsWritesEdge] = []
    fk_edges: List[FkEdge] = []
    column_refs: List[ColumnRef] = []
    if not sql_text:
        return edges, fk_edges, column_refs
    seen: set = set()
    tables, cols, fks = _extract_tables_columns_fk_from_sql(sql_text, sql_key, api_key)
    for schema, table, rel in tables:
        key = (sql_key, schema, table, rel)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            ReadsWritesEdge(
                sql_key=sql_key,
                table_schema=schema,
                table_name=table,
                relation=rel,
            )
        )
    for col in cols:
        column_refs.append(col)
    for fk in fks:
        fk_edges.append(fk)
    return edges, fk_edges, column_refs


def _build_sql_batches(
    chunks: List[SqlStatementChunk],
    max_tokens: int,
) -> List[List[SqlStatementChunk]]:
    """청크 리스트를 max_tokens 이하로 묶어 배치 리스트 반환. 청크별 토큰 = sql_key + sql_text."""
    from exp.config import estimate_tokens
    batches: List[List[SqlStatementChunk]] = []
    current: List[SqlStatementChunk] = []
    current_tokens = 0
    for c in chunks:
        sql_key = getattr(c, "sql_key", "") or ""
        sql_text = (getattr(c, "sql_text", "") or "").strip()
        t = estimate_tokens(sql_key) + estimate_tokens(sql_text)
        if t > max_tokens:
            t = max_tokens
        if current and current_tokens + t > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(c)
        current_tokens += t
    if current:
        batches.append(current)
    return batches


def _format_prev_summary(
    prev_results: List[Tuple[List[Tuple[str, str, str]], List[Any], List[Any], Optional[List[Dict[str, str]]]]],
) -> str:
    """이전 조각들 LLM 결과에서 물리테이블·관계·별칭→물리테이블만 요약. 별칭은 물리테이블 목록에 넣지 않음."""
    if not prev_results:
        return ""
    parts: List[str] = []
    seen: set = set()
    alias_pairs: List[str] = []
    for item in prev_results:
        tables = item[0]
        alias_map = item[3] if len(item) >= 4 and item[3] else []
        aliases_in_this = {str(ent.get("alias", "")).strip() for ent in (alias_map or []) if isinstance(ent, dict) and ent.get("alias")}
        for schema, name, rel in tables:
            name_stripped = (name or "").strip()
            if name_stripped in aliases_in_this:
                continue
            key = (schema or "", name_stripped, rel or "")
            if key in seen:
                continue
            seen.add(key)
            parts.append(f"{schema}.{name_stripped}({rel})" if schema else f"{name_stripped}({rel})")
        for ent in alias_map if isinstance(alias_map, list) else []:
            if isinstance(ent, dict) and ent.get("alias") and ent.get("physical_table"):
                alias_pairs.append(f"{ent.get('alias')}={ent.get('physical_table')}")
    if not parts and not alias_pairs:
        return ""
    out = "[이전 조각까지 누적된 요약(prev_summary). 아래 내용을 참고하여 이번 조각 결과에 포함·누적하세요:"
    if parts:
        out += " 물리테이블 " + ", ".join(parts[:20]) + (" ..." if len(parts) > 20 else "")
    if alias_pairs:
        out += (" " if out else "") + "| 별칭→물리테이블: " + ", ".join(alias_pairs[:30])
    out += ". 이 조각에 위 테이블이 없으면 해당 테이블에 컬럼을 연결하지 마세요. 별칭은 테이블로 출력하지 마세요.]"
    return out


def _extract_tables_columns_fk_batch_via_llm(
    batch: List[SqlStatementChunk],
    api_key: str,
    prev_summaries: Optional[List[str]] = None,
) -> List[Tuple[List[Tuple[str, str, str]], List[ColumnRef], List[FkEdge]]]:
    """여러 SQL 청크를 한 번에 LLM에 보내 테이블/컬럼/FK 추출. prev_summaries 있으면 각 항목에 이전 조각 요약 전달."""
    if not batch or not api_key:
        return []
    from util.rule_loader import RuleLoader
    from exp.config import estimate_tokens
    sql_items = []
    for i, c in enumerate(batch):
        sql_key = getattr(c, "sql_key", "") or ""
        sql_text = (getattr(c, "sql_text", "") or "").strip()
        prev = ""
        if prev_summaries and i < len(prev_summaries):
            prev = (prev_summaries[i] or "").strip()
        sql_items.append({
            "sql_key": sql_key,
            "sql_text": sql_text,
            "prev_summary": prev,
        })
    try:
        loader = RuleLoader(target_lang="exp")
        result = loader.execute(
            "sql_table_extract",
            {"sql_items": sql_items},
            api_key=api_key,
        )
    except Exception as e:
        logger.warning("[EXP:STEP5] LLM sql_table_extract 오류: %s", e)
        return [([], [], [], []) for _ in batch]
    out: List[Tuple[List[Tuple[str, str, str]], List[ColumnRef], List[FkEdge], List[Dict[str, str]]]] = []
    results_list = result.get("results") or []
    for i, res in enumerate(results_list):
        if not isinstance(res, dict):
            out.append(([], [], [], []))
            continue
        alias_map: List[Dict[str, str]] = []
        for row in (res.get("alias_map") or []):
            if isinstance(row, dict) and row.get("alias") and row.get("physical_table"):
                alias_map.append({
                    "alias": str(row.get("alias", "")).strip(),
                    "physical_table": str(row.get("physical_table", "")).strip(),
                })
        aliases_set = {ent.get("alias", "").strip() for ent in alias_map if ent.get("alias")}
        tables: List[Tuple[str, str, str]] = []
        for row in (res.get("tables") or []):
            if not isinstance(row, dict) or not row.get("name"):
                continue
            name = str(row.get("name") or "").strip()
            if name in aliases_set:
                continue
            schema = str(row.get("schema") or "").strip()
            rel = str(row.get("relation") or "SELECT").strip().upper()
            if rel not in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                rel = "SELECT"
            tables.append((schema, name, rel))
        alias_to_physical = {e["alias"]: e["physical_table"] for e in alias_map if e.get("alias") and e.get("physical_table")}
        columns: List[ColumnRef] = []
        for row in (res.get("columns") or []):
            if not isinstance(row, dict) or not row.get("column_name"):
                continue
            tbl = str(row.get("table_name") or "").strip()
            if tbl in alias_to_physical:
                tbl = alias_to_physical[tbl]
            columns.append(
                ColumnRef(
                    table_schema=str(row.get("table_schema") or "").strip(),
                    table_name=tbl,
                    column_name=str(row.get("column_name") or "").strip(),
                )
            )
        fks: List[FkEdge] = []
        for row in (res.get("fks") or []):
            if isinstance(row, dict) and row.get("from_table") and row.get("to_table"):
                fks.append(
                    FkEdge(
                        from_schema=str(row.get("from_schema") or "").strip(),
                        from_table=str(row.get("from_table") or "").strip(),
                        from_column=str(row.get("from_column") or "").strip(),
                        to_schema=str(row.get("to_schema") or "").strip(),
                        to_table=str(row.get("to_table") or "").strip(),
                        to_column=str(row.get("to_column") or "").strip(),
                        candidate=True,
                    )
                )
        out.append((tables, columns, fks, alias_map))
    while len(out) < len(batch):
        out.append(([], [], [], []))
    return out


def _process_one_sql_batch(
    batch: List[SqlStatementChunk],
    api_key: str,
    prev_summaries: Optional[List[str]] = None,
) -> Tuple[List[ReadsWritesEdge], List[FkEdge], List[ColumnRef], List[Tuple[List[Tuple[str, str, str]], List[ColumnRef], List[FkEdge], List[Dict[str, str]]]]]:
    """한 배치에 대해 LLM 호출 후 edges/fk_edges/column_refs 반환. merge 규칙: 컬럼은 같은 조각의 tables에 있을 때만 수용."""
    edges: List[ReadsWritesEdge] = []
    fk_edges: List[FkEdge] = []
    column_refs: List[ColumnRef] = []
    per_chunk_raw: List[Tuple[List[Tuple[str, str, str]], List[ColumnRef], List[FkEdge], List[Dict[str, str]]]] = []
    if not batch or not api_key:
        return edges, fk_edges, column_refs, per_chunk_raw
    per_chunk = _extract_tables_columns_fk_batch_via_llm(batch, api_key, prev_summaries)
    for chunk, (tables, cols, fks, alias_map) in zip(batch, per_chunk):
        per_chunk_raw.append((tables, cols, fks, alias_map))
        sql_key = getattr(chunk, "sql_key", "") or ""
        tables_set = {(s or "", t or "") for s, t, _ in tables}
        seen: set = set()
        for schema, table, rel in tables:
            key = (sql_key, schema, table, rel)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                ReadsWritesEdge(
                    sql_key=sql_key,
                    table_schema=schema,
                    table_name=table,
                    relation=rel,
                )
            )
        for col in cols:
            if (col.table_schema or "", col.table_name or "") in tables_set:
                column_refs.append(col)
        for fk in fks:
            fk_edges.append(fk)
    return edges, fk_edges, column_refs, per_chunk_raw


def _link_sql_to_tables_per_key_pipeline(
    by_key: Dict[str, List[SqlStatementChunk]],
    results_by_key: Dict[str, List[Any]],
    api_key: str,
    workers: int,
) -> Tuple[List[ReadsWritesEdge], List[FkEdge], List[ColumnRef]]:
    """키 단위 파이프라인: sql_key별로 조각을 순차 처리, 서로 다른 키는 병렬. 라운드 동기화 제거로 대기 시간 감소."""
    edges: List[ReadsWritesEdge] = []
    fk_edges: List[FkEdge] = []
    column_refs: List[ColumnRef] = []
    seen_edge: set = set()
    seen_col: set = set()
    lock = threading.Lock()
    total_pieces = sum(len(v) for v in by_key.values())
    work_queue: queue.Queue = queue.Queue()
    completed_count = [0]  # mutable for closure

    for k in by_key:
        work_queue.put((k, 0, by_key[k][0], ""))

    def worker() -> None:
        while True:
            try:
                item = work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            key, piece_i, chunk, prev = item
            if key is None:
                work_queue.task_done()
                break
            try:
                e, fk, col, raw = _process_one_sql_batch([chunk], api_key, [prev])
            except Exception as ex:
                logger.warning("[EXP:STEP5] SQL 키 단위 LLM 예외: %s", ex)
                e, fk, col, raw = [], [], [], [([], [], [], [])]
            with lock:
                if raw and len(raw) > 0:
                    results_by_key.setdefault(key, []).append(raw[0])
                for x in e:
                    k = (x.sql_key, x.table_schema, x.table_name, x.relation)
                    if k not in seen_edge:
                        seen_edge.add(k)
                        edges.append(x)
                fk_edges.extend(fk)
                for c in col:
                    ckey = (c.table_schema, c.table_name, c.column_name)
                    if ckey not in seen_col:
                        seen_col.add(ckey)
                        column_refs.append(c)
                completed_count[0] += 1
                n, t = completed_count[0], total_pieces
                if n % 10 == 0 or n == t:
                    logger.info("[EXP:STEP5] SQL→테이블(키 단위 파이프라인) 완료 %d/%d", n, t)
                if piece_i + 1 < len(by_key[key]):
                    chunk_next = by_key[key][piece_i + 1]
                    prev_next = _format_prev_summary(results_by_key.get(key, [])[: piece_i + 1])
                    work_queue.put((key, piece_i + 1, chunk_next, prev_next))

    logger.info(
        "[EXP:STEP5] SQL→테이블 키 단위 파이프라인 시작 | 키=%d 조각=%d 워커=%d (라운드 없음, 완료된 키부터 다음 조각 진행)",
        len(by_key),
        total_pieces,
        workers,
    )
    threads = [threading.Thread(target=worker, daemon=False) for _ in range(min(workers, total_pieces))]
    for t in threads:
        t.start()
    while True:
        with lock:
            n = completed_count[0]
        if n >= total_pieces:
            break
        time.sleep(0.05)
    for _ in threads:
        work_queue.put((None, 0, None, ""))
    for t in threads:
        t.join()
    return edges, fk_edges, column_refs


def link_sql_to_tables(
    sql_chunks: List[SqlStatementChunk],
    api_key: Optional[str] = None,
    max_workers: Optional[int] = None,
) -> Tuple[List[ReadsWritesEdge], List[FkEdge], List[ColumnRef]]:
    """SqlStatement → Table (LLM). 확장(키워드 경계 분할) 후 prev_summary 전달. 기본은 키 단위 파이프라인(라운드 제거)."""
    edges: List[ReadsWritesEdge] = []
    fk_edges: List[FkEdge] = []
    column_refs: List[ColumnRef] = []
    seen_edge: set = set()
    seen_col: set = set()
    api_key = api_key or os.environ.get("LLM_API_KEY", "")
    from exp.config import (
        get_llm_parallel_workers,
        MAX_LLM_BATCH_TOKEN,
        USE_SQL_PER_KEY_PIPELINE,
    )
    workers = max_workers if max_workers is not None else get_llm_parallel_workers()
    chunks_to_process = [
        c for c in sql_chunks
        if (getattr(c, "sql_text", "") or "").strip()
    ]
    if not chunks_to_process:
        logger.info("[EXP:STEP5] link_sql_to_tables  처리할 SQL 청크 없음")
        return edges, fk_edges, column_refs

    expanded = _expand_chunks_with_sql_split(chunks_to_process)
    logger.info(
        "[EXP:STEP5] SQL 분할 확장 | 원본 청크=%d → 확장 후=%d",
        len(chunks_to_process),
        len(expanded),
    )

    by_key: Dict[str, List[SqlStatementChunk]] = {}
    for c in expanded:
        k = getattr(c, "sql_key", "") or ""
        by_key.setdefault(k, []).append(c)
    max_pieces = max(len(v) for v in by_key.values()) if by_key else 0
    results_by_key: Dict[str, List[Tuple[List[Tuple[str, str, str]], List[ColumnRef], List[FkEdge], List[Dict[str, str]]]]] = {k: [] for k in by_key}

    if USE_SQL_PER_KEY_PIPELINE and workers > 1:
        edges, fk_edges, column_refs = _link_sql_to_tables_per_key_pipeline(
            by_key, results_by_key, api_key, workers
        )
    else:
        for round_i in range(max_pieces):
            round_data: List[Tuple[str, SqlStatementChunk, str]] = []
            for k in sorted(by_key.keys()):
                if round_i >= len(by_key[k]):
                    continue
                chunk = by_key[k][round_i]
                prev = _format_prev_summary(results_by_key.get(k, [])[:round_i])
                round_data.append((k, chunk, prev))
            if not round_data:
                continue
            round_chunks = [c for _, c, _ in round_data]
            round_prevs = [p for _, _, p in round_data]
            index_in_round = {id(c): i for i, (_, c, _) in enumerate(round_data)}
            batches = _build_sql_batches(round_chunks, MAX_LLM_BATCH_TOKEN)
            logger.info(
                "[EXP:STEP5] SQL→테이블 라운드 %d/%d 시작 | 이번 라운드 청크=%d 배치=%d (라운드는 순차, 배치만 병렬)",
                round_i + 1,
                max_pieces,
                len(round_chunks),
                len(batches),
            )
            if round_i == 0:
                logger.info(
                    "[EXP:STEP5] link_sql_to_tables | 청크=%d → 배치=%d (max_tokens=%d)",
                    len(round_chunks),
                    len(batches),
                    MAX_LLM_BATCH_TOKEN,
                )
            if workers <= 1:
                for batch in batches:
                    prev_summaries = [round_prevs[index_in_round[id(c)]] for c in batch]
                    e, fk, col, raw = _process_one_sql_batch(batch, api_key, prev_summaries)
                    for j, chunk in enumerate(batch):
                        if j < len(raw):
                            results_by_key.setdefault(getattr(chunk, "sql_key", "") or "", []).append(raw[j])
                    for x in e:
                        key = (x.sql_key, x.table_schema, x.table_name, x.relation)
                        if key not in seen_edge:
                            seen_edge.add(key)
                            edges.append(x)
                    fk_edges.extend(fk)
                    for c in col:
                        ckey = (c.table_schema, c.table_name, c.column_name)
                        if ckey not in seen_col:
                            seen_col.add(ckey)
                            column_refs.append(c)
            else:
                total_batches = len(batches)
                completed = 0
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            _process_one_sql_batch,
                            batch,
                            api_key,
                            [round_prevs[index_in_round[id(c)]] for c in batch],
                        ): batch
                        for batch in batches
                    }
                    for future in as_completed(futures):
                        batch = futures[future]
                        try:
                            e, fk, col, raw = future.result()
                            for j, chunk in enumerate(batch):
                                if j < len(raw):
                                    results_by_key.setdefault(getattr(chunk, "sql_key", "") or "", []).append(raw[j])
                            for x in e:
                                key = (x.sql_key, x.table_schema, x.table_name, x.relation)
                                if key not in seen_edge:
                                    seen_edge.add(key)
                                    edges.append(x)
                            fk_edges.extend(fk)
                            for c in col:
                                ckey = (c.table_schema, c.table_name, c.column_name)
                                if ckey not in seen_col:
                                    seen_col.add(ckey)
                                    column_refs.append(c)
                        except Exception as ex:
                            logger.warning("[EXP:STEP5] SQL 배치 LLM 처리 예외: %s", ex)
                        completed += 1
                        if completed == total_batches:
                            logger.info("[EXP:STEP5] SQL→테이블 LLM 완료  배치=%d/%d", completed, total_batches)
                        else:
                            logger.debug("[EXP:STEP5] SQL→테이블 LLM 진행  배치=%d/%d", completed, total_batches)

    logger.info("[EXP:STEP5] link_sql_to_tables 완료  reads_writes=%d  fk_edges=%d  column_refs=%d",
        len(edges), len(fk_edges), len(column_refs))
    return edges, fk_edges, column_refs


def _sql_op_to_method_relation(sql_relation: str) -> str:
    """SqlStatement→Table 연산(SELECT/INSERT/UPDATE/DELETE)을 Method→Table 관계(READS/WRITES)로 변환."""
    r = (sql_relation or "").strip().upper()
    if r in ("INSERT", "UPDATE", "DELETE"):
        return "WRITES"
    return "READS"


def derive_method_table_edges(
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
) -> List[MethodTableEdge]:
    """CALLS_SQL(SqlStatement) + reads_writes(SELECT/INSERT/UPDATE/DELETE)로 Method/Field→Table(READS/WRITES) 유도."""
    sql_to_tables: Dict[str, List[Tuple[str, str, str]]] = {}  # sql_key -> [(schema, table, sql_op)]
    for rw in reads_writes:
        if rw.sql_key not in sql_to_tables:
            sql_to_tables[rw.sql_key] = []
        sql_to_tables[rw.sql_key].append((rw.table_schema, rw.table_name, rw.relation))
    result: List[MethodTableEdge] = []
    seen: set = set()
    for re in refer_to_edges:
        if re.target_type != "SqlStatement":
            continue
        field_id = getattr(re, "field_id", None)
        for schema, table, sql_rel in sql_to_tables.get(re.target_id, []):
            method_rel = _sql_op_to_method_relation(sql_rel)
            dedup_key = (re.method_id, field_id or "", schema, table, method_rel)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            result.append(
                MethodTableEdge(
                    method_id=re.method_id,
                    table_schema=schema,
                    table_name=table,
                    relation=method_rel,
                    field_id=field_id,
                )
            )
    return result


def run_step5(
    method_facts_list: List[MethodFacts],
    sql_index: Dict[str, SqlStatementChunk],
    sql_chunks: List[SqlStatementChunk],
    config_index: Optional[Any] = None,
    ref_candidates_by_method: Optional[Dict[str, List[Any]]] = None,
    symbol_type_index: Optional[Dict[str, Dict[str, str]]] = None,
    class_index: Optional[Dict[str, Any]] = None,
    field_chunks: Optional[List[Dict[str, Any]]] = None,
    data_dir: Optional[str] = None,
    field_facts_list: Optional[List[Any]] = None,
    method_index_by_class: Optional[Dict[str, Dict[str, List[str]]]] = None,
    run_dir: Optional[str] = None,
    neo4j_writer: Optional[Any] = None,
    neo4j_batch_size: Optional[int] = None,
    artifact_chunks: Optional[List[Any]] = None,
) -> Tuple[
    List[ReferToEdge],
    List[ReadsWritesEdge],
    List[FkEdge],
    List[ColumnRef],
    List[MethodTableEdge],
    List[UnresolvedLink],
    List[Any],
    List[Any],
]:
    """Step 5: Method/Field→REFER_TO(키 in 코드), SQL→Table(LLM)과 Call LLM 병렬, Method/Field→Table 유도."""
    config_count = len(config_index) if config_index and hasattr(config_index, "__len__") else 0
    logger.info(
        "[EXP:STEP5] 링킹 진입  method_facts=%d sql_index=%d sql_chunks=%d config_index=%d field_chunks=%s",
        len(method_facts_list),
        len(sql_index),
        len(sql_chunks),
        config_count,
        len(field_chunks) if field_chunks else 0,
    )
    logger.info("[EXP:STEP5] [매칭] 결정론 매칭 (키 in 코드, sql/config/class 인덱스)")
    refer_to_edges, unresolved = link_chunks_to_indexes(
        method_facts_list,
        field_chunks=field_chunks,
        sql_index=sql_index,
        config_index=config_index,
        class_index=class_index,
    )
    det_matched = len(refer_to_edges)
    logger.info("[EXP:STEP5] link_chunks_to_indexes 완료  refer_to=%d  미연결=%d", det_matched, len(unresolved))
    api_key = os.environ.get("LLM_API_KEY", "")
    writer = neo4j_writer
    batch_size = neo4j_batch_size or 30

    # 선행 flush: method + refer_to (이미 가진 데이터만)
    if writer and batch_size:
        try:
            import asyncio
            from exp.neo4j import build_method_queries, build_refer_to_queries
            q_method = build_method_queries(method_facts_list)
            q_refer = build_refer_to_queries(refer_to_edges)
            if q_method or q_refer:
                asyncio.run(writer.run_queries_batch_sync(q_method + q_refer, batch_size=batch_size))
                logger.info("[EXP:STEP5] [Neo4j] 선행 flush  method+refer_to  queries=%d", len(q_method) + len(q_refer))
        except Exception as e:
            logger.warning("[EXP:STEP5] Neo4j 선행 flush 스킵: %s", e)

    combined_table_chunks: List[SqlStatementChunk] = list(sql_chunks or [])
    # Java 엔티티 클래스 필드 → sql_items에 합류
    entity_fqns = identify_entity_classes(class_index or {})
    for fqn in entity_fqns:
        info = (class_index or {}).get(fqn)
        if not isinstance(info, dict):
            continue
        class_key = info.get("class_key") or fqn
        fields = [f for f in (field_facts_list or []) if getattr(f, "class_key", "") == class_key]
        if not fields:
            continue
        field_text = "\n".join(
            f"{f.name} ({getattr(f, 'field_type', '?')})"
            + (f" {f.annotations}" if getattr(f, "annotations", None) else "")
            for f in fields
        )
        combined_table_chunks.append(SqlStatementChunk(
            sql_key=f"entity::{fqn}",
            sql_type="entity_definition",
            sql_text=field_text,
            start_line=0,
            end_line=0,
            file_path="",
        ))
    # XML 엔티티 청크 (ejb_entity, entitymodel_entity) → sql_items에 합류
    for ac in (artifact_chunks or []):
        ct = getattr(ac, "chunk_type", "")
        if ct not in ("ejb_entity", "entitymodel_entity"):
            continue
        text = (getattr(ac, "text", "") or "").strip()
        if not text:
            continue
        combined_table_chunks.append(SqlStatementChunk(
            sql_key=f"entity_xml::{getattr(ac, 'chunk_id', '')}",
            sql_type="entity_definition",
            sql_text=text,
            start_line=getattr(ac, "start_line", 0),
            end_line=getattr(ac, "end_line", 0),
            file_path=getattr(ac, "source_path", ""),
        ))
    entity_count = len(combined_table_chunks) - len(sql_chunks or [])
    if entity_count:
        logger.info("[EXP:STEP5] 엔티티 청크 %d개를 table LLM 입력에 합류 (sql=%d + entity=%d)", entity_count, len(sql_chunks or []), entity_count)

    def _run_table_llm() -> Tuple[List[ReadsWritesEdge], List[FkEdge], List[ColumnRef]]:
        return link_sql_to_tables(combined_table_chunks, api_key=api_key)

    def _run_call_llm() -> Tuple[List[Any], List[Any]]:
        from exp.call_batch import run_call_batch_pipeline
        return run_call_batch_pipeline(
            data_dir or "",
            method_facts_list,
            field_facts_list or [],
            symbol_type_index or {},
            method_index_by_class or {},
            use_llm=True,
            api_key=api_key,
            run_dir=run_dir,
        )

    def _run_uml() -> None:
        """상속(EXTENDS/IMPLEMENTS) + 연관(ASSOCIATION) 쿼리 생성 후 즉시 flush."""
        from exp.neo4j import (
            build_uml_inheritance_queries,
            build_uml_association_queries,
        )
        cidx = class_index or {}
        q_inheritance = build_uml_inheritance_queries(cidx)
        field_relations_by_class: Dict[str, List[Dict[str, Any]]] = {}
        if field_facts_list and cidx and api_key:
            from util.rule_loader import RuleLoader
            loader = RuleLoader(target_lang="framework")
            by_class: Dict[str, List[Any]] = {}
            for f in field_facts_list:
                key = getattr(f, "class_key", None) or ""
                if not key:
                    continue
                by_class.setdefault(key, []).append(f)
            for class_key, fields_list in by_class.items():
                info = None
                for v in cidx.values():
                    if isinstance(v, dict) and v.get("class_key") == class_key:
                        info = v
                        break
                if not info:
                    continue
                class_name = info.get("name") or (info.get("fqn") or "").rsplit(".", 1)[-1]
                class_meta = (info.get("comment") or "") or (info.get("annotations") or "")
                fields = []
                for f in fields_list:
                    ft = getattr(f, "field_type", None)
                    if not ft:
                        continue
                    fields.append({
                        "name": getattr(f, "name", ""),
                        "field_type": ft,
                        "comment": getattr(f, "comment", None),
                        "annotations": getattr(f, "annotations", None),
                    })
                if not fields:
                    continue
                try:
                    result = loader.execute(
                        "association",
                        {"class_name": class_name, "class_meta": class_meta or "", "fields": fields, "locale": "ko"},
                        api_key,
                    )
                    if isinstance(result, dict) and result.get("fieldRelations"):
                        field_relations_by_class[class_key] = result["fieldRelations"]
                except Exception as ex:
                    logger.debug("[EXP:STEP5] association LLM 스킵 class_key=%s: %s", class_key, ex)
        q_assoc = build_uml_association_queries(cidx, field_relations_by_class)
        all_uml = q_inheritance + q_assoc
        if writer and batch_size and all_uml:
            try:
                import asyncio
                asyncio.run(writer.run_queries_batch_sync(all_uml, batch_size=batch_size))
                logger.info("[EXP:STEP5] [Neo4j] UML flush  EXTENDS/IMPLEMENTS/ASSOCIATION  queries=%d", len(all_uml))
            except Exception as e:
                logger.warning("[EXP:STEP5] Neo4j UML flush 스킵: %s", e)

    logger.info("[EXP:STEP5] [3갈래] SQL→테이블 / Call LLM / UML 병렬  갈래 완료 시 Neo4j 즉시 반영")
    reads_writes_list: List[ReadsWritesEdge] = []
    fk_edges_list: List[FkEdge] = []
    column_refs_list: List[ColumnRef] = []
    calls_edges: List[Any] = []
    unresolved_calls: List[Any] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_table = executor.submit(_run_table_llm)
        future_call = executor.submit(_run_call_llm)
        future_uml = executor.submit(_run_uml)
        for future in as_completed([future_table, future_call, future_uml]):
            try:
                if future == future_table:
                    reads_writes_list, fk_edges_list, column_refs_list = future.result()
                    method_table_edges_local = derive_method_table_edges(refer_to_edges, reads_writes_list)
                    if writer and batch_size:
                        try:
                            import asyncio
                            from exp.neo4j import (
                                build_table_queries_from_reads_writes,
                                build_column_queries,
                                build_has_column_queries,
                                build_reads_writes_queries,
                                build_fk_to_table_queries,
                                build_fk_to_column_queries,
                                build_method_table_from_writes_queries,
                            )
                            q_t = build_table_queries_from_reads_writes(reads_writes_list)
                            q_c = build_column_queries(column_refs_list)
                            q_hc = build_has_column_queries(column_refs_list)
                            q_rw = build_reads_writes_queries(reads_writes_list)
                            q_fk = build_fk_to_table_queries(fk_edges_list)
                            q_fkc = build_fk_to_column_queries(fk_edges_list)
                            q_mt = build_method_table_from_writes_queries(method_table_edges_local)
                            asyncio.run(writer.run_queries_batch_sync(q_t + q_c + q_hc + q_rw + q_fk + q_fkc + q_mt, batch_size=batch_size))
                            logger.info("[EXP:STEP5] [Neo4j] Table 갈래 flush  queries=%d", len(q_t) + len(q_c) + len(q_hc) + len(q_rw) + len(q_fk) + len(q_fkc) + len(q_mt))
                        except Exception as e:
                            logger.warning("[EXP:STEP5] Neo4j Table 갈래 flush 스킵: %s", e)
                elif future == future_call:
                    calls_edges, unresolved_calls = future.result()
                    if writer and batch_size:
                        try:
                            import asyncio
                            from exp.call_batch import derive_uses_from_calls
                            from exp.neo4j import build_calls_queries, build_uses_queries
                            uses = derive_uses_from_calls(calls_edges, method_facts_list)
                            q_calls = build_calls_queries(calls_edges) if calls_edges else []
                            q_uses = build_uses_queries(uses) if uses else []
                            if q_calls or q_uses:
                                asyncio.run(writer.run_queries_batch_sync(q_calls + q_uses, batch_size=batch_size))
                                logger.info("[EXP:STEP5] [Neo4j] Call 갈래 flush  CALLS+USES  queries=%d", len(q_calls) + len(q_uses))
                        except Exception as e:
                            logger.warning("[EXP:STEP5] Neo4j Call 갈래 flush 스킵: %s", e)
                else:
                    future.result()
            except Exception as e:
                logger.warning("[EXP:STEP5] 병렬 갈래 예외: %s", e)
    reads_writes = reads_writes_list
    fk_edges = fk_edges_list
    column_refs = column_refs_list
    logger.info(
        "[EXP:STEP5] [완료] reads_writes=%d  fk_edges=%d  column_refs=%d  calls=%d  unresolved_calls=%d",
        len(reads_writes),
        len(fk_edges),
        len(column_refs),
        len(calls_edges),
        len(unresolved_calls),
    )
    logger.info("[EXP:STEP5] [Method–Table] REFER_TO + READS/WRITES 엣지 유도")
    method_table_edges = derive_method_table_edges(refer_to_edges, reads_writes)
    logger.info("[EXP:STEP5] 링킹 완료  refer_to=%d  reads_writes=%d  method_table=%d  미연결=%d",
        len(refer_to_edges), len(reads_writes), len(method_table_edges), len(unresolved))
    return refer_to_edges, reads_writes, fk_edges, column_refs, method_table_edges, unresolved, calls_edges, unresolved_calls
