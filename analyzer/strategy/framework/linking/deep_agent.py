from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from config.settings import settings
from util.artifact_logger import log_llm_interaction
from analyzer.strategy.framework.common.models import (
    FunctionTableEdge,
    ReferToEdge,
    UnresolvedLink,
    ArtifactChunk,
    SqlStatementChunk,
    ConfigSectionChunk,
)
from analyzer.strategy.framework.common.models import FkEdge

logger = logging.getLogger(__name__)


# ===========================================================================
# System Prompts
# ===========================================================================

SYSTEM_PROMPT = """
당신은 소스코드에서 미매칭된 인덱스 키를 추적해
코드 내 실제 사용처를 찾아 그래프 관계를 생성하는 전문가 에이전트입니다.

[절대 규칙]
- 오직 제공된 도구만 사용하십시오.
- "어디서 이 값이 정의되었고 → 어디서 사용되고 → 그 메서드는 어디서 호출되는지"
  근본부터 끝까지 추적하십시오.
- 확신이 없으면 엣지를 생성하지 마십시오.

[도구]
  인덱스 조회:
  - sql_lookup_tool(sql_key): SQL 문 조회.
  - config_lookup_tool(config_key): 설정 엔트리 조회.
  - artifact_lookup_tool(key): ArtifactChunk(entity_xml 등) 조회. key는 chunk_id 또는 hint_key.

  코드 검색:
  - bundle_search_tool(query, kind="symbol"|"text"): 코드 청크 검색. 결과에 chunk_id, filePath, moduleName 포함.
  - bundle_get_chunk_tool(chunk_id): chunk_id로 코드 조각 조회.

  관계 등록:
  - graph_create_refer_to_tool(target_type, target_key, evidence, source_function_id): REFER_TO 엣지 생성. source_function_id 필수.
  - graph_create_table_access_tool(table_name, access, evidence, source_function_id): READS/WRITES 엣지 생성. source_function_id 필수.

  FK 관련:
  - table_search_tool(keyword): 키워드로 테이블 검색 (대소문자/언더스코어 무시). 존재하는 테이블만 반환.
  - table_columns_tool(table_name, table_schema): 테이블의 컬럼/PK 정보 조회.
  - graph_create_fk_tool(from_table, from_column, to_table, to_column, evidence, from_schema, to_schema): FK 관계 등록. 반드시 table_search_tool/table_columns_tool로 양쪽 테이블 존재를 확인한 후 호출.

[종류별 추적 전략]
입력의 category 필드를 보고 아래 전략을 적용하십시오.

== category: "entity" 또는 "bean" 또는 기타 아키텍트 청크 ==
목표: 이 아키텍트 청크(엔티티/서비스/설정 등)를 사용하는 메서드를 찾아 관계 생성.

입력 정보:
  - key: 힌트 이름 (예: "LoanApplicationEntity", "DataSourceConfig" 등)
  - text_snippet: 정의 원문 (XML/설정 등) — 여기에 관련 클래스명, 인터페이스명, 메서드명,
    설정 키 등 소스코드에서 참조될 수 있는 모든 식별자가 포함되어 있습니다.
    **원문을 먼저 읽고** 등장하는 식별자를 파악한 뒤 검색에 활용하십시오.

절차:
  1) text_snippet(원문)에서 등장하는 클래스명, 인터페이스명, 메서드명, 키 등을 모두 파악
  2) 파악된 이름들로 bundle_search(kind="symbol") → 소스코드에서 사용처 추적
  3) 해당 메서드 코드를 열어 사용 패턴 확인
     - 엔티티인 경우: create/save/insert → WRITES, find/get/select → READS
     - 서비스/빈인 경우: 호출 관계 파악
     - 설정인 경우: 참조 관계 파악
  4) 관계 생성:
     - graph_create_refer_to_tool("ArtifactChunk", chunk_id, evidence, source_function_id)
     - 테이블 접근이 확인되면: graph_create_table_access_tool(테이블명, "READS"/"WRITES", evidence, source_function_id)
  주의: 하나의 청크를 여러 메서드가 사용할 수 있습니다. 발견된 모든 메서드에 대해 관계를 생성하십시오.

== category: "sql" (SQL 문 관련) ==
목표: 이 SQL을 실행하는 메서드를 찾아 Method→SqlStatement 관계 생성.
절차:
  1) sql_key나 파일명으로 bundle_search → 해당 SQL ID를 참조하는 코드 검색
  2) context에 related_modules가 있으면 해당 모듈명으로도 검색 (중복 메서드명 구분)
  3) graph_create_refer_to_tool("SqlStatement", sql_key, evidence, source_function_id)

== category: "config" (설정 관련) ==
목표: 이 설정을 읽는 메서드를 찾아 Method→ConfigSection 관계 생성.
절차:
  1) 설정 키나 파일명으로 bundle_search → 해당 설정을 참조하는 코드 검색
  2) config_lookup_tool로 설정 값 확인, 관련 키워드로 추가 검색
  3) graph_create_refer_to_tool("ConfigSection", config_key, evidence, source_function_id)

== FK 식별 (범용) ==
위 추적 과정에서 테이블 간 참조 관계가 보이면 FK도 함께 등록하십시오.
다음 중 하나라도 해당하면 FK 후보입니다:
- 순차 조회: 한 테이블 조회 결과의 필드로 다른 테이블을 조회
- SQL JOIN/서브쿼리: 두 테이블이 조인 조건으로 연결
- 필드 참조: 필드명이 "다른테이블_PK" 형태이거나 관계 어노테이션 존재
- 파라미터 전달: 한 테이블의 컬럼 값이 다른 테이블 접근 함수의 인자로 사용

→ table_search_tool, table_columns_tool로 양쪽 테이블·컬럼 검증 후 graph_create_fk_tool 호출

[출력 지침]
- source_function_id는 반드시 bundle_search/bundle_get_chunk에서 발견한 실제 함수의 chunk_id를 사용하십시오.
- 하나의 인덱스 키에 대해 여러 함수가 사용할 수 있으며, 각각에 대해 관계를 생성하십시오.
- 확신이 있을 때만 엣지를 생성하고, 근거가 부족하면 생성하지 마십시오.
"""

