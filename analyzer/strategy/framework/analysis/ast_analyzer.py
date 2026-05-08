# ---------------------------------------------------------------------------
# 메서드별 코드 분석 (Phase 3 에서 배치 호출됨)
# ---------------------------------------------------------------------------

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

from analyzer.strategy.framework.common.domain.lang_profile import get_lang_profile
from analyzer.strategy.framework.common.models.node_models import (
    CallsEdge,
    FunctionFacts,
    ReadsWritesEdge,
    ReferToEdge,
    VariableFacts,
)
from analyzer.strategy.framework.common.domain.ontology import get_ontology
from config.settings import settings
from util.code_text_utils import calculate_code_token as estimate_tokens
from util.llm_rule_runner import prompts

logger = logging.getLogger(__name__)


VALID_MODULE_STEREOTYPES = frozenset({
    "Controller", "Service", "Repository", "Entity", "DTO",
    "Config", "Component", "Aspect", "Filter", "Listener",
    "Validator", "Mapper", "Util", "ExceptionHandler",
    "Gateway", "Facade", "Factory", "Scheduler", "Client", "Enum",
    "Module", "DataStructure",
})


def compute_stereotype(
    function_id: str,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
) -> str:
    """테이블 접근 기반 stereotype fallback. 쓰기 있으면 Command, 아니면 Query."""
    sql_keys = {e.target_id for e in refer_to_edges if e.function_id == function_id and e.target_type == "SqlStatement"}
    writes = any(
        rw.sql_key in sql_keys and rw.relation in ("WRITES", "INSERT", "UPDATE", "DELETE")
        for rw in reads_writes
    )
    return "Command" if writes else "Query"


def _build_module_intent_index(module_index: Dict[str, Any]) -> Dict[str, str]:
    """module_index 에서 module_key → intent 역인덱스 구성.

    intent 는 ``pre_analysis._collect_module_intents`` 가 module_pre_analysis LLM 호출 결과로
    module_index entry 에 주입한 짧은 비즈니스 요약 — 헤더 주석·어노테이션·시그니처가 모두
    여기로 정제되어 들어가므로, 다운스트림 LLM 입력은 intent 한 줄만 사용.
    """
    return {
        info["module_key"]: (info.get("intent") or "")
        for info in (module_index or {}).values()
        if isinstance(info, dict) and info.get("module_key")
    }


def _get_module_intent(module_intent_index: Dict[str, str], module_key: Optional[str]) -> Optional[str]:
    """module_intent_index 에서 module_key 의 intent 반환. O(1)."""
    if not module_key:
        return None
    return module_intent_index.get(module_key) or None


def _build_linked_sql_context(
    function_id: str,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    sql_index: Optional[Dict[str, Any]] = None,
    sql_text_max_len: int = 800,
) -> str:
    """메서드가 참조하는 SQL 노드의 내용(SQL 일부 + 테이블·relation)을 하나의 문자열로 구성. LLM context용."""
    sql_keys = {e.target_id for e in refer_to_edges if e.function_id == function_id and e.target_type == "SqlStatement"}
    if not sql_keys:
        return ""
    parts: List[str] = []
    for sql_key in sorted(sql_keys):
        chunk = (sql_index or {}).get(sql_key)
        sql_snippet = ""
        if chunk is not None:
            if isinstance(chunk, dict):
                sql_snippet = (chunk.get("sql_text") or "")[:sql_text_max_len]
            else:
                sql_snippet = (getattr(chunk, "sql_text", "") or "")[:sql_text_max_len]
        rw_list = [f"{rw.table_name}({rw.relation})" for rw in reads_writes if rw.sql_key == sql_key]
        tables_ops = ", ".join(rw_list[:30]) if rw_list else "(없음)"
        block = f"sql_key: {sql_key} | SQL 일부: {sql_snippet} | 테이블·연산: {tables_ops}"
        parts.append(block)
    if not parts:
        return ""
    return "[연결된 SQL] " + " || ".join(parts)


LINKED_ARCHITECTURE_CONTEXT_MAX = int(os.environ.get("ANALYSIS_CONTEXT_MAX", "8000"))


