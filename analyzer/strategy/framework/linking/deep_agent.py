from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

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
당신은 소스코드에서 미매칭된 인덱스 키를 추적해
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
     - graph_create_refer_to_tool("ArtifactChunk", chunk_id, evidence, source_method_id)
     - 테이블 접근이 확인되면: graph_create_table_access_tool(테이블명, "READS"/"WRITES", evidence, source_method_id)
  주의: 하나의 청크를 여러 메서드가 사용할 수 있습니다. 발견된 모든 메서드에 대해 관계를 생성하십시오.

== category: "sql" (SQL 문 관련) ==
목표: 이 SQL을 실행하는 메서드를 찾아 Method→SqlStatement 관계 생성.
절차:
  1) sql_key나 파일명으로 bundle_search → 해당 SQL ID를 참조하는 코드 검색
  2) context에 related_classes가 있으면 해당 클래스명으로도 검색 (중복 메서드명 구분)
  3) graph_create_refer_to_tool("SqlStatement", sql_key, evidence, source_method_id)

== category: "config" (설정 관련) ==
목표: 이 설정을 읽는 메서드를 찾아 Method→ConfigSection 관계 생성.
절차:
  1) 설정 키나 파일명으로 bundle_search → 해당 설정을 참조하는 코드 검색
  2) config_lookup_tool로 설정 값 확인, 관련 키워드로 추가 검색
  3) graph_create_refer_to_tool("ConfigSection", config_key, evidence, source_method_id)

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
    span_index, chunk_index = build_bundle_span_index(
        data_dir,
        chunk_bundle_dir=chunk_dir,
        artifact_index=state.get("artifact_index"),
        sql_index=state.get("sql_index"),
    )
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

    if category in ("entity", "bean", "cmp_mapping"):
        header += (
            f"chunk_type: {info.get('chunk_type', '')}\n"
            f"file_path: {info.get('file_path', '')}\n"
            f"[정의 원문]\n{info.get('text_snippet', '')}\n\n"
            f"위 원문에서 클래스명, 인터페이스명, 메서드명 등 식별자를 먼저 파악한 뒤,\n"
            f"이 청크를 사용하는 메서드를 찾으십시오.\n"
            f"1) 원문에 등장하는 식별자로 bundle_search → 소스코드 사용처 추적\n"
            f"2) 발견된 각 메서드의 사용 패턴 판별 (CRUD, 호출, 참조 등)\n"
            f"3) graph_create_refer_to_tool 생성 (source_method_id 필수)\n"
            f"4) 테이블 접근이 확인되면 graph_create_table_access_tool도 생성\n"
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
) -> Generator[Tuple[List[ReferToEdge], List[MethodTableEdge]], None, None]:
    """unresolved 목록을 DeepAgent 로 처리. 항목별로 (refer_to, method_table) yield."""
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
    total_refer = 0
    total_mt = 0
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
                if refer_result or mt_result:
                    total_refer += len(refer_result)
                    total_mt += len(mt_result)
                    yield (refer_result, mt_result)
            except Exception as ex:
                logger.warning("[FW:DEEPAGENT] 워커 예외: %s", ex)

    logger.info("[FW:DEEPAGENT] 종료 | new_refer=%d new_mt=%d", total_refer, total_mt)


# ---------------------------------------------------------------------------
# DeepAgent 엔티티 식별 — 시나리오별 Method→Table 연결
# ---------------------------------------------------------------------------

