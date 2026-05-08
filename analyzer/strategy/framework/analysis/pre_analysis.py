"""Framework Phase 3 코드 분석에 주입할 컨텍스트 수집기.

수집 대상:
  - constants_context (CONSTANT 노드 → 텍스트)
  - referenced_vars_by_function (REFERENCES + INIT_BY 출처)
  - codebase_pattern (Phase 1 codebase_profile.pattern 그대로)
  - reads_writes_by_function (function_table_edges 기반)
  - ddl_context_by_function (단일 컨텍스트):
      schema = 그래프 Table/Column 노드 (catalog 무관, 항상 생성)
      sample = catalog sample_rows (catalog 활성 시 schema 위에 부착)

정책 정렬:
  - 참조 변수 Neo4j 조회 실패 → fail-fast (이전과 동일)
  - catalog 비활성 (`CATALOG_SERVICE_URL` 미설정) → schema 만 사용, sample 부착 skip
  - catalog 활성 + 호출 실패 → fail-fast (사용자가 활성화했으면 떠있어야 함)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from analyzer.common.pipeline.analysis_context import (
    fetch_constants_context,
    format_table_context,
)
from client.catalog_client import CatalogClient
from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.llm_rule_runner import prompts

logger = logging.getLogger(__name__)


async def collect_analysis_context(st: Any, client: Neo4jClient, orchestrator: Any) -> Dict[str, Any]:
    """LLM 분석 prompt 에 주입할 컨텍스트 dict 반환.

    Returns:
        ``{constants_context, referenced_vars_by_function, codebase_pattern,
           reads_writes_by_function, ddl_context_by_function,
           module_intents_by_module_key}``
    """
    ctx: Dict[str, Any] = {
        "constants_context": "",
        "referenced_vars_by_function": {},
        "codebase_pattern": "",
        "reads_writes_by_function": {},
        "ddl_context_by_function": {},
        "module_intents_by_module_key": {},
    }

    rw_by_fn = _collect_reads_writes_by_function(st)
    ctx["reads_writes_by_function"] = rw_by_fn

    ctx["codebase_pattern"] = _build_codebase_pattern(st)
    ctx["constants_context"] = await fetch_constants_context(client)
    ctx["referenced_vars_by_function"] = await _collect_referenced_vars(client)

    # ddl_context: schema 는 그래프에서 항상 생성, sample 은 catalog 활성 시만 부착.
    await _attach_table_context(ctx, st, client, orchestrator, rw_by_fn)

    # module_intent / module_display_name: 모듈마다 1회 LLM 호출 (code_analysis 전).
    # 헤더 주석의 변경 이력·작성자 노이즈를 제거하고 1~3 문장 비즈니스 의도만 추출.
    intents = _collect_module_intents(st)
    ctx["module_intents_by_module_key"] = intents
    if intents:
        await _persist_module_intents_to_graph(intents, st, client)

    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# 모듈 사전 요약 (module_intent + module_display_name) — Phase 3 함수 분석 전 1회
# ─────────────────────────────────────────────────────────────────────────────


def _collect_module_intents(st: Any) -> Dict[str, Dict[str, str]]:
    """모듈마다 module_pre_analysis LLM 1회 호출 → ``{module_key: {intent, display_name}}``.

    헤더 주석 4KB → 한 줄 (~0.4KB) 로 압축해 함수 분석 (code_analysis) 의 매 호출 input 부담 ↓.
    부수: ``st.module_index`` 의 해당 entry 에 ``intent`` / ``display_name`` 키도 직접 주입
    (다운스트림 _build_module_key_index 가 활용).
    LLM 호출 실패는 fallback (해당 모듈만 skip — 함수 분석은 module_intent 없이 진행).
    """
    module_index = getattr(st, "module_index", None) or {}
    if not module_index:
        return {}

    api_key = settings.llm.api_key
    if not api_key:
        logger.info("[PHASE:3:MODULE_PRE] api_key 없음 — module_intent 수집 skip")
        return {}

    locale = settings.locale
    lang = getattr(st, "lang", "java")
    function_facts_list = getattr(st, "function_facts_list", None) or []

    # 모듈별 함수 시그니처 그룹핑
    sigs_by_module_key: Dict[str, list] = {}
    for f in function_facts_list:
        mk = getattr(f, "module_key", "") or ""
        if not mk:
            continue
        # FunctionFacts: fqn / signature / params / return_type — name 속성 없음. fqn 마지막 segment 사용.
        fqn = getattr(f, "fqn", "") or ""
        short_name = fqn.rsplit(".", 1)[-1] if "." in fqn else (fqn or getattr(f, "function_id", ""))
        params = getattr(f, "params", "") or ""
        ret = getattr(f, "return_type", "") or ""
        sig = f"{short_name}({params})" + (f" → {ret}" if ret else "")
        sigs_by_module_key.setdefault(mk, []).append(sig)

    out: Dict[str, Dict[str, str]] = {}
    ok_count = 0
    skip_count = 0
    for fqn, info in module_index.items():
        if not isinstance(info, dict):
            continue
        module_key = info.get("module_key") or ""
        if not module_key:
            continue
        comment = (info.get("comment") or "").strip()
        annotations = (info.get("annotations") or "").strip()
        sigs = sigs_by_module_key.get(module_key, [])
        # 자료 0 — 추출할 게 없으면 skip (intent / display_name 빈 값으로 두고 다른 모듈 처리)
        if not (comment or annotations or sigs):
            skip_count += 1
            continue
        inputs: Dict[str, Any] = {
            "module_fqn": fqn,
            "locale": locale,
            "language": lang,
        }
        if comment:
            inputs["module_comment"] = comment
        if annotations:
            inputs["module_annotations"] = annotations
        if sigs:
            inputs["function_signatures"] = "\n".join(f"- {s}" for s in sigs[:80])
        try:
            result = prompts.execute_raw("framework", "module_pre_analysis", inputs, api_key)
        except Exception as e:
            logger.warning(
                "[PHASE:3:MODULE_PRE] LLM 호출 실패 (해당 모듈 skip) | module=%s | %s: %s",
                fqn, type(e).__name__, e,
            )
            skip_count += 1
            continue
        if not isinstance(result, dict):
            skip_count += 1
            continue
        intent = (result.get("module_intent") or "").strip()
        display_name = (result.get("module_display_name") or "").strip()
        if not (intent or display_name):
            skip_count += 1
            continue
        out[module_key] = {"intent": intent, "display_name": display_name}
        # module_index entry 자체에 intent / display_name 주입 (다운스트림 가 lookup 편의)
        info["intent"] = intent
        info["display_name"] = display_name
        ok_count += 1
    logger.info(
        "[PHASE:3:MODULE_PRE] module_intent 수집 | ok=%d skip=%d total=%d",
        ok_count, skip_count, len(module_index),
    )
    return out


async def _persist_module_intents_to_graph(
    intents: Dict[str, Dict[str, str]],
    st: Any,
    client: Neo4jClient,
) -> None:
    """모듈 노드에 ``display_name`` / ``intent`` 속성 SET.

    Phase 2 에서 모듈 노드는 이미 생성됐으므로 (display_name/intent 는 빈 값) 사후 SET.
    노드 매칭 키는 ``fqn``.
    """
    module_index = getattr(st, "module_index", None) or {}
    rows = []
    for fqn, info in module_index.items():
        if not isinstance(info, dict):
            continue
        module_key = info.get("module_key") or ""
        rec = intents.get(module_key)
        if not rec:
            continue
        rows.append({
            "fqn": fqn,
            "display_name": rec.get("display_name") or "",
            "intent": rec.get("intent") or "",
        })
    if not rows:
        return
    query = (
        "UNWIND $rows AS row "
        "MATCH (m {fqn: row.fqn}) "
        "SET m.display_name = row.display_name, m.intent = row.intent"
    )
    try:
        await client.execute_queries([{"query": query, "params": {"rows": rows}}])
        logger.info("[PHASE:3:MODULE_PRE] 모듈 노드 display_name/intent SET | rows=%d", len(rows))
    except Exception as e:
        logger.warning(
            "[PHASE:3:MODULE_PRE] 모듈 노드 SET 실패 (계속 진행) | %s: %s",
            type(e).__name__, e,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 함수별 R/W 매핑 — function_table_edges 기반
# ─────────────────────────────────────────────────────────────────────────────


def _collect_reads_writes_by_function(st: Any) -> Dict[str, list]:
    rw_by_fn: Dict[str, list] = {}
    for e in (st.function_table_edges or []):
        fid = e.function_id
        tbl = (e.table_name or "").strip()
        rel = (e.relation or "").strip().upper()
        op = (getattr(e, "op", "") or "").strip().upper()
        if not (fid and tbl and rel):
            continue
        triple = (tbl, rel, op)
        bucket = rw_by_fn.setdefault(fid, [])
        if triple not in bucket:
            bucket.append(triple)
    if rw_by_fn:
        logger.info(
            "[PHASE:3:CONTEXT] 함수별 R/W 맵 수집 | functions=%d edges=%d",
            len(rw_by_fn), sum(len(v) for v in rw_by_fn.values()),
        )
    return rw_by_fn


# ─────────────────────────────────────────────────────────────────────────────
# 코드베이스 컨벤션 (Phase 1 codebase_profile.pattern 그대로 주입)
# ─────────────────────────────────────────────────────────────────────────────


def _build_codebase_pattern(st: Any) -> str:
    cp = getattr(st, "codebase_profile", None)
    pattern = getattr(cp, "pattern", "") if cp else ""
    if not pattern:
        return ""
    logger.info(
        "[PHASE:3:CONTEXT] 코드베이스 패턴 주입 | len=%d", len(pattern),
    )
    return pattern


# ─────────────────────────────────────────────────────────────────────────────
# 참조 변수 + INIT_BY 초기화 출처
# ─────────────────────────────────────────────────────────────────────────────


# 화이트리스트: 함수가 직접 사용한 식별자 중 "함수 외부에 정의된 것" 만 매칭.
# - FUNCTION 제외: 형제 함수 시그니처 누수 방지 (CALLS 와 의미 중복).
# - PARAMETER 제외: 자기 함수 시그니처에 이미 들어있어 fiv 에 또 넣을 필요 X.
#   또한 link 단계가 동명 PARAMETER 노드를 자기 함수 외에도 연결하는 경우가 있어 노이즈 유발.
_REFERENCED_VARS_QUERY = (
    "MATCH (f:FUNCTION)-[:REFERENCES]->(v) "
    "WHERE v:VARIABLE OR v:GLOBAL_VARIABLE OR v:CONSTANT OR v:DEFINE "
    "OPTIONAL MATCH (v)-[:INIT_BY]->(src) "
    "RETURN f.function_id AS fid, "
    "       v.name AS name, v.variable_type AS vtype, "
    "       v.start_line AS line, v.code_text AS code, "
    "       src.code_text AS src_code"
)


async def _collect_referenced_vars(client: Neo4jClient) -> Dict[str, list]:
    """REFERENCES + INIT_BY → function_id 별 변수/상수/매크로 컨텍스트.

    텍스트 포맷은 DBMS ``referenced_variables`` 와 동일:
        ``<name> (<vtype>) → 사용 라인: <line>``
        ``  ↳ 초기화 출처: <src_code>``  (INIT_BY 가 있을 때만)

    Neo4j 조회 실패는 fail-fast (이전 정책과 동일).
    """
    try:
        results = await client.execute_queries([_REFERENCED_VARS_QUERY])
    except Exception as e:
        logger.error(
            "[PHASE:3:CONTEXT] 참조 변수 수집 실패 (Fail-Fast) | %s: %s",
            type(e).__name__, e, exc_info=True,
        )
        raise

    out: Dict[str, list] = {}
    init_by_count = 0
    if results and results[0]:
        for row in results[0]:
            fid = row.get("fid", "")
            name = (row.get("name") or "").strip()
            if not (fid and name):
                continue
            vtype = (row.get("vtype") or "").strip()
            line = row.get("line")
            head = f"{name} ({vtype})" if vtype else name
            if line:
                head += f" → 사용 라인: {line}"
            src_code = (row.get("src_code") or "").strip()
            if src_code:
                entry = f"{head}\n  ↳ 초기화 출처: {src_code}"
                init_by_count += 1
            else:
                entry = head
            out.setdefault(fid, []).append(entry)
    if out:
        logger.info(
            "[PHASE:3:CONTEXT] 참조 변수 수집 | functions=%d init_by_pairs=%d",
            len(out), init_by_count,
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 테이블 컨텍스트 (ddl_context) — schema 는 항상, sample 은 catalog 활성 시만
# ─────────────────────────────────────────────────────────────────────────────


_TABLE_SCHEMA_QUERY = (
    "MATCH (t:Table) "
    "OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column) "
    "WITH t, collect({name: c.name, dtype: c.dtype, "
    "                 nullable: c.nullable, "
    "                 description: c.description}) AS cols "
    "RETURN t.name AS name, t.description AS description, cols"
)


async def _attach_table_context(
    ctx: Dict[str, Any],
    st: Any,
    client: Neo4jClient,
    orchestrator: Any,
    rw_by_fn: Dict[str, list],
) -> None:
    """함수별 ddl_context (schema + 가능 시 sample) 단일 통합 빌더.

    1) 그래프 Table/Column 노드 → schema_map (catalog 무관, 항상 생성).
       sample_map 호환 형식으로 만들어 ``format_table_context`` 재사용.
    2) catalog 활성 시 sample_rows 부착 (schema_map 위 sample_map 머지).
    3) 함수별 전이 호출 그래프로 ``ddl_context_by_function`` 채움.

    catalog 비활성 / datasource 미설정 → schema 만으로 진행 (skip 아님).
    catalog 활성 + 네트워크 실패 → fail-fast (raise).
    """
    schema_map = await _fetch_schema_map(client)
    if not schema_map:
        logger.info("[PHASE:3:TABLE_CTX] Table 노드 0개 — ddl_context 생략")
        return

    if settings.catalog.enabled and orchestrator.datasource_name:
        logger.info(
            "[PHASE:3:CATALOG_CALL] catalog 요청 | datasource=%s tables=%d url=%s",
            orchestrator.datasource_name, len(schema_map), settings.catalog.service_url,
        )
        sample_map = await CatalogClient().sample_context(
            datasource=orchestrator.datasource_name,
            table_names=sorted(schema_map.keys()),
        )
        matched = sum(1 for v in sample_map.values() if v is not None)
        logger.info("[PHASE:3:CATALOG_CALL] catalog 응답 | 매칭=%d/%d", matched, len(sample_map))
        schema_map = _merge_samples_into_schema(schema_map, sample_map)
    else:
        logger.info(
            "[PHASE:3:TABLE_CTX] catalog 비활성 또는 datasource 미설정 — schema 만으로 진행 (sample 미부착)"
        )

    ddl_by_fn = _build_ddl_context_by_function(st, schema_map, rw_by_fn)
    ctx["ddl_context_by_function"] = ddl_by_fn

    all_fids_count = len(st.function_facts_list or [])
    logger.info(
        "[PHASE:3:TABLE_CTX] 함수별 ddl_context 구성 | 함수 매핑=%d/%d tables=%d",
        len(ddl_by_fn), all_fids_count, len(schema_map),
    )


async def _fetch_schema_map(client: Neo4jClient) -> Dict[str, Dict[str, Any]]:
    """그래프 Table/Column 노드에서 catalog sample_map 호환 dict 생성.

    형식: ``{table_name: {resolved, columns:[{name,dtype,nullable,description}], sample_rows:[]}}``
    sample_rows 는 빈 리스트 (catalog 부착 단계에서 채워짐).
    """
    try:
        results = await client.execute_queries([_TABLE_SCHEMA_QUERY])
    except Exception as e:
        logger.error("[PHASE:3:TABLE_CTX] schema 조회 실패 (Fail-Fast) | %s: %s",
                     type(e).__name__, e, exc_info=True)
        raise

    schema_map: Dict[str, Dict[str, Any]] = {}
    rows = results[0] if results and results[0] else []
    for row in rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        cols = [c for c in (row.get("cols") or []) if c.get("name")]
        schema_map[name] = {
            "resolved": name,
            "description": (row.get("description") or "").strip(),
            "columns": cols,
            "sample_rows": [],
        }
    return schema_map


def _merge_samples_into_schema(
    schema_map: Dict[str, Dict[str, Any]],
    sample_map: Dict[str, Optional[Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """catalog 응답의 sample_rows·resolved name 을 schema_map 에 부착."""
    for tname, sample in sample_map.items():
        if not sample or tname not in schema_map:
            continue
        schema_map[tname]["sample_rows"] = sample.get("sample_rows") or []
        resolved = sample.get("resolved")
        if resolved:
            schema_map[tname]["resolved"] = resolved
    return schema_map


def _build_ddl_context_by_function(
    st: Any, schema_map: Dict[str, Dict[str, Any]], rw_by_fn: Dict[str, list],
) -> Dict[str, str]:
    """호출 그래프 전이로 상위 함수가 하위 호출 경유 접근하는 테이블도 포함.

    각 테이블은 ``format_table_context`` (DBMS·framework 공용) 로 포맷.
    """
    direct: Dict[str, set[str]] = {
        fid: {t for t, _rel, _op in triples} for fid, triples in rw_by_fn.items()
    }
    callees: Dict[str, set[str]] = {}
    for ce in (st.calls_edges or []):
        caller = ce.caller_function_id
        callee = ce.callee_function_id
        if caller and callee:
            callees.setdefault(caller, set()).add(callee)

    def transitive(fid: str, seen: set[str]) -> set[str]:
        if fid in seen:
            return set()
        seen.add(fid)
        acc = set(direct.get(fid, set()))
        for cal in callees.get(fid, ()):
            acc |= transitive(cal, seen)
        return acc

    all_fids = {m.function_id for m in (st.function_facts_list or [])}
    ddl_by_fn: Dict[str, str] = {}
    for fid in all_fids:
        tables_used = transitive(fid, set())
        if not tables_used:
            continue
        section = format_table_context(schema_map, sorted(tables_used))
        if section:
            ddl_by_fn[fid] = section
    return ddl_by_fn


__all__ = ["collect_analysis_context"]
