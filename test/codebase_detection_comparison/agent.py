"""Deep Agent 구성 — 도구 3 개 (grep / read_file / read_function).

최종 요약은 LLM 이 tool_call 없는 마지막 텍스트 메시지로 출력. 별도 submit 도구 없음.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langchain_core.tools import tool

from client.llm_client import get_llm
from config.settings import settings

from .tools.tree_sitter_tools import SKIP_DIRS, extract_function_body

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """너는 코드베이스 분석 전문가다.

## 응답 규칙 (무한 hang 방지)
- 탐색 중: 반드시 **tool_call (도구 호출 API)** 로만. 도구명·인자를 텍스트로 적지 말 것.
- 최종 결과만 tool_call 없는 **텍스트 메시지** 로 출력.

## 도구
- `grep(pattern, path_glob=None)` — 전체 코드베이스 검색. 반환: `file:line  매치라인`.
- `read_file(path, max_lines=200)` — XML/SQL/설정/소스 등 아무 파일이나 앞쪽 읽기.
- `read_function(file_path, function_name)` — 소스 파일의 함수 1 개 본문 (tree-sitter).

## 임무
수집 컨텍스트 + 도구로 **프레임워크 패턴**과 **DB 접근 패턴(호출 방식·식별 방식)** 만 확정.
테이블 전수 수집이 목적 아님 — 패턴 확증되면 즉시 종료.

## 원칙
- **관찰한 것만 쓴다.** 못 본 것 지어내기 금지. 접근 불가 (외부 헤더 등) 만 "확인 불가".
- **확정 조건**: 호출 방식 1~2 개 실제 코드 라인 + 테이블/엔티티 식별 방식 관찰.
  이 둘 나오면 **즉시 stop**. 추가 grep/read 금지.
- 테이블 목록은 이미 본 범위에서 자연스럽게 나오는 것만 언급. 일부러 더 찾지 말 것.
- 같은 파일·함수 두 번 읽기 금지. **도구 호출 총 5 회 이내 권장**.

## 최종 출력 (tool_call 없는 텍스트, 2 블록)

프레임워크 패턴
- 한두 문장. 식별된 이름 + 관찰 근거 원문 1~2 개 인용.

DB 접근 패턴
- 호출 방식: 실제 코드 라인 원문 1~2 개.
- 식별 방식: 문자열 리터럴 / XML 매퍼 id / JPA 어노테이션 / 헤더 파일명 등 — 관찰 근거 1 개.
- 명명 규칙: 관찰된 패턴이 있으면 한 줄 (예: `z*_*_s0000`). 없으면 생략.
- 관찰된 테이블 예시: 이미 본 범위에서 3~5 개 나열. 더 안 찾음.
"""


def create_agent(root_path: Path, api_key: str = ""):
    """Deep Agent 반환. 도구 3 개만 노출."""

    @tool
    def grep(pattern: str, path_glob: str = "") -> str:
        """코드베이스 전체에서 정규식 매치. 반환: `file:line  라인` 목록 (최대 80 줄)."""
        import re

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"INVALID_REGEX: {exc}"

        results: list[str] = []
        glob_pat = path_glob or "*"
        for p in root_path.rglob(glob_pat):
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            try:
                with p.open("r", encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if regex.search(line):
                            rel = p.relative_to(root_path).as_posix()
                            results.append(f"{rel}:{i}  {line.rstrip()[:200]}")
                            if len(results) >= 80:
                                break
            except OSError:
                continue
            if len(results) >= 80:
                break
        if not results:
            return "(매치 없음)"
        return "\n".join(results)

    @tool
    def read_file(path: str, max_lines: int = 200) -> str:
        """파일 앞쪽 max_lines 줄을 반환. 상대 경로는 프로젝트 루트 기준."""
        raw = (path or "").strip().replace("\\", "/")
        if len(raw) >= 3 and raw.startswith("/") and raw[2] == ":":
            raw = raw[1:]
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root_path / raw
        if not candidate.exists() or not candidate.is_file():
            return f"FILE_NOT_FOUND: '{path}'"
        try:
            with candidate.open("r", encoding="utf-8", errors="replace") as fh:
                lines = [next(fh, None) for _ in range(max_lines)]
        except OSError as exc:
            return f"READ_ERROR: {exc}"
        text = "".join(ln for ln in lines if ln is not None)
        return text or "(빈 파일)"

    @tool
    def read_function(file_path: str, function_name: str) -> str:
        """소스 파일에서 함수 1 개 본문 추출 (tree-sitter). 큰 함수는 앞부분만."""
        raw = (file_path or "").strip().replace("\\", "/")
        if len(raw) >= 3 and raw.startswith("/") and raw[2] == ":":
            raw = raw[1:]
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root_path / raw
        if not candidate.exists():
            return f"FILE_NOT_FOUND: '{file_path}'"
        body, n_tokens, truncated = extract_function_body(
            candidate, function_name, max_tokens=2000
        )
        if not body:
            return f"FUNCTION_NOT_FOUND: '{function_name}' in '{candidate}'"
        suffix = f"\n[잘림: 첫 ~{n_tokens} tokens]" if truncated else ""
        return body + suffix

    llm = get_llm(api_key=api_key or settings.llm.api_key, model=settings.llm.model)
    agent = create_deep_agent(
        model=llm,
        tools=[grep, read_file, read_function],
        system_prompt=SYSTEM_PROMPT,
    )
    return agent


def count_tool_calls(result: dict[str, Any]) -> int:
    return sum(
        len(getattr(msg, "tool_calls", None) or [])
        for msg in result.get("messages", [])
    )


def extract_final_text(result: dict[str, Any]) -> str:
    """마지막 AI 메시지의 text content 를 최종 요약으로 반환."""
    for msg in reversed(result.get("messages", [])):
        if getattr(msg, "type", "") != "ai":
            continue
        if getattr(msg, "tool_calls", None):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            # content blocks (Anthropic-style)
            parts = [b.get("text", "") for b in content if isinstance(b, dict)]
            content = "\n".join(p for p in parts if p)
        if content and content.strip():
            return content.strip()
    return ""
