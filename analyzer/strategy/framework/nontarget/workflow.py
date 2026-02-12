"""Nontarget 분석 파이프라인 워커 함수

Step 1: 청킹 2-Pass (ChunkerAgent — DeepAgent)
Step 2: 요약 (RuleLoader LLM chain — 배치 병렬)
Step 3: 링크 (확정적 Cypher + LLM chain 파이프라인)
Step 4: Description (RuleLoader LLM chain)

Step 1만 DeepAgent 사용, Step 2/3/4는 고정 파이프라인.
"""

import asyncio
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from analyzer.strategy.framework.nontarget.agents.deepagents_client import (
    DeepAgentsClient,
    extract_agent_output,
    parse_json_from_output,
)
from analyzer.strategy.framework.nontarget.processors.block_splitter import (
    BlockJSON,
    ChunkerContext,
    ChunkingRule,
    BoundaryCandidate,
    OpenBlockState,
    read_file_with_line_numbers,
    make_overlap_windows,
    split_blocks,
)
from analyzer.strategy.framework.nontarget.tools.context_tool import (
    count_tokens_tiktoken,
)
from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget
from analyzer.strategy.framework.nontarget.cypher_templates import (
    commit_block_method_refers_to,
)
from config.settings import settings
from util.llm_audit import log_llm_interaction, ainvoke_with_audit
from util.rule_loader import RuleLoader
from client.llm_client import get_llm
from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough


# ============================================================================
# Context
# ============================================================================

@dataclass
class RuntimeCtx:
    """런타임 컨텍스트 — Neo4j 클라이언트, 락"""
    client: Any = None
    cypher_lock: Optional[asyncio.Lock] = None


# ============================================================================
# RuleLoader / LLM chain 싱글턴 (모듈 레벨 캐싱)
# ============================================================================

_rule_loader_instance: Optional[RuleLoader] = None
_llm_chain_instance = None


def _nontarget_rule_loader() -> RuleLoader:
    """nontarget 전용 RuleLoader (싱글턴)"""
    global _rule_loader_instance
    if _rule_loader_instance is None:
        _rule_loader_instance = RuleLoader(target_lang="nontarget")
    return _rule_loader_instance


def _get_llm_chain():
    """nontarget 분석용 LLM chain (싱글턴)"""
    global _llm_chain_instance
    if _llm_chain_instance is None:
        llm = get_llm(api_key=settings.llm.api_key)
        _llm_chain_instance = (
            RunnablePassthrough()
            | PromptTemplate.from_template("{prompt}")
            | llm
            | JsonOutputParser()
        )
    return _llm_chain_instance


# ============================================================================
# Step 1: 청킹 2-Pass (DeepAgent 유지)
# ============================================================================