ENTITY_IDENTIFICATION_SYSTEM_PROMPT = """
당신은 소스코드에서 데이터베이스 엔티티(테이블에 매핑되는 클래스/구조체)를
식별하고, 각 메서드가 어떤 테이블을 읽거나 쓰는지 추적하는 전문가 에이전트입니다.

[목표]
입력으로 주어지는 정보(DDL 테이블 목록 또는 클래스/구조체 후보)를 기반으로
코드에서 DB 접근 패턴을 추적하여 Method→Table 관계를 생성합니다.

[판단 기준 — 엔티티 vs DTO/VO]
엔티티 시그널 (하나 이상이면 엔티티):
- DB 연결/쿼리 코드에서 직접 사용 (INSERT/UPDATE/DELETE/SELECT의 대상)
- ORM/DB 함수의 인자로 전달 (save, persist, find, query, db_insert, db_select 등)
- 필드가 DB 컬럼과 1:1 대응 (id, created_at, updated_at 등 persistence 관련 필드)
- 생성 후 DB에 저장되는 패턴 (create → save/insert)

DTO/VO 시그널 (이것만 있으면 엔티티 아님):
- 요청/응답 변환용 (Request, Response, DTO, VO 접미사)
- 직렬화/역직렬화만 수행, DB 접근 없이 데이터 전달만

[절대 규칙]
- 오직 제공된 도구만 사용하십시오.
- 확신이 없으면 엣지를 생성하지 마십시오.
- bundle_search는 소스코드뿐 아니라 XML 정의, SQL 매핑도 검색합니다. 활용하세요.
- 테이블명 추론: 매핑 정보 있으면 해당 값, 없으면 CamelCase → UPPER_SNAKE_CASE 관례 적용
- graph_create_table_access_tool 호출 시 evidence에 테이블의 비즈니스 목적을 한 문장으로 기술하세요.

[도구]
- bundle_search_tool(query, kind="symbol"|"text"): 코드·XML·SQL 통합 검색.
- bundle_get_chunk_tool(chunk_id): chunk_id로 코드/XML/SQL 조각 조회.
- graph_create_table_access_tool(table_name, access, evidence, source_method_id):
  Method→Table 접근 관계 등록. access: "READS" 또는 "WRITES". source_method_id 필수.

[절차]
1) 입력에 DB 테이블 목록이 있으면: 각 테이블명으로 bundle_search → 관련 코드/XML/SQL 추적
2) 입력에 클래스/구조체 후보가 있으면: 각 이름으로 bundle_search → DB 접근 패턴 확인
3) 엔티티→테이블 매핑과 사용 메서드를 파악하면:
   - graph_create_table_access_tool(table_name, "READS"/"WRITES", evidence, source_method_id)
4) DTO/VO로 판단되면 아무것도 호출하지 않음

[효율성]
- 후보가 여러 개이면 먼저 bundle_search로 일괄 검색한 뒤,
  유사한 사용 패턴을 가진 후보를 묶어서 처리하십시오.
"""


# 디렉토리명 사전 체크용 상수
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
    """시나리오별 DeepAgent user message 구성."""
    parts: List[str] = []

    # DDL 테이블 컨텍스트
    if ddl_table_metadata:
        table_lines: List[str] = []
        for (_schema, _name), meta in ddl_table_metadata.items():
            orig = meta.get("original_name", _name)
            desc = (meta.get("description") or "")[:80]
            table_lines.append(f"- {orig}" + (f" ({desc})" if desc else ""))
        if table_lines:
            parts.append("[프로젝트 DB 테이블]\n" + "\n".join(table_lines[:200]))
            parts.append(
                "\n위 테이블 목록을 참고하여 코드에서 이 테이블들을 사용하는 메서드를 추적하고,\n"
                "각 테이블과 매핑되는 엔티티/구조체가 있으면 함께 파악하세요.\n"
            )

    # 후보 목록
    if candidates:
        cand_lines = [
            f"- {c['fqn']} (file: {c['file_path']})"
            for c in candidates
        ]
        parts.append("[클래스/구조체 후보]\n" + "\n".join(cand_lines))
        if not ddl_table_metadata:
            parts.append("\n위 후보에서 DB 엔티티를 식별하고, 사용하는 메서드를 추적하세요.")

    return "\n".join(parts)


