"""4-Step 파이프라인 워커 함수

각 단계별 독립 워커 함수를 제공합니다:
- run_chunker_2pass: Pass1(패턴학습) + Pass2(시스템분리) → BlockJSON
- run_summarizer_batched: start_line 정렬 + tiktoken 2000 배치 → SummaryJSON
- run_linker: kind별 그룹핑 + 키워드 5개 묶음 → LinkerResultJSON
- run_description: 엔티티 단위 evidence 합성 → DescriptionJSON

에이전트는 JSON만 반환, Neo4j 저장은 시스템(cypher_templates.py)이 담당.
"""

import asyncio
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from langchain_core.messages import HumanMessage

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
from analyzer.strategy.framework.nontarget.utils.trace_logger import TraceLogger
from config.settings import settings


# ============================================================================
# 메모리 헬퍼 (시스템이 직접 읽기/쓰기)
# ============================================================================

def _mem_read(path: str) -> str:
    """mem/ 파일 읽기 (없으면 빈 문자열)"""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except Exception as e:
        log_nontarget("MEM", f"읽기 실패: {path} - {e}")
    return ""


def _mem_write(path: str, content: str):
    """mem/ 파일 쓰기 (디렉토리 자동 생성)"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        log_nontarget("MEM", f"저장: {path} ({len(content)} chars)")
    except Exception as e:
        log_nontarget("MEM", f"쓰기 실패: {path} - {e}")


def _mem_read_json(path: str) -> Dict[str, Any]:
    """mem/ JSON 파일 읽기 (없으면 빈 dict)"""
    content = _mem_read(path)
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
    return {}


def _mem_write_json(path: str, data: Any):
    """mem/ JSON 파일 쓰기"""
    _mem_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def _mem_append_md(path: str, section: str):
    """mem/ 마크다운 파일에 섹션 추가 (중복 방지)"""
    existing = _mem_read(path)
    if section.strip() and section.strip() not in existing:
        _mem_write(path, existing + "\n" + section if existing else section)


def _pattern_mem_path(file_path: str) -> str:
    """파일 확장자 기반 패턴 메모리 경로"""
    ext = os.path.splitext(file_path)[1].lower().replace(".", "")
    return f"mem/patterns/chunking_{ext}.json"


# ============================================================================
# State / Context
# ============================================================================

class NontargetState(TypedDict, total=False):
    """워크플로우 결과 상태"""
    file_path: str
    blocks: List[Dict[str, Any]]
    summaries: List[Dict[str, Any]]
    status: str  # success, partial, failed
    error: Optional[str]


@dataclass
class RuntimeCtx:
    """런타임 컨텍스트 — Neo4j 클라이언트, 락, 트레이스 로거"""
    client: Any = None
    cypher_lock: Optional[asyncio.Lock] = None
    trace_logger: Optional[TraceLogger] = None


# ============================================================================
# Step 1: 청킹 2-Pass
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
        window_size: 윈도우 크기 (줄 수)
        overlap: 오버랩 줄 수

    Returns:
        (file_path, List[BlockJSON dict])
    """
    log_nontarget("WORKFLOW", f"[Step 1] 청킹 2-Pass 시작: {file_path}")

    if deepagents_client is None:
        deepagents_client = DeepAgentsClient()

    # === 메모리 읽기: 기존 패턴 ===
    pattern_path = _pattern_mem_path(file_path)
    existing_patterns = _mem_read_json(pattern_path)
    if existing_patterns:
        log_nontarget("MEM", f"기존 패턴 로드: {pattern_path}")

    # 파일 읽기 + 라인번호 부착
    try:
        raw_lines, _ = read_file_with_line_numbers(file_path)
    except Exception as e:
        log_nontarget("WORKFLOW", f"파일 읽기 실패: {file_path} - {e}")
        return file_path, []

    if not raw_lines:
        return file_path, []

    # 오버랩 윈도우 생성
    windows = make_overlap_windows(raw_lines, window_size, overlap)
    log_nontarget("WORKFLOW", f"  윈도우 {len(windows)}개 생성 ({len(raw_lines)}줄)")

    # === 작업 상태 메모리 기록 ===
    _mem_write("mem/working/current.md",
        f"# 현재 작업\n- 파일: {file_path}\n- 단계: Step 1 청킹\n"
        f"- 윈도우: {len(windows)}개\n- 총 줄수: {len(raw_lines)}\n")

    # === Pass 1: 패턴 학습 ===
    chunker_agent = deepagents_client.create_chunker_agent(ctx)
    chunker_ctx = ChunkerContext()

    # 기존 패턴이 있으면 초기 규칙으로 주입
    if existing_patterns.get("rules"):
        for r in existing_patterns["rules"]:
            chunker_ctx.rules.append(ChunkingRule(
                rule_id=r.get("rule_id", ""),
                pattern=r.get("pattern", ""),
                rule_type=r.get("rule_type", "header"),
                priority=r.get("priority", 5),
                confidence=r.get("confidence", 0.3),  # 기존 패턴은 낮은 confidence로 시작
                examples=r.get("examples", []),
            ))
        log_nontarget("MEM", f"기존 규칙 {len(chunker_ctx.rules)}개 주입")

    for win in windows:
        win_idx = win["window_idx"]
        log_nontarget("WORKFLOW", f"  Pass1 윈도우 {win_idx + 1}/{len(windows)}")

        # ChunkerAgent 호출: 윈도우 텍스트 + 이전 ctx + 기존 패턴 힌트
        task_msg = json.dumps({
            "window_text": win["text"],
            "window_idx": win_idx,
            "total_windows": len(windows),
            "start_line": win["start_line"],
            "end_line": win["end_line"],
            "is_last": win["is_last"],
            "previous_ctx": chunker_ctx.to_dict(),
            "existing_pattern_hint": existing_patterns.get("hint", ""),
        }, ensure_ascii=False)

        try:
            result = await chunker_agent.ainvoke({
                "messages": [HumanMessage(content=task_msg)]
            })
            output = extract_agent_output(result)
            parsed = parse_json_from_output(output)

            # ctx 업데이트
            chunker_ctx = _merge_chunker_response(chunker_ctx, parsed)

            # === 매 윈도우 후 패턴 메모리 갱신 ===
            _mem_write_json(pattern_path, {
                "file_ext": os.path.splitext(file_path)[1].lower(),
                "rules": chunker_ctx.to_dict()["rules"],
                "hint": f"규칙 {len(chunker_ctx.rules)}개, 경계후보 {len(chunker_ctx.boundaries)}개",
                "learned_from": file_path,
                "window_progress": f"{win_idx + 1}/{len(windows)}",
            })

            if ctx.trace_logger:
                ctx.trace_logger.log_agent_call(
                    "chunker", f"window {win_idx + 1}/{len(windows)}", output[:500]
                )

        except Exception as e:
            log_nontarget("WORKFLOW", f"  Pass1 윈도우 {win_idx + 1} 실패: {e}")

    log_nontarget(
        "WORKFLOW",
        f"  Pass1 완료: 규칙 {len(chunker_ctx.rules)}개, "
        f"경계후보 {len(chunker_ctx.boundaries)}개",
    )

    # === Pass 2: 시스템 분리 ===
    blocks = split_blocks(file_path, raw_lines, chunker_ctx)
    blocks_dict = [b.to_dict() for b in blocks]

    # === 최종 패턴 메모리 저장 ===
    _mem_write_json(pattern_path, {
        "file_ext": os.path.splitext(file_path)[1].lower(),
        "rules": chunker_ctx.to_dict()["rules"],
        "boundaries_count": len(chunker_ctx.boundaries),
        "blocks_count": len(blocks_dict),
        "hint": f"규칙 {len(chunker_ctx.rules)}개 → {len(blocks_dict)}개 블록",
        "learned_from": file_path,
        "completed": True,
    })

    # === 작업 상태 갱신 ===
    _mem_write("mem/working/current.md",
        f"# 현재 작업\n- 파일: {file_path}\n- 단계: Step 1 완료\n"
        f"- 블록: {len(blocks_dict)}개\n")

    log_nontarget("WORKFLOW", f"[Step 1] 완료: {file_path} → {len(blocks_dict)}개 블록")
    return file_path, blocks_dict


