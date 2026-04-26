"""Framework Phase 3 코드 분석에 주입할 컨텍스트 수집기.

원본 위치 (이전):
  - `analyzer/strategy/framework/pipeline/phase_analysis.py::collect_analysis_context`
    (~165줄)

수집 대상:
  - constants_context (CONSTANT 노드 → 텍스트)
  - referenced_vars_by_function (REFERENCES + INIT_BY 출처)
  - codebase_conventions (Phase 1 codebase_profile.summary_sections)
  - reads_writes_by_function (function_table_edges 기반)
  - table_sample_data + table_sample_data_by_function (catalog 1회 호출 + 호출그래프 전이)

정책 정렬:
  - 참조 변수 Neo4j 조회 실패 → fail-fast (이전과 동일)
  - catalog 호출 실패는 정당화된 silent fallback (catalog 미기동도 정상). errlog 강화.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from analyzer.common.pipeline.analysis_context import (
    fetch_constants_context,
    format_table_context,
)
from client.catalog_client import CatalogClient
from client.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


async def collect_analysis_context(st: Any, client: Neo4jClient, orchestrator: Any) -> Dict[str, Any]:
    """LLM 분석 prompt 에 주입할 컨텍스트 dict 반환.

    Returns:
        ``{constants_context, referenced_vars_by_function, table_sample_data,
           codebase_conventions, reads_writes_by_function,
           table_sample_data_by_function}`` (마지막 키는 catalog 성공 시에만)
    """
    ctx: Dict[str, Any] = {
        "constants_context": "",
        "referenced_vars_by_function": {},
        "table_sample_data": "",
        "codebase_conventions": "",
        "reads_writes_by_function": {},
    }

    rw_by_fn = _collect_reads_writes_by_function(st)
    ctx["reads_writes_by_function"] = rw_by_fn

    ctx["codebase_conventions"] = _build_codebase_conventions(st)
    ctx["constants_context"] = await fetch_constants_context(client)
    ctx["referenced_vars_by_function"] = await _collect_referenced_vars(client)

    # catalog 표본 — catalog 미기동/datasource 미설정 시 errlog 후 빈 컨텍스트로 진행 (정당화 fallback)
    await _attach_table_samples(ctx, st, client, orchestrator, rw_by_fn)

    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# 함수별 R/W 매핑 — function_table_edges 기반
# ─────────────────────────────────────────────────────────────────────────────


def _collect_reads_writes_by_function(st: Any) -> Dict[str, list]:
    rw_by_fn: Dict[str, list] = {}
    for e in (st.function_table_edges or []):
        fid = e.function_id
        tbl = (e.table_name or "").strip()
        rel = (e.relation or "").strip().upper()
        if not (fid and tbl and rel):
            continue
        pair = (tbl, rel)
        bucket = rw_by_fn.setdefault(fid, [])
        if pair not in bucket:
            bucket.append(pair)
    if rw_by_fn:
        logger.info(
            "[PHASE:3:CONTEXT] 함수별 R/W 맵 수집 | functions=%d edges=%d",
            len(rw_by_fn), sum(len(v) for v in rw_by_fn.values()),
        )
    return rw_by_fn


# ─────────────────────────────────────────────────────────────────────────────
# 코드베이스 컨벤션 (Phase 1 codebase_profile.summary_sections)
# ─────────────────────────────────────────────────────────────────────────────


def _build_codebase_conventions(st: Any) -> str:
    cp = getattr(st, "codebase_profile", None)
    if not (cp and getattr(cp, "summary_sections", None)):
        return ""
    sections = cp.summary_sections
    blocks: list[str] = []
    for key in ("framework", "data_access", "idioms"):
        block = sections.get(key)
        if block:
            blocks.append(block)
    if not blocks:
        return ""
    text = "\n\n".join(blocks)
    logger.info(
        "[PHASE:3:CONTEXT] 코드베이스 컨벤션 주입 | sections=%s len=%d",
        ",".join(sorted(sections.keys())), len(text),
    )
    return text


# ─────────────────────────────────────────────────────────────────────────────
# 참조 변수 + INIT_BY 초기화 출처
# ─────────────────────────────────────────────────────────────────────────────


_REFERENCED_VARS_QUERY = (
    "MATCH (f:FUNCTION)-[:REFERENCES]->(v) "
    "OPTIONAL MATCH (v)-[:INIT_BY]->(src) "
    "RETURN f.function_id AS fid, v.code_text AS code, "
    "       src.name AS src_name, src.code_text AS src_code"
)


async def _collect_referenced_vars(client: Neo4jClient) -> Dict[str, list]:
    """REFERENCES + INIT_BY → function_id 별 변수 코드 컨텍스트.

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
            code = (row.get("code") or "").strip()
            if not (fid and code):
                continue
            src_code = (row.get("src_code") or "").strip()
            if src_code:
                entry = f"{code}\n  ↳ 초기화 출처: {src_code}"
                init_by_count += 1
            else:
                entry = code
            out.setdefault(fid, []).append(entry)
    if out:
        logger.info(
            "[PHASE:3:CONTEXT] 참조 변수 수집 | functions=%d init_by_pairs=%d",
            len(out), init_by_count,
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# catalog 호출로 테이블 샘플 + 함수별 전이적 매핑 부착
# ─────────────────────────────────────────────────────────────────────────────


async def _attach_table_samples(
    ctx: Dict[str, Any],
    st: Any,
    client: Neo4jClient,
    orchestrator: Any,
    rw_by_fn: Dict[str, list],
) -> None:
    """catalog 1회 batch 호출로 sample_map 받고 ctx 에 table_sample_data 부착.

    실패 시 errlog 후 빈 컨텍스트로 return — catalog 미기동도 정상 시나리오라 raise 하지 않음.
    """
    datasource_name = orchestrator.datasource_name
    if not datasource_name:
        logger.info("[PHASE:3:TABLE_CTX] datasource 미설정 — 테이블 샘플 수집 skip")
        return

    reads_writes = st.reads_writes or []
    try:
        identified = _gather_identified_tables(reads_writes, await _fetch_all_table_names(client))
        if not identified:
            logger.info("[PHASE:3:TABLE_CTX] 식별 테이블 0개 — catalog 호출 생략")
            return

        logger.info(
            "[PHASE:3:CATALOG_CALL] catalog 요청 | datasource=%s tables=%d",
            datasource_name, len(identified),
        )
        sample_map = await CatalogClient().sample_context(
            datasource=datasource_name,
            table_names=sorted(identified),
        )
        matched = sum(1 for v in sample_map.values() if v is not None)
        logger.info(
            "[PHASE:3:CATALOG_CALL] catalog 응답 | 매칭=%d/%d", matched, len(sample_map),
        )

        ctx["table_sample_data"] = format_table_context(sample_map, sorted(identified))

        # 호출 그래프 전이로 함수별 컨텍스트 구성
        ts_by_fn = _build_table_samples_by_function(st, sample_map, rw_by_fn)
        ctx["table_sample_data_by_function"] = ts_by_fn

        all_fids_count = len(st.function_facts_list or [])
        logger.info(
            "[PHASE:3:TABLE_CTX] 함수별 컨텍스트 구성 | 함수 매핑=%d/%d 전체 data_len=%d",
            len(ts_by_fn), all_fids_count, len(ctx["table_sample_data"]),
        )
    except Exception as e:  # noqa: SILENT_FAIL — catalog 미기동도 정상 시나리오 (LLM 컨텍스트 보강은 옵셔널)
        logger.error(
            "[PHASE:3:TABLE_CTX] 테이블 샘플 수집 skip (catalog 미기동 가능성) | %s: %s",
            type(e).__name__, e, exc_info=True,
        )


async def _fetch_all_table_names(client: Neo4jClient) -> set[str]:
    results = await client.execute_queries([
        "MATCH (t:Table) RETURN DISTINCT t.name AS name"
    ])
    return {r["name"] for r in (results[0] or []) if r.get("name")}


def _gather_identified_tables(reads_writes: list, all_tables: set[str]) -> set[str]:
    """링킹이 식별한 테이블 이름.

    0개이면 Neo4j Table 노드 전체로 폴백 — LLM 컨텍스트 보강용으로,
    링킹이 식별 못 한 시나리오(예: SQL 분석 누락)에서도 catalog 샘플을 부착하기 위함.
    분석 정확도 자체에는 영향 없음 (보강 컨텍스트 제공 범위만 결정).
    """
    identified = {rw.table_name for rw in reads_writes if rw.table_name}
    if not identified:
        identified = all_tables
    return identified


def _build_table_samples_by_function(
    st: Any, sample_map: Dict[str, Any], rw_by_fn: Dict[str, list],
) -> Dict[str, str]:
    """호출 그래프 전이로 상위 함수가 하위 호출 경유 접근하는 테이블도 포함."""
    direct: Dict[str, set[str]] = {
        fid: {t for t, _op in pairs} for fid, pairs in rw_by_fn.items()
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
    ts_by_fn: Dict[str, str] = {}
    for fid in all_fids:
        tables_used = transitive(fid, set())
        if not tables_used:
            continue
        section = format_table_context(sample_map, sorted(tables_used))
        if section:
            ts_by_fn[fid] = section
    return ts_by_fn


__all__ = ["collect_analysis_context"]