async def run_chunker_2pass(
    file_path: str,
    ctx: RuntimeCtx,
    deepagents_client: Optional[DeepAgentsClient] = None,
    window_size: int = 500,
    overlap: int = 50,
) -> Tuple[str, List[Dict[str, Any]]]:
    """2-Pass 청킹: Pass1(패턴학습) + Pass2(시스템분리)

    Args:
        file_path: 분석 대상 파일 경로
        ctx: RuntimeCtx
        deepagents_client: DeepAgentsClient (None이면 새로 생성)

    Returns:
        (file_path, List[BlockJSON dict])
    """
    log_nontarget("WORKFLOW", "[Step 1] 청킹 2-Pass 시작", file_path=file_path)

    if deepagents_client is None:
        deepagents_client = DeepAgentsClient()

    # 파일 읽기 + 라인번호 부착
    try:
        raw_lines, _ = read_file_with_line_numbers(file_path)
    except Exception as e:
        log_nontarget("WORKFLOW", f"파일 읽기 실패: {e}", file_path=file_path)
        return file_path, []

    if not raw_lines:
        return file_path, []

    # 오버랩 윈도우 생성
    windows = make_overlap_windows(raw_lines, window_size, overlap)
    log_nontarget("WORKFLOW", f"윈도우 {len(windows)}개 생성 ({len(raw_lines)}줄)", file_path=file_path)

    # === Pass 1: 패턴 학습 ===
    chunker_agent = deepagents_client.create_chunker_agent(ctx)
    chunker_ctx = ChunkerContext()

    for win in windows:
        win_idx = win["window_idx"]
        log_nontarget("WORKFLOW", f"Pass1 윈도우 {win_idx + 1}/{len(windows)}", file_path=file_path)

        task_msg = json.dumps({
            "window_text": win["text"],
            "window_idx": win_idx,
            "total_windows": len(windows),
            "start_line": win["start_line"],
            "end_line": win["end_line"],
            "is_last": win["is_last"],
            "previous_ctx": chunker_ctx.to_dict(),
        }, ensure_ascii=False)

        try:
            result = await chunker_agent.ainvoke({
                "messages": [HumanMessage(content=task_msg)]
            })
            output = extract_agent_output(result)
            parsed = parse_json_from_output(output)

            log_llm_interaction(
                prompt_name="chunker",
                input_payload={"task": task_msg, "window_idx": win_idx, "total_windows": len(windows)},
                output_payload=parsed,
                metadata={
                    "file_path": file_path,
                    "window_idx": win_idx,
                    "start_line": win["start_line"],
                    "end_line": win["end_line"],
                },
                sort_key=win["start_line"],
            )

            chunker_ctx = _merge_chunker_response(chunker_ctx, parsed)

        except Exception as e:
            log_nontarget("WORKFLOW", f"Pass1 윈도우 {win_idx + 1} 실패: {e}", file_path=file_path)

    log_nontarget(
        "WORKFLOW",
        f"Pass1 완료: 규칙 {len(chunker_ctx.rules)}개, "
        f"경계후보 {len(chunker_ctx.boundaries)}개",
        file_path=file_path,
    )

    # === Pass 2: 시스템 분리 ===
    blocks = split_blocks(file_path, raw_lines, chunker_ctx)
    blocks_dict = [b.to_dict() for b in blocks]

    log_nontarget("WORKFLOW", f"[Step 1] 완료: {len(blocks_dict)}개 블록", file_path=file_path)
    return file_path, blocks_dict


def _merge_chunker_response(
    ctx: ChunkerContext,
    response: Dict[str, Any],
) -> ChunkerContext:
    """ChunkerAgent 응답을 기존 ctx에 병합합니다."""
    new_rules = response.get("rules", [])
    existing_ids = {r.rule_id for r in ctx.rules}
    for r in new_rules:
        rule = ChunkingRule(
            rule_id=r.get("rule_id", f"r{len(ctx.rules)}"),
            pattern=r.get("pattern", ""),
            rule_type=r.get("rule_type", "header"),
            priority=r.get("priority", 5),
            confidence=r.get("confidence", 0.5),
            examples=r.get("examples", []),
        )
        if rule.rule_id in existing_ids:
            ctx.rules = [
                rule if old.rule_id == rule.rule_id else old
                for old in ctx.rules
            ]
        else:
            ctx.rules.append(rule)
            existing_ids.add(rule.rule_id)

    existing_lines = {b.line_no for b in ctx.boundaries}
    for b in response.get("boundaries", []):
        line_no = b.get("line_no", 0)
        if line_no > 0 and line_no not in existing_lines:
            ctx.boundaries.append(BoundaryCandidate(
                line_no=line_no,
                reason=b.get("reason", ""),
                rule_id=b.get("rule_id", ""),
                confidence=b.get("confidence", 0.5),
            ))
            existing_lines.add(line_no)

    ob = response.get("open_block")
    if ob:
        ctx.open_block = OpenBlockState(
            start_line=ob.get("start_line", 0),
            expected_end_pattern=ob.get("expected_end_pattern", ""),
            depth=ob.get("depth", 0),
        )
    elif ob is None and response.get("boundaries"):
        ctx.open_block = None

    # corrections 로깅 (에이전트가 이전 판단을 번복/수정한 내역)
    corrections = response.get("corrections", [])
    if corrections:
        log_nontarget("WORKFLOW", f"corrections {len(corrections)}건: {corrections[:3]}")

    return ctx


# ============================================================================
# Step 2: 요약 (RuleLoader LLM chain)
# ============================================================================