def _merge_chunker_response(
    ctx: ChunkerContext,
    response: Dict[str, Any],
) -> ChunkerContext:
    """ChunkerAgent 응답을 기존 ctx에 병합합니다."""
    # 규칙 업데이트
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
            # 기존 규칙 업데이트
            ctx.rules = [
                rule if old.rule_id == rule.rule_id else old
                for old in ctx.rules
            ]
        else:
            ctx.rules.append(rule)
            existing_ids.add(rule.rule_id)

    # 경계 후보 추가 (중복 라인 제거)
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

    # 열린 블록 상태
    ob = response.get("open_block")
    if ob:
        ctx.open_block = OpenBlockState(
            start_line=ob.get("start_line", 0),
            expected_end_pattern=ob.get("expected_end_pattern", ""),
            depth=ob.get("depth", 0),
        )
    elif ob is None and response.get("boundaries"):
        # 명시적으로 null이면 열린 블록 없음
        ctx.open_block = None

    return ctx


# ============================================================================
# Step 2: 요약 (배치)
# ============================================================================

async def run_summarizer_batched(
    file_path: str,
    blocks: List[Dict[str, Any]],
    ctx: RuntimeCtx,
    deepagents_client: Optional[DeepAgentsClient] = None,
    max_batch_tokens: int = 2000,
) -> Tuple[str, List[Dict[str, Any]]]:
    """블록을 start_line 정렬 + tiktoken 배치로 SummarizerAgent 호출

    Args:
        file_path: 파일 경로
        blocks: BlockJSON dict 리스트
        ctx: RuntimeCtx
        max_batch_tokens: 배치당 최대 토큰

    Returns:
        (file_path, List[SummaryJSON])
    """
    log_nontarget("WORKFLOW", f"[Step 2] 요약 시작: {file_path} ({len(blocks)}개 블록)")

    if not blocks:
        return file_path, []

    if deepagents_client is None:
        deepagents_client = DeepAgentsClient()

    # === 메모리 읽기: 기존 테이블/설정 컨텍스트 ===
    tables_context = _mem_read("mem/context/topics/tables.md")
    config_context = _mem_read("mem/context/topics/config.md")

    # start_line 기준 오름차순 정렬
    sorted_blocks = sorted(blocks, key=lambda b: b.get("start_line", 0))

    # tiktoken 배치 묶음
    batches = _make_token_batches(sorted_blocks, max_batch_tokens)
    log_nontarget("WORKFLOW", f"  배치 {len(batches)}개 생성")

    # SummarizerAgent 호출
    summarizer_agent = deepagents_client.create_summarizer_agent(ctx)
    all_summaries: List[Dict[str, Any]] = []

    for batch_idx, batch in enumerate(batches):
        log_nontarget("WORKFLOW", f"  배치 {batch_idx + 1}/{len(batches)} ({len(batch)}개 블록)")

        # 배치 입력 구성
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

        task_msg = json.dumps({
            "blocks": batch_input,
            "file_path": file_path,
            "batch_idx": batch_idx,
            "total_batches": len(batches),
            "known_tables_hint": tables_context[:500] if tables_context else "",
            "known_config_hint": config_context[:300] if config_context else "",
        }, ensure_ascii=False)

        try:
            result = await summarizer_agent.ainvoke({
                "messages": [HumanMessage(content=task_msg)]
            })
            output = extract_agent_output(result)
            parsed = parse_json_from_output(output)

            results = parsed.get("results", [])
            for r in results:
                r.setdefault("file_path", file_path)
            all_summaries.extend(results)

        except Exception as e:
            log_nontarget("WORKFLOW", f"  배치 {batch_idx + 1} 실패: {e}")

    # === 메모리 쓰기: 발견된 테이블/설정 정보 ===
    _update_tables_memory(all_summaries, file_path)
    _update_config_memory(all_summaries, file_path)

    # === 작업 상태 갱신 ===
    _mem_write("mem/working/current.md",
        f"# 현재 작업\n- 파일: {file_path}\n- 단계: Step 2 완료\n"
        f"- 요약: {len(all_summaries)}개\n")

    log_nontarget("WORKFLOW", f"[Step 2] 완료: {file_path} → {len(all_summaries)}개 요약")
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
    """블록별 summary/kind/keywords를 파일 단위로 집계합니다.

    Returns:
        {file_path: {kinds, keywords, tables, file_summary_text}}
    """
    # summary_results를 file_path 기준 맵으로
    summaries_by_file: Dict[str, List[Dict[str, Any]]] = {}
    for file_path, sums in summary_results:
        summaries_by_file.setdefault(file_path, []).extend(sums)

    file_summaries: Dict[str, Dict[str, Any]] = {}

    for file_path, blocks in chunk_results:
        sums = summaries_by_file.get(file_path, [])

        # kinds 집계 (빈도 순)
        kind_counts: Dict[str, int] = defaultdict(int)
        all_keywords: List[str] = []
        all_tables: List[str] = []
        summary_texts: List[str] = []

        for s in sums:
            kind = s.get("kind", "unknown")
            kind_counts[kind] += 1
            all_keywords.extend(s.get("keywords", []))
            summary_texts.append(s.get("summary", ""))

            for t in s.get("tables", []):
                tname = t.get("name", "")
                if tname:
                    all_tables.append(tname)

        # 상위 2개 kinds
        sorted_kinds = sorted(kind_counts.items(), key=lambda x: x[1], reverse=True)
        top_kinds = [k for k, _ in sorted_kinds[:2]]

        # 키워드 중복 제거 (빈도 순 상위 30개)
        kw_counts: Dict[str, int] = defaultdict(int)
        for kw in all_keywords:
            kw_counts[kw] += 1
        top_keywords = [
            kw for kw, _ in sorted(kw_counts.items(), key=lambda x: x[1], reverse=True)[:30]
        ]

        # 파일 요약 (블록 요약의 앞 5개)
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
        }

    log_nontarget("WORKFLOW", f"FileSummary 생성 완료: {len(file_summaries)}개 파일")
    return file_summaries