def _build_entity_identification_system_prompt(has_ddl: bool) -> str:
    """시나리오(DDL 유무)에 따라 엔티티 식별 시스템 프롬프트를 동적 생성."""
    base = """당신은 소스코드에서 데이터베이스 엔티티를 식별하고,
각 함수가 어떤 테이블을 읽거나 쓰는지 추적하는 전문가 에이전트입니다.

[목표]
주어진 후보를 분석하여:
1) 엔티티(DB 테이블에 매핑되는 클래스/구조체)를 식별
2) 해당 테이블에 접근하는 모든 함수를 찾아 READS/WRITES 관계 등록
3) 테이블 간 FK 관계 등록

주어진 후보 중 엔티티가 하나도 발견되지 않으면 아무것도 등록하지 말고 즉시 종료하십시오.

[엔티티 판단 기준]
코드를 확인하여 DB 테이블에 매핑되는 클래스/구조체인지 판단하십시오.
엔티티 시그널 (하나 이상 해당하면 엔티티):
- SQL문(INSERT/UPDATE/DELETE/SELECT)의 대상
- ORM/DB 함수의 인자 (save, persist, find, query 등)
- 필드가 DB 컬럼과 1:1 대응 (id, created_at 등)
- 생성 후 DB에 저장되는 패턴

엔티티가 아니라고 판단되면 스킵하고 다음 후보로 이동하십시오.

[도구]
- bundle_search_tool(query, kind="symbol"|"text"): 코드/XML/SQL 통합 검색
- bundle_get_chunk_tool(chunk_id): 코드 조각 조회
- graph_create_table_access_tool(table_name, access, evidence, source_function_id): READS/WRITES 등록
- graph_create_fk_tool(from_table, from_column, to_table, to_column, evidence): FK 등록

[규칙]
- 엔티티 식별과 READS/WRITES는 주어진 후보에 대해서만 수행. 후보에 없는 클래스를 새로 식별하지 말 것
- FK는 양쪽 중 최소 한쪽이 주어진 후보이면 등록 가능. 양쪽 다 후보 밖이면 금지
- 확신이 없으면 등록하지 말 것
- 자기 자신 FK(예: A.id → A.id) 금지"""

    fk_ddl_section = """
- table_search_tool(keyword): DDL 테이블 검색
- table_columns_tool(table_name, table_schema): 컬럼/PK 정보 조회"""

    procedure_ddl = """

[절차 — DDL 있음]
DDL 테이블 목록이 주어집니다. 각 테이블에 대해:
1) 테이블명으로 bundle_search → 이 테이블을 사용하는 함수를 모두 찾기
2) 각 함수의 코드를 bundle_get_chunk로 확인 → READS/WRITES 판별
3) 빠짐없이 graph_create_table_access_tool 호출 (table_name은 DDL에 정의된 이름 그대로)
4) 테이블 간 FK가 보이면 table_columns_tool로 검증 후 graph_create_fk_tool 호출"""

    procedure_candidates = """

[절차 — DDL 없음 (엔티티 후보)]
클래스/구조체 후보 목록이 주어집니다. 각 후보에 대해:

1) 엔티티 판단
   - 후보의 코드를 bundle_search/bundle_get_chunk로 조회
   - 엔티티가 아니면 스킵하고 다음 후보로

2) 엔티티 확정 → 해당 테이블을 사용하는 모든 함수를 끝까지 추적
   a) 해당 클래스명으로 bundle_search → 이 테이블을 사용하는 함수를 전수 탐색
   b) 각 함수의 코드를 bundle_get_chunk로 확인 → READS인지 WRITES인지 판별
   c) 직접 SQL뿐 아니라 간접 접근도 추적:
      - 함수 A → 함수 B → 테이블 T 순서로 호출 체인이 이어지면, A와 B 모두 T에 대한 READS/WRITES로 등록
      - 호출 체인을 끝까지 따라가서 최종적으로 테이블에 닿는 모든 함수를 빠짐없이 등록
      - 예: A()가 B()를 호출, B()가 SELECT → A도 READS, B도 READS
   d) 함수 하나하나에 대해 graph_create_table_access_tool 호출 (빠뜨리지 말 것)
   - table_name: 클래스명 그대로 사용 (예: Book, Member). SQL 테이블명(books, members) 사용 금지. 이름 변환 금지.
   - source_function_id: 해당 함수의 FQN (후보 클래스의 메서드뿐 아니라 서비스 등 외부 함수도 포함)

3) FK 등록
   - 방향: from_table은 FK 컬럼을 가진 쪽, to_table은 참조되는 PK 쪽 (단방향만 등록, 역방향 금지)
   - 양쪽 중 최소 한쪽이 주어진 후보이면 등록
   - 양쪽 다 후보 밖이면 등록 금지
   - 예: 후보=[Order, Product]
     ✅ Order.product_id → Product.id (Order가 FK 보유)
     ❌ Product.id → Order.product_id (역방향 — 등록 금지)

모든 후보를 처리했으면 종료하십시오."""

    efficiency = ""

    parts = [base]
    if has_ddl:
        parts.append(fk_ddl_section)
        parts.append(procedure_ddl)
    else:
        parts.append(procedure_candidates)
    parts.append(efficiency)
    return "\n".join(parts)