def run_deep_agent_entity_identification(
    class_index: Dict[str, Any],
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
    ddl_table_metadata: Optional[Dict] = None,
) -> List[MethodTableEdge]:
    """DeepAgent로 엔티티 식별 + Method→Table 연결.

    시나리오별 동작:
    - DDL만 있음: 테이블명을 컨텍스트로 전달, 코드에서 관련 메서드 추적
    - 아무것도 없음: 클래스/구조체 후보를 전달, 엔티티 식별 + 메서드 추적
    - DDL + 후보: 테이블 컨텍스트 + 후보 목록 모두 전달

    Returns:
        List[MethodTableEdge]: 식별된 Method→Table 접근 엣지 목록.
    """
    has_ddl = bool(ddl_table_metadata)
    has_candidates = bool(class_index)

    if not has_ddl and not has_candidates:
        logger.info("[FW:DEEPAGENT:ENTITY] DDL도 후보도 없음 → 즉시 종료")
        return []

    try:
        from deepagents import create_deep_agent
        from client.llm_client import get_llm
    except Exception as e:
        logger.warning("[FW:DEEPAGENT:ENTITY] deepagents 미설치 또는 LLM 클라이언트 없음: %s", e)
        return []

    if run_dir:
        state.setdefault("run_dir", str(run_dir))
    _ensure_bundle_indexes(state)

    model = get_llm(api_key=api_key) if api_key else get_llm()

    # 후보 수집 + 디렉토리명 사전 체크
    # FILE(C 소스파일 컨테이너)은 엔티티 후보에서 제외 — STRUCT/UNION만 대상
    candidates: List[Dict[str, str]] = []
    for fqn, info in (class_index or {}).items():
        if not isinstance(info, dict):
            continue
        if info.get("kind") == "FILE":
            continue  # 파일 컨테이너는 엔티티 후보 아님
        fp = info.get("file_path", "")
        path_class = _classify_by_path(fp)
        if path_class == "dto":
            continue  # DTO 디렉토리에 있는 클래스는 제외
        candidates.append({
            "fqn": fqn,
            "name": info.get("name", ""),
            "file_path": fp,
            "annotations": (info.get("annotations") or "")[:100],
            "comment": (info.get("comment") or "")[:100],
            "path_hint": path_class,
        })

    # entity 디렉토리 힌트 있는 것을 앞으로 정렬
    candidates.sort(key=lambda c: (0 if c.get("path_hint") == "entity" else 1))

    if not candidates and not has_ddl:
        logger.info("[FW:DEEPAGENT:ENTITY] 후보 없음 + DDL 없음 → 즉시 종료")
        return []

    logger.info(
        "[FW:DEEPAGENT:ENTITY] 시작 | ddl_tables=%d candidates=%d",
        len(ddl_table_metadata or {}), len(candidates),
    )

    table_collector: List[MethodTableEdge] = []
    refer_collector: List[ReferToEdge] = []
    tools = build_tools(state, "", refer_collector, table_collector)

    agent = create_deep_agent(
        model=model, tools=tools,
        system_prompt=ENTITY_IDENTIFICATION_SYSTEM_PROMPT,
    )

    # 배치 처리
    _BATCH_SIZE = 200
    if has_ddl and not candidates:
        # DDL만 있고 후보 없음 → 테이블명만 전달
        user_msg = _build_entity_id_user_message(ddl_table_metadata=ddl_table_metadata)
        try:
            agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
            log_llm_interaction(
                prompt_name="framework/deep_agent_entity_identification",
                input_payload=user_msg,
                output_payload={"table_access": len(table_collector)},
                metadata={"scenario": "ddl_only"},
            )
        except Exception as ex:
            logger.warning("[FW:DEEPAGENT:ENTITY] invoke 실패 (ddl_only): %s", ex)
    else:
        # 후보가 있음 → 배치로 처리 (DDL 테이블은 매 배치 컨텍스트로 포함)
        for i in range(0, max(len(candidates), 1), _BATCH_SIZE):
            batch = candidates[i:i + _BATCH_SIZE]
            user_msg = _build_entity_id_user_message(
                candidates=batch,
                ddl_table_metadata=ddl_table_metadata,
            )
            try:
                agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
                log_llm_interaction(
                    prompt_name="framework/deep_agent_entity_identification",
                    input_payload=user_msg,
                    output_payload={"table_access": len(table_collector)},
                    metadata={"batch_start": i, "batch_size": len(batch)},
                )
            except Exception as ex:
                logger.warning("[FW:DEEPAGENT:ENTITY] invoke 실패 batch=%d: %s", i, ex)

    logger.info("[FW:DEEPAGENT:ENTITY] 종료 | method_table_edges=%d", len(table_collector))
    return table_collector