# ============================================================================
# Step 3: 링크
# ============================================================================

async def run_linker(
    file_path: str,
    file_summary: Dict[str, Any],
    ctx: RuntimeCtx,
    deepagents_client: Optional[DeepAgentsClient] = None,
) -> Tuple[str, Dict[str, Any]]:
    """kind별 그룹핑 + 키워드 5개 묶음으로 LinkerAgent 호출

    Args:
        file_path: 파일 경로
        file_summary: FileSummary (kinds, keywords, tables, file_summary_text)
        ctx: RuntimeCtx

    Returns:
        (file_path, LinkerResultJSON)
    """
    log_nontarget("WORKFLOW", f"[Step 3] 링크 시작: {file_path}")

    if deepagents_client is None:
        deepagents_client = DeepAgentsClient()

    # === 메모리 읽기: 기존 테이블/관계 컨텍스트 ===
    tables_context = _mem_read("mem/context/topics/tables.md")
    relations_context = _mem_read("mem/context/topics/relations.md")

    # Neo4j에서 이 파일의 블록 정보 조회
    block_infos = await _fetch_block_infos(file_path, ctx)
    if not block_infos:
        log_nontarget("WORKFLOW", f"  블록 없음 → 링크 스킵")
        return file_path, {"refers_to": [], "from": [], "writes": [], "fk_to_table": [], "fk_to": []}

    # kind별 그룹핑
    kind_groups = _group_by_kind(block_infos)

    linker_agent = deepagents_client.create_linker_agent(ctx)
    merged_result: Dict[str, List] = {
        "refers_to": [], "from": [], "writes": [],
        "fk_to_table": [], "fk_to": [],
    }

    for kind, blocks in kind_groups.items():
        # 키워드 수집 후 5개씩 묶음
        all_kw = []
        for b in blocks:
            all_kw.extend(b.get("keywords", []))
        all_kw = list(dict.fromkeys(all_kw))  # 순서 유지 중복 제거

        kw_batches = [all_kw[i:i+5] for i in range(0, len(all_kw), 5)]
        if not kw_batches:
            kw_batches = [[]]

        for kw_batch in kw_batches:
            task_msg = json.dumps({
                "kind": kind,
                "keywords": kw_batch,
                "file_summary": file_summary,
                "block_ids": [b.get("id", "") for b in blocks],
                "file_path": file_path,
                "known_tables_hint": tables_context[:500] if tables_context else "",
                "known_relations_hint": relations_context[:300] if relations_context else "",
            }, ensure_ascii=False)

            try:
                result = await linker_agent.ainvoke({
                    "messages": [HumanMessage(content=task_msg)]
                })
                output = extract_agent_output(result)
                parsed = parse_json_from_output(output)

                for key in merged_result:
                    merged_result[key].extend(parsed.get(key, []))

            except Exception as e:
                log_nontarget("WORKFLOW", f"  Linker ({kind}, {kw_batch[:3]}) 실패: {e}")

    total = sum(len(v) for v in merged_result.values())

    # === 메모리 쓰기: 발견된 관계 정보 ===
    _update_relations_memory(merged_result, file_path)

    # === 작업 상태 갱신 ===
    _mem_write("mem/working/current.md",
        f"# 현재 작업\n- 파일: {file_path}\n- 단계: Step 3 완료\n"
        f"- 관계: {total}개\n")

    log_nontarget("WORKFLOW", f"[Step 3] 완료: {file_path} → {total}개 관계 후보")
    return file_path, merged_result


