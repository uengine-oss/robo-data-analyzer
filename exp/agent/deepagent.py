"""deepagent: REFER/READ/WRITE unresolved 처리. deepagents create_deep_agent + 툴(bundle_search, sql_lookup 등)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from exp.models import MethodTableEdge, ReferToEdge, UnresolvedLink

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """프로젝트 전체 탐색 금지. 허용된 도구만 사용하라. unresolved 1건만 해결하라.
가능하면 sql_key 복원 후 sql_lookup으로 해결하라. 확신 없으면 graph_create_refer_to 하지 말고 필요한 증거만 한 줄 제안하라."""


def _short_method_id(method_id: str) -> str:
    """로그용: method_id를 ClassName.methodName 형태로 축약."""
    if not method_id or "." not in method_id:
        return method_id or ""
    parts = method_id.rsplit(".", 2)
    return ".".join(parts[-2:]) if len(parts) >= 2 else method_id


def _unresolved_task_description(u: UnresolvedLink) -> str:
    """에이전트용 사용자 메시지: 이 메서드의 미해결 참조를 위 도구만 써서 해결하고 graph_create_refer_to로 연결하라."""
    parts = [f"method_id: {u.method_id}", "ref_candidates:"]
    for c in u.ref_candidates or []:
        k = c.get("kind", "") if isinstance(c, dict) else getattr(c, "kind", "")
        v = c.get("value") if isinstance(c, dict) else getattr(c, "value", None)
        parts.append(f"  - kind={k} value={v}")
    parts.append("Use only the provided tools. Resolve to a sql_key or config_key and call graph_create_refer_to(method_id, target_type, target_key, evidence).")
    return "\n".join(parts)


def get_method_context(method_id: str, method_facts_list: List[Any]) -> Optional[Dict[str, Any]]:
    """method_facts_list에서 MethodFacts 조회 후 code, params, class_key, package_name, fqn 등만 dict로 반환. 파일 읽기 없음."""
    for m in method_facts_list:
        if getattr(m, "method_id", None) == method_id:
            return {
                "method_id": m.method_id,
                "fqn": getattr(m, "fqn", ""),
                "signature": getattr(m, "signature", ""),
                "params": getattr(m, "params", ""),
                "class_key": getattr(m, "class_key", ""),
                "package_name": getattr(m, "package_name", ""),
                "code_ref": getattr(m, "code_ref", ""),
                "code": (getattr(m, "code", "") or "")[:2000],
            }
    return None


def get_symbol_type(method_id: str, symbol: str, symbol_type_index: Dict[str, Dict[str, str]], method_facts_list: List[Any], field_facts_list: List[Any], class_index: Dict[str, Any]) -> Optional[str]:
    """해당 메서드에서 symbol(변수/필드명)의 타입을 정적으로 추론. param + field + symbol_type_index."""
    types = (symbol_type_index.get(method_id) or {}).get(symbol)
    if types:
        return types
    for m in method_facts_list:
        if getattr(m, "method_id", None) != method_id:
            continue
        params_str = getattr(m, "params", "") or ""
        for part in params_str.split(","):
            part = part.strip()
            tokens = part.split()
            if len(tokens) >= 2 and tokens[-1].rstrip(")") == symbol:
                return tokens[-2]
        break
    class_key = None
    for m in method_facts_list:
        if getattr(m, "method_id", None) == method_id:
            class_key = getattr(m, "class_key", None)
            break
    if class_key:
        for f in field_facts_list:
            if getattr(f, "class_key", None) == class_key and getattr(f, "name", None) == symbol:
                return getattr(f, "field_type", None) or ""
    return None


def lookup_class_candidates(simple_name: str, scope_hint: Optional[str], class_index: Dict[str, Any], class_index_simple: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """class_index에서 simpleName 또는 fqn.endswith(simpleName) 인 항목 목록. scope_hint 있으면 package 필터."""
    out: List[Dict[str, Any]] = []
    fqns = class_index_simple.get(simple_name, [])
    if not fqns and simple_name in class_index:
        info = class_index[simple_name]
        if isinstance(info, dict):
            out.append({"fqn": info.get("fqn", simple_name), "name": info.get("name", simple_name), "package_name": info.get("package_name", "")})
        return out
    for fqn in fqns:
        info = class_index.get(fqn)
        if isinstance(info, dict) and (not scope_hint or (info.get("package_name") or "").startswith(scope_hint or "")):
            out.append({"fqn": fqn, "name": info.get("name", ""), "package_name": info.get("package_name", "")})
    return out


def sql_lookup(sql_key: str, sql_index: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """sql_key → SqlStatement (sql_text 등). 툴 sql_lookup."""
    s = sql_index.get(sql_key)
    if not s:
        return None
    return {
        "sql_key": getattr(s, "sql_key", sql_key),
        "sql_type": getattr(s, "sql_type", ""),
        "sql_text": getattr(s, "sql_text", ""),
        "file_path": getattr(s, "file_path", ""),
        "start_line": getattr(s, "start_line", 0),
        "end_line": getattr(s, "end_line", 0),
    }


def config_lookup(config_key: str, config_index: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """config_key → ConfigEntry(value 등). 툴 config_lookup."""
    c = config_index.get(config_key)
    if not c:
        return None
    return {
        "config_key_path": config_key,
        "config_key": config_key,
        "value": (getattr(c, "raw_snippet", "") or getattr(c, "value", "") or "")[:500],
        "raw_snippet": (getattr(c, "raw_snippet", "") or "")[:200],
        "file_path": getattr(c, "file_path", ""),
        "start_line": getattr(c, "start_line", 0),
        "end_line": getattr(c, "end_line", 0),
    }


def graph_create_refer_to(
    method_id: str,
    target_type: str,
    target_key: str,
    evidence: Optional[Dict[str, Any]] = None,
    field_id: Optional[str] = None,
) -> ReferToEdge:
    """REFER_TO 엣지 생성. Method 또는 Field 소스. target_type: SqlStatement|ConfigSection|ArtifactChunk|Class."""
    return ReferToEdge(method_id=method_id, target_type=target_type, target_id=target_key, evidence=evidence or {}, confidence=0.9, field_id=field_id)


def graph_create_table_access(
    method_id: str,
    table_name: str,
    access: str,
    evidence: Optional[Dict[str, Any]] = None,
    field_id: Optional[str] = None,
) -> MethodTableEdge:
    """Method/Field→Table READS/WRITES 엣지 생성. access: READS|WRITES."""
    return MethodTableEdge(method_id=method_id, table_schema="", table_name=table_name, relation=access, field_id=field_id)


def build_tools(
    state: Dict[str, Any],
    method_id: str,
    refer_collector: List[ReferToEdge],
    table_collector: List[MethodTableEdge],
    field_id: Optional[str] = None,
) -> List[Any]:
    """LangChain 툴 목록 생성. Method/Field 소스 지원."""
    from langchain_core.tools import tool
    from exp.chunk_bundle import bundle_search as _bundle_search, bundle_get_chunk as _bundle_get_chunk
    from exp.steps.step5_linking import sql_parse as _sql_parse

    sql_index = state.get("sql_index") or {}
    config_index = state.get("config_index") or {}
    span_index = state.get("span_index") or {}
    chunk_index = state.get("chunk_index") or {}
    data_dir = state.get("data_dir") or ""

    @tool
    def sql_lookup_tool(sql_key: str) -> str:
        """Look up SQL statement by key (e.g. namespace.id). Returns JSON with sql_key, sql_text, file_path or empty."""
        r = sql_lookup(sql_key, sql_index)
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def config_lookup_tool(config_key: str) -> str:
        """Look up config entry by key. Returns JSON with value, file_path or empty."""
        r = config_lookup(config_key, config_index)
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def bundle_search_tool(query: str, kind: str = "symbol") -> str:
        """Search bundle for symbol or text. kind: symbol|text. Returns list of {chunk_id, filePath, span, score}."""
        results = _bundle_search(data_dir, query, kind, span_index=span_index, chunk_index=chunk_index)
        return json.dumps(results[:20], ensure_ascii=False)

    @tool
    def bundle_get_chunk_tool(chunk_id: str) -> str:
        """Get code snippet by chunk_id. Returns {text, filePath, span}."""
        r = _bundle_get_chunk(chunk_id, chunk_index=chunk_index)
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def sql_parse_tool(sql_text: str) -> str:
        """Parse SQL to operation, tables, columns. Returns JSON with operation, tables, confidence."""
        r = _sql_parse(sql_text)
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def graph_create_refer_to_tool(target_type: str, target_key: str, evidence: str = "") -> str:
        """Create REFER_TO edge: method/field -> target. target_type: SqlStatement|ConfigSection|ArtifactChunk|Class. target_key: sql_key or config_key or chunk_id or fqn."""
        edge = graph_create_refer_to(method_id, target_type, target_key, {"note": evidence} if evidence else None, field_id=field_id)
        refer_collector.append(edge)
        return "ok"

    @tool
    def graph_create_table_access_tool(table_name: str, access: str, evidence: str = "") -> str:
        """Create Method/Field->Table READS or WRITES edge. access: READS|WRITES."""
        edge = graph_create_table_access(method_id, table_name, access, {"note": evidence} if evidence else None, field_id=field_id)
        table_collector.append(edge)
        return "ok"

    return [
        sql_lookup_tool,
        config_lookup_tool,
        bundle_search_tool,
        bundle_get_chunk_tool,
        sql_parse_tool,
        graph_create_refer_to_tool,
        graph_create_table_access_tool,
    ]


def run_deep_agent(
    unresolved_list: List[UnresolvedLink],
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[List[ReferToEdge], List[MethodTableEdge]]:
    """unresolved 목록을 결정론 단축 후 남은 건만 deepagents create_deep_agent로 처리. (new_refer_to_edges, new_method_table_edges) 반환."""
    # 항상 터미널에 보이도록 첫 로그부터 명시적으로 출력
    logger.info("")
    logger.info("[EXP:DEEPAGENT] ========== DeepAgent 단계 시작 ==========")
    api_status = "설정됨" if api_key else "미설정"
    logger.info("[EXP:DEEPAGENT] 진입 | unresolved=%d건, API 키=%s, run_dir=%s", len(unresolved_list), api_status, run_dir)
    if not unresolved_list:
        logger.info("[EXP:DEEPAGENT] unresolved 없음 → 즉시 반환 (new_refer=0, new_mt=0)")
        return [], []
    logger.info("[EXP:DEEPAGENT] [섹션] 결정론 매칭 (SQL_KEY_LITERAL / CONFIG_KEY_REF) → sql_index·config_index 직접 조회")
    new_refer: List[ReferToEdge] = []
    new_mt: List[MethodTableEdge] = []
    method_facts_list = state.get("method_facts_list") or []
    sql_index = state.get("sql_index") or {}
    config_index = state.get("config_index") or {}
    class_index = state.get("class_index") or {}
    class_index_simple = state.get("class_index_simple") or {}
    symbol_type_index = state.get("symbol_type_index") or {}
    field_facts_list = state.get("field_facts_list") or []

    log_events = os.environ.get("DEEPAGENT_LOG_EVENTS", "true").strip().lower() == "true"
    verbose = os.environ.get("DEEPAGENT_VERBOSE", "false").strip().lower() == "true"
    use_stream = log_events and run_dir is not None

    still_unresolved: List[UnresolvedLink] = []
    determ_resolved_count = 0
    for u in unresolved_list:
        resolved = False
        short_id = _short_method_id(u.method_id)
        u_field_id = getattr(u, "field_id", None)
        for c in u.ref_candidates or []:
            kind = c.get("kind", "") if isinstance(c, dict) else getattr(c, "kind", "")
            value = c.get("value") if isinstance(c, dict) else getattr(c, "value", None)
            if not value:
                continue
            if kind == "SQL_KEY_LITERAL":
                if value in sql_index:
                    new_refer.append(ReferToEdge(method_id=u.method_id, target_type="SqlStatement", target_id=value, evidence={"source": "deepagent"}, confidence=0.95, field_id=u_field_id))
                    logger.info("[EXP:DEEPAGENT] 결정론 매칭 성공 | method=%s kind=SQL_KEY_LITERAL value=%s → sql_index 존재 → REFER_TO(SqlStatement)", short_id, value)
                    resolved = True
                    determ_resolved_count += 1
                    break
                else:
                    logger.info("[EXP:DEEPAGENT] 결정론 매칭 실패 | method=%s kind=SQL_KEY_LITERAL value=%s → sql_index 없음", short_id, value)
            elif kind == "CONFIG_KEY_REF":
                if value in config_index:
                    new_refer.append(ReferToEdge(method_id=u.method_id, target_type="ConfigSection", target_id=value, evidence={"source": "deepagent"}, confidence=0.95, field_id=u_field_id))
                    logger.info("[EXP:DEEPAGENT] 결정론 매칭 성공 | method=%s kind=CONFIG_KEY_REF value=%s → config_index 존재 → REFER_TO(ConfigSection)", short_id, value)
                    resolved = True
                    determ_resolved_count += 1
                    break
                else:
                    logger.info("[EXP:DEEPAGENT] 결정론 매칭 실패 | method=%s kind=CONFIG_KEY_REF value=%s → config_index 없음", short_id, value)
        if not resolved:
            still_unresolved.append(u)
            if u.ref_candidates:
                cand_summary = "; ".join(
                    f"{getattr(c, 'kind', '')}={getattr(c, 'value', '')}"
                    for c in (u.ref_candidates or [])[:5]
                )
                logger.info("[EXP:DEEPAGENT] LLM 전달 대상 | method=%s candidates=%s", short_id, cand_summary)

    logger.info("[EXP:DEEPAGENT] 결정론 매칭 결과 | resolved=%d still_unresolved=%d", determ_resolved_count, len(still_unresolved))

    if not still_unresolved or not api_key:
        logger.info(
            "[EXP:DEEPAGENT] LLM 호출 스킵 | still_unresolved=%d api_key_set=%s (결정론만 적용, new_refer=%d)",
            len(still_unresolved),
            bool(api_key),
            len(new_refer),
        )
        logger.info("[EXP:DEEPAGENT] ========== DeepAgent 단계 종료 ==========")
        return new_refer, new_mt

    logger.info("[EXP:DEEPAGENT] [섹션] LLM 에이전트 호출 | 남은 unresolved=%d건 (도구: sql_lookup, config_lookup, bundle_search, graph_create_refer_to 등)", len(still_unresolved))

    try:
        from deepagents import create_deep_agent
        from client.llm_client import get_llm
    except ImportError as e:
        logger.warning("[EXP:DEEPAGENT] deepagents 또는 client.llm_client 미설치: %s", e)
        logger.info("[EXP:DEEPAGENT] 스킵 | reason=deepagents_not_installed")
        logger.info("[EXP:DEEPAGENT] ========== DeepAgent 단계 종료 ==========")
        return new_refer, new_mt

    # 프로젝트 LLM 설정(LLM_API_KEY, LLM_API_BASE, LLM_MODEL)으로 모델 생성.
    # deepagents 기본값(Anthropic) 대신 명시적으로 전달해 인증/엔드포인트 일관성 유지.
    model = get_llm(api_key=api_key)

    total = len(still_unresolved)
    success_count = 0
    fail_count = 0
    progress_interval = max(1, min(10, total // 5))  # 10건마다 또는 5구간
    all_events: List[Dict[str, Any]] = []

    for idx, u in enumerate(still_unresolved, start=1):
        short_id = _short_method_id(u.method_id)
        field_id = getattr(u, "field_id", None)
        logger.info("[EXP:DEEPAGENT] LLM 처리 (%d/%d) method=%s", idx, total, short_id)
        refer_collector: List[ReferToEdge] = []
        table_collector: List[MethodTableEdge] = []
        tools = build_tools(state, u.method_id, refer_collector, table_collector, field_id=field_id)
        agent = create_deep_agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)
        try:
            if use_stream:
                events: List[Dict[str, Any]] = []
                for namespace, chunk in agent.stream(
                    {"messages": [{"role": "user", "content": _unresolved_task_description(u)}]},
                    stream_mode="updates",
                    subgraphs=True,
                ):
                    event = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "method_id": u.method_id,
                        "namespace": list(namespace),
                        "chunk": chunk,
                    }
                    events.append(event)
                    all_events.append(event)
                    if verbose:
                        logger.info("[EXP:DEEPAGENT] stream namespace=%s chunk=%s", namespace, chunk)
                new_refer.extend(refer_collector)
                new_mt.extend(table_collector)
                success_count += 1
            else:
                agent.invoke({"messages": [{"role": "user", "content": _unresolved_task_description(u)}]})
                new_refer.extend(refer_collector)
                new_mt.extend(table_collector)
                success_count += 1
        except Exception as e:
            fail_count += 1
            logger.warning("[EXP:DEEPAGENT] invoke 실패 method=%s error=%s", short_id, e)
        if idx % progress_interval == 0:
            logger.info("[EXP:DEEPAGENT] 진행 %d/%d 완료 (성공=%d 실패=%d)", idx, total, success_count, fail_count)

    if use_stream and all_events and run_dir is not None:
        out_path = run_dir / "deepagent_events.json"
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(all_events, f, ensure_ascii=False, indent=2, default=str)
            logger.info("[EXP:DEEPAGENT] 이벤트 JSON 저장 | path=%s events=%d", out_path, len(all_events))
        except Exception as e:
            logger.warning("[EXP:DEEPAGENT] 이벤트 JSON 저장 실패 | path=%s error=%s", out_path, e)

    logger.info(
        "[EXP:DEEPAGENT] 종료 | LLM 처리 성공=%d 실패=%d, new_refer=%d, new_mt=%d",
        success_count,
        fail_count,
        len(new_refer),
        len(new_mt),
    )
    logger.info("[EXP:DEEPAGENT] ========== DeepAgent 단계 종료 ==========")
    return new_refer, new_mt