async def run_summarizer_batched(
    file_path: str,
    blocks: List[Dict[str, Any]],
    ctx: RuntimeCtx,
    max_batch_tokens: int = 2000,
    batch_concurrency: int = 20,
) -> Tuple[str, List[Dict[str, Any]]]:
    """블록을 start_line 정렬 + tiktoken 배치로 LLM chain 병렬 호출

    Args:
        file_path: 파일 경로
        blocks: BlockJSON dict 리스트
        ctx: RuntimeCtx
        max_batch_tokens: 배치당 최대 토큰
        batch_concurrency: 배치 병렬 처리 동시성 제한

    Returns:
        (file_path, List[SummaryJSON])
    """
    log_nontarget("WORKFLOW", f"[Step 2] 요약 시작: {len(blocks)}개 블록", file_path=file_path)

    if not blocks:
        return file_path, []

    sorted_blocks = sorted(blocks, key=lambda b: b.get("start_line", 0))
    batches = _make_token_batches(sorted_blocks, max_batch_tokens)
    log_nontarget("WORKFLOW", f"배치 {len(batches)}개 생성 (병렬: {batch_concurrency}개씩)", file_path=file_path)

    rule_loader = _nontarget_rule_loader()
    semaphore = asyncio.Semaphore(batch_concurrency)

    async def _process_batch(
        batch_idx: int,
        batch: List[Dict[str, Any]],
    ) -> Tuple[int, List[Dict[str, Any]]]:
        async with semaphore:
            log_nontarget("WORKFLOW", f"배치 {batch_idx + 1}/{len(batches)} ({len(batch)}개 블록)", file_path=file_path)

            batch_input = [
                {
                    "block_name": b.get("name", ""),
                    "file_path": b.get("file_path", file_path),
                    "start_line": b.get("start_line", 0),
                    "end_line": b.get("end_line", 0),
                    "node_code": b.get("node_code", ""),
                }
                for b in batch
            ]

            inputs = {
                "blocks": batch_input,
                "file_path": file_path,
                "batch_idx": batch_idx,
                "total_batches": len(batches),
            }

            try:
                prompt_text = rule_loader.render_prompt("summary", inputs)
                chain = _get_llm_chain()

                parsed = await ainvoke_with_audit(
                    chain,
                    {"prompt": prompt_text},
                    prompt_name="nontarget/summary",
                    input_payload={"batch_idx": batch_idx, "batch_size": len(batch), "file_path": file_path},
                    metadata={
                        "file_path": file_path,
                        "batch_idx": batch_idx,
                        "total_batches": len(batches),
                        "block_count": len(batch),
                    },
                    sort_key=batch[0].get("start_line", 0) if batch else None,
                )

                results = parsed.get("results", []) if isinstance(parsed, dict) else []
                for r in results:
                    r.setdefault("file_path", file_path)
                return batch_idx, results

            except Exception as e:
                log_nontarget("WORKFLOW", f"배치 {batch_idx + 1} 실패: {e}", file_path=file_path)
                return batch_idx, []

    batch_results = await asyncio.gather(*[
        _process_batch(batch_idx, batch)
        for batch_idx, batch in enumerate(batches)
    ])

    batch_results_sorted = sorted(batch_results, key=lambda x: x[0])
    all_summaries: List[Dict[str, Any]] = []
    for _, results in batch_results_sorted:
        all_summaries.extend(results)

    log_nontarget("WORKFLOW", f"[Step 2] 완료: {len(all_summaries)}개 요약", file_path=file_path)
    return file_path, all_summaries


def _make_token_batches(
    blocks: List[Dict[str, Any]],
    max_tokens: int,
) -> List[List[Dict[str, Any]]]:
    """블록을 tiktoken 기준 배치로 묶습니다."""
    batches: List[List[Dict[str, Any]]] = []
    current_batch: List[Dict[str, Any]] = []
    current_tokens = 0

    for block in blocks:
        code = block.get("node_code", "")
        tokens = count_tokens_tiktoken(code)

        if current_batch and current_tokens + tokens > max_tokens:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(block)
        current_tokens += tokens

    if current_batch:
        batches.append(current_batch)

    return batches


# ============================================================================
# Step 2.5: FileSummary 생성 (시스템 로직, LLM 불필요)
# ============================================================================

