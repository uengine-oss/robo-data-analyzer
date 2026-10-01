"""소스 파일 텍스트 캐시 — 같은 파일을 여러 함수/range 가 읽을 때 1회만 read.

**인스턴스 단위** — step 함수 안에서 만들어 쓰고 step 이 끝나면 GC. 모듈 전역
캐시를 쓰지 않는다 (장수명 API 서버에서 run 사이 stale 방지). step5(컨텍스트
빌드)·step6(range 본문)·step4 DBMS(DML 본문) 가 공유.
"""
from __future__ import annotations

from pathlib import Path


class SourceFileCache:
    """파일 경로 → 텍스트 라인 캐시. read 실패는 빈 리스트 (1회만 시도)."""

    def __init__(self) -> None:
        self._lines: dict[str, list[str]] = {}

    def lines(self, file_path: str) -> list[str]:
        """파일 텍스트 라인 목록 — 캐시 적중 시 즉시. 실패 시 빈 리스트."""
        cached = self._lines.get(file_path)
        if cached is not None:
            return cached
        try:
            lines = Path(file_path).read_text(encoding="utf-8", errors="ignore").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        self._lines[file_path] = lines
        return lines

    def slice(self, file_path: str, start_line: int, end_line: int) -> str:
        """1-indexed ``start_line``~``end_line`` 본문 (``end_line`` ≤ 0 이면 파일 끝까지)."""
        lines = self.lines(file_path)
        if not lines:
            return ""
        end = end_line if end_line > 0 else len(lines)
        return "\n".join(lines[max(start_line - 1, 0):end])

    def leading_comment(self, file_path: str, start_line: int, max_lines: int = 60) -> str:
        """``start_line`` 바로 위에 붙은 주석 블록 (``--`` · ``/* */``). 없으면 빈 문자열.

        레거시 프로시저는 업무 설명을 ``CREATE`` 위 머리 주석에 적는다. 파서가 주는 범위는
        ``CREATE`` 줄부터라 그 주석이 범위 밖으로 빠지고, LLM 은 변수 이름만 보고 테이블·컬럼의
        뜻을 짐작하게 된다(실측: 머리 주석에 "상품 가입(TB07)의 적합성 판정(C041)" 이라고
        적혀 있는데 C041 을 "처리 결과 값"으로 설명). 이름이 난독화된 스키마에서는 이 주석이
        의미의 거의 유일한 원천이다.
        """
        lines = self.lines(file_path)
        i = min(start_line - 1, len(lines)) - 1
        block: list[str] = []
        while i >= 0 and len(block) < max_lines:
            t = lines[i].strip()
            if t == "" and not block:
                i -= 1
                continue
            if t.startswith(("--", "/*", "*", "*/")) or t.endswith("*/"):
                block.append(lines[i])
                i -= 1
                continue
            break
        return "\n".join(reversed(block)).strip()