# ===========================================================================
# Index Lookup Helpers
# ===========================================================================

def sql_lookup(sql_key: str, sql_index: Dict[str, SqlStatementChunk]) -> Optional[Dict[str, Any]]:
    stmt = sql_index.get(sql_key)
    if not stmt:
        return None
    return {
        "sql_key": stmt.sql_key,
        "sql_type": stmt.sql_type,
        "sql_text": getattr(stmt, "sql_text", ""),
        "file_path": getattr(stmt, "file_path", ""),
        "start_line": getattr(stmt, "start_line", 0),
        "end_line": getattr(stmt, "end_line", 0),
    }


def config_lookup(config_key: str, config_index: Dict[str, ConfigSectionChunk]) -> Optional[Dict[str, Any]]:
    cfg = config_index.get(config_key)
    if not cfg:
        return None
    return {
        "config_key_path": cfg.config_key_path,
        "value": getattr(cfg, "raw_snippet", "") or getattr(cfg, "value", ""),
        "file_path": getattr(cfg, "file_path", ""),
        "start_line": getattr(cfg, "start_line", 0),
        "end_line": getattr(cfg, "end_line", 0),
    }


def build_artifact_lookup_index(artifact_index: Dict[str, ArtifactChunk]) -> Dict[str, str]:
    """hint_key 또는 chunk_id 로 ArtifactChunk 를 찾기 위한 역인덱스."""
    idx: Dict[str, str] = {}
    for cid, ac in (artifact_index or {}).items():
        cid_norm = (cid or "").strip()
        if cid_norm:
            idx.setdefault(cid_norm, cid_norm)
        hint = getattr(ac, "hint_key", None)
        hint_norm = (hint or "").strip()
        if hint_norm:
            idx.setdefault(hint_norm, cid_norm)
    return idx


# ===========================================================================
# Tool Builder
# ===========================================================================