def build_file_summaries(
    chunk_results: List[Tuple[str, List[Dict[str, Any]]]],
    summary_results: List[Tuple[str, List[Dict[str, Any]]]],
) -> Dict[str, Dict[str, Any]]:
    """블록별 summary/kind/keywords를 파일 단위로 집계합니다."""
    summaries_by_file: Dict[str, List[Dict[str, Any]]] = {}
    for file_path, sums in summary_results:
        summaries_by_file.setdefault(file_path, []).extend(sums)

    file_summaries: Dict[str, Dict[str, Any]] = {}

    for file_path, blocks in chunk_results:
        sums = summaries_by_file.get(file_path, [])

        kind_counts: Dict[str, int] = defaultdict(int)
        all_keywords: List[str] = []
        all_tables: List[str] = []
        summary_texts: List[str] = []
        all_fk_inferred: List[Dict[str, Any]] = []

        for s in sums:
            kind = s.get("kind", "unknown")
            kind_counts[kind] += 1
            all_keywords.extend(s.get("keywords", []))
            summary_texts.append(s.get("summary", ""))

            for t in s.get("tables", []):
                tname = t.get("name", "")
                if tname:
                    all_tables.append(tname)

            all_fk_inferred.extend(s.get("fk_inferred", []))

        sorted_kinds = sorted(kind_counts.items(), key=lambda x: x[1], reverse=True)
        top_kinds = [k for k, _ in sorted_kinds[:2]]

        kw_counts: Dict[str, int] = defaultdict(int)
        for kw in all_keywords:
            kw_counts[kw] += 1
        top_keywords = [
            kw for kw, _ in sorted(kw_counts.items(), key=lambda x: x[1], reverse=True)[:30]
        ]

        file_summary_text = " | ".join(
            s[:80] for s in summary_texts[:5] if s.strip()
        )

        file_summaries[file_path] = {
            "kinds": top_kinds,
            "keywords": top_keywords,
            "tables": list(set(all_tables)),
            "file_summary_text": file_summary_text,
            "block_count": len(blocks),
            "summary_count": len(sums),
            "fk_inferred": all_fk_inferred,
        }

    log_nontarget("WORKFLOW", f"FileSummary 생성 완료: {len(file_summaries)}개 파일")
    return file_summaries


_KEYWORD_SEARCH_LIMIT = 10


async def _find_nontarget_blocks_by_keyword(
    file_path: str,
    keyword: str,
    ctx: RuntimeCtx,
) -> List[Dict[str, Any]]:
    """키워드를 포함하는 NONTARGET_BLOCK(Query/Schema) + 관련 테이블(role 포함) 검색"""
    if not ctx.client:
        return []

    query = """
    MATCH (b:NONTARGET_BLOCK)
    WHERE b.file_path = $file_path
      AND toLower(b.kind) IN ['query', 'schema']
      AND $keyword IN b.keywords
    OPTIONAL MATCH (b)-[r:REFERS_TO]->(t:Table)
    RETURN b.name AS block_name,
           b.file_path AS file_path,
           b.summary AS summary,
           collect(DISTINCT {table_name: t.name, role: r.role}) AS tables
    """
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    query, {"file_path": file_path, "keyword": keyword}
                )
        else:
            result = await ctx.client.execute_with_params(
                query, {"file_path": file_path, "keyword": keyword}
            )

        if not result:
            return []
        return [dict(r) if isinstance(r, dict) else r.data() for r in result]

    except Exception as e:
        log_nontarget("WORKFLOW", f"NONTARGET_BLOCK 검색 실패 ({keyword}): {e}", file_path=file_path)
        return []


