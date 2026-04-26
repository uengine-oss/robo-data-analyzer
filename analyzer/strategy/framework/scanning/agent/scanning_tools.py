"""scanning 에이전트 도구 — 파일 탐색 (grep / read_file / read_function / glob / ls).

Profile 에이전트가 프로젝트 루트를 자율 탐색하며 프레임워크/DB 접근 패턴을
수집할 때 쓰는 도구. tree-sitter 기반 함수 본문 추출 도구 포함.
"""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.tools import tool

from analyzer.strategy.framework.common.domain.analysis_constants import SKIP_DIRS
from analyzer.strategy.framework.scanning.tree_sitter_tools import extract_function_body


def build_tools(root: Path) -> list:
    """scanning 에이전트용 도구 세트.

    `root` 를 캡처해 모든 도구가 동일 루트 기준 상대 경로를 처리.
    """

    @tool
    def grep(pattern: str, path_glob: str = "") -> str:
        """코드베이스 전체에서 정규식 매치. 반환: `file:line  라인` (최대 80 줄)."""
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"INVALID_REGEX: {exc}"
        results: list[str] = []
        # 상대 패턴만 허용. 선행 '/' 나 프로젝트 루트 경로 제거.
        glob_pat = (path_glob or "*").lstrip("/\\")
        try:
            candidates = root.rglob(glob_pat)
        except (NotImplementedError, ValueError) as exc:
            return f"INVALID_GLOB: '{path_glob}' ({exc})"
        for p in candidates:
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            try:
                with p.open("r", encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if regex.search(line):
                            try:
                                rel = p.relative_to(root).as_posix()
                            except ValueError:
                                rel = str(p)
                            results.append(f"{rel}:{i}  {line.rstrip()[:200]}")
                            if len(results) >= 80:
                                break
            except OSError:
                continue
            if len(results) >= 80:
                break
        return "\n".join(results) if results else "(매치 없음)"

    @tool
    def read_file(path: str, max_lines: int = 200) -> str:
        """파일 앞쪽 max_lines 줄을 반환. 상대 경로는 프로젝트 루트 기준."""
        raw = (path or "").strip().replace("\\", "/")
        if len(raw) >= 3 and raw.startswith("/") and raw[2] == ":":
            raw = raw[1:]
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / raw
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
        """소스 파일에서 함수 1 개 본문 (tree-sitter). 큰 함수는 앞부분만."""
        raw = (file_path or "").strip().replace("\\", "/")
        if len(raw) >= 3 and raw.startswith("/") and raw[2] == ":":
            raw = raw[1:]
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / raw
        if not candidate.exists():
            return f"FILE_NOT_FOUND: '{file_path}'"
        body, n_tokens, truncated = extract_function_body(
            candidate, function_name, max_tokens=2000,
        )
        if not body:
            return f"FUNCTION_NOT_FOUND: '{function_name}' in '{candidate}'"
        suffix = f"\n[잘림: 첫 ~{n_tokens} tokens]" if truncated else ""
        return body + suffix

    @tool
    def glob_search(pattern: str, path: str = "") -> str:
        """glob 패턴으로 파일 검색. 예: '**/*.xml', 'src/**/*.java'. 상대 경로는 프로젝트 루트 기준."""
        base = root / path.strip().replace("\\", "/").lstrip("/") if path.strip() else root
        if not base.exists() or not base.is_dir():
            return f"DIR_NOT_FOUND: '{path}'"
        glob_pat = pattern.strip().lstrip("/\\")
        results: list[str] = []
        try:
            for p in base.rglob(glob_pat):
                if not p.is_file():
                    continue
                if any(part in SKIP_DIRS for part in p.parts):
                    continue
                try:
                    results.append(p.relative_to(root).as_posix())
                except ValueError:
                    results.append(str(p))
                if len(results) >= 200:
                    break
        except (NotImplementedError, ValueError) as exc:
            return f"INVALID_GLOB: '{pattern}' ({exc})"
        return "\n".join(results) if results else "(매치 없음)"

    @tool
    def ls(path: str = "") -> str:
        """디렉토리 내용을 반환. 상대 경로는 프로젝트 루트 기준. 파일/디렉토리 구분 표시."""
        raw = (path or "").strip().replace("\\", "/").lstrip("/")
        target = root / raw if raw else root
        if not target.exists() or not target.is_dir():
            return f"DIR_NOT_FOUND: '{path}'"
        entries: list[str] = []
        try:
            for item in sorted(target.iterdir()):
                if item.name.startswith("."):
                    continue
                if item.name in SKIP_DIRS:
                    continue
                rel = item.relative_to(root).as_posix()
                prefix = "d " if item.is_dir() else "f "
                entries.append(f"{prefix}{rel}")
        except OSError as exc:
            return f"READ_ERROR: {exc}"
        return "\n".join(entries[:200]) if entries else "(빈 디렉토리)"

    return [grep, read_file, read_function, glob_search, ls]