def _build_linked_architecture_context(
    function_id: str,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    sql_index: Optional[Dict[str, Any]] = None,
    config_index: Optional[Dict[str, Any]] = None,
    module_index: Optional[Dict[str, Any]] = None,
    variable_facts_list: Optional[List[VariableFacts]] = None,
    sql_text_max_len: int = 800,
    config_snippet_max_len: int = 500,
    module_info_max_len: int = 300,
    module_fields_max_len: int = 400,
    total_max_len: int = 0,
) -> str:
    """메서드가 참조하는 모든 아키텍처 노드(SQL, Config, Module)의 내용을 하나의 문자열로 구성. LLM context용."""
    parts: List[str] = []
    sql_ctx = _build_linked_sql_context(function_id, refer_to_edges, reads_writes, sql_index, sql_text_max_len)
    if sql_ctx:
        parts.append(sql_ctx)

    config_keys = {e.target_id for e in refer_to_edges if e.function_id == function_id and e.target_type == "ConfigSection"}
    for ckey in sorted(config_keys):
        chunk = (config_index or {}).get(ckey)
        snippet = ""
        if chunk is not None:
            if isinstance(chunk, dict):
                snippet = (chunk.get("raw_snippet") or "")[:config_snippet_max_len]
            else:
                snippet = (getattr(chunk, "raw_snippet", "") or "")[:config_snippet_max_len]
        parts.append(f"[연결된 Config] config_key: {ckey} | 내용: {snippet}")

    module_ids = {e.target_id for e in refer_to_edges if e.function_id == function_id and e.target_type == "Module"}
    for cid in sorted(module_ids):
        info = (module_index or {}).get(cid)
        if not info and module_index:
            for k, v in module_index.items():
                if not isinstance(v, dict):
                    continue
                if v.get("fqn") == cid or v.get("module_key") == cid or k == cid:
                    info = v
                    break
        desc = ""
        if isinstance(info, dict):
            name = info.get("name") or info.get("module_key") or cid
            comment = (info.get("comment") or "")[:module_info_max_len]
            desc = f"name: {name}" + (f" | comment: {comment}" if comment else "")
            # 참조 클래스의 필드 정보 포함 (LLM 분석 컨텍스트용)
            ref_module_key = info.get("module_key") or cid
            fields_desc = _build_module_fields_context(ref_module_key, variable_facts_list, max_len=module_fields_max_len)
            if fields_desc:
                desc += f" | fields: {fields_desc}"
        else:
            desc = str(cid)
        parts.append(f"[연결된 Module] target_id: {cid} | {desc}")
    out = " ".join(parts) if parts else ""
    if total_max_len > 0 and len(out) > total_max_len:
        out = out[: total_max_len - 20] + " ... [truncated]"
    return out


def _build_module_fields_context(
    module_key: Optional[str],
    variable_facts_list: Optional[List[VariableFacts]],
    max_len: int = 500,
) -> str:
    """해당 클래스의 필드/상수 선언을 간결한 문자열로 구성. 메서드 분석 context용.

    static final 상수를 우선 포함하여 LLM이 상수값을 해석할 수 있도록 한다.
    """
    if not module_key or not variable_facts_list:
        return ""
    fields = [f for f in variable_facts_list if f.module_key == module_key]
    if not fields:
        return ""
    parts: List[str] = []
    # static final 상수를 우선 배치
    constants = [f for f in fields if f.code and ("static" in f.code.lower() and "final" in f.code.lower())]
    others = [f for f in fields if f not in constants]
    for f in constants + others:
        code_line = f.code.strip()
        if code_line:
            # 한 줄로 압축
            code_line = " ".join(code_line.split())
            parts.append(code_line)
        else:
            entry = f.name
            if f.variable_type:
                entry = f"{f.variable_type} {f.name}"
            parts.append(entry)
    result = " | ".join(parts)
    if len(result) > max_len:
        result = result[:max_len - 15] + " ... [truncated]"
    return result