async def run_linker(
    file_path: str,
    file_summary: Dict[str, Any],
    ctx: RuntimeCtx,
) -> Tuple[str, Dict[str, Any]]:
    """Linker: 키워드 기반 메서드 검색 + LLM 분석으로 FROM/WRITES만 생성

    흐름:
    - Phase A: Keyword → Method 매칭 (Cypher CONTAINS)
    - Phase B: Method 분석 (병렬 LLM chain)
    - 결과에서 FROM/WRITES 관계만 수집 (FK는 Summarizer 전용)

    Args:
        file_path: 파일 경로
        file_summary: FileSummary
        ctx: RuntimeCtx

    Returns:
        (file_path, {from, writes})
    """
    log_nontarget("WORKFLOW", "[Step 3] 링크 시작", file_path=file_path)

    results: Dict[str, List] = {
        "from": [],
        "writes": [],
    }

    if not ctx.client:
        return file_path, results

    all_keywords = file_summary.get("keywords", [])

    if not all_keywords:
        log_nontarget("WORKFLOW", "키워드 없음 -> 링크 스킵", file_path=file_path)
        return file_path, results

    log_nontarget("WORKFLOW", f"키워드 {len(all_keywords)}개로 검색 시작", file_path=file_path)

    keyword_matches = await asyncio.gather(*[
        _search_methods_by_keyword(kw, ctx) for kw in all_keywords
    ])

    matched_pairs: List[Tuple[str, Dict[str, Any]]] = []
    for kw, methods in zip(all_keywords, keyword_matches):
        if methods:
            for m in methods:
                matched_pairs.append((kw, m))
            log_nontarget("WORKFLOW", f"  keyword '{kw}': {len(methods)}개 메서드 발견", file_path=file_path)

    if not matched_pairs:
        log_nontarget("WORKFLOW", "매칭된 메서드 없음 -> 링크 스킵", file_path=file_path)
        return file_path, results

    log_nontarget("WORKFLOW", f"Phase A 완료: {len(matched_pairs)}개 (keyword, method) 쌍", file_path=file_path)

    rule_loader = _nontarget_rule_loader()
    analysis_sem = asyncio.Semaphore(10)

    # 키워드별로 관련 논타겟 블록(summaries + tables/role) 미리 조회
    block_matches = await asyncio.gather(*[
        _find_nontarget_blocks_by_keyword(file_path, kw, ctx) for kw in all_keywords
    ])
    blocks_by_keyword: Dict[str, List[Dict[str, Any]]] = {
        kw: blocks for kw, blocks in zip(all_keywords, block_matches)
    }

    # LLM 분석 (논타겟 블록 정보 + 메서드 코드 동시 전달)
    analysis_results = await asyncio.gather(*[
        _analyze_method(kw, m, rule_loader, blocks_by_keyword.get(kw, []), analysis_sem, file_path)
        for kw, m in matched_pairs
    ])

    # NONTARGET_BLOCK → METHOD REFERS_TO 생성을 위한 링크 수집
    block_method_links: List[Dict[str, Any]] = []

    for (kw, method_info), analysis in zip(matched_pairs, analysis_results):
        if analysis is None:
            continue

        _collect_links(results, analysis, method_info, file_path)

        # 같은 키워드를 가진 NONTARGET_BLOCK(Query/Schema)들을 METHOD와 연결
        method_id = method_info.get("id", "")
        if not method_id:
            continue

        for b in blocks_by_keyword.get(kw, []):
            block_method_links.append({
                "block_name": b.get("block_name", ""),
                "file_path": b.get("file_path", file_path),
                "method_id": method_id,
                "keyword": kw,
                "evidence": (b.get("summary") or "")[:200],
            })

    # NONTARGET_BLOCK → METHOD REFERS_TO 커밋
    if block_method_links:
        await commit_block_method_refers_to(ctx, block_method_links)

    total = sum(len(v) for v in results.values())
    log_nontarget(
        "WORKFLOW",
        f"[Step 3] 완료: FROM {len(results['from'])}개, WRITES {len(results['writes'])}개 (총 {total}개)",
        file_path=file_path,
    )
    return file_path, results


async def _search_methods_by_keyword(
    keyword: str,
    ctx: RuntimeCtx,
) -> List[Dict[str, Any]]:
    """Cypher CONTAINS로 keyword를 포함하는 METHOD/CONSTRUCTOR/FIELD 검색"""
    if not ctx.client:
        return []

    query = """
    MATCH (n)
    WHERE n.node_code IS NOT NULL
      AND n.node_code CONTAINS $keyword
      AND (n:METHOD OR n:CONSTRUCTOR OR n:FIELD)
    RETURN elementId(n) AS id,
           labels(n)[0] AS label,
           n.name AS name,
           substring(n.node_code, 0, 2000) AS code,
           n.return_type AS return_type,
           n.file_path AS file_path,
           n.start_line AS start_line
    LIMIT $limit
    """
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    query, {"keyword": keyword, "limit": _KEYWORD_SEARCH_LIMIT}
                )
        else:
            result = await ctx.client.execute_with_params(
                query, {"keyword": keyword, "limit": _KEYWORD_SEARCH_LIMIT}
            )

        if not result:
            return []
        return [dict(r) if isinstance(r, dict) else r.data() for r in result]

    except Exception as e:
        log_nontarget("WORKFLOW", f"keyword 검색 실패 ({keyword}): {e}")
        return []


