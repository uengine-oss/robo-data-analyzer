"""Profile Agent — 프레임워크/DB 접근 패턴 요약 생성.

도구 3 개 (grep / read_file / read_function) 만 사용.
리소스 파일 분류는 scanner 가 이미 끝냈으므로 여기서는 자연어 요약만 담당한다.

create_agent + 최소 미들웨어 사용 (FilesystemMiddleware 제거 → 내장 도구 비활성화).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from langchain.agents import create_agent
from langchain.agents.middleware import (
    ContextEditingMiddleware,
    ClearToolUsesEdit,
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.tools import tool

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware

from client.llm_client import get_llm
from config.settings import settings

from analyzer.strategy.framework.common.constants import SKIP_DIRS
from analyzer.strategy.framework.scanning.models import CodebaseInfo
from analyzer.strategy.framework.scanning.tree_sitter_tools import extract_function_body

logger = logging.getLogger(__name__)

# 프롬프트는 프로젝트 최상위 rules/ 아래에서 로드 (다른 Deep Agent 프롬프트들과 같은 위치)
_RULES_PATH = (
    Path(__file__).resolve().parents[5]
    / "rules" / "deep_agent" / "profile.yaml"
)


def _load_rules() -> tuple[str, str]:
    data = yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8"))
    return data["system_prompt"], data["user_template"]


SYSTEM_PROMPT, _USER_TEMPLATE = _load_rules()


def _build_tools(root: Path):

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


def _render_prompt(info: CodebaseInfo, resources: dict[str, list[str]]) -> str:
    lines: list[str] = []
    for cat in ("SQL", "ENTITY", "CONFIG", "DOCS"):
        paths = resources.get(cat) or []
        if paths:
            preview = ", ".join(paths[:8])
            more = f" (+ {len(paths) - 8} more)" if len(paths) > 8 else ""
            lines.append(f"- {cat}: {preview}{more}")
    resources_summary = "\n".join(lines) if lines else "(없음)"

    if info.function_index:
        idx_lines: list[str] = []
        for path, sigs in info.function_index:
            idx_lines.append(f"- {path}")
            for sig in sigs:
                idx_lines.append(f"    · {sig}")
        function_index_block = "\n".join(idx_lines)
    else:
        function_index_block = "(없음)"

    return _USER_TEMPLATE.format(
        extensions=info.extensions or "(비어있음)",
        manifest_name=info.manifest_name or "(없음)",
        manifest_content=info.manifest_content or "(없음)",
        config_name=info.config_name or "(없음)",
        config_content=info.config_content or "(없음)",
        import_frequency=info.import_frequency or "(비어있음)",
        sample_file_count=info.sample_file_count,
        representative_file=info.representative_file or "(없음)",
        representative_skeleton=info.representative_skeleton or "(없음)",
        function_index=function_index_block,
        resources_summary=resources_summary,
    )


def _extract_final_text(result: dict[str, Any]) -> str:
    for msg in reversed(result.get("messages", [])):
        if getattr(msg, "type", "") != "ai":
            continue
        if getattr(msg, "tool_calls", None):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content if isinstance(b, dict)]
            content = "\n".join(p for p in parts if p)
        if content and content.strip():
            return content.strip()
    return ""


def _count_tool_calls(result: dict[str, Any]) -> int:
    return sum(
        len(getattr(msg, "tool_calls", None) or [])
        for msg in result.get("messages", [])
    )


def run_profile_agent(
    root: Path,
    info: CodebaseInfo,
    resources: dict[str, list[str]],
    api_key: str = "",
) -> tuple[str, int]:
    """Profile Agent 실행. (final_text, tool_calls_count) 반환."""
    llm = get_llm(api_key=api_key or settings.llm.api_key, model=settings.llm.model)
    tools = _build_tools(root)

    middleware = [
        TodoListMiddleware(),
        ContextEditingMiddleware(edits=[
            ClearToolUsesEdit(trigger=70_000, keep=3),
        ]),
        SummarizationMiddleware(model=llm, trigger=("tokens", 85_000)),
        ToolCallLimitMiddleware(run_limit=50),
        ModelCallLimitMiddleware(run_limit=25, thread_limit=200),
        PatchToolCallsMiddleware(),
    ]
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=middleware,
    )

    user_msg = _render_prompt(info, resources)
    logger.info("[PHASE:1:PROFILE] Agent 시작")
    result = agent.invoke({"messages": [{"role": "user", "content": user_msg}]})

    final_text = _extract_final_text(result)
    tool_calls = _count_tool_calls(result)
    logger.info("[PHASE:1:PROFILE] Agent 완료 | tool_calls=%d", tool_calls)
    return final_text, tool_calls
