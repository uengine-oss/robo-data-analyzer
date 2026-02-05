"""비타겟 파일 블록화 로직

파일을 의미 있는 블록으로 분할합니다.
규칙 기반 또는 라인 기반 블록화를 지원합니다.

반복 샘플링:
- 대용량 파일(만 라인 이상)에서도 패턴을 정확히 파악
- 여러 구간을 순차적으로 분석하여 패턴 메모리에 축적
- 발견된 패턴을 다른 구간에서 검증하여 확정
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage

from analyzer.strategy.framework.nontarget.nontarget_logger import log_nontarget


def chunk_by_lines(
    file_path: str,
    max_lines: int = 200,
) -> List[Dict[str, Any]]:
    """라인 기준으로 블록화 (기본 전략)
    
    Args:
        file_path: 파일 경로
        max_lines: 블록당 최대 라인 수
        
    Returns:
        블록 리스트 [{"start_line": 1, "end_line": 200, "code": "..."}, ...]
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        log_nontarget("CHUNK", f"파일 읽기 실패: {file_path} - {e}")
        return []
    
    blocks: List[Dict[str, Any]] = []
    total_lines = len(lines)
    
    for start in range(0, total_lines, max_lines):
        end = min(start + max_lines, total_lines)
        block_lines = lines[start:end]
        code = "".join(f"{start + i + 1}: {line}" for i, line in enumerate(block_lines))
        
        blocks.append({
            "start_line": start + 1,
            "end_line": end,
            "code": code,
        })
    
    log_nontarget("CHUNK", f"라인 블록화 완료: {file_path} → {len(blocks)}개 블록")
    return blocks