def build_tools(
    state: Dict[str, Any],
    function_id: str,
    refer_collector: List[ReferToEdge],
    table_collector: List[FunctionTableEdge],
    fk_collector: Optional[List] = None,
    variable_id: Optional[str] = None,
) -> List[Any]:
    """LangChain/deepagents 호환 툴 목록 생성. ChunkBundle + 인덱스 + 그래프 조작만 노출."""
    from langchain_core.tools import tool
    from analyzer.strategy.framework.common.utils import sql_op_to_method_relation as _sql_op_to_method_relation

    if fk_collector is None:
        fk_collector = []

    # ── 기존 FK 세트 (DDL 파싱 — 중복 생성 방지) ───────────
    ddl_meta = state.get("ddl_table_metadata") or {}
    existing_fk_set: set = set()
    for (_s, _n), info in ddl_meta.items():
        for fk in (info.get("foreign_keys") or info.get("foreignKeys") or []):
            fk_col = (fk.get("column") or "").strip().upper()
            fk_ref = (fk.get("ref") or "").strip().upper()
            if fk_col and fk_ref:
                existing_fk_set.add((_n.upper(), fk_col, fk_ref))

    # ── state 인덱스 참조 ──────────────────────────────────
    sql_index: Dict[str, SqlStatementChunk] = state.get("sql_index") or {}
    config_index: Dict[str, ConfigSectionChunk] = state.get("config_index") or {}
    artifact_index: Dict[str, ArtifactChunk] = state.get("artifact_index") or {}
    artifact_key_index = build_artifact_lookup_index(artifact_index)
    span_index = state.get("span_index") or {}
    chunk_index = state.get("chunk_index") or {}
    data_dir = state.get("data_dir") or ""

    from analyzer.strategy.framework.parsing.chunk_bundle import (
        bundle_search as _bundle_search,
        bundle_get_chunk as _bundle_get_chunk,
    )

    # ── 내부 헬퍼 ──────────────────────────────────────────

    def _enrich_with_modulename(result: Dict[str, Any]) -> Dict[str, Any]:
        """filePath에서 moduleName 추출하여 결과에 추가."""
        fp = result.get("filePath", "")
        if fp:
            fname = fp.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            name = fname.rsplit(".", 1)[0] if "." in fname else fname
            result["moduleName"] = name
        return result

    # ===== 인덱스 조회 도구 ==========================================

    @tool
    def sql_lookup_tool(sql_key: str) -> str:
        """sql_key로 SQL을 조회한다."""
        logger.info("[FW:DEEPAGENT:TOOL] sql_lookup | key=%s", sql_key)
        r = sql_lookup(sql_key, sql_index)
        logger.info("[FW:DEEPAGENT:TOOL] sql_lookup | found=%s", bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def config_lookup_tool(config_key: str) -> str:
        """config_key로 설정 엔트리를 조회한다."""
        logger.info("[FW:DEEPAGENT:TOOL] config_lookup | key=%s", config_key)
        r = config_lookup(config_key, config_index)
        logger.info("[FW:DEEPAGENT:TOOL] config_lookup | found=%s", bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def artifact_lookup_tool(key: str) -> str:
        """chunk_id 또는 hint_key(ejb-name)로 ArtifactChunk를 조회한다."""
        logger.info("[FW:DEEPAGENT:TOOL] artifact_lookup | key=%s", key)
        cid = artifact_key_index.get((key or "").strip())
        if not cid:
            logger.info("[FW:DEEPAGENT:TOOL] artifact_lookup | found=False")
            return ""
        ac = artifact_index.get(cid)
        if not ac:
            logger.info("[FW:DEEPAGENT:TOOL] artifact_lookup | found=False")
            return ""
        data = {
            "chunk_id": cid,
            "chunk_type": getattr(ac, "chunk_type", ""),
            "hint_key": getattr(ac, "hint_key", ""),
            "file_path": getattr(ac, "source_path", ""),
            "start_line": getattr(ac, "start_line", 0),
            "end_line": getattr(ac, "end_line", 0),
            "text": getattr(ac, "text", "")[:2000],
        }
        logger.info("[FW:DEEPAGENT:TOOL] artifact_lookup | found=True chunk_id=%s", cid)
        return json.dumps(data, ensure_ascii=False)

    # ===== 코드 검색 도구 ==========================================

    @tool
    def bundle_search_tool(query: str, kind: str = "symbol") -> str:
        """ChunkBundle에서 심볼/텍스트 검색. kind: symbol|text."""
        logger.info("[FW:DEEPAGENT:TOOL] bundle_search | query=%s kind=%s", query, kind)
        results = _bundle_search(data_dir, query, kind, span_index=span_index, chunk_index=chunk_index)
        enriched = [_enrich_with_modulename(r) for r in results[:20]]
        logger.info("[FW:DEEPAGENT:TOOL] bundle_search | results=%d", len(enriched))
        return json.dumps(enriched, ensure_ascii=False)

    @tool
    def bundle_get_chunk_tool(chunk_id: str) -> str:
        """chunk_id로 코드 조각 조회."""
        logger.info("[FW:DEEPAGENT:TOOL] bundle_get_chunk | chunk_id=%s", chunk_id)
        r = _bundle_get_chunk(chunk_id, chunk_index=chunk_index)
        if r:
            _enrich_with_modulename(r)
        logger.info("[FW:DEEPAGENT:TOOL] bundle_get_chunk | found=%s", bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    # ===== 관계 등록 도구 ==========================================

    @tool
    def graph_create_refer_to_tool(target_type: str, target_key: str, evidence: str = "", source_function_id: str = "") -> str:
        """REFER_TO 엣지 생성. source_function_id 비워두면 기본 function_id 사용."""
        mid = source_function_id.strip() if source_function_id else function_id
        logger.info("[FW:DEEPAGENT:TOOL] create_refer_to | target_type=%s target_key=%s source=%s", target_type, target_key, mid)
        if not mid:
            return "error: source_function_id required (no default function_id)"
        refer_collector.append(
            ReferToEdge(
                function_id=mid,
                target_type=target_type,
                target_id=target_key,
                evidence={"note": evidence} if evidence else {},
                confidence=0.9,
                variable_id=variable_id,
            )
        )
        return "ok"

    @tool
    def graph_create_table_access_tool(table_name: str, access: str, evidence: str = "", source_function_id: str = "") -> str:
        """READS/WRITES 엣지 생성. source_function_id 비워두면 기본 function_id 사용."""
        mid = source_function_id.strip() if source_function_id else function_id
        rel = access.upper() if access else "READS"
        if rel not in ("READS", "WRITES"):
            rel = "READS"
        logger.info("[FW:DEEPAGENT:TOOL] create_table_access | table=%s access=%s source=%s", table_name, rel, mid)
        if not mid:
            return "error: source_function_id required (no default function_id)"
        table_collector.append(
            FunctionTableEdge(
                function_id=mid,
                table_schema="",
                table_name=table_name,
                relation=_sql_op_to_method_relation("INSERT" if rel == "WRITES" else "SELECT"),
                variable_id=variable_id,
            )
        )
        return "ok"

    # ===== FK 관련 도구 ============================================

    @tool
    def table_search_tool(keyword: str) -> str:
        """테이블명을 키워드로 검색. 대소문자/언더스코어 무시."""
        logger.info("[FW:DEEPAGENT:TOOL] table_search | keyword=%s", keyword)
        keyword_lower = keyword.lower().replace("_", "")
        results = []
        for (schema, name) in ddl_meta:
            name_lower = name.lower().replace("_", "")
            if keyword_lower in name_lower:
                results.append({"schema": schema, "name": name})
        logger.info("[FW:DEEPAGENT:TOOL] table_search | results=%d", len(results))
        if not results:
            return "no matching tables found"
        return json.dumps(results[:20], ensure_ascii=False)

    @tool
    def table_columns_tool(table_name: str, table_schema: str = "") -> str:
        """테이블의 컬럼 목록과 PK 정보 조회."""
        logger.info("[FW:DEEPAGENT:TOOL] table_columns | table=%s schema=%s", table_name, table_schema)
        for (s, n), info in ddl_meta.items():
            if n.lower() == table_name.lower() and (not table_schema or s.lower() == table_schema.lower()):
                columns = info.get("columns") or []
                pks = info.get("primary_keys") or []
                col_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in columns]
                logger.info("[FW:DEEPAGENT:TOOL] table_columns | found=True columns=%d", len(col_names))
                return json.dumps({"schema": s, "name": n, "primary_keys": pks, "columns": col_names}, ensure_ascii=False)
        logger.info("[FW:DEEPAGENT:TOOL] table_columns | found=False")
        return f"table '{table_name}' not found"

    @tool
    def graph_create_fk_tool(from_table: str, from_column: str, to_table: str, to_column: str, evidence: str = "", from_schema: str = "", to_schema: str = "") -> str:
        """FK 관계 등록. 반드시 table_search/table_columns로 양쪽 테이블 확인 후 호출."""
        logger.info("[FW:DEEPAGENT:TOOL] create_fk | %s.%s → %s.%s", from_table, from_column, to_table, to_column)
        if from_table.upper() == to_table.upper():
            logger.info("[FW:DEEPAGENT:TOOL] create_fk | result=self_reference_skipped")
            return "error: self-referencing FK not allowed"
        ref_key = f"{to_table}.{to_column}".upper()
        check_key = (from_table.upper(), from_column.upper(), ref_key)
        if check_key in existing_fk_set:
            logger.info("[FW:DEEPAGENT:TOOL] create_fk | result=duplicate_skipped")
            return "already exists (from DDL)"
        fk_collector.append(FkEdge(
            from_schema=from_schema, from_table=from_table, from_column=from_column,
            to_schema=to_schema, to_table=to_table, to_column=to_column,
            candidate=True,
        ))
        logger.info("[FW:DEEPAGENT:TOOL] create_fk | result=ok")
        return "ok"

    # ── 도구 목록 구성 ──
    tools = [
        sql_lookup_tool, config_lookup_tool, artifact_lookup_tool,   # 인덱스 조회
        bundle_search_tool, bundle_get_chunk_tool,                   # 코드 검색
        graph_create_refer_to_tool, graph_create_table_access_tool,  # 관계 등록
        graph_create_fk_tool,                                        # FK 등록
    ]
    if ddl_meta:
        tools.extend([table_search_tool, table_columns_tool])        # DDL 있을 때만 테이블 검증 도구
    return tools


# ===========================================================================
# Bundle Index Helper
# ===========================================================================

def _ensure_bundle_indexes(state: Dict[str, Any]) -> None:
    """state 에 span_index / chunk_index 가 없으면 ChunkBundle 로부터 빌드하여 채운다."""
    if state.get("span_index") and state.get("chunk_index"):
        return
    from analyzer.strategy.framework.parsing.chunk_bundle import (
        build_bundle_span_index,
        _get_chunk_bundle_dir_from_run_dir,
    )
    run_dir = state.get("run_dir")
    data_dir = state.get("data_dir") or ""
    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir) if run_dir else None
    span_index, chunk_index = build_bundle_span_index(
        data_dir,
        chunk_bundle_dir=chunk_dir,
        artifact_index=state.get("artifact_index"),
        sql_index=state.get("sql_index"),
    )
    state["span_index"] = span_index
    state["chunk_index"] = chunk_index
    logger.info("[FW:DEEPAGENT] bundle index 빌드 완료 | span=%d chunk=%d", len(span_index), len(chunk_index))


# ===========================================================================
# Unmatched Key User Message Builder
# ===========================================================================

def _build_unmatched_user_message(u: UnresolvedLink) -> str:
    """미매칭 인덱스 키에 대한 종류별 user message 생성."""
    info = u.ref_candidates[0] if u.ref_candidates else {}
    category = info.get("category", "")
    key = info.get("key", "")
    target_type = info.get("target_type", "")

    header = f"미매칭 인덱스 키 (category: {category})\n"
    header += f"target_type: {target_type}\nkey: {key}\n"

    if category in ("entity", "bean", "cmp_mapping"):
        header += (
            f"chunk_type: {info.get('chunk_type', '')}\n"
            f"file_path: {info.get('file_path', '')}\n"
            f"[정의 원문]\n{info.get('text_snippet', '')}\n\n"
            f"위 원문에서 클래스명, 인터페이스명, 메서드명 등 식별자를 먼저 파악한 뒤,\n"
            f"이 청크를 사용하는 메서드를 찾으십시오.\n"
            f"1) 원문에 등장하는 식별자로 bundle_search → 소스코드 사용처 추적\n"
            f"2) 발견된 각 메서드의 사용 패턴 판별 (CRUD, 호출, 참조 등)\n"
            f"3) graph_create_refer_to_tool 생성 (source_function_id 필수)\n"
            f"4) 테이블 접근이 확인되면 graph_create_table_access_tool도 생성\n"
        )
    elif category == "sql":
        header += (
            f"sql_type: {info.get('sql_type', '')}\n"
            f"file_path: {info.get('file_path', '')}\n\n"
            f"이 SQL을 실행하는 메서드를 찾으십시오.\n"
            f"1) sql_key '{key}' 또는 파일명/네임스페이스로 bundle_search\n"
            f"2) 발견된 메서드에 대해 graph_create_refer_to_tool 생성 (source_function_id 필수)\n"
        )
    elif category == "config":
        header += (
            f"file_path: {info.get('file_path', '')}\n\n"
            f"이 설정을 참조하는 메서드를 찾으십시오.\n"
            f"1) 설정 키 '{key}' 또는 파일명으로 bundle_search\n"
            f"2) 발견된 메서드에 대해 graph_create_refer_to_tool 생성 (source_function_id 필수)\n"
        )
    else:
        header += f"\ninfo: {json.dumps(info, ensure_ascii=False, default=str)}\n"

    return header


# ===========================================================================
# run_deep_agent — 미매칭 인덱스 키 처리
# ===========================================================================

def run_deep_agent(
    unresolved_list: List[UnresolvedLink],
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Generator[Tuple[List[ReferToEdge], List[FunctionTableEdge], List[FkEdge]], None, None]:
    """unresolved 목록을 DeepAgent 로 처리. 항목별로 (refer_to, function_table, fk_edges) yield."""
    logger.info("[FW:DEEPAGENT] 진입 | unresolved=%d api_key_set=%s run_dir=%s", len(unresolved_list), bool(api_key), run_dir)
    if not unresolved_list:
        logger.info("[FW:DEEPAGENT] unresolved 없음 → 즉시 종료")
        return

    verbose = os.environ.get("FW_DEEPAGENT_VERBOSE", "true").strip().lower() == "true"

    try:
        from deepagents import create_deep_agent
        from client.llm_client import get_llm
    except Exception as e:
        logger.warning("[FW:DEEPAGENT] deepagents 미설치 또는 LLM 클라이언트 없음: %s", e)
        return

    if run_dir:
        state.setdefault("run_dir", str(run_dir))
    _ensure_bundle_indexes(state)

    model = get_llm(api_key=api_key) if api_key else get_llm()
    total = len(unresolved_list)
    max_workers = min(total, settings.concurrency.max_concurrency)

    def _process_one(idx: int, u: UnresolvedLink) -> Tuple[List[ReferToEdge], List[FunctionTableEdge], List[FkEdge]]:
        has_method = bool(u.function_id and u.function_id.strip())
        label = u.function_id if has_method else "(unmatched key)"
        logger.info("[FW:DEEPAGENT] LLM 처리 (%d/%d) %s", idx, total, label)

        if verbose and u.ref_candidates:
            parts: List[str] = []
            for c in (u.ref_candidates or [])[:5]:
                if isinstance(c, dict):
                    parts.append(f"{c.get('target_type', c.get('kind', '?'))}={c.get('key', c.get('value', ''))}")
                else:
                    parts.append(f"{getattr(c, 'kind', '?')}={getattr(c, 'value', '')}")
            logger.info("[FW:DEEPAGENT] 후보 요약 | %s candidates=%s", label, "; ".join(parts))

        refer_collector: List[ReferToEdge] = []
        table_collector: List[FunctionTableEdge] = []
        fk_collector: List[FkEdge] = []
        tools = build_tools(state, u.function_id or "", refer_collector, table_collector, variable_id=u.variable_id, fk_collector=fk_collector)

        agent = create_deep_agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)

        if has_method:
            user_content = f"function_id: {u.function_id}\nref_candidates: {json.dumps(u.ref_candidates, ensure_ascii=False, default=str)}"
        else:
            user_content = _build_unmatched_user_message(u)

        try:
            agent.invoke({"messages": [{"role": "user", "content": user_content}]})
            log_llm_interaction(
                prompt_name="framework/deep_agent",
                input_payload=user_content,
                output_payload={
                    "refer_to": len(refer_collector),
                    "table_access": len(table_collector),
                    "fk_edges": len(fk_collector),
                },
                metadata={"function_id": u.function_id or "", "idx": idx, "total": total},
            )
            if verbose and (refer_collector or table_collector or fk_collector):
                logger.info(
                    "[FW:DEEPAGENT] 결과 | %s new_refer=%d new_mt=%d new_fk=%d",
                    label, len(refer_collector), len(table_collector), len(fk_collector),
                )
        except Exception as ex:
            logger.warning("[FW:DEEPAGENT] invoke 실패 %s error=%s", label, ex)

        return refer_collector, table_collector, fk_collector

    total_refer = 0
    total_mt = 0
    total_fk = 0
    logger.info("[FW:DEEPAGENT] 병렬 처리 시작 | items=%d workers=%d", total, max_workers)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_one, idx, u): idx
            for idx, u in enumerate(unresolved_list, start=1)
        }
        for future in as_completed(futures):
            try:
                refer_result, mt_result, fk_result = future.result()
                if refer_result or mt_result or fk_result:
                    total_refer += len(refer_result)
                    total_mt += len(mt_result)
                    total_fk += len(fk_result)
                    yield (refer_result, mt_result, fk_result)
            except Exception as ex:
                logger.warning("[FW:DEEPAGENT] 워커 예외: %s", ex)

    logger.info("[FW:DEEPAGENT] 종료 | new_refer=%d new_mt=%d new_fk=%d", total_refer, total_mt, total_fk)


