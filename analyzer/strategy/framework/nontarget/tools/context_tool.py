"""컨텍스트 관리 도구

토큰 기준 컨텍스트 사용량 확인 및 압축 도구입니다.
"""

import json
import os
from datetime import datetime
from typing import Annotated, Any, Dict, List

from langchain_core.tools import StructuredTool

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget


def count_tokens_tiktoken(text: str, model: str = "gpt-4") -> int:
    """tiktoken으로 정확한 토큰 계산
    
    Args:
        text: 토큰을 계산할 텍스트
        model: 사용할 모델 (기본: gpt-4)
        
    Returns:
        토큰 수
    """
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except ImportError:
        # tiktoken이 없으면 대략적 계산 (4자당 1토큰)
        log_nontarget("TOOL", "tiktoken 미설치 - 대략적 계산 사용")
        return len(text) // 4
    except Exception as e:
        log_nontarget("TOOL", f"토큰 계산 오류: {e}")
        return len(text) // 4


def auto_check_context(limit: int = 100000) -> Dict[str, Any]:
    """에이전트 호출 전 시스템 자동 컨텍스트 체크
    
    시스템이 에이전트 호출 전에 자동으로 컨텍스트를 확인합니다.
    에이전트가 직접 호출하지 않아도 됩니다.
    
    Args:
        limit: 토큰 한도 (기본: 100,000)
        
    Returns:
        {
            "total_tokens": int,
            "limit": int,
            "status": "OK" | "WARNING" | "OVER_LIMIT",
            "recommendation": str,
            "files_to_archive": [str]  # 한도 초과 시 아카이브 대상
        }
    """
    total_tokens = 0
    file_tokens: List[tuple] = []  # (path, tokens)
    
    # working 폴더
    working_dir = "mem/working"
    if os.path.isdir(working_dir):
        for fname in os.listdir(working_dir):
            fpath = os.path.join(working_dir, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                    tokens = count_tokens_tiktoken(content)
                    file_tokens.append((fpath, tokens))
                    total_tokens += tokens
                except Exception:
                    pass
    
    # context 폴더
    context_dir = "mem/context"
    if os.path.isdir(context_dir):
        for root, _, files in os.walk(context_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                    tokens = count_tokens_tiktoken(content)
                    file_tokens.append((fpath, tokens))
                    total_tokens += tokens
                except Exception:
                    pass
    
    # 상태 결정
    warning_threshold = int(limit * 0.8)  # 80%
    if total_tokens > limit:
        status = "OVER_LIMIT"
        recommendation = "즉시 컨텍스트 압축 필요"
    elif total_tokens > warning_threshold:
        status = "WARNING"
        recommendation = "곧 한도 초과 예상 - 정리 권장"
    else:
        status = "OK"
        recommendation = "정상"
    
    # 한도 초과 시 아카이브 대상 선정 (큰 파일 순)
    files_to_archive: List[str] = []
    if status in ("WARNING", "OVER_LIMIT"):
        sorted_files = sorted(file_tokens, key=lambda x: x[1], reverse=True)
        excess = total_tokens - int(limit * 0.7)  # 70%까지 줄이기
        archived_tokens = 0
        for fpath, tokens in sorted_files:
            if archived_tokens >= excess:
                break
            if "buffer" in fpath or "archive" not in fpath:
                files_to_archive.append(fpath)
                archived_tokens += tokens
    
    result = {
        "total_tokens": total_tokens,
        "limit": limit,
        "status": status,
        "usage_percent": round(total_tokens / limit * 100, 1),
        "recommendation": recommendation,
        "files_to_archive": files_to_archive,
    }
    
    log_nontarget("TOOL", f"auto_check_context: {total_tokens}/{limit} ({result['usage_percent']}%) - {status}")
    return result


def create_check_context_limit_tool() -> StructuredTool:
    """컨텍스트 토큰 사용량 확인 도구 생성 (tiktoken 기반)
    
    에이전트가 현재 컨텍스트 토큰 사용량을 확인하고,
    한도 초과 여부를 판단할 수 있도록 합니다.
    """
    def check_context_limit() -> str:
        """현재 컨텍스트 토큰 사용량 확인 (tiktoken 사용)"""
        try:
            total_tokens = 0
            token_details: Dict[str, int] = {}
            
            # working 폴더 토큰 계산
            working_dir = "mem/working"
            if os.path.isdir(working_dir):
                for fname in os.listdir(working_dir):
                    fpath = os.path.join(working_dir, fname)
                    if os.path.isfile(fpath):
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                            # tiktoken으로 정확한 토큰 계산
                            tokens = count_tokens_tiktoken(content)
                            token_details[f"working/{fname}"] = tokens
                            total_tokens += tokens
            
            # context 폴더 토큰 계산
            context_dir = "mem/context"
            if os.path.isdir(context_dir):
                for root, _, files in os.walk(context_dir):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                            tokens = count_tokens_tiktoken(content)
                            rel_path = os.path.relpath(fpath, "mem")
                            token_details[rel_path] = tokens
                            total_tokens += tokens
            
            # 한도 설정 (100,000 토큰)
            limit = 100000
            warning_threshold = int(limit * 0.8)
            
            if total_tokens > limit:
                status = "OVER_LIMIT"
            elif total_tokens > warning_threshold:
                status = "WARNING"
            else:
                status = "OK"
            
            result = {
                "total_tokens": total_tokens,
                "limit": limit,
                "status": status,
                "usage_percent": round(total_tokens / limit * 100, 1),
                "details": token_details,
                "recommendation": "즉시 압축 필요" if status == "OVER_LIMIT" else 
                                "정리 권장" if status == "WARNING" else "정상"
            }
            
            log_nontarget("TOOL", f"check_context_limit: {total_tokens}/{limit} ({result['usage_percent']}%) - {status}")
            return json.dumps(result, ensure_ascii=False, indent=2)
            
        except Exception as e:
            log_nontarget("TOOL", f"check_context_limit 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
    
    return StructuredTool.from_function(
        name="check_context_limit",
        description="""현재 메모리(mem/) 폴더의 토큰 사용량을 tiktoken으로 정확히 측정합니다.

=== 호출 시점 ===
- 여러 파일을 연속 처리할 때 주기적으로 (3~5 파일마다)
- write_memory를 많이 호출한 후
- 응답이 느려진다고 느껴질 때

=== 반환 필드 ===
- total_tokens: mem/working/ + mem/context/ 총 토큰 수
- limit: 100,000 토큰 (하드 리밋)
- status: "OK" (80% 미만), "WARNING" (80~100%), "OVER_LIMIT" (100% 초과)
- usage_percent: 사용률 백분율
- details: 파일별 토큰 수 (어떤 파일이 큰지 확인 가능)
- recommendation: "정상", "정리 권장", "즉시 압축 필요"

=== 후속 조치 ===
- OK: 계속 진행
- WARNING: 불필요한 working/buffer.md 내용 정리 고려
- OVER_LIMIT: compress_context("archive_old") 즉시 호출하여 buffer를 archive로 이동""",
        func=check_context_limit,
    )


def create_compress_context_tool(ctx: Any) -> StructuredTool:
    """컨텍스트 압축 도구 생성
    
    한도 초과 시 오래된 정보를 archive로 이동하고 요약합니다.
    """
    trace_logger = getattr(ctx, 'trace_logger', None) if ctx else None
    
    def compress_context(strategy: Annotated[str, "압축 전략: archive_old 또는 summarize"] = "archive_old") -> str:
        """컨텍스트를 압축합니다.
        
        Args:
            strategy: 압축 전략
                - archive_old: 오래된 정보를 archive로 이동
                - summarize: working/buffer.md 내용을 요약
        """
        try:
            if strategy == "archive_old":
                # working/buffer.md가 있으면 archive로 이동
                buffer_path = "mem/working/buffer.md"
                if os.path.exists(buffer_path):
                    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
                    archive_path = f"mem/archive/{timestamp}__buffer.md"
                    
                    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
                    os.rename(buffer_path, archive_path)
                    
                    # 빈 buffer.md 생성
                    with open(buffer_path, "w", encoding="utf-8") as f:
                        f.write("# Working Buffer\n\n*압축 후 초기화됨*\n")
                    
                    if trace_logger:
                        trace_logger.log_tool_call("compress_context", {"strategy": strategy}, "archived")
                    
                    return json.dumps({
                        "status": "success",
                        "action": "archived",
                        "from": buffer_path,
                        "to": archive_path
                    }, ensure_ascii=False)
                
                return json.dumps({
                    "status": "no_action",
                    "reason": "buffer.md가 비어있음"
                }, ensure_ascii=False)
            
            elif strategy == "summarize":
                # working/current.md 내용을 요약 (단순 버전)
                current_path = "mem/working/current.md"
                if os.path.exists(current_path):
                    with open(current_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    # 단순 요약: 마지막 500자만 유지
                    if len(content) > 500:
                        summarized = content[:200] + "\n\n[... 중간 내용 압축됨 ...]\n\n" + content[-200:]
                        with open(current_path, "w", encoding="utf-8") as f:
                            f.write(summarized)
                        
                        if trace_logger:
                            trace_logger.log_tool_call("compress_context", {"strategy": strategy}, "summarized")
                        
                        return json.dumps({
                            "status": "success",
                            "action": "summarized",
                            "original_length": len(content),
                            "new_length": len(summarized)
                        }, ensure_ascii=False)
                
                return json.dumps({
                    "status": "no_action",
                    "reason": "current.md가 충분히 작음"
                }, ensure_ascii=False)
            
            return json.dumps({
                "status": "error",
                "reason": f"알 수 없는 전략: {strategy}"
            }, ensure_ascii=False)
            
        except Exception as e:
            log_nontarget("TOOL", f"compress_context 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
    
    return StructuredTool.from_function(
        name="compress_context",
        description="""메모리 토큰 사용량을 줄이는 압축 도구. check_context_limit에서 WARNING/OVER_LIMIT일 때 호출합니다.

=== 압축 전략 ===

1. "archive_old" (기본값, 권장):
   - mem/working/buffer.md → mem/archive/YYYY-MM-DDThh-mm-ss__buffer.md로 이동
   - 빈 buffer.md를 새로 생성
   - 효과: buffer에 쌓인 임시 메모를 아카이브하여 토큰 확보
   - 주의: archive로 이동한 내용은 read_memory로 다시 읽을 수 있음

2. "summarize":
   - mem/working/current.md가 500자 초과 시 앞뒤 200자만 남기고 중간 생략
   - 효과: current.md 크기 축소
   - 주의: 중간 내용이 손실되므로 중요한 정보는 topics/에 미리 저장할 것

=== 사용 순서 ===
1. check_context_limit() → status 확인
2. status가 WARNING/OVER_LIMIT이면 compress_context("archive_old") 호출
3. 여전히 OVER_LIMIT이면 compress_context("summarize") 추가 호출
4. check_context_limit()으로 결과 확인""",
        func=compress_context,
    )