async def _fetch_block_infos(
    file_path: str,
    ctx: RuntimeCtx,
) -> List[Dict[str, Any]]:
    """Neo4j에서 파일의 NONTARGET_BLOCK 정보 조회"""
    if not ctx.client:
        return []

    query = """
    MATCH (b:NONTARGET_BLOCK {file_path: $file_path})
    WHERE b.active = true
    RETURN elementId(b) AS id, b.name AS name,
           b.kind AS kind, b.keywords AS keywords,
           b.summary AS summary, b.start_line AS start_line
    ORDER BY b.start_line
    """
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(
                    query, {"file_path": file_path}
                )
        else:
            result = await ctx.client.execute_with_params(
                query, {"file_path": file_path}
            )

        if not result:
            return []
        return [dict(r) if isinstance(r, dict) else r.data() for r in result]

    except Exception as e:
        log_nontarget("WORKFLOW", f"블록 정보 조회 실패: {file_path} - {e}")
        return []


def _group_by_kind(
    block_infos: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """블록을 kind별로 그룹핑"""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for b in block_infos:
        kind = b.get("kind", "unknown") or "unknown"
        groups[kind].append(b)
    return dict(groups)


# ============================================================================
# Step 4: Description
# ============================================================================

async def run_description(
    entity_id: str,
    evidence: Dict[str, Any],
    file_summaries: Dict[str, Dict[str, Any]],
    ctx: RuntimeCtx,
    deepagents_client: Optional[DeepAgentsClient] = None,
    max_evidence_tokens: int = 4000,
) -> Tuple[str, Dict[str, Any]]:
    """엔티티(Table/Column) description 합성 — tiktoken 4000 배치 분할

    evidence summaries가 max_evidence_tokens을 초과하면 배치로 분할하여
    DescriptionAgent를 여러 번 호출한 뒤 최종 합성합니다.

    Args:
        entity_id: Neo4j elementId
        evidence: EvidenceSummary (build_entity_evidence 결과)
        file_summaries: 파일별 FileSummary
        ctx: RuntimeCtx
        max_evidence_tokens: 배치당 최대 토큰 (기본 4000)

    Returns:
        (entity_id, DescriptionJSON)
    """
    if deepagents_client is None:
        deepagents_client = DeepAgentsClient()

    # === 메모리 읽기: 기존 테이블 컨텍스트 ===
    tables_context = _mem_read("mem/context/topics/tables.md")

    # 관련 FileSummary 추출
    related_files = evidence.get("related_files", [])
    related_file_summaries = {
        fp: file_summaries.get(fp, {}) for fp in related_files
    }

    summaries = evidence.get("summaries", [])

    # tiktoken 기준 배치 분할
    evidence_batches = _make_evidence_batches(summaries, max_evidence_tokens)

    description_agent = deepagents_client.create_description_agent(ctx)

    if len(evidence_batches) <= 1:
        # 배치 분할 불필요 — 단일 호출
        return await _call_description_agent(
            description_agent, entity_id, evidence,
            summaries, related_file_summaries,
            tables_context_hint=tables_context[:500] if tables_context else "",
        )

    # 다중 배치: 배치별 부분 description 수집
    log_nontarget(
        "WORKFLOW",
        f"Description 배치 분할: {entity_id} → {len(evidence_batches)}개 배치",
    )
    partial_descriptions: List[str] = []

    for batch_idx, batch in enumerate(evidence_batches):
        try:
            task_msg = json.dumps({
                "entity_id": entity_id,
                "entity_type": evidence.get("entity_type", "Table"),
                "name": evidence.get("name", ""),
                "current_description": evidence.get("current_desc", ""),
                "current_summary": evidence.get("current_summary", ""),
                "summaries": batch,
                "file_summaries": related_file_summaries,
                "batch_idx": batch_idx,
                "total_batches": len(evidence_batches),
                "is_partial": True,
            }, ensure_ascii=False)

            result = await description_agent.ainvoke({
                "messages": [HumanMessage(content=task_msg)]
            })
            output = extract_agent_output(result)
            parsed = parse_json_from_output(output)

            descs = parsed.get("descriptions", [])
            if descs:
                partial_descriptions.append(descs[0].get("description", ""))

        except Exception as e:
            log_nontarget("WORKFLOW", f"Description 배치 {batch_idx + 1} 실패: {e}")

    # 최종 합성: 부분 description을 모아 최종 호출
    if not partial_descriptions:
        return entity_id, {"entity_id": entity_id, "description": ""}

    if len(partial_descriptions) == 1:
        return entity_id, {
            "entity_id": entity_id,
            "description": partial_descriptions[0],
        }

    # 부분 결과를 합성하는 최종 호출
    try:
        synthesis_msg = json.dumps({
            "entity_id": entity_id,
            "entity_type": evidence.get("entity_type", "Table"),
            "name": evidence.get("name", ""),
            "current_description": evidence.get("current_desc", ""),
            "partial_descriptions": partial_descriptions,
            "is_synthesis": True,
        }, ensure_ascii=False)

        result = await description_agent.ainvoke({
            "messages": [HumanMessage(content=synthesis_msg)]
        })
        output = extract_agent_output(result)
        parsed = parse_json_from_output(output)

        descs = parsed.get("descriptions", [])
        if descs:
            return entity_id, descs[0]
    except Exception as e:
        log_nontarget("WORKFLOW", f"Description 합성 실패: {entity_id} - {e}")

    # 합성 실패 시 부분 결과 연결
    return entity_id, {
        "entity_id": entity_id,
        "description": " ".join(partial_descriptions),
    }


async def _call_description_agent(
    agent: Any,
    entity_id: str,
    evidence: Dict[str, Any],
    summaries: List[Dict[str, Any]],
    related_file_summaries: Dict[str, Dict[str, Any]],
    tables_context_hint: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """DescriptionAgent 단일 호출 헬퍼"""
    task_msg = json.dumps({
        "entity_id": entity_id,
        "entity_type": evidence.get("entity_type", "Table"),
        "name": evidence.get("name", ""),
        "current_description": evidence.get("current_desc", ""),
        "current_summary": evidence.get("current_summary", ""),
        "summaries": summaries,
        "file_summaries": related_file_summaries,
        "known_tables_hint": tables_context_hint,
    }, ensure_ascii=False)

    try:
        result = await agent.ainvoke({
            "messages": [HumanMessage(content=task_msg)]
        })
        output = extract_agent_output(result)
        parsed = parse_json_from_output(output)

        descriptions = parsed.get("descriptions", [])
        if descriptions:
            return entity_id, descriptions[0]
        return entity_id, {"entity_id": entity_id, "description": ""}

    except Exception as e:
        log_nontarget("WORKFLOW", f"Description 실패: {entity_id} - {e}")
        return entity_id, {"entity_id": entity_id, "description": ""}


def _make_evidence_batches(
    summaries: List[Dict[str, Any]],
    max_tokens: int,
) -> List[List[Dict[str, Any]]]:
    """evidence summaries를 tiktoken 기준 배치로 분할합니다."""
    if not summaries:
        return [[]]

    batches: List[List[Dict[str, Any]]] = []
    current_batch: List[Dict[str, Any]] = []
    current_tokens = 0

    for s in summaries:
        text = s.get("summary", "")
        tokens = count_tokens_tiktoken(text)

        if current_batch and current_tokens + tokens > max_tokens:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(s)
        current_tokens += tokens

    if current_batch:
        batches.append(current_batch)

    return batches


async def build_entity_evidence(
    ctx: RuntimeCtx,
) -> Dict[str, Dict[str, Any]]:
    """Neo4j에서 Table/Column별 사용 흔적을 수집합니다.

    그래프 traversal만 수행 (LLM 불필요).
    REFERS_TO / FROM / WRITES / FK_TO_TABLE 관계로 연결된 노드의 summary만 수집.

    Returns:
        {entity_id: {
            name, entity_type, current_desc, current_summary,
            summaries: [{source_type, summary, relation_type}],
            related_files: [file_path, ...]
        }}
    """
    if not ctx.client:
        return {}

    query = """
    MATCH (t:Table)
    WHERE t.description IS NULL OR t.description = '' OR t.description_source <> 'nontarget_enriched'
    OPTIONAL MATCH (code)-[r1:FROM|WRITES]->(t)
    WHERE code.summary IS NOT NULL
    OPTIONAL MATCH (block:NONTARGET_BLOCK)-[r2:REFERS_TO]->(t)
    WHERE block.summary IS NOT NULL
    OPTIONAL MATCH (t)-[r3:FK_TO_TABLE]->(fk:Table)
    WHERE fk.summary IS NOT NULL OR fk.description IS NOT NULL
    RETURN
      elementId(t) AS entity_id,
      t.name AS name,
      'Table' AS entity_type,
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
      }) AS fk_summaries
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
            entity_id = row.get("entity_id", "")
            if not entity_id:
                continue

            # summary 합치기 + null 제거
            all_summaries = []
            related_files = set()

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

            if not all_summaries:
                continue

            evidence_map[entity_id] = {
                "name": row.get("name", ""),
                "entity_type": row.get("entity_type", "Table"),
                "current_desc": row.get("current_desc", ""),
                "current_summary": row.get("current_summary", ""),
                "summaries": all_summaries,
                "related_files": list(related_files),
            }

        log_nontarget("WORKFLOW", f"Entity evidence 수집: {len(evidence_map)}개 엔티티")
        return evidence_map

    except Exception as e:
        log_nontarget("WORKFLOW", f"Entity evidence 수집 실패: {e}")
        return {}


# ============================================================================
# 벡터화 (기존 유지)
# ============================================================================

async def vectorize_all_blocks(
    ctx: RuntimeCtx,
    batch_size: int = 50,
) -> int:
    """모든 NONTARGET_BLOCK의 summary를 벡터화합니다."""
    if not ctx.client:
        return 0

    from openai import AsyncOpenAI
    from client.embedding_client import EmbeddingClient

    api_key = getattr(settings, 'openai_api_key', None)
    if not api_key:
        log_nontarget("VECTORIZE", "OpenAI API 키 없음 - 벡터화 건너뜀")
        return 0

    # summary가 있고 summary_vec이 없는 블록 조회
    query = """
    MATCH (n:NONTARGET_BLOCK)
    WHERE n.summary IS NOT NULL AND n.summary <> ''
      AND (n.summary_vec IS NULL OR size(n.summary_vec) = 0)
    RETURN elementId(n) AS nid, n.summary AS summary
    """

    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(query, {})
        else:
            result = await ctx.client.execute_with_params(query, {})
    except Exception as e:
        log_nontarget("VECTORIZE", f"블록 조회 실패: {e}")
        return 0

    nodes = [dict(r) if isinstance(r, dict) else r.data() for r in (result or [])]
    if not nodes:
        return 0

    openai_client = AsyncOpenAI(api_key=api_key)
    embedding_client = EmbeddingClient(openai_client)
    vectorized_count = 0

    for i in range(0, len(nodes), batch_size):
        batch = nodes[i:i + batch_size]
        texts = [item.get("summary", "") for item in batch]

        try:
            vectors = await embedding_client.embed_batch(texts)

            updates = []
            for item, vector in zip(batch, vectors):
                if vector:
                    updates.append({"nid": item["nid"], "vector": vector})
                    vectorized_count += 1

            if updates:
                update_query = """
                UNWIND $items AS item
                MATCH (n) WHERE elementId(n) = item.nid
                SET n.summary_vec = item.vector
                """
                if ctx.cypher_lock:
                    async with ctx.cypher_lock:
                        await ctx.client.execute_with_params(update_query, {"items": updates})
                else:
                    await ctx.client.execute_with_params(update_query, {"items": updates})

        except Exception as e:
            log_nontarget("VECTORIZE", f"벡터화 배치 실패: {e}")

    log_nontarget("VECTORIZE", f"벡터화 완료: {vectorized_count}개")
    return vectorized_count


# ============================================================================
# 메모리 갱신 헬퍼 (Summarizer/Linker 단계 후)
# ============================================================================

def _update_tables_memory(
    summaries: List[Dict[str, Any]],
    file_path: str,
):
    """요약 결과에서 테이블/컬럼 정보를 mem/context/topics/tables.md에 추가"""
    tables_found: List[str] = []

    for s in summaries:
        for t in s.get("tables", []):
            name = t.get("name", "")
            if not name:
                continue

            entity_name = t.get("entity_name", "")
            mapping_type = t.get("mapping_type", "")
            columns = t.get("columns", [])
            col_names = ", ".join(c.get("name", "") for c in columns[:10])

            section = f"## {name}\n"
            if entity_name:
                section += f"- entity_name: {entity_name}\n"
            if mapping_type:
                section += f"- mapping_type: {mapping_type}\n"
            section += f"- schema: {t.get('schema', 'public')}\n"
            if col_names:
                section += f"- 컬럼: {col_names}\n"
            section += f"- 발견 파일: {file_path}\n"

            _mem_append_md("mem/context/topics/tables.md", section)
            tables_found.append(name)

    if tables_found:
        log_nontarget("MEM", f"tables.md 갱신: {', '.join(tables_found[:5])}")


def _update_config_memory(
    summaries: List[Dict[str, Any]],
    file_path: str,
):
    """요약 결과에서 설정 정보를 mem/context/topics/config.md에 추가"""
    config_found = False

    for s in summaries:
        kind = s.get("kind", "")
        if kind != "config":
            continue

        keywords = s.get("keywords", [])
        summary_text = s.get("summary", "")

        if keywords or summary_text:
            section = f"## {os.path.basename(file_path)}\n"
            if keywords:
                section += f"- 키: {', '.join(keywords[:10])}\n"
            if summary_text:
                section += f"- 요약: {summary_text[:200]}\n"
            section += f"- 파일: {file_path}\n"

            _mem_append_md("mem/context/topics/config.md", section)
            config_found = True

    if config_found:
        log_nontarget("MEM", f"config.md 갱신: {file_path}")


def _update_relations_memory(
    links_json: Dict[str, Any],
    file_path: str,
):
    """링크 결과를 mem/context/topics/relations.md에 추가"""
    total = sum(len(v) for v in links_json.values() if isinstance(v, list))
    if total == 0:
        return

    section = f"## {os.path.basename(file_path)}\n"
    for rel_type, items in links_json.items():
        if not items:
            continue
        section += f"- {rel_type.upper()}: {len(items)}개\n"

    section += f"- 총 관계: {total}개\n"
    section += f"- 파일: {file_path}\n"

    _mem_append_md("mem/context/topics/relations.md", section)
    log_nontarget("MEM", f"relations.md 갱신: {file_path} ({total}개 관계)")