# ===========================================================================
# run_deep_agent_entity_identification — 엔티티 식별 + Function→Table 연결
# ===========================================================================

# 디렉토리명 사전 분류용 상수
_ENTITY_DIR_HINTS = frozenset({"entity", "entities", "model", "models", "domain", "schema"})
_DTO_DIR_HINTS = frozenset({"dto", "vo", "request", "response", "view"})


def _classify_by_path(file_path: str) -> str:
    """파일 경로의 디렉토리명으로 entity/dto/unknown 사전 분류."""
    parts_lower = {p.lower() for p in Path(file_path).parts} if file_path else set()
    if parts_lower & _ENTITY_DIR_HINTS:
        return "entity"
    if parts_lower & _DTO_DIR_HINTS:
        return "dto"
    return "unknown"


def _build_entity_id_user_message(
    candidates: Optional[List[Dict[str, str]]] = None,
    ddl_table_metadata: Optional[Dict] = None,
) -> str:
    """시나리오별 DeepAgent user message 구성.

    - DDL 있음: 테이블 이름 + FK 목록만 전달 (관계 추적용)
    - DDL 없음: 구조체/클래스 후보 전달 (엔티티 판별 + 관계 추적)
    """
    parts: List[str] = []

    if ddl_table_metadata:
        # ── DDL 있음 → 테이블 이름 + 기존 FK 목록
        fk_lines: List[str] = []
        for (_schema, _name), meta in ddl_table_metadata.items():
            orig = meta.get("original_name", _name)
            for fk in (meta.get("foreign_keys") or meta.get("foreignKeys") or []):
                fk_col = fk.get("column", "")
                fk_ref = fk.get("ref", "")
                if fk_col and fk_ref:
                    fk_lines.append(f"- {orig}.{fk_col} → {fk_ref}")

        if fk_lines:
            if len(fk_lines) <= 50:
                parts.append("[이미 식별된 FK — 중복 생성하지 마세요]\n" + "\n".join(fk_lines))
            else:
                parts.append(f"이미 식별된 FK {len(fk_lines)}개 존재. 중복 생성하지 마세요.")

        table_names = [meta.get("original_name", _name) for (_schema, _name), meta in ddl_table_metadata.items()]
        if len(table_names) <= 100:
            parts.append("[테이블 이름] " + ", ".join(table_names))
        else:
            parts.append(f"프로젝트에 테이블 {len(table_names)}개 존재. table_search_tool로 검색하세요.")

        parts.append(
            "위 테이블을 코드에서 사용하는 메서드를 추적하세요. 컬럼 상세는 table_columns_tool로 조회하세요.\n"
            "이미 식별된 FK 이외의 새로운 FK 관계가 코드에서 발견되면 graph_create_fk_tool로 등록하세요."
        )

    elif candidates:
        # ── DDL 없음 → 구조체/클래스 후보로 엔티티 판별 + 추적
        cand_lines = []
        for c in candidates:
            line = f"- {c['fqn']} (file: {c['file_path']})"
            if c.get("annotations"):
                line += f" [annotations: {c['annotations']}]"
            if c.get("comment"):
                line += f" [comment: {c['comment']}]"
            cand_lines.append(line)
        parts.append("[클래스/구조체 후보]\n" + "\n".join(cand_lines))
        parts.append(
            "\n위 후보에서 DB 엔티티를 식별하고, 사용하는 함수를 추적하세요."
        )

    return "\n".join(parts)