def _invoke_analysis_llm(
    m: FunctionFacts,
    api_key: str,
    lang: str,
    locale: str = "ko",
    calls_outgoing: Optional[List[str]] = None,
    module_intent: Optional[str] = None,
    linked_architecture_context: Optional[str] = None,
    called_method_summaries: Optional[List[Dict[str, Any]]] = None,
    referenced_symbols: Optional[str] = None,
    tables: Optional[str] = None,
    codebase_pattern: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """메서드 1개 분석 → summary/dependencies/stereotype/rules/examples/questions."""
    if not api_key:
        raise RuntimeError(
            f"_analyze_code_node: api_key 누락 — service 진입점에서 검증되어야 함 (function_id={m.function_id})"
        )
    if not m.code.strip():
        logger.info(
            "[PHASE:3:CODE_ANALYSIS] LLM 스킵 (코드 비어있음) | function_id=%s",
            m.function_id,
        )
        return None
    code = m.code.strip()
    language = get_lang_profile().display_name(lang)
    onto = get_ontology()
    is_oop = onto.is_oop(lang)
    valid_stereotypes = sorted(onto.valid_stereotypes(lang))

    inputs: Dict[str, Any] = {
        "code": code,
        "startLine": m.start_line,
        "endLine": m.end_line,
        "locale": locale,
        "language": language,
        "is_oop": is_oop,
        "valid_stereotypes": valid_stereotypes,
        "has_table_context": bool(tables),
    }
    if module_intent and module_intent.strip():
        inputs["module_intent"] = module_intent.strip()

    context_parts: List[str] = []
    if calls_outgoing:
        context_parts.append("calls_outgoing: " + ", ".join(sorted(calls_outgoing)))
    if linked_architecture_context and linked_architecture_context.strip():
        context_parts.append(linked_architecture_context.strip())
    if called_method_summaries:
        called_snippets: List[str] = []
        for cs in called_method_summaries:
            cls = str(cs.get("module_name") or "")
            name = str(cs.get("function_name") or "")
            summ = str(cs.get("summary") or "")
            if not summ:
                continue
            id_part = f"{cls}.{name}" if cls and name else (name or cls or "")
            called_snippets.append(f"{id_part}: {summ}" if id_part else summ)
        if called_snippets:
            called_snippets.sort()
            context_parts.append("called_method_summaries: " + " || ".join(called_snippets))
            inputs["has_called_methods"] = True
    if context_parts:
        inputs["context"] = "; ".join(context_parts)

    if referenced_symbols and referenced_symbols.strip():
        inputs["referenced_symbols"] = referenced_symbols.strip()
    if tables and tables.strip():
        inputs["tables"] = tables.strip()
    if codebase_pattern and codebase_pattern.strip():
        inputs["codebase_pattern"] = codebase_pattern.strip()
    # Fail-Fast: LLM 예외는 그대로 상위로 전파.
    # 빈 응답(dict 가 아니거나 summary 비어있음) 은 LLM 의 명시적 "분석 불가" 판정으로 해석.
    # 호출 그래프 사이클로 누락된 노드는 호출자가 None 체크 후 그 함수만 분석 결과 없이 진행.
    result = prompts.execute_raw("framework", "code_analysis", inputs, api_key)
    if not isinstance(result, dict) or not result.get("summary"):
        logger.error(
            "[PHASE:3:CODE_ANALYSIS] LLM 빈 응답 — 분석 불가 판정 | function_id=%s result_type=%s",
            m.function_id, type(result).__name__,
        )
        return None  # noqa: SILENT_FAIL — LLM 이 분석 불가 판정한 함수는 skip (callers 그래프 처리 시 호출자가 처리)
    logger.info(
        "[PHASE:3:CODE_ANALYSIS] LLM 분석 완료 | function_id=%s has_summary=%s deps=%d rules=%d examples=%d questions=%d",
        m.function_id,
        bool(result.get("summary")),
        len(result.get("dependencies") or []),
        len(result.get("rules") or []),
        len(result.get("examples") or []),
        len(result.get("questions") or []),
    )
    return result


def _analyze_code_node(
    m: FunctionFacts,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    api_key: str,
    lang: str,
    locale: str,
    module_index: Optional[Dict[str, Any]] = None,
    sql_index: Optional[Dict[str, Any]] = None,
    config_index: Optional[Dict[str, Any]] = None,
    module_intent_index: Optional[Dict[str, str]] = None,
    called_summaries: Optional[List[Dict[str, Any]]] = None,
    variable_facts_list: Optional[List[VariableFacts]] = None,
    tables_context_by_function: Optional[Dict[str, str]] = None,
    referenced_symbols_by_function: Optional[Dict[str, List[str]]] = None,
    codebase_pattern: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """단일 메서드에 대해 stereotype + (summary, dependencies) 계산."""
    default_stereotype = compute_stereotype(m.function_id, refer_to_edges, reads_writes)
    summary = f"{m.fqn} ({default_stereotype}); lines {m.start_line}-{m.end_line}"
    method_edges = [e for e in refer_to_edges if e.function_id == m.function_id]
    calls_outgoing = [f"{e.target_type}:{e.target_id}" for e in method_edges]
    linked_architecture_context = _build_linked_architecture_context(
        m.function_id,
        refer_to_edges,
        reads_writes,
        sql_index=sql_index,
        config_index=config_index,
        module_index=module_index,
        variable_facts_list=variable_facts_list,
        total_max_len=LINKED_ARCHITECTURE_CONTEXT_MAX,
    )
    module_intent = _get_module_intent(module_intent_index or {}, m.module_key)
    referenced_symbols = "\n".join(
        (referenced_symbols_by_function or {}).get(m.function_id, [])
    )
    tables_section = (tables_context_by_function or {}).get(m.function_id) or ""
    dependencies: List[Dict[str, Any]] = []
    table_insights: List[Dict[str, Any]] = []
    rules: List[Dict[str, Any]] = []
    examples: List[Dict[str, Any]] = []
    questions: List[Dict[str, Any]] = []
    stereotype = ""
    actor = ""

    if api_key:
        analysis = _invoke_analysis_llm(
            m,
            api_key,
            lang,
            locale=locale,
            calls_outgoing=calls_outgoing,
            module_intent=module_intent,
            linked_architecture_context=linked_architecture_context or None,
            called_method_summaries=called_summaries,
            referenced_symbols=referenced_symbols or None,
            tables=tables_section or None,
            codebase_pattern=codebase_pattern or None,
        )
        if analysis:
            summary = analysis.get("summary") or summary
            actor = analysis.get("actor") or ""
            dependencies = list(analysis.get("dependencies") or [])
            table_insights = list(analysis.get("tableInsights") or [])
            rules = list(analysis.get("rules") or [])
            examples = list(analysis.get("examples") or [])
            questions = list(analysis.get("questions") or [])
            raw_stereotype = (analysis.get("stereotype") or "").strip()
            if get_ontology().is_valid_stereotype(raw_stereotype, lang):
                stereotype = raw_stereotype

    # LLM 이 stereotype 을 못 정하면 테이블 접근 기반 fallback
    if not stereotype:
        stereotype = default_stereotype

    data = {
        "stereotype": stereotype,
        "summary": summary,
        "actor": actor,
        "fqn": m.fqn,
        "dependencies": dependencies,
        "tableInsights": table_insights,
        "rules": rules,
        "examples": examples,
        "questions": questions,
    }
    return m.function_id, data


_ANALYSIS_BATCH_SIZE = 20


def analyze_and_enrich(
    function_facts_list: List[FunctionFacts],
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    lang: str,
    use_llm: bool = False,
    api_key: Optional[str] = None,
    locale: str = "ko",
    parallel: bool = False,
    module_index: Optional[Dict[str, Any]] = None,
    sql_index: Optional[Dict[str, Any]] = None,
    config_index: Optional[Dict[str, Any]] = None,
    calls_edges: Optional[List[CallsEdge]] = None,
    variable_facts_list: Optional[List[VariableFacts]] = None,
    tables_context_by_function: Optional[Dict[str, str]] = None,
    referenced_symbols_by_function: Optional[Dict[str, List[str]]] = None,
    codebase_pattern: Optional[str] = None,
) -> Generator[Dict[str, Dict[str, Any]], None, None]:
    """메서드별 stereotype, summary — 20개씩 배치로 yield.

    - LLM 사용(O) + calls_edges 있음  → 호출 그래프 하위 → 상위 순으로 레벨별 병렬, 20개씩 yield.
    - 그 외(LLM 미사용 / calls_edges 없음) → 기존 방식대로 메서드 단위 개별 처리, 20개씩 yield.
    """
    if use_llm and not api_key:
        raise RuntimeError(
            "[CODE_ANALYSIS] use_llm=True 인데 api_key 가 비어있음 — "
            "service 진입점에서 settings.llm.api_key 검증 필요"
        )
    module_intent_index = _build_module_intent_index(module_index or {})
    logger.info(
        "[PHASE:3:CODE_ANALYSIS] 요약 진입 | function_facts=%d use_llm=%s api_key_set=%s parallel=%s calls_edges=%d",
        len(function_facts_list),
        use_llm,
        bool(api_key),
        parallel,
        len(calls_edges or []),
    )
    total_yielded = 0
    effective_api_key = api_key if use_llm else ""

    def _flush_batch(batch: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """배치를 복사하여 반환 (yield 용)."""
        return dict(batch)

    # LLM을 못 쓰는 경우 / 호출 그래프가 없는 경우 → 기존 방식 유지
    if not (use_llm and effective_api_key and calls_edges):
        if parallel and use_llm and effective_api_key:
            workers = settings.concurrency.max_concurrency
            total = len(function_facts_list)
            workers_actual = max(1, min(workers, total))
            logger.info(
                "[PHASE:3:CODE_ANALYSIS] 요약 LLM 병렬 시작 (그래프 미사용) | 메서드=%d 워커=%d (동시에 %d개까지 처리)",
                total,
                workers,
                workers_actual,
            )
            completed = 0
            batch: Dict[str, Dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=workers_actual) as executor:
                futures = {
                    executor.submit(
                        _analyze_code_node,
                        m,
                        refer_to_edges,
                        reads_writes,
                        effective_api_key,
                        lang,
                        locale,
                        module_index,
                        sql_index,
                        config_index,
                        module_intent_index,
                        None,
                        variable_facts_list,
                        tables_context_by_function,
                        referenced_symbols_by_function,
                        codebase_pattern,
                    ): m
                    for m in function_facts_list
                }
                for future in as_completed(futures):
                    function_id, data = future.result()  # 실패 시 Fail-Fast: 상위로 전파
                    batch[function_id] = data
                    completed += 1
                    if len(batch) >= _ANALYSIS_BATCH_SIZE:
                        total_yielded += len(batch)
                        logger.debug("[PHASE:3:CODE_ANALYSIS] 배치 yield  %d/%d", total_yielded, total)
                        yield _flush_batch(batch)
                        batch.clear()
                    if completed == total:
                        logger.info("[PHASE:3:CODE_ANALYSIS] analysis LLM 완료  %d/%d", completed, total)
                    else:
                        logger.debug("[PHASE:3:CODE_ANALYSIS] analysis LLM 진행  %d/%d", completed, total)
            if batch:
                total_yielded += len(batch)
                yield _flush_batch(batch)
        else:
            batch = {}
            for m in function_facts_list:
                # 실패 시 Fail-Fast: 상위로 전파
                function_id, data = _analyze_code_node(
                    m,
                    refer_to_edges,
                    reads_writes,
                    effective_api_key,
                    lang,
                    locale,
                    module_index,
                    sql_index,
                    config_index,
                    module_intent_index,
                    None,
                    variable_facts_list,
                    tables_context_by_function,
                    referenced_symbols_by_function,
                    codebase_pattern,
                )
                batch[function_id] = data
                if len(batch) >= _ANALYSIS_BATCH_SIZE:
                    total_yielded += len(batch)
                    yield _flush_batch(batch)
                    batch.clear()
            if batch:
                total_yielded += len(batch)
                yield _flush_batch(batch)
        logger.info("[PHASE:3:CODE_ANALYSIS] 요약 완료 | summary_count=%d", total_yielded)
        return

    # -------- 호출 그래프 기반 bottom-up 요약 (단일 LLM 호출 경로) --------
    fact_by_id: Dict[str, FunctionFacts] = {m.function_id: m for m in function_facts_list}
    node_ids = set(fact_by_id.keys())
    children: Dict[str, List[str]] = {}
    parents: Dict[str, List[str]] = {}
    for e in calls_edges or []:
        c = e.caller_function_id
        cal = e.callee_function_id
        if not c or not cal or c not in node_ids or cal not in node_ids:
            continue
        children.setdefault(c, []).append(cal)
        parents.setdefault(cal, []).append(c)

    # 자식이 없는 노드(리프)부터 시작
    leaf_ids = [n for n in node_ids if n not in children]
    remaining_children: Dict[str, int] = {c: len(children[c]) for c in children}

    summary_cache: Dict[str, str] = {}
    result: Dict[str, Dict[str, Any]] = {}

    workers = settings.concurrency.max_concurrency

    def summarize_one(node_id: str) -> Tuple[str, Dict[str, Any]]:
        m = fact_by_id[node_id]
        called_list: List[Dict[str, Any]] = []
        for callee_id in children.get(node_id, []):
            if callee_id not in summary_cache:
                continue
            callee_m = fact_by_id.get(callee_id)
            if not callee_m:
                continue
            fqn = getattr(callee_m, "fqn", "")
            module_fqn = fqn.rsplit(".", 1)[0] if "." in fqn else ""
            module_name = module_fqn.split(".")[-1] if module_fqn else ""
            function_name = fqn.rsplit(".", 1)[-1] if "." in fqn else fqn
            # 하위 함수의 테이블 접근 정보 수집
            callee_tables = [
                f"{rw.table_name}({rw.relation})"
                for rw in (reads_writes or [])
                if rw.function_id == callee_id and rw.table_name
            ]
            entry = {
                "module_name": module_name,
                "function_name": function_name,
                "summary": summary_cache[callee_id],
            }
            if callee_tables:
                entry["tables"] = ", ".join(callee_tables)
            called_list.append(entry)
        _, data = _analyze_code_node(
            m,
            refer_to_edges,
            reads_writes,
            effective_api_key,
            lang,
            locale,
            module_index,
            sql_index,
            config_index,
            module_intent_index,
            called_list or None,
            variable_facts_list,
            tables_context_by_function,
            referenced_symbols_by_function,
            codebase_pattern,
        )
        return node_id, data

    processed: Set[str] = set()
    batch: Dict[str, Dict[str, Any]] = {}
    max_workers = max(1, workers)

    logger.info(
        "[PHASE:3:CODE_ANALYSIS] 호출 그래프 기반 요약 시작 (즉시 실행 모드) | leaf=%d 전체=%d 워커=%d",
        len(leaf_ids),
        len(node_ids),
        max_workers,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # leaf 노드부터 즉시 submit
        futures = {executor.submit(summarize_one, nid): nid for nid in leaf_ids}

        while futures:
            # 완료된 future 하나씩 처리 (실패 시 Fail-Fast: 상위로 전파)
            done_iter = as_completed(futures)
            for future in done_iter:
                nid = futures.pop(future)
                node_id, data = future.result()
                result[node_id] = data
                summary_cache[node_id] = data.get("summary", "")
                processed.add(node_id)
                batch[node_id] = data

                # 부모 노드가 ready되면 즉시 submit
                for p in parents.get(node_id, []):
                    if p in remaining_children:
                        remaining_children[p] -= 1
                        if remaining_children[p] == 0:
                            futures[executor.submit(summarize_one, p)] = p

                # 20개 모이면 yield
                if len(batch) >= _ANALYSIS_BATCH_SIZE:
                    total_yielded += len(batch)
                    logger.debug("[PHASE:3:CODE_ANALYSIS] 배치 yield  %d개", len(batch))
                    yield _flush_batch(batch)
                    batch.clear()

                break  # as_completed iterator를 갱신하기 위해 재진입

    # 남은 배치 flush
    if batch:
        total_yielded += len(batch)
        yield _flush_batch(batch)

    # 사이클로 인해 정상 순서에서 누락된 노드 — 독립 처리
    remaining = node_ids - processed
    if remaining:
        logger.error(
            "[PHASE:3:CODE_ANALYSIS] 호출 그래프 사이클로 누락된 노드 | remaining=%d", len(remaining)
        )
        batch = {}
        for mid in remaining:
            m = fact_by_id[mid]
            # 실패 시 Fail-Fast: 상위로 전파
            _, data = _analyze_code_node(
                m,
                refer_to_edges,
                reads_writes,
                effective_api_key,
                lang,
                locale,
                module_index,
                sql_index,
                config_index,
                module_intent_index,
                None,
                variable_facts_list,
                tables_context_by_function,
                referenced_symbols_by_function,
                codebase_pattern,
            )
            batch[mid] = data
            if len(batch) >= _ANALYSIS_BATCH_SIZE:
                total_yielded += len(batch)
                yield _flush_batch(batch)
                batch.clear()
        if batch:
            total_yielded += len(batch)
            yield _flush_batch(batch)

    logger.info("[PHASE:3:CODE_ANALYSIS] 호출 그래프 기반 요약 완료 | summary_count=%d", total_yielded)




def _invoke_module_summary_llm(
    module_fqn: str,
    items: List[Dict[str, Any]],
    module_intent: Optional[str],
    prev_summary: Optional[str],
    api_key: str,
    locale: str = "ko",
    kind: Optional[str] = None,
) -> Tuple[Optional[str], str, Dict[str, List[str]]]:
    """module_summary_batch 규칙 실행 → (모듈 요약, moduleStereotype, unified_actors) 반환.

    api_key 누락은 service 진입점 검증 누락이라 raise. items 비어있으면 빈 결과 반환.
    """
    if not api_key:
        raise RuntimeError(
            f"_invoke_module_summary_llm: api_key 누락 (module_fqn={module_fqn})"
        )
    if not items:
        return None, "", {}
    inputs: Dict[str, Any] = {
        "module_fqn": module_fqn,
        "items": items,
        "locale": locale,
    }
    if kind:
        inputs["kind"] = kind
    if module_intent:
        inputs["module_intent"] = module_intent
    if prev_summary:
        inputs["prev_summary"] = prev_summary

    # Fail-Fast: LLM 예외는 상위로 전파.
    result = prompts.execute_raw("framework", "module_analysis", inputs, api_key)
    if not isinstance(result, Dict):
        logger.error(
            "[MODULE_SUMMARY] LLM 결과가 dict 아님 | module_fqn=%s type=%s",
            module_fqn, type(result).__name__,
        )
        raise RuntimeError(
            f"module_analysis LLM 결과가 dict 가 아님: {type(result).__name__} (module_fqn={module_fqn})"
        )
    summary = result.get("summary") or None
    raw_module_st = (result.get("moduleStereotype") or "").strip()
    module_stereotype = raw_module_st if raw_module_st in VALID_MODULE_STEREOTYPES else ""
    unified_actors = result.get("unified_actors") or {}
    return summary, module_stereotype, unified_actors


def run_module_summary(
    module_index: Dict[str, Any],
    analysis_by_function: Dict[str, Dict[str, Any]],
    summary_by_variable: Dict[str, Dict[str, Any]],
    function_facts_list: List[FunctionFacts],
    variable_facts_list: List[VariableFacts],
    api_key: Optional[str] = None,
    locale: str = "ko",
) -> Generator[Dict[str, Dict[str, Any]], None, None]:
    """모듈별 요약 — 모듈 완료 즉시 yield.

    api_key 가 명시되지 않으면 settings.llm.api_key 사용 (service 진입점 검증 후 단일 출처 폴백).
    """
    if not api_key:
        api_key = settings.llm.api_key
    if not api_key:
        raise RuntimeError(
            "run_module_summary: api_key 누락 — service 진입점에서 검증되어야 함"
        )
    module_intent_index = _build_module_intent_index(module_index or {})
    # module_key → kind 매핑 (FILE, STRUCT, CLASS 등)
    key_to_kind: Dict[str, str] = {}
    for info in (module_index or {}).values():
        if isinstance(info, dict) and info.get("module_key"):
            key_to_kind[info["module_key"]] = info.get("kind", "")

    by_class: Dict[str, List[Dict[str, Any]]] = {}
    for m in function_facts_list or []:
        ck = getattr(m, "module_key", None)
        if not ck:
            continue
        name = m.fqn.rsplit(".", 1)[-1] if m.fqn else m.function_id
        data = analysis_by_function.get(m.function_id) or {}
        summary = data.get("summary", "") if isinstance(data, dict) else ""
        if not summary:
            summary = f"{name}; method"
        actor = data.get("actor", "") if isinstance(data, dict) else ""
        item = {"name": name, "summary": summary}
        if actor:
            item["actor"] = actor
        by_class.setdefault(ck, []).append(item)
    for f in variable_facts_list or []:
        ck = getattr(f, "module_key", None)
        if not ck:
            continue
        data = summary_by_variable.get(f.variable_id) or {}
        summary = data.get("summary", "") if isinstance(data, dict) else ""
        if not summary:
            summary = f"{f.name}; field"
        by_class.setdefault(ck, []).append({"name": f.name, "summary": summary})

    def _summarize_one_module(module_key: str, items: List[Dict[str, Any]]) -> Tuple[str, str, str, Dict[str, List[str]]]:
        """단일 모듈 요약 — Map-Reduce 패턴.

        1) 모듈 항목을 max_batch_token 단위 청크로 분할
        2) Map: 각 청크를 병렬로 LLM 부분 요약 (서로 의존성 없음)
        3) Reduce: 부분 요약이 여러 개면 한 번 더 LLM으로 통합 요약
        """
        module_fqn = module_key
        if not items:
            return module_fqn, "", "", {}
        module_intent = _get_module_intent(module_intent_index, module_key)
        module_kind = key_to_kind.get(module_key)

        # 토큰 기반 청크 분할
        chunks: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        current_tokens = 0
        max_tokens = settings.batch.max_batch_token
        for item in items:
            t = estimate_tokens((item.get("name") or "") + " " + (item.get("summary") or ""))
            if current and current_tokens + t > max_tokens:
                chunks.append(current)
                current = []
                current_tokens = 0
            current.append(item)
            current_tokens += t
        if current:
            chunks.append(current)

        # api_key 부재는 위 진입점에서 raise — 여기서는 LLM 호출 보장됨.

        # Map: 각 청크 독립 LLM 부분 요약 병렬 실행
        def _summarize_chunk(chunk: List[Dict[str, Any]]):
            return _invoke_module_summary_llm(
                module_fqn, chunk, module_intent,
                prev_summary=None, api_key=api_key, locale=locale, kind=module_kind,
            )

        if len(chunks) == 1:
            partial_results = [_summarize_chunk(chunks[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(len(chunks), settings.concurrency.max_concurrency)) as inner:
                partial_results = list(inner.map(_summarize_chunk, chunks))

        # Reduce: 부분 요약 통합
        partial_summaries = [s for s, _, _ in partial_results if s]
        module_stereotype = next((st for _, st, _ in partial_results if st), "")
        all_unified_actors: Dict[str, List[str]] = {}
        for _, _, chunk_unified in partial_results:
            for rep, syns in (chunk_unified or {}).items():
                all_unified_actors.setdefault(rep, []).extend(syns)

        if len(partial_summaries) <= 1:
            final_summary = partial_summaries[0] if partial_summaries else ""
        else:
            # 부분 요약들을 items 형태로 묶어 1회 통합 호출
            merge_items = [
                {"name": f"part_{i + 1}", "summary": s}
                for i, s in enumerate(partial_summaries)
            ]
            final_summary, merged_st, merged_unified = _invoke_module_summary_llm(
                module_fqn, merge_items, module_intent,
                prev_summary=None, api_key=api_key, locale=locale, kind=module_kind,
            )
            if not final_summary:
                logger.error(
                    "[MODULE_SUMMARY] 통합 요약 실패 — 통합 LLM 이 빈 summary 반환 | module_fqn=%s partial_count=%d",
                    module_fqn, len(partial_summaries),
                )
                raise RuntimeError(
                    f"module_analysis 통합 요약 실패 (module_fqn={module_fqn}): 통합 LLM 이 빈 summary 반환"
                )
            if merged_st:
                module_stereotype = merged_st
            for rep, syns in (merged_unified or {}).items():
                all_unified_actors.setdefault(rep, []).extend(syns)

        return module_fqn, final_summary, module_stereotype, all_unified_actors

    _CLASS_BATCH_SIZE = 5
    total_yielded = 0
    batch: Dict[str, Dict[str, Any]] = {}
    workers = settings.concurrency.max_concurrency
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_summarize_one_module, ck, items): ck
                for ck, items in by_class.items()
            }
            for future in as_completed(futures):
                # Fail-Fast: 실패 시 상위로 전파 (module summary 중 하나라도 실패하면 분석 중단)
                module_fqn, summary, module_stereotype, unified = future.result()
                total_yielded += 1
                batch[module_fqn] = {"summary": summary, "moduleStereotype": module_stereotype, "unified_actors": unified}
                if len(batch) >= _CLASS_BATCH_SIZE:
                    yield batch
                    batch = {}
    else:
        for module_key, items in by_class.items():
            module_fqn, summary, module_stereotype, unified = _summarize_one_module(module_key, items)
            total_yielded += 1
            batch[module_fqn] = {"summary": summary, "moduleStereotype": module_stereotype, "unified_actors": unified}
            if len(batch) >= _CLASS_BATCH_SIZE:
                yield batch
                batch = {}
    if batch:
        yield batch
    logger.info("[PHASE:3:CODE_ANALYSIS] Module 요약 완료 | module_count=%d", total_yielded)