async def _find_callers(
    method_id: str,
    ctx: RuntimeCtx,
) -> List[Dict[str, Any]]:
    """CALLS 관계를 통해 caller 메서드 찾기"""
    if not ctx.client:
        return []

    query = """
    MATCH (caller)-[:CALLS]->(callee)
    WHERE elementId(callee) = $method_id
      AND (caller:METHOD OR caller:CONSTRUCTOR)
    RETURN elementId(caller) AS id,
           labels(caller)[0] AS label,
           caller.name AS name,
           substring(caller.node_code, 0, 2000) AS code,
           caller.return_type AS return_type,
           caller.file_path AS file_path
    LIMIT 20
    """
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    query, {"method_id": method_id}
                )
        else:
            result = await ctx.client.execute_with_params(
                query, {"method_id": method_id}
            )

        if not result:
            return []
        return [dict(r) if isinstance(r, dict) else r.data() for r in result]

    except Exception as e:
        log_nontarget("WORKFLOW", f"caller 검색 실패 ({method_id}): {e}")
        return []


async def _analyze_method(
    keyword: str,
    method_info: Dict[str, Any],
    rule_loader: RuleLoader,
    related_blocks: List[Dict[str, Any]],
    semaphore: asyncio.Semaphore,
    file_path: str,
) -> Optional[Dict[str, Any]]:
    """LLM chain으로 메서드 분석 (논타겟 summary/테이블 + 코드 기반 FROM/WRITES 판별)"""
    async with semaphore:
        method_name = method_info.get("name", "unknown")
        method_code = method_info.get("code", "")
        return_type = method_info.get("return_type", "void") or "void"

        if not method_code.strip():
            return None

        # 논타겟 블록 정보에서 block_summaries / candidate_tables 구성
        block_summaries: List[Dict[str, Any]] = []
        candidate_tables: List[Dict[str, Any]] = []
        seen_tables: set = set()

        for b in related_blocks or []:
            summary = b.get("summary", "")
            tables = b.get("tables", []) or []
            block_summaries.append({
                "summary": summary,
                "tables": tables,
            })
            for t in tables:
                tname = t.get("table_name")
                role = t.get("role") or "references"
                if not tname:
                    continue
                key = (tname, role)
                if key in seen_tables:
                    continue
                seen_tables.add(key)
                candidate_tables.append({
                    "name": tname,
                    "role": role,
                })

        inputs = {
            "keyword": keyword,
            "method_name": method_name,
            "method_code": method_code,
            "method_return_type": return_type,
            "block_summaries": block_summaries or None,
            "candidate_tables": candidate_tables or None,
        }

        try:
            prompt_text = rule_loader.render_prompt("linker_analyze", inputs)
            chain = _get_llm_chain()

            parsed = await ainvoke_with_audit(
                chain,
                {"prompt": prompt_text},
                prompt_name="nontarget/linker_analyze",
                input_payload={"keyword": keyword, "method_name": method_name},
                metadata={
                    "file_path": file_path,
                    "method_id": method_info.get("id", ""),
                    "return_type": return_type,
                },
            )

            return parsed if isinstance(parsed, dict) else {}

        except Exception as e:
            log_nontarget("WORKFLOW", f"메서드 분석 실패 ({method_name}, {keyword}): {e}", file_path=file_path)
            return None


def _collect_links(
    results: Dict[str, List],
    analysis: Dict[str, Any],
    method_info: Dict[str, Any],
    file_path: str,
):
    """분석 결과에서 FROM/WRITES 관계 수집 (FK는 Summarizer 전용)"""
    method_id = method_info.get("id", "")
    method_name = method_info.get("name", "")

    for table_name in analysis.get("tables_read", []):
        results["from"].append({
            "source_id": method_id,
            "source_name": method_name,
            "target_table": table_name,
            "source_file": file_path,
            "evidence": analysis.get("evidence", ""),
        })

    for table_name in analysis.get("tables_written", []):
        results["writes"].append({
            "source_id": method_id,
            "source_name": method_name,
            "target_table": table_name,
            "source_file": file_path,
            "evidence": analysis.get("evidence", ""),
        })