def chunk_by_rules(
    file_path: str,
    rules: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """규칙 기반 블록화
    
    Args:
        file_path: 파일 경로
        rules: 블록화 규칙 JSON
            - boundary: "top_level_key" | "delimiter" | "indent"
            - max_block_lines: 최대 블록 라인 수
            - key_patterns: 키 후보 정규식 패턴 리스트
            
    Returns:
        블록 리스트
    """
    boundary = rules.get("boundary", "line")
    max_lines = rules.get("max_block_lines", 200)
    
    if boundary == "line":
        return chunk_by_lines(file_path, max_lines)
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        log_nontarget("CHUNK", f"파일 읽기 실패: {file_path} - {e}")
        raise RuntimeError(f"파일 읽기 실패: {file_path} - {e}") from e
    
    blocks: List[Dict[str, Any]] = []
    key_patterns = rules.get("key_patterns", [])
    
    if boundary == "top_level_key":
        blocks = _chunk_by_top_level_key(lines, key_patterns, max_lines)
    elif boundary == "delimiter":
        delimiter = rules.get("delimiter", "---")
        blocks = _chunk_by_delimiter(lines, delimiter, max_lines)
    elif boundary == "indent":
        blocks = _chunk_by_indent(lines, max_lines)
    else:
        raise RuntimeError(f"알 수 없는 boundary 타입: {boundary}. 지원: top_level_key, delimiter, indent, line")
    
    log_nontarget("CHUNK", f"규칙 블록화 완료: {file_path} → {len(blocks)}개 블록 (boundary={boundary})")
    return blocks


def _chunk_by_top_level_key(
    lines: List[str],
    key_patterns: List[str],
    max_lines: int,
) -> List[Dict[str, Any]]:
    """최상위 키 기준 블록화 (YAML 등) - 문맥 단위 보존
    
    원칙:
    - 최상위 키의 모든 하위 항목을 함께 유지
    - max_lines 도달해도 다음 최상위 키까지 기다림 (soft limit)
    - 요소가 중간에서 잘리지 않도록 보장
    """
    blocks: List[Dict[str, Any]] = []
    current_block_lines: List[Tuple[int, str]] = []
    current_start = 1
    soft_limit = max_lines + 100  # 최대 추가 허용 라인
    
    # 패턴 컴파일
    compiled_patterns = []
    for p in key_patterns:
        try:
            compiled_patterns.append(re.compile(p))
        except re.error:
            pass
    
    # 기본 패턴: 들여쓰기 없는 키
    if not compiled_patterns:
        compiled_patterns.append(re.compile(r'^[a-zA-Z0-9_-]+:'))
    
    for i, line in enumerate(lines, 1):
        is_boundary = False
        
        # 최상위 키 패턴 매칭 (들여쓰기 없는 라인)
        for pattern in compiled_patterns:
            if pattern.match(line) and not line.startswith(' ') and not line.startswith('\t'):
                is_boundary = True
                break
        
        # 자연 경계에서만 분할 (요소가 잘리지 않게)
        if is_boundary and current_block_lines:
            # max_lines 이상이거나, 충분한 크기 (20줄 이상)면 분할
            if len(current_block_lines) >= max_lines or len(current_block_lines) >= 20:
                code = "".join(f"{ln}: {txt}" for ln, txt in current_block_lines)
                blocks.append({
                    "start_line": current_start,
                    "end_line": current_block_lines[-1][0],
                    "code": code,
                })
                current_block_lines = []
                current_start = i
        
        current_block_lines.append((i, line))
        
        # soft_limit 초과 시 경고 로그 (하지만 강제 분할하지 않음)
        if len(current_block_lines) >= soft_limit:
            log_nontarget("CHUNK", f"경고: 블록 크기 {len(current_block_lines)}줄 (soft_limit={soft_limit}), "
                         f"라인 {current_start}-{i}에서 자연 경계를 기다리는 중")
    
    # 마지막 블록
    if current_block_lines:
        code = "".join(f"{ln}: {txt}" for ln, txt in current_block_lines)
        blocks.append({
            "start_line": current_start,
            "end_line": current_block_lines[-1][0],
            "code": code,
        })
    
    return blocks


def _chunk_by_delimiter(
    lines: List[str],
    delimiter: str,
    max_lines: int,
) -> List[Dict[str, Any]]:
    """구분자 기준 블록화 - 문맥 단위 보존
    
    원칙:
    - 구분자(---, GO 등)에서만 분할
    - max_lines 도달해도 다음 구분자까지 기다림
    - 문맥이 중간에서 잘리지 않도록 보장
    """
    blocks: List[Dict[str, Any]] = []
    current_block_lines: List[Tuple[int, str]] = []
    current_start = 1
    soft_limit = max_lines + 100
    
    # 정규식 구분자 지원 (예: </select>|</insert>)
    try:
        delimiter_pattern = re.compile(delimiter)
        use_regex = "|" in delimiter or "(" in delimiter
    except re.error:
        use_regex = False
    
    for i, line in enumerate(lines, 1):
        is_delimiter = False
        if use_regex:
            is_delimiter = bool(delimiter_pattern.search(line.strip()))
        else:
            is_delimiter = line.strip() == delimiter
        
        if is_delimiter and current_block_lines:
            # 구분자를 현재 블록에 포함하고 분할
            current_block_lines.append((i, line))
            code = "".join(f"{ln}: {txt}" for ln, txt in current_block_lines)
            blocks.append({
                "start_line": current_start,
                "end_line": current_block_lines[-1][0],
                "code": code,
            })
            current_block_lines = []
            current_start = i + 1
            continue
        
        current_block_lines.append((i, line))
        
        # soft_limit 초과 시 경고 (강제 분할 안함)
        if len(current_block_lines) >= soft_limit:
            log_nontarget("CHUNK", f"경고: 블록 크기 {len(current_block_lines)}줄, "
                         f"라인 {current_start}-{i}에서 구분자를 기다리는 중")
    
    if current_block_lines:
        code = "".join(f"{ln}: {txt}" for ln, txt in current_block_lines)
        blocks.append({
            "start_line": current_start,
            "end_line": current_block_lines[-1][0],
            "code": code,
        })
    
    return blocks


def _chunk_by_indent(
    lines: List[str],
    max_lines: int,
) -> List[Dict[str, Any]]:
    """들여쓰기 기준 블록화 - 문맥 단위 보존
    
    원칙:
    - 들여쓰기 0인 라인(최상위 레벨)에서만 분할
    - max_lines 도달해도 다음 최상위 라인까지 기다림
    - 하위 항목이 잘리지 않도록 보장
    """
    blocks: List[Dict[str, Any]] = []
    current_block_lines: List[Tuple[int, str]] = []
    current_start = 1
    soft_limit = max_lines + 100
    
    for i, line in enumerate(lines, 1):
        # 빈 라인이 아니고 들여쓰기가 없으면 경계 (최상위 레벨)
        is_boundary = (
            line.strip() and
            not line.startswith(' ') and
            not line.startswith('\t')
        )
        
        # 자연 경계에서만 분할 (요소가 잘리지 않게)
        if is_boundary and current_block_lines:
            # 최소 크기(20줄) 이상이면 분할
            if len(current_block_lines) >= 20:
                code = "".join(f"{ln}: {txt}" for ln, txt in current_block_lines)
                blocks.append({
                    "start_line": current_start,
                    "end_line": current_block_lines[-1][0],
                    "code": code,
                })
                current_block_lines = []
                current_start = i
        
        current_block_lines.append((i, line))
        
        # soft_limit 초과 시 경고 (강제 분할 안함 - 자연 경계 대기)
        if len(current_block_lines) >= soft_limit:
            log_nontarget("CHUNK", f"경고: 블록 크기 {len(current_block_lines)}줄, "
                         f"라인 {current_start}-{i}에서 최상위 라인을 기다리는 중")
    
    if current_block_lines:
        code = "".join(f"{ln}: {txt}" for ln, txt in current_block_lines)
        blocks.append({
            "start_line": current_start,
            "end_line": current_block_lines[-1][0],
            "code": code,
        })
    
    return blocks


def extract_sample(file_path: str, sample_lines: int = 200) -> str:
    """파일에서 샘플 추출 (head/middle/tail)
    
    Args:
        file_path: 파일 경로
        sample_lines: 각 섹션당 라인 수
        
    Returns:
        샘플 텍스트 (head + middle + tail)
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return ""
    
    total = len(lines)
    if total <= sample_lines * 3:
        # 전체 파일이 작으면 전체 반환
        return "".join(f"{i+1}: {line}" for i, line in enumerate(lines))
    
    # head
    head = lines[:sample_lines]
    head_text = "=== HEAD ===\n" + "".join(f"{i+1}: {line}" for i, line in enumerate(head))
    
    # middle
    mid_start = (total - sample_lines) // 2
    middle = lines[mid_start:mid_start + sample_lines]
    middle_text = f"\n=== MIDDLE (from line {mid_start+1}) ===\n"
    middle_text += "".join(f"{mid_start+i+1}: {line}" for i, line in enumerate(middle))
    
    # tail
    tail = lines[-sample_lines:]
    tail_start = total - sample_lines
    tail_text = f"\n=== TAIL (from line {tail_start+1}) ===\n"
    tail_text += "".join(f"{tail_start+i+1}: {line}" for i, line in enumerate(tail))
    
    return head_text + middle_text + tail_text


# ============================================================================
# 전체 파일 순차 분석 (메모리 축적 방식)
# ============================================================================

def extract_chunks_for_analysis(
    file_path: str,
    chunk_lines: int = 500,  # 약 2000토큰 (한 번에 분석할 라인 수)
) -> List[Dict[str, Any]]:
    """전체 파일을 순차적으로 읽어 분석용 청크로 분할
    
    일부분만 샘플링하지 않고, 전체 파일을 chunk_lines 단위로 나눠
    순차적으로 분석합니다. 메모리에 패턴을 축적하여 완벽히 파악합니다.
    
    Args:
        file_path: 파일 경로
        chunk_lines: 한 번에 분석할 라인 수 (약 2000토큰 ≈ 500줄)
        
    Returns:
        [{"chunk_idx": 0, "text": "...", "line_start": 1, "line_end": 500, "is_last": False}, ...]
        
    Raises:
        RuntimeError: 파일 읽기 실패
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        log_nontarget("CHUNK", f"파일 읽기 실패: {file_path} - {e}")
        raise RuntimeError(f"파일 읽기 실패: {file_path} - {e}") from e
    
    total = len(lines)
    chunks: List[Dict[str, Any]] = []
    
    # 전체 파일을 chunk_lines 단위로 분할
    chunk_idx = 0
    for start in range(0, total, chunk_lines):
        end = min(start + chunk_lines, total)
        
        # 청크 텍스트 생성 (라인 번호 포함)
        chunk_text = "".join(
            f"{start + j + 1}: {lines[start + j]}"
            for j in range(end - start)
        )
        
        chunks.append({
            "chunk_idx": chunk_idx,
            "line_start": start + 1,
            "line_end": end,
            "text": chunk_text,
            "is_last": end >= total,
            "progress": f"{end}/{total} ({end * 100 // total}%)",
        })
        chunk_idx += 1
    
    log_nontarget("CHUNK", f"전체 파일 분할 완료: {file_path} → {len(chunks)}개 청크 ({total} lines)")
    return chunks


# 하위 호환성을 위한 별칭
def extract_samples_iterative(
    file_path: str,
    sample_lines: int = 500,
    num_samples: int = None,  # 무시됨 - 전체 파일 분석
) -> List[Dict[str, Any]]:
    """[DEPRECATED] extract_chunks_for_analysis 사용을 권장합니다."""
    chunks = extract_chunks_for_analysis(file_path, sample_lines)
    # 기존 형식으로 변환
    return [
        {
            "section": chunk["progress"],
            "line_start": chunk["line_start"],
            "line_end": chunk["line_end"],
            "text": chunk["text"],
        }
        for chunk in chunks
    ]


def _merge_patterns(
    existing: Dict[str, Any],
    new_response: Dict[str, Any],
) -> Dict[str, Any]:
    """패턴 메모리 병합
    
    새로 발견된 패턴을 기존 메모리와 병합합니다.
    - discovered_patterns: 새로 발견된 패턴 추가
    - confirmed_patterns: 여러 구간에서 확인된 패턴
    - rejected_patterns: 일부 구간에서 불일치한 패턴
    
    Args:
        existing: 기존 패턴 메모리
        new_response: 새 분석 응답
        
    Returns:
        병합된 패턴 메모리
    """
    # discovered에서 새로 발견된 패턴
    new_discovered = new_response.get("discovered_patterns", [])
    new_confirmed = new_response.get("confirmed_patterns", [])
    new_rejected = new_response.get("rejected_patterns", [])
    
    # 기존 메모리
    existing_discovered = set(existing.get("discovered_patterns", []))
    existing_confirmed = set(existing.get("confirmed_patterns", []))
    existing_rejected = set(existing.get("rejected_patterns", []))
    
    # 새로 확인된 패턴은 confirmed로 승격
    for pattern in new_confirmed:
        if pattern in existing_discovered:
            existing_discovered.discard(pattern)
            existing_confirmed.add(pattern)
        elif pattern not in existing_rejected:
            existing_confirmed.add(pattern)
    
    # 거부된 패턴은 rejected로 이동
    for pattern in new_rejected:
        existing_discovered.discard(pattern)
        existing_confirmed.discard(pattern)
        existing_rejected.add(pattern)
    
    # 새로 발견된 패턴 추가 (이미 처리된 건 제외)
    for pattern in new_discovered:
        if pattern not in existing_confirmed and pattern not in existing_rejected:
            existing_discovered.add(pattern)
    
    # key_patterns 업데이트 (가장 최근 것 사용)
    key_patterns = new_response.get("key_patterns", existing.get("key_patterns", []))
    boundary_type = new_response.get("boundary_type", existing.get("boundary_type", "line"))
    confidence = new_response.get("confidence", existing.get("confidence", 0.5))
    
    # block_roles 누적 (이전 청크 + 새 청크)
    existing_block_roles = existing.get("block_roles", [])
    new_block_roles = new_response.get("block_roles", [])
    merged_block_roles = existing_block_roles + new_block_roles
    
    return {
        "discovered_patterns": list(existing_discovered),
        "confirmed_patterns": list(existing_confirmed),
        "rejected_patterns": list(existing_rejected),
        "key_patterns": key_patterns,
        "boundary_type": boundary_type,
        "confidence": confidence,
        "notes": new_response.get("notes", existing.get("notes", "")),
        "block_roles": merged_block_roles,  # 블록 역할 누적
    }


def _finalize_rules(pattern_memory: Dict[str, Any], file_path: str) -> Dict[str, Any]:
    """패턴 메모리에서 최종 규칙 생성
    
    Args:
        pattern_memory: 축적된 패턴 메모리
        file_path: 파일 경로 (타입 추론용)
        
    Returns:
        블록화 규칙 JSON
    """
    # 파일 타입 추론
    ext = os.path.splitext(file_path)[1].lower()
    file_type_map = {
        ".yaml": "yaml", ".yml": "yaml",
        ".xml": "xml",
        ".properties": "properties",
        ".ini": "ini",
        ".json": "json",
        ".sql": "sql",
    }
    file_type = file_type_map.get(ext, "text")
    
    boundary = pattern_memory.get("boundary_type", "line")
    key_patterns = pattern_memory.get("key_patterns", [])
    confidence = pattern_memory.get("confidence", 0.5)
    
    # 최종 규칙 생성
    rules = {
        "file_type": file_type,
        "boundary": boundary,
        "max_block_lines": 150 if boundary != "line" else 200,
        "key_patterns": key_patterns,
        "notes": pattern_memory.get("notes", ""),
        "version": 1,
        "confidence": confidence,
        "confirmed_patterns": pattern_memory.get("confirmed_patterns", []),
        "block_roles": pattern_memory.get("block_roles", []),  # 블록 역할 정보
    }
    
    # delimiter 타입이면 추가 정보
    if boundary == "delimiter":
        # confirmed_patterns에서 delimiter 추출 시도
        delimiters = [p for p in pattern_memory.get("confirmed_patterns", []) if len(p) < 10]
        if delimiters:
            rules["delimiter"] = delimiters[0]
        else:
            rules["delimiter"] = "---"
    
    log_nontarget("CHUNK", f"규칙 확정: boundary={boundary}, confidence={confidence:.2f}")
    return rules


def discover_patterns_iteratively(
    file_path: str,
    ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """전체 파일을 순차적으로 분석하여 패턴 발견 및 메모리 축적
    
    일부분만 샘플링하지 않고, 전체 파일을 500줄 단위(약 2000토큰)로
    순차적으로 분석합니다. 메모리에 패턴을 축적하여 완벽히 파악합니다.
    
    Args:
        file_path: 대상 파일 경로
        ctx: RuntimeCtx (선택)
        
    Returns:
        블록화 규칙 JSON
        
    Raises:
        RuntimeError: 분석 실패 시 (fallback 없음)
    """
    from analyzer.strategy.framework.nontarget.deepagents_client import (
        DeepAgentsClient,
        _extract_agent_output,
        _parse_json_from_output,
    )
    
    # 전체 파일을 500줄 단위로 분할 (약 2000토큰)
    chunks = extract_chunks_for_analysis(file_path, chunk_lines=500)
    
    if not chunks:
        raise RuntimeError(f"파일 분석용 청크 추출 실패: {file_path}")
    
    # 패턴 메모리 초기화
    pattern_memory: Dict[str, Any] = {
        "discovered_patterns": [],
        "confirmed_patterns": [],
        "rejected_patterns": [],
        "key_patterns": [],
        "boundary_type": "line",
        "confidence": 0.5,
        "notes": "",
        "analyzed_lines": 0,
        "total_lines": chunks[-1]["line_end"],
        "block_roles": [],  # 블록별 역할 기록 (Summary 때 참고)
    }
    
    client = DeepAgentsClient()
    
    log_nontarget("CHUNK", f"전체 파일 순차 분석 시작: {file_path} ({len(chunks)}개 청크, {pattern_memory['total_lines']} lines)")
    
    import time
    
    for chunk in chunks:
        # 이전 패턴 상태 저장 (비교용)
        previous_patterns = list(pattern_memory.get("confirmed_patterns", [])) + list(pattern_memory.get("discovered_patterns", []))
        
        # yaml 템플릿 변수 준비
        template_vars = {
            "file_path": file_path,
            "chunk_idx": chunk['chunk_idx'] + 1,
            "total_chunks": len(chunks),
            "line_start": chunk['line_start'],
            "line_end": chunk['line_end'],
            "progress": chunk['progress'],
            "is_last": chunk['is_last'],
            "pattern_memory": json.dumps(pattern_memory, ensure_ascii=False, indent=2),
            "chunk_text": chunk['text'],
        }
        
        # 에이전트 생성 (매 청크마다 새로운 template_vars로)
        agent = client.create_agent("chunker_agent", ctx=ctx, template_vars=template_vars)
        
        # 에이전트 실행 - yaml 프롬프트에 이미 모든 지침이 포함됨
        user_input = "위 청크를 분석하여 패턴을 발견하고, block_roles를 기록하세요."

        # 에이전트 호출 (실패 시 즉시 중단 - fallback 없음) + 트레이싱
        start_time = time.time()
        result = agent.invoke({"messages": [HumanMessage(content=user_input)]})
        elapsed_ms = int((time.time() - start_time) * 1000)
        
        output = _extract_agent_output(result)
        new_patterns = _parse_json_from_output(output)
        
        # 새로 발견된 것들 추출
        new_discoveries = {
            "new_discovered": new_patterns.get("discovered_patterns", []),
            "newly_confirmed": new_patterns.get("confirmed_patterns", []),
            "rejected": new_patterns.get("rejected_patterns", []),
            "block_roles": new_patterns.get("block_roles", []),
        }
        reasoning = new_patterns.get("reasoning", new_patterns.get("notes", ""))
        
        # 상세 에이전트 호출 트레이싱
        if ctx and hasattr(ctx, "trace_logger") and ctx.trace_logger:
            # 1) 패턴 분석 단계 상세 로깅 (청크 내용/프롬프트/응답 전체 저장)
            ctx.trace_logger.log_pattern_analysis_step(
                chunk_idx=chunk["chunk_idx"],
                chunk_content=chunk["text"],  # 전체 청크 내용
                previous_patterns=previous_patterns,
                agent_prompt=user_input,  # 전체 프롬프트
                agent_response=output,  # 전체 응답
                new_discoveries=new_discoveries,
                reasoning=reasoning,
                line_start=chunk["line_start"],
                line_end=chunk["line_end"],
            )
            
            # 2) 에이전트 호출 트레이싱 (확장된 필드 포함)
            ctx.trace_logger.log_agent_call(
                agent_name="chunker_agent",
                prompt=user_input,  # 전체 저장
                response=output,  # 전체 저장
                tokens_used=0,  # TODO: 토큰 추적 추가
                duration_ms=elapsed_ms,
                purpose=f"파일 구조 분석 - 청크 {chunk['chunk_idx']+1}/{len(chunks)}",
                input_context=f"이전 패턴: {len(previous_patterns)}개, 라인 범위: {chunk['line_start']}-{chunk['line_end']}",
                parsed_result=new_patterns,
                reasoning=reasoning,
            )
        
        # 패턴 병합
        pattern_memory = _merge_patterns(pattern_memory, new_patterns)
        pattern_memory["analyzed_lines"] = chunk["line_end"]
        
        # trace_logger에 패턴 발견 기록 (요약 형태)
        if ctx and hasattr(ctx, "trace_logger") and ctx.trace_logger:
            ctx.trace_logger.log_pattern_discovery(
                chunk_idx=chunk["chunk_idx"],
                total_chunks=len(chunks),
                line_start=chunk["line_start"],
                line_end=chunk["line_end"],
                pattern_memory=pattern_memory,
            )
        
        log_nontarget("CHUNK", f"청크 {chunk['chunk_idx']+1}/{len(chunks)} 분석 완료: "
                     f"discovered={len(pattern_memory['discovered_patterns'])}, "
                     f"confirmed={len(pattern_memory['confirmed_patterns'])}, "
                     f"progress={chunk['progress']}")
    
    # 최종 규칙 생성
    rules = _finalize_rules(pattern_memory, file_path)
    
    log_nontarget("CHUNK", f"전체 파일 분석 완료: {file_path} → boundary={rules.get('boundary')}, confidence={rules.get('confidence', 0):.2f}")
    return rules


def load_rules_from_memory(file_path: str) -> Optional[Dict[str, Any]]:
    """mem/rules/에서 기존 규칙 로드
    
    Args:
        file_path: 대상 파일 경로
        
    Returns:
        규칙 JSON 또는 None
    """
    # 파일 패턴에서 규칙 파일명 생성
    base_name = os.path.basename(file_path)
    name_pattern = base_name.replace(".", "_").replace("-", "_")
    
    # mem/rules/ 디렉토리 탐색
    rules_dir = "mem/rules"
    if not os.path.isdir(rules_dir):
        return None
    
    for rule_file in os.listdir(rules_dir):
        if rule_file.startswith(f"chunking__{name_pattern}") and rule_file.endswith(".json"):
            rule_path = os.path.join(rules_dir, rule_file)
            try:
                with open(rule_path, "r", encoding="utf-8") as f:
                    rules = json.load(f)
                log_nontarget("CHUNK", f"규칙 로드: {rule_path}")
                return rules
            except Exception:
                pass
    
    return None


def save_rules_to_memory(file_path: str, rules: Dict[str, Any]) -> None:
    """mem/rules/에 규칙 저장
    
    Args:
        file_path: 대상 파일 경로
        rules: 규칙 JSON
    """
    base_name = os.path.basename(file_path)
    name_pattern = base_name.replace(".", "_").replace("-", "_")
    version = rules.get("version", 1)
    
    rules_dir = "mem/rules"
    os.makedirs(rules_dir, exist_ok=True)
    
    rule_file = f"chunking__{name_pattern}__v{version}.json"
    rule_path = os.path.join(rules_dir, rule_file)
    
    try:
        with open(rule_path, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
        log_nontarget("CHUNK", f"규칙 저장: {rule_path}")
    except Exception as e:
        log_nontarget("CHUNK", f"규칙 저장 실패: {e}")

