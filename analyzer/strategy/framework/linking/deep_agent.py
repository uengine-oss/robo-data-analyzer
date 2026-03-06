from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from util.artifact_logger import log_llm_interaction
from analyzer.strategy.framework.models import (
    MethodFacts,
    MethodTableEdge,
    ReferToEdge,
    UnresolvedLink,
    ArtifactChunk,
    SqlStatementChunk,
    ConfigSectionChunk,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
당신은 레거시 엔터프라이즈 시스템에서 미매칭된 인덱스 키를 추적해
코드 내 실제 사용처를 찾아 그래프 관계를 생성하는 전문가 에이전트입니다.

[절대 규칙]
- 오직 제공된 도구만 사용하십시오.
- "어디서 이 값이 정의되었고 → 어디서 사용되고 → 그 메서드는 어디서 호출되는지"
  근본부터 끝까지 추적하십시오.
- 확신이 없으면 엣지를 생성하지 마십시오.

[도구]
- bundle_search_tool(query, kind="symbol"|"text"): 코드 청크 검색. 결과에 chunk_id, filePath, className 포함.
- bundle_get_chunk_tool(chunk_id): chunk_id로 코드 조각 조회.
- sql_lookup_tool(sql_key): SQL 문 조회.
- config_lookup_tool(config_key): 설정 엔트리 조회.
- artifact_lookup_tool(key): ArtifactChunk(entity_xml 등) 조회. key는 chunk_id 또는 hint_key.
- graph_create_refer_to_tool(target_type, target_key, evidence, source_method_id): REFER_TO 엣지 생성. source_method_id 필수.
- graph_create_table_access_tool(table_name, access, evidence, source_method_id): READS/WRITES 엣지 생성. source_method_id 필수.

[종류별 추적 전략]
입력의 category 필드를 보고 아래 전략을 적용하십시오.

== category: "entity" (EJB 엔티티 / 테이블 관련) ==
목표: 이 엔티티를 CRUD하는 메서드를 모두 찾아 Method→ArtifactChunk, Method→Table 관계 생성.
절차:
  1) 엔티티 이름(key)으로 bundle_search(kind="symbol") → 관련 상수 정의 찾기
     예: "LoanApplicationEntity" 검색 → JNDI_LOAN_APPLICATION_ENTITY 상수 발견
  2) bundle_get_chunk로 상수의 실제 값 확인
     예: JNDI_LOAN_APPLICATION_ENTITY = "java:comp/env/ejb/LoanApplicationEntity"
  3) 그 상수명으로 다시 bundle_search(kind="symbol") → 사용하는 메서드 코드 발견
     예: ServiceLocator.getInstance().getLocalHome(LoanConstants.JNDI_LOAN_APPLICATION_ENTITY)
  4) bundle_get_chunk로 해당 메서드 코드를 열어 CRUD 패턴 확인
     - create(), remove() → WRITES
     - findByPrimaryKey(), findAll(), findByXxx() → READS
  5) artifact_lookup_tool(엔티티명)로 entity_xml 정의를 확인, 테이블명 파악
     (abstract-schema-name 에서 CamelCase → SCREAMING_SNAKE_CASE 관례)
  6) 관계 생성:
     - graph_create_refer_to_tool("ArtifactChunk", chunk_id, evidence, source_method_id=발견한 메서드 ID)
     - graph_create_table_access_tool(테이블명, "READS" 또는 "WRITES", evidence, source_method_id=발견한 메서드 ID)
  주의: 하나의 엔티티를 여러 메서드가 사용할 수 있습니다. 발견된 모든 메서드에 대해 관계를 생성하십시오.

== category: "bean" (EJB 세션 빈 관련) ==
목표: 이 세션 빈을 호출하는 메서드를 찾아 Method→ArtifactChunk 관계 생성.
절차:
  1) 빈 이름(key)으로 bundle_search(kind="symbol") → JNDI 상수나 Home 인터페이스 사용처 검색
  2) 상수 값 확인 → 사용하는 메서드 추적 (entity와 동일 패턴)
  3) graph_create_refer_to_tool("ArtifactChunk", chunk_id, evidence, source_method_id=발견한 메서드 ID)

== category: "sql" (SQL 문 관련) ==
목표: 이 SQL을 실행하는 메서드를 찾아 Method→SqlStatement 관계 생성.
절차:
  1) sql_key나 파일명으로 bundle_search → 해당 SQL ID를 참조하는 코드 검색
  2) 매퍼 인터페이스명, namespace 등으로 추가 검색
  3) graph_create_refer_to_tool("SqlStatement", sql_key, evidence, source_method_id=발견한 메서드 ID)