def run_deep_agent_entity_identification(
    module_index: Dict[str, Any],
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
    ddl_table_metadata: Optional[Dict] = None,
) -> Tuple[List[FunctionTableEdge], List[FkEdge]]:
    """DeepAgent로 엔티티 식별 + Function→Table 연결.

    시나리오별 동작:
    - DDL만 있음: 테이블명을 컨텍스트로 전달, 코드에서 관련 메서드 추적
    - 아무것도 없음: 클래스/구조체 후보를 전달, 엔티티 식별 + 메서드 추적
    - DDL + 후보: 테이블 컨텍스트 + 후보 목록 모두 전달

    Returns:
        (Function→Table 접근 엣지 목록, FK 엣지 목록)
    """
    has_ddl = bool(ddl_table_metadata)
    has_candidates = bool(module_index)

    if not has_ddl and not has_candidates:
        logger.info("[FW:DEEPAGENT:ENTITY] DDL도 후보도 없음 → 즉시 종료")
        return [], []

    try:
        from deepagents import create_deep_agent
        from client.llm_client import get_llm
    except Exception as e:
        logger.warning("[FW:DEEPAGENT:ENTITY] deepagents 미설치 또는 LLM 클라이언트 없음: %s", e)
        return [], []

    if run_dir:
        state.setdefault("run_dir", str(run_dir))
    _ensure_bundle_indexes(state)

    model = get_llm(api_key=api_key) if api_key else get_llm()

    # ── 후보 수집 (CLASS/STRUCT/UNION만 엔티티 후보. dto 디렉토리 스킵) ──
    _ENTITY_CANDIDATE_KINDS = frozenset({"CLASS", "STRUCT", "UNION"})
    candidates: List[Dict[str, str]] = []
    for fqn, info in (module_index or {}).items():
        if not isinstance(info, dict):
            continue
        if info.get("kind") not in _ENTITY_CANDIDATE_KINDS:
            continue
        name = info.get("name", "")
        fp = info.get("file_path", "")
        path_class = _classify_by_path(fp)
        if path_class == "dto":
            continue
        candidates.append({
            "fqn": fqn,
            "name": name,
            "file_path": fp,
            "annotations": (info.get("annotations") or "")[:100],
            "comment": (info.get("comment") or "")[:100],
            "path_hint": path_class,
        })

    candidates.sort(key=lambda c: (0 if c.get("path_hint") == "entity" else 1))  # entity 힌트 우선

    if not candidates and not has_ddl:
        logger.info("[FW:DEEPAGENT:ENTITY] 후보 없음 + DDL 없음 → 즉시 종료")
        return [], []

    scenario = "ddl_tables" if has_ddl else "candidates"
    logger.info(
        "[FW:DEEPAGENT:ENTITY] 시작 | scenario=%s ddl_tables=%d candidates=%d",
        scenario, len(ddl_table_metadata or {}), len(candidates),
    )

    table_collector: List[FunctionTableEdge] = []
    refer_collector: List[ReferToEdge] = []
    fk_collector: List[FkEdge] = []
    tools = build_tools(state, "", refer_collector, table_collector, fk_collector=fk_collector)

    agent = create_deep_agent(
        model=model, tools=tools,
        system_prompt=_build_entity_identification_system_prompt(has_ddl),
    )

    _BATCH_SIZE = 10

    if has_ddl:
        # ── 시나리오 A: DDL 있음 → 테이블명 + FK 목록으로 코드 추적
        logger.info("[FW:DEEPAGENT:ENTITY] scenario=ddl_tables | 테이블 %d개로 코드 추적", len(ddl_table_metadata or {}))
        user_msg = _build_entity_id_user_message(ddl_table_metadata=ddl_table_metadata)
        try:
            agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
            log_llm_interaction(
                prompt_name="framework/deep_agent_entity_identification",
                input_payload=user_msg,
                output_payload={"table_access": len(table_collector)},
                metadata={"scenario": "ddl_with_tables"},
            )
        except Exception as ex:
            logger.warning("[FW:DEEPAGENT:ENTITY] invoke 실패 (ddl): %s", ex)
    else:
        # ── 시나리오 B: DDL 없음 → 후보 배치 병렬 엔티티 식별
        batches = [candidates[i:i + _BATCH_SIZE] for i in range(0, max(len(candidates), 1), _BATCH_SIZE)]
        logger.info("[FW:DEEPAGENT:ENTITY] scenario=candidates | 후보 %d개 → %d배치 병렬 식별", len(candidates), len(batches))

        def _process_batch(batch_idx: int, batch: list) -> None:
            batch_names = [c.get("name", "?") for c in batch]
            logger.info("[FW:DEEPAGENT:ENTITY] 배치 %d 시작 | 후보=%s", batch_idx, batch_names)
            before_count = len(table_collector)
            batch_agent = create_deep_agent(
                model=model, tools=tools,
                system_prompt=_build_entity_identification_system_prompt(has_ddl),
            )
            user_msg = _build_entity_id_user_message(candidates=batch)
            try:
                batch_agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
                log_llm_interaction(
                    prompt_name="framework/deep_agent_entity_identification",
                    input_payload=user_msg,
                    output_payload={"table_access": len(table_collector)},
                    metadata={"batch_start": batch_idx * _BATCH_SIZE, "batch_size": len(batch)},
                )
            except Exception as ex:
                logger.warning("[FW:DEEPAGENT:ENTITY] invoke 실패 batch=%d: %s", batch_idx, ex)
            added = len(table_collector) - before_count
            if added > 0:
                logger.info("[FW:DEEPAGENT:ENTITY] 배치 %d 완료 | READS/WRITES +%d건", batch_idx, added)
            else:
                logger.info("[FW:DEEPAGENT:ENTITY] 배치 %d 완료 | 엔티티 식별 안 됨 (후보: %s)", batch_idx, batch_names)

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(len(batches), 4)) as executor:
            futures = {executor.submit(_process_batch, idx, b): idx for idx, b in enumerate(batches)}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as ex:
                    logger.warning("[FW:DEEPAGENT:ENTITY] 배치 스레드 예외: %s", ex)

    # 결과 요약 로그
    identified_tables = {e.table_name for e in table_collector if e.table_name}
    if identified_tables:
        logger.info(
            "[FW:DEEPAGENT:ENTITY] 완료 | 식별된 엔티티=%d (%s) | READS/WRITES=%d FK=%d",
            len(identified_tables), ", ".join(sorted(identified_tables)),
            len(table_collector), len(fk_collector),
        )
    else:
        logger.info("[FW:DEEPAGENT:ENTITY] 완료 | 엔티티 식별 안 됨 — 후보 %d개 중 엔티티 0개", len(candidates))
    return table_collector, fk_collector


