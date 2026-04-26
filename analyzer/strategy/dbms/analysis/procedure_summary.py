"""DBMS 프로시저 단위 요약 — LLM 호출 + Bottom-up Rule 조립.

원본 위치 (이전):
  - `analyzer/strategy/dbms/pipeline/phase_summary.py` 의
    `_analyze_summary_only`, `_split_summaries_by_token`, `_collect_variable_declarations`,
    `_collect_called_procedure_summaries` 그리고 `process_procedure` 클로저 일부
    + `_build_final_analysis` (Bottom-up Rule 조립)

책임 (한 도메인 = 한 파일):
  1. 프로시저별 LLM 호출 (`unit_analysis` rule)
  2. 토큰 한도 기반 chunk 분할
  3. DECLARE 변수 선언 수집 (LLM 컨텍스트 강화)
  4. CALLS 관계로 연결된 호출 프로시저 요약 수집 (Neo4j 조회)
  5. chunk 결과 → 최종 summary 통합
  6. Bottom-up Rule/Example 조립 (analysis_map → EM Cypher 빌더 입력)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.code_text_utils import calculate_code_token
from util.llm_rule_runner import prompts
from util.llm_call_safety import get_executor

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 입력 컨텍스트 수집
# ═══════════════════════════════════════════════════════════════════════════


def collect_variable_declarations(
    nodes: List[Any],
    proc_key: str,
    var_types: frozenset,
) -> str:
    """프로시저 내 DECLARE 블록의 변수 선언/초기값을 수집.

    Args:
        nodes: 처리기의 StatementNode 리스트 (`processor._nodes`)
        proc_key: 프로시저 unit_key
        var_types: VARIABLE_DECLARATION_TYPES (PACKAGE_VARIABLE/DECLARE/SPEC)
    """
    parts: List[str] = []
    for node in nodes:
        if node.unit_key == proc_key and node.node_type in var_types and node.code:
            code_line = " ".join(node.code.strip().split())
            if code_line:
                parts.append(code_line)
    return " | ".join(parts)


async def collect_called_procedure_summaries(
    client: Neo4jClient,
    proc_name: str,
    directory: str,
    file_name: str,
    *,
    max_items: int = 20,
) -> str:
    """CALLS 관계로 연결된 프로시저들의 summary 를 수집.

    PARENT_OF*-CALLS 경로로 호출 대상 프로시저를 찾아 summary 가 있는 것만 수집.
    실패 시 fail-fast (errlog + raise) — silent fallback 금지.
    """
    query = (
        "MATCH (p {procedure_name: $proc_name, directory: $directory, file_name: $file_name})"
        "-[:PARENT_OF*]->(node)-[:CALLS]->(callee) "
        "WHERE callee.summary IS NOT NULL AND callee.summary <> '' "
        "RETURN DISTINCT callee.procedure_name AS name, callee.summary AS summary "
        "ORDER BY name"
    )
    params = {"proc_name": proc_name, "directory": directory, "file_name": file_name}
    try:
        results = await client.execute_queries([query], params)
    except Exception as e:
        logger.error(
            "[PROCEDURE_SUMMARY] 호출 프로시저 요약 수집 실패 | proc=%s | %s: %s",
            proc_name, type(e).__name__, e, exc_info=True,
        )
        raise RuntimeError(
            f"호출 프로시저 요약 Neo4j 조회 실패: proc={proc_name} ({type(e).__name__}: {e})"
        ) from e

    rows = results[0] if results and results[0] else []
    parts: List[str] = []
    for r in rows[:max_items]:
        name = (r.get("name") or "").strip()
        summ = (r.get("summary") or "").strip()
        if not summ:
            continue
        parts.append(f"- {name}: {summ}" if name else f"- {summ}")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# 토큰 한도 기반 chunk 분할
# ═══════════════════════════════════════════════════════════════════════════


def split_summaries_by_token(summaries: Dict[str, str], max_token: int) -> List[Dict[str, str]]:
    """summary 사전을 토큰 한도 내에서 여러 chunk 로 분할.

    각 항목 ``{key: value}`` 의 텍스트 토큰을 합산하면서 max_token 초과 시 chunk 확정.
    """
    if not summaries:
        return []
    chunks: List[Dict[str, str]] = []
    current_chunk: Dict[str, str] = {}
    current_tokens = 0
    for key, value in summaries.items():
        item_text = f"{key}: {value}"
        item_tokens = calculate_code_token(item_text)
        if current_tokens + item_tokens > max_token and current_chunk:
            chunks.append(current_chunk)
            current_chunk = {}
            current_tokens = 0
        current_chunk[key] = value
        current_tokens += item_tokens
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


# ═══════════════════════════════════════════════════════════════════════════
# unit_analysis LLM 호출
# ═══════════════════════════════════════════════════════════════════════════


def invoke_unit_analysis(
    summaries: Dict[str, str],
    api_key: str,
    locale: str,
    *,
    variable_declarations: str = "",
    constants_context: str = "",
    called_procedure_summaries: str = "",
) -> Dict[str, Any]:
    """rules/dbms/unit_analysis.yaml 호출 — 프로시저 전체 summary 한 편 생성."""
    inputs: Dict[str, Any] = {"summaries": summaries, "locale": locale}
    if variable_declarations:
        inputs["variable_declarations"] = variable_declarations
    if constants_context:
        inputs["constants_context"] = constants_context
    if called_procedure_summaries:
        inputs["called_procedure_summaries"] = called_procedure_summaries
    return prompts.execute_raw("dbms", "unit_analysis", inputs, api_key)


# ═══════════════════════════════════════════════════════════════════════════
# 메인: 프로시저 1개 → 통합 summary 문자열
# ═══════════════════════════════════════════════════════════════════════════


async def run_chunks_continuous(
    chunks: List[Any],
    processor_fn,
    label: str,
) -> List[Any]:
    """chunk 리스트를 AsyncBatchExecutor 의 worker pool 에서 continuous 실행.

    순수 LLM 병렬 사이트의 표준 패턴 — 결과는 완료 순으로 수집.
    """
    executor = get_executor()
    results: List[Any] = []

    def _on(r):
        if r is not None:
            results.append(r)

    await executor.run_batch(items=chunks, processor=processor_fn, on_result=_on, label=label)
    return results


async def summarize_procedure(
    *,
    summaries: Dict[str, str],
    deep_compact_code: str,
    api_key: str,
    locale: str,
    variable_declarations: str = "",
    constants_context: str = "",
    called_procedure_summaries: str = "",
) -> str:
    """프로시저 1개의 통합 summary 생성.

    deep_compact_code 가 있으면 그것을 단일 PROCEDURE_COMPACT chunk 로 사용.
    없으면 입력 summaries 를 토큰 한도로 분할해서 chunk 별 LLM 호출 후 통합.
    """
    max_chunk_token = settings.batch.max_summary_chunk_token

    if deep_compact_code and deep_compact_code.strip():
        chunks = split_summaries_by_token(
            {"PROCEDURE_COMPACT": deep_compact_code.strip()}, max_chunk_token
        )
    else:
        chunks = split_summaries_by_token(summaries, max_chunk_token)

    if not chunks:
        return ""

    async def process_chunk(chunk: Dict[str, str]) -> Dict[str, Any]:
        result = await asyncio.to_thread(
            invoke_unit_analysis,
            chunk, api_key, locale,
            variable_declarations=variable_declarations,
            constants_context=constants_context,
            called_procedure_summaries=called_procedure_summaries,
        )
        if not isinstance(result, dict):
            logger.error(
                "[PROCEDURE_SUMMARY] unit_analysis 결과가 dict 아님 | type=%s",
                type(result).__name__,
            )
            raise RuntimeError(
                f"unit_analysis LLM 결과가 dict 가 아님: {type(result).__name__}"
            )
        return result

    chunk_results = await run_chunks_continuous(chunks, process_chunk, label="DBMS_UNIT_SUMMARY")
    chunk_summaries = [r.get("summary", "") for r in chunk_results if r and r.get("summary")]

    if not chunk_summaries:
        return ""
    if len(chunk_summaries) == 1:
        return chunk_summaries[0]

    # chunk 가 2개 이상이면 LLM 1회 더 호출해 통합 summary 생성.
    # 실패 시 fail-fast — 폴백 join 금지 (정책: 모든 폴백 제거).
    combined = {f"CHUNK_{i+1}": s for i, s in enumerate(chunk_summaries)}
    merged = await asyncio.to_thread(
        invoke_unit_analysis,
        combined, api_key, locale,
        variable_declarations=variable_declarations,
        constants_context=constants_context,
        called_procedure_summaries=called_procedure_summaries,
    )
    if not isinstance(merged, dict):
        logger.error(
            "[PROCEDURE_SUMMARY] chunk 통합 LLM 결과가 dict 아님 | type=%s chunks=%d",
            type(merged).__name__, len(chunk_summaries),
        )
        raise RuntimeError(
            f"chunk 통합 LLM 결과가 dict 가 아님: {type(merged).__name__}"
        )
    final = merged.get("summary")
    if not final:
        logger.error(
            "[PROCEDURE_SUMMARY] chunk 통합 LLM 결과에 summary 누락 | chunks=%d",
            len(chunk_summaries),
        )
        raise RuntimeError(
            f"chunk 통합 LLM 결과에 summary 누락 (chunks={len(chunk_summaries)})"
        )
    return final


# ═══════════════════════════════════════════════════════════════════════════
# Bottom-up Rule/Example 조립 (analysis_map → EM Cypher 빌더 입력)
# ═══════════════════════════════════════════════════════════════════════════


def assemble_final_analysis(proc_root: Any) -> Dict[str, Any]:
    """``proc_root.analysis_map`` (Bottom-up 결과) → EM Cypher 빌더 입력 dict.

    ast_analyzer 가 노드 트리에 채워준 raw rules + questions 를 phase_summary 가
    EM Cypher 빌더에 넘길 형태로 변환. rule_id 순번 부여 (R1, R2, ...) +
    statement 조립 (condition + " 이면 " + result). Rule 안 nested examples 를
    flat 리스트로 분리하면서 rule_id 연결.

    proc_root.analysis_map 이 빈 dict 면 그 프로시저에 룰이 없는 것으로 해석 (폴백 없음).

    Returns:
        ``{"rules": [...], "examples": [...], "questions": [...]}``
        rules 의 각 항목은 ``{rule_id, flow_id, guard_rule_id, branch_from, statement,
        coupled_domains}`` 형태.
    """
    if not proc_root or not getattr(proc_root, "analysis_map", None):
        return {"rules": [], "examples": [], "questions": []}

    raw_rules = proc_root.analysis_map.get("rules") or []
    raw_questions = proc_root.analysis_map.get("questions") or []

    final_rules: List[Dict[str, Any]] = []
    final_examples: List[Dict[str, Any]] = []

    for idx, r in enumerate(raw_rules, start=1):
        if not isinstance(r, dict):
            continue
        rule_id = f"R{idx}"
        cond = (r.get("condition") or "").strip()
        result = (r.get("result") or "").strip()
        if not result:
            continue
        statement = f"{cond} 이면 {result}" if cond else result

        final_rules.append({
            "rule_id": rule_id,
            "flow_id": str(idx),
            "guard_rule_id": None,
            "branch_from": None,
            "statement": statement,
            "coupled_domains": [],
        })

        # Rule 안 nested examples → flat 리스트로 분리, rule_id 연결
        for ex in (r.get("examples") or []):
            if not isinstance(ex, dict):
                continue
            ex_copy = dict(ex)
            ex_copy["rule_id"] = rule_id
            final_examples.append(ex_copy)

    return {
        "rules": final_rules,
        "examples": final_examples,
        "questions": raw_questions,
    }


__all__ = [
    "collect_variable_declarations",
    "collect_called_procedure_summaries",
    "split_summaries_by_token",
    "invoke_unit_analysis",
    "run_chunks_continuous",
    "summarize_procedure",
    "assemble_final_analysis",
]
