"""비타겟 파일 블록화 로직 (레거시 하위호환)

파일을 의미 있는 블록으로 분할합니다.
규칙 기반 또는 라인 기반 블록화를 지원합니다.

NOTE: 새 파이프라인에서는 block_splitter.py를 사용합니다.
이 모듈은 하위 호환성을 위해 유지됩니다.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget


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


# [REMOVED] extract_samples_iterative — 레거시 별칭, 새 파이프라인에서 미사용
# [REMOVED] _finalize_rules — 레거시 패턴 메모리 → 규칙 변환, block_splitter로 대체
# [REMOVED] discover_patterns_iteratively — 레거시 에이전트 기반 패턴 발견, run_chunker_2pass로 대체


def load_rules_from_memory(file_path: str) -> Optional[Dict[str, Any]]:
    """mem/patterns/에서 기존 규칙 로드 (새 폴더 구조)
    
    Args:
        file_path: 대상 파일 경로
        
    Returns:
        규칙 JSON 또는 None
    """
    base_name = os.path.basename(file_path)
    name_pattern = base_name.replace(".", "_").replace("-", "_")
    
    # 새 폴더 구조: mem/patterns/
    rules_dir = "mem/patterns"
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
    """mem/patterns/에 규칙 저장 (새 폴더 구조)
    
    Args:
        file_path: 대상 파일 경로
        rules: 규칙 JSON
    """
    base_name = os.path.basename(file_path)
    name_pattern = base_name.replace(".", "_").replace("-", "_")
    version = rules.get("version", 1)
    
    # 새 폴더 구조: mem/patterns/
    rules_dir = "mem/patterns"
    os.makedirs(rules_dir, exist_ok=True)
    
    rule_file = f"chunking__{name_pattern}__v{version}.json"
    rule_path = os.path.join(rules_dir, rule_file)
    
    try:
        with open(rule_path, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
        log_nontarget("CHUNK", f"규칙 저장: {rule_path}")
    except Exception as e:
        log_nontarget("CHUNK", f"규칙 저장 실패: {e}")