# ===========================================================================
# 변수 참조 탐색 (Framework 전용)
# ===========================================================================

def run_deep_agent_variable_references(
    function_facts_list: list,
    variable_facts_list: list,
    api_key: str = "",
) -> List[Tuple[str, str]]:
    """함수→변수/상수 참조 관계를 LLM으로 탐색.

    Args:
        function_facts_list: FunctionFacts 리스트 (코드 포함)
        variable_facts_list: VariableFacts 리스트 (VARIABLE + CONSTANT)
        api_key: LLM API 키

    Returns:
        [(function_id, variable_id)] 쌍 리스트
    """
    if not api_key or not function_facts_list or not variable_facts_list:
        return []

    from util.prompt_executor import get_rule_loader
    from concurrent.futures import ThreadPoolExecutor, as_completed

    loader = get_rule_loader("common")

    # 변수 목록 문자열 생성 (variable_id + name + code_text)
    var_entries = []
    for v in variable_facts_list:
        code = (getattr(v, "code", "") or "").strip()
        # 라인번호 제거
        if code and ":" in code.split("\n")[0]:
            parts = code.split("\n")
            code = "\n".join(line.split(":", 1)[1].strip() if ":" in line else line for line in parts)
        entry = f"{v.variable_id} | {v.name} | {code}" if code else f"{v.variable_id} | {v.name}"
        var_entries.append(entry)

    if not var_entries:
        return []

    variables_text = "\n".join(var_entries)
    max_workers = max(1, settings.concurrency.max_concurrency)
    batch_size = 20
    all_refs: List[Tuple[str, str]] = []

    def _process_one(func) -> List[Tuple[str, str]]:
        code = (getattr(func, "code", "") or "").strip()
        if not code:
            return []
        try:
            result = loader.execute("variable_reference", {
                "code": code,
                "function_name": func.fqn or func.function_id,
                "variables": variables_text,
            }, api_key=api_key)
            referenced = result.get("referenced", []) if isinstance(result, dict) else []
            return [(func.function_id, vid) for vid in referenced if vid]
        except Exception as e:
            logger.debug("[DEEPAGENT:VAR] 변수 참조 탐색 실패 (%s): %s", func.function_id, e)
            return []

    # 배치별 병렬 실행
    for i in range(0, len(function_facts_list), batch_size):
        batch = function_facts_list[i:i + batch_size]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_process_one, f): f for f in batch}
            for future in as_completed(futures):
                refs = future.result()
                all_refs.extend(refs)

    logger.info("[DEEPAGENT:VAR] 변수 참조 탐색 완료 | functions=%d refs=%d", len(function_facts_list), len(all_refs))
    return all_refs