== category: "config" (설정 관련) ==
목표: 이 설정을 읽는 메서드를 찾아 Method→ConfigSection 관계 생성.
절차:
  1) 설정 키나 파일명으로 bundle_search → 해당 설정을 참조하는 코드 검색
  2) config_lookup_tool로 설정 값 확인, 관련 키워드로 추가 검색
  3) graph_create_refer_to_tool("ConfigSection", config_key, evidence, source_method_id=발견한 메서드 ID)

[출력 지침]
- source_method_id는 반드시 bundle_search/bundle_get_chunk에서 발견한 실제 메서드의 chunk_id를 사용하십시오.
- 하나의 인덱스 키에 대해 여러 메서드가 사용할 수 있으며, 각각에 대해 관계를 생성하십시오.
- 확신이 있을 때만 엣지를 생성하고, 근거가 부족하면 생성하지 마십시오.
"""


def sql_lookup(sql_key: str, sql_index: Dict[str, SqlStatementChunk]) -> Optional[Dict[str, Any]]:
    stmt = sql_index.get(sql_key)
    if not stmt:
        return None
    return {
        "sql_key": stmt.sql_key,
        "sql_type": stmt.sql_type,
        "sql_text": getattr(stmt, "sql_text", "") or "",
        "file_path": getattr(stmt, "file_path", "") or "",
        "start_line": getattr(stmt, "start_line", 0),
        "end_line": getattr(stmt, "end_line", 0),
    }


def config_lookup(config_key: str, config_index: Dict[str, ConfigSectionChunk]) -> Optional[Dict[str, Any]]:
    cfg = config_index.get(config_key)
    if not cfg:
        return None
    return {
        "config_key_path": cfg.config_key_path,
        "value": getattr(cfg, "raw_snippet", "") or getattr(cfg, "value", "") or "",
        "file_path": getattr(cfg, "file_path", "") or "",
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


def build_tools(
    state: Dict[str, Any],
    method_id: str,
    refer_collector: List[ReferToEdge],
    table_collector: List[MethodTableEdge],
    field_id: Optional[str] = None,
) -> List[Any]:
    """LangChain/deepagents 호환 툴 목록 생성. ChunkBundle + 인덱스 + 그래프 조작만 노출."""
    from langchain_core.tools import tool
    from analyzer.strategy.framework.common.utils import sql_op_to_method_relation as _sql_op_to_method_relation

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

    @tool
    def sql_lookup_tool(sql_key: str) -> str:
        """sql_key 로 SQL 을 조회한다. 결과는 JSON 문자열이다."""
        r = sql_lookup(sql_key, sql_index)
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def config_lookup_tool(config_key: str) -> str:
        """config_key 로 설정 엔트리를 조회한다. 결과는 JSON 문자열이다."""
        r = config_lookup(config_key, config_index)
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def artifact_lookup_tool(key: str) -> str:
        """chunk_id 또는 hint_key(ejb-name) 로 ArtifactChunk 를 조회한다. 결과는 JSON 문자열이다."""
        cid = artifact_key_index.get((key or "").strip())
        if not cid:
            return ""
        ac = artifact_index.get(cid)
        if not ac:
            return ""
        data = {
            "chunk_id": cid,
            "chunk_type": getattr(ac, "chunk_type", ""),
            "hint_key": getattr(ac, "hint_key", ""),
            "file_path": getattr(ac, "source_path", "") or "",
            "start_line": getattr(ac, "start_line", 0),
            "end_line": getattr(ac, "end_line", 0),
            "text": (getattr(ac, "text", "") or "")[:2000],
        }
        return json.dumps(data, ensure_ascii=False)

    def _enrich_with_classname(result: Dict[str, Any]) -> Dict[str, Any]:
        """filePath에서 className 추출하여 결과에 추가."""
        fp = result.get("filePath", "")
        if fp:
            fname = fp.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            name = fname.rsplit(".", 1)[0] if "." in fname else fname
            result["className"] = name
        return result

    @tool
    def bundle_search_tool(query: str, kind: str = "symbol") -> str:
        """ChunkBundle에서 심볼/텍스트를 검색. kind: symbol|text. 결과에 chunk_id, filePath, className 포함."""
        results = _bundle_search(data_dir, query, kind, span_index=span_index, chunk_index=chunk_index)
        return json.dumps([_enrich_with_classname(r) for r in results[:20]], ensure_ascii=False)

    @tool
    def bundle_get_chunk_tool(chunk_id: str) -> str:
        """chunk_id로 코드 조각을 조회. 결과에 text, filePath, className, span 포함."""
        r = _bundle_get_chunk(chunk_id, chunk_index=chunk_index)
        if r:
            _enrich_with_classname(r)
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def graph_create_refer_to_tool(target_type: str, target_key: str, evidence: str = "", source_method_id: str = "") -> str:
        """Method/Field → (SqlStatement|ConfigSection|ArtifactChunk|Class) REFER_TO 엣지를 생성한다.
        source_method_id: 미매칭 인덱스 키 탐색 시 발견한 메서드 ID. 비워두면 기본 method_id 사용."""
        mid = source_method_id.strip() if source_method_id else method_id
        if not mid:
            return "error: source_method_id required (no default method_id)"
        refer_collector.append(
            ReferToEdge(
                method_id=mid,
                target_type=target_type,
                target_id=target_key,
                evidence={"note": evidence} if evidence else {},
                confidence=0.9,
                field_id=field_id,
            )
        )
        return "ok"

    @tool
    def graph_create_table_access_tool(table_name: str, access: str, evidence: str = "", source_method_id: str = "") -> str:
        """Method/Field → Table READS/WRITES 엣지를 생성한다.
        source_method_id: 미매칭 인덱스 키 탐색 시 발견한 메서드 ID. 비워두면 기본 method_id 사용."""
        mid = source_method_id.strip() if source_method_id else method_id
        if not mid:
            return "error: source_method_id required (no default method_id)"
        rel = access.upper() if access else "READS"
        if rel not in ("READS", "WRITES"):
            rel = "READS"
        table_collector.append(
            MethodTableEdge(
                method_id=mid,
                table_schema="",
                table_name=table_name,
                relation=_sql_op_to_method_relation("INSERT" if rel == "WRITES" else "SELECT"),
                field_id=field_id,
            )
        )
        return "ok"

    return [
        sql_lookup_tool,
        config_lookup_tool,
        artifact_lookup_tool,
        bundle_search_tool,
        bundle_get_chunk_tool,
        graph_create_refer_to_tool,
        graph_create_table_access_tool,
    ]


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
    span_index, chunk_index = build_bundle_span_index(data_dir, chunk_bundle_dir=chunk_dir)
    state["span_index"] = span_index
    state["chunk_index"] = chunk_index
    logger.info("[FW:DEEPAGENT] bundle index 빌드 완료 | span=%d chunk=%d", len(span_index), len(chunk_index))


def _build_unmatched_user_message(u: UnresolvedLink) -> str:
    """미매칭 인덱스 키에 대한 종류별 user message 생성."""
    info = u.ref_candidates[0] if u.ref_candidates else {}
    category = info.get("category", "")
    key = info.get("key", "")
    target_type = info.get("target_type", "")

    header = f"미매칭 인덱스 키 (category: {category})\n"
    header += f"target_type: {target_type}\nkey: {key}\n"

    if category == "entity":
        header += (
            f"chunk_type: {info.get('chunk_type', '')}\n"
            f"file_path: {info.get('file_path', '')}\n"
            f"text_snippet:\n{info.get('text_snippet', '')}\n\n"
            f"이 엔티티를 CRUD하는 메서드를 찾으십시오.\n"
            f"1) 엔티티명 '{key}'으로 bundle_search → 관련 상수/변수 정의 찾기\n"
            f"2) 상수 사용처 추적 → Home interface lookup 메서드 발견\n"
            f"3) 발견된 각 메서드에 대해 CRUD 패턴(create→WRITES, find→READS 등) 판별\n"
            f"4) graph_create_refer_to_tool + graph_create_table_access_tool 생성 (source_method_id 필수)\n"
        )
    elif category == "bean":
        header += (
            f"chunk_type: {info.get('chunk_type', '')}\n"
            f"file_path: {info.get('file_path', '')}\n"
            f"text_snippet:\n{info.get('text_snippet', '')}\n\n"
            f"이 세션 빈을 호출하는 메서드를 찾으십시오.\n"
            f"1) 빈명 '{key}'으로 bundle_search → JNDI 상수 또는 Home interface 사용처 검색\n"
            f"2) 발견된 각 메서드에 대해 graph_create_refer_to_tool 생성 (source_method_id 필수)\n"
        )
    elif category == "sql":
        header += (
            f"sql_type: {info.get('sql_type', '')}\n"
            f"file_path: {info.get('file_path', '')}\n\n"
            f"이 SQL을 실행하는 메서드를 찾으십시오.\n"
            f"1) sql_key '{key}' 또는 파일명/네임스페이스로 bundle_search\n"
            f"2) 발견된 메서드에 대해 graph_create_refer_to_tool 생성 (source_method_id 필수)\n"
        )
    elif category == "config":
        header += (
            f"file_path: {info.get('file_path', '')}\n\n"
            f"이 설정을 참조하는 메서드를 찾으십시오.\n"
            f"1) 설정 키 '{key}' 또는 파일명으로 bundle_search\n"
            f"2) 발견된 메서드에 대해 graph_create_refer_to_tool 생성 (source_method_id 필수)\n"
        )
    else:
        header += f"\ninfo: {json.dumps(info, ensure_ascii=False, default=str)}\n"

    return header


def run_deep_agent(
    unresolved_list: List[UnresolvedLink],
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[List[ReferToEdge], List[MethodTableEdge]]:
    """unresolved 목록을 DeepAgent 로 처리한다. (new_refer_to_edges, new_method_table_edges) 를 반환."""
    logger.info("[FW:DEEPAGENT] 진입 | unresolved=%d api_key_set=%s run_dir=%s", len(unresolved_list), bool(api_key), run_dir)
    if not unresolved_list:
        logger.info("[FW:DEEPAGENT] unresolved 없음 → 즉시 종료")
        return [], []

    new_refer: List[ReferToEdge] = []
    new_mt: List[MethodTableEdge] = []

    verbose = os.environ.get("FW_DEEPAGENT_VERBOSE", "true").strip().lower() == "true"

    try:
        from deepagents import create_deep_agent
        from client.llm_client import get_llm
    except Exception as e:
        logger.warning("[FW:DEEPAGENT] deepagents 미설치 또는 LLM 클라이언트 없음: %s", e)
        return new_refer, new_mt

    # span_index / chunk_index 가 state 에 없으면 자동 빌드
    if run_dir:
        state.setdefault("run_dir", str(run_dir))
    _ensure_bundle_indexes(state)

    model = get_llm(api_key=api_key) if api_key else get_llm()
    max_workers = min(len(unresolved_list), settings.concurrency.max_concurrency)

    def _process_one(idx: int, u: UnresolvedLink) -> Tuple[List[ReferToEdge], List[MethodTableEdge]]:
        has_method = bool(u.method_id and u.method_id.strip())
        label = u.method_id if has_method else "(unmatched key)"
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
        table_collector: List[MethodTableEdge] = []
        tools = build_tools(state, u.method_id or "", refer_collector, table_collector, field_id=u.field_id)

        agent = create_deep_agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)

        if has_method:
            user_content = f"method_id: {u.method_id}\nref_candidates: {json.dumps(u.ref_candidates, ensure_ascii=False, default=str)}"
        else:
            user_content = _build_unmatched_user_message(u)

        try:
            result = agent.invoke({"messages": [{"role": "user", "content": user_content}]})
            log_llm_interaction(
                prompt_name="framework/deep_agent",
                input_payload=user_content,
                output_payload={
                    "refer_to": len(refer_collector),
                    "table_access": len(table_collector),
                },
                metadata={"method_id": u.method_id or "", "idx": idx, "total": total},
            )
            if verbose and (refer_collector or table_collector):
                logger.info(
                    "[FW:DEEPAGENT] 결과 | %s new_refer=%d new_mt=%d",
                    label, len(refer_collector), len(table_collector),
                )
        except Exception as ex:
            logger.warning("[FW:DEEPAGENT] invoke 실패 %s error=%s", label, ex)
        return refer_collector, table_collector

    total = len(unresolved_list)
    logger.info("[FW:DEEPAGENT] 병렬 처리 시작 | items=%d workers=%d", total, max_workers)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_one, idx, u): idx
            for idx, u in enumerate(unresolved_list, start=1)
        }
        for future in as_completed(futures):
            try:
                refer_result, mt_result = future.result()
                new_refer.extend(refer_result)
                new_mt.extend(mt_result)
            except Exception as ex:
                logger.warning("[FW:DEEPAGENT] 워커 예외: %s", ex)

    logger.info("[FW:DEEPAGENT] 종료 | new_refer=%d new_mt=%d", len(new_refer), len(new_mt))
    return new_refer, new_mt