# ============================================================================
# Step 4: Description (RuleLoader LLM chain)
# ============================================================================

async def run_description(
    table_id: str,
    table_evidence: Dict[str, Any],
    file_summaries: Dict[str, Dict[str, Any]],
    ctx: RuntimeCtx,
) -> Tuple[str, Dict[str, Any]]:
    """Table-Column 쌍 description 합성 (LLM chain)

    Args:
        table_id: Table의 Neo4j elementId
        table_evidence: build_table_column_evidence 결과
        file_summaries: 파일별 FileSummary
        ctx: RuntimeCtx

    Returns:
        (table_id, {table_id, table_description, columns})
    """
    table_name = table_evidence.get("name", "")

    related_files = table_evidence.get("related_files", [])
    related_file_summaries = {
        fp: file_summaries.get(fp, {}) for fp in related_files
    }

    summaries = table_evidence.get("summaries", [])
    columns = table_evidence.get("columns", [])

    rule_loader = _nontarget_rule_loader()

    inputs = {
        "table_name": table_name,
        "table_schema": table_evidence.get("schema", "public"),
        "current_description": table_evidence.get("current_desc", ""),
        "current_summary": table_evidence.get("current_summary", ""),
        "evidence_summaries": summaries,
        "columns": [
            {
                "column_id": c.get("id", ""),
                "column_name": c.get("name", ""),
                "current_description": c.get("description", ""),
                "data_type": c.get("dtype", ""),
            }
            for c in columns
        ],
        "file_summaries": related_file_summaries,
    }

    try:
        prompt_text = rule_loader.render_prompt("description", inputs)
        chain = _get_llm_chain()

        parsed = await ainvoke_with_audit(
            chain,
            {"prompt": prompt_text},
            prompt_name="nontarget/description",
            input_payload={"table_name": table_name},
            metadata={
                "table_id": table_id,
                "table_name": table_name,
                "evidence_count": len(summaries),
                "column_count": len(columns),
            },
        )

        # LLM JSON 응답 파싱
        if isinstance(parsed, dict):
            table_desc = parsed.get("table_description", "") or ""
            column_descs = parsed.get("column_descriptions", {}) or {}
        else:
            table_desc = ""
            column_descs = {}

        # table_description 비어 있으면 이름 기반 기본 설명 생성 (증거 부족/LLM 응답 이상 방어)
        if not table_desc.strip():
            if table_name:
                table_desc = (
                    f"{table_name} 테이블입니다. "
                    f"애플리케이션에서 {table_name}과(와) 관련된 업무 데이터를 저장하는 용도로 사용됩니다."
                )
            else:
                table_desc = "업무 시스템에서 사용되는 데이터를 저장하는 테이블입니다."

        column_results = []
        for c in columns:
            col_name = c.get("name", "")
            if not col_name:
                continue

            col_desc = column_descs.get(col_name, "") or ""

            # 컬럼 설명이 비어 있으면 컬럼 이름/타입 기반 기본 설명 생성
            if not col_desc.strip():
                dtype = c.get("dtype") or c.get("data_type") or ""
                if dtype:
                    col_desc = f"{col_name} 컬럼입니다. {dtype} 타입의 값을 저장합니다."
                else:
                    col_desc = f"{col_name} 컬럼입니다. 업무에서 의미 있는 값을 저장합니다."

            column_results.append({
                "column_id": c.get("id", ""),
                "column_name": col_name,
                "description": col_desc,
            })

        return table_id, {
            "table_id": table_id,
            "table_name": table_name,
            "table_description": table_desc,
            "columns": column_results,
        }

    except Exception as e:
        log_nontarget("WORKFLOW", f"Description 실패: {table_name} - {e}")

        # LLM 호출/파싱 실패 시에도 최소한의 기본 설명은 생성
        fallback_columns = table_evidence.get("columns", [])
        fallback_column_results = []
        for c in fallback_columns:
            col_name = c.get("name", "")
            if not col_name:
                continue
            dtype = c.get("dtype") or c.get("data_type") or ""
            if dtype:
                col_desc = f"{col_name} 컬럼입니다. {dtype} 타입의 값을 저장합니다."
            else:
                col_desc = f"{col_name} 컬럼입니다. 업무에서 의미 있는 값을 저장합니다."
            fallback_column_results.append({
                "column_id": c.get("id", ""),
                "column_name": col_name,
                "description": col_desc,
            })

        if table_name:
            fallback_table_desc = (
                f"{table_name} 테이블입니다. "
                f"애플리케이션에서 {table_name}과(와) 관련된 데이터를 저장하는 용도로 사용됩니다."
            )
        else:
            fallback_table_desc = "업무 시스템에서 사용되는 데이터를 저장하는 테이블입니다."

        return table_id, {
            "table_id": table_id,
            "table_name": table_name,
            "table_description": fallback_table_desc,
            "columns": fallback_column_results,
        }


# ============================================================================
# Table-Column evidence 수집 (Cypher, LLM 불필요)
# ============================================================================

async def build_table_column_evidence(
    ctx: RuntimeCtx,
) -> Dict[str, Dict[str, Any]]:
    """Neo4j에서 Table-Column 쌍별 사용 흔적을 수집합니다."""
    if not ctx.client:
        return {}

    query = """
    MATCH (t:Table)
    OPTIONAL MATCH (code)-[r1:FROM|WRITES]->(t)
    WHERE code.summary IS NOT NULL
    OPTIONAL MATCH (block:NONTARGET_BLOCK)-[r2:REFERS_TO]->(t)
    WHERE block.summary IS NOT NULL
    OPTIONAL MATCH (t)-[r3:FK_TO_TABLE]->(fk:Table)
    WHERE fk.summary IS NOT NULL OR fk.description IS NOT NULL
    OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
    RETURN
      elementId(t) AS table_id,
      t.name AS name,
      t.schema AS schema,
      t.description AS current_desc,
      t.summary AS current_summary,
      collect(DISTINCT {
        source_type: CASE
          WHEN type(r1) IN ['FROM', 'WRITES'] AND code.kind = 'query' THEN 'code_dml'
          WHEN type(r1) IN ['FROM', 'WRITES'] THEN 'code_general'
          ELSE null END,
        summary: code.summary,
        relation_type: type(r1),
        file_path: code.file_path
      }) AS code_summaries,
      collect(DISTINCT {
        source_type: 'nontarget_doc',
        summary: block.summary,
        relation_type: 'REFERS_TO',
        file_path: block.file_path
      }) AS block_summaries,
      collect(DISTINCT {
        source_type: 'fk_table',
        summary: COALESCE(fk.description, fk.summary, fk.name),
        relation_type: 'FK_TO_TABLE',
        file_path: null
      }) AS fk_summaries,
      collect(DISTINCT {
        id: elementId(c),
        name: c.name,
        description: c.description,
        dtype: COALESCE(c.dtype, c.data_type, '')
      }) AS columns
    """

    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(query, {})
        else:
            result = await ctx.client.execute_with_params(query, {})

        if not result:
            return {}

        evidence_map: Dict[str, Dict[str, Any]] = {}
        for r in result:
            row = dict(r) if isinstance(r, dict) else r.data()
            table_id = row.get("table_id", "")
            if not table_id:
                continue
            all_summaries = []
            related_files: set = set()

            for s in row.get("code_summaries", []):
                if s.get("summary") and s.get("source_type"):
                    all_summaries.append(s)
                    if s.get("file_path"):
                        related_files.add(s["file_path"])

            for s in row.get("block_summaries", []):
                if s.get("summary"):
                    all_summaries.append(s)
                    if s.get("file_path"):
                        related_files.add(s["file_path"])

            for s in row.get("fk_summaries", []):
                if s.get("summary"):
                    all_summaries.append(s)

            columns = [
                c for c in row.get("columns", [])
                if c.get("id") and c.get("name")
            ]

            evidence_map[table_id] = {
                "name": row.get("name", ""),
                "schema": row.get("schema", "public"),
                "current_desc": row.get("current_desc", ""),
                "current_summary": row.get("current_summary", ""),
                "summaries": all_summaries,
                "columns": columns,
                "related_files": list(related_files),
            }

        log_nontarget("WORKFLOW", f"Table-Column evidence 수집: {len(evidence_map)}개 테이블")
        return evidence_map

    except Exception as e:
        log_nontarget("WORKFLOW", f"Table-Column evidence 수집 실패: {e}")
        return {}


